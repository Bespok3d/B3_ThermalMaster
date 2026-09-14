# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The USB camera: finding it, reading it, keeping it alive, and telling it things.

The rule that shapes this module is that commands and frames share the same endpoints. Sending a
command from anywhere but the thread that owns the camera means either the acknowledgement is read
as pixels or a frame is read as the acknowledgement, and the stream is desynchronised for the rest
of the session. So the network sets a flag and this drains it between two frames.
"""

from __future__ import annotations

import contextlib
import dataclasses
import sys
import threading
import time
from typing import TYPE_CHECKING, cast

# The vendored driver and pyusb ship no annotations, and neither is ours to annotate: the
# driver is pinned upstream source (VENDORING.md). Everything they hand back is therefore
# Any, which is why the few values that cross this boundary are converted explicitly below
# rather than being passed straight through.
import usb.core  # type: ignore[import-untyped]
from p3_camera import (  # type: ignore[import-not-found]
    COMMANDS,
    VID,
    FrameMarkerMismatchError,
    GainMode,
    Model,
    P3Camera,
    get_model_config,
)

if TYPE_CHECKING:
    import numpy as np

    from .server import LatestFrame
    from .settings import RendererSource, SettingsStore


# Gain is a device command; emissivity is arithmetic done here. They sit together on the control
# page because to a person they are both "how the camera reads", but only one of them travels over
# USB, and that difference decides which thread each one runs on.
#
# The driver's enum has an AUTO alongside these two, and its own comment says the protocol does not
# implement it: set_gain_mode records the mode and sends nothing. So it is not offered.
GAIN_HIGH = "high"


GAIN_LOW = "low"


VALID_GAINS = (GAIN_HIGH, GAIN_LOW)


DEFAULT_GAIN = GAIN_HIGH


GAIN_MODES = {GAIN_HIGH: GainMode.HIGH, GAIN_LOW: GainMode.LOW}


GAIN_DESCRIPTIONS = {
    GAIN_HIGH: "High sensitivity, -20 to 150 C",
    GAIN_LOW: "Wide range, 0 to 550 C",
}


SHUTTER_IDLE = "idle"


SHUTTER_PENDING = "pending"


SHUTTER_DONE = "done"


SHUTTER_FAILED = "failed"


# The form field the calibrate button posts under. Named "command" and emphatically not "action":
# a named form control becomes a property of its own form element in the DOM, so a button named
# "action" makes `form.action` return that button instead of the URL the form posts to. The page's
# script read `form.action`, fetched "[object HTMLButtonElement]", got a 404, and fell back to a
# plain submit, which does not carry the pressed button. The result was a page that reloaded and a
# calibration that was never requested, with nothing in any log to say so.
SHUTTER_FIELD = "command"


SHUTTER_ACTION = "shutter"


# How many reads in a row may come back empty before the camera counts as stalled rather than slow.
# At the idle sleep below this is a fifth of a second of nothing, where a healthy camera delivers
# twenty-five frames a second.
MAX_CONSECUTIVE_FRAME_FAILURES = 20


INITIAL_RECONNECT_DELAY_SECONDS = 3.0


MAX_RECONNECT_DELAY_SECONDS = 60.0


RECONNECT_BACKOFF_FACTOR = 2.0


# Both cameras speak the same protocol and differ only in sensor size, so the plugin drives
# whichever is plugged in rather than being told. The product IDs come from the driver's own model
# configs, so there is one place that knows them and it is not this file.
SUPPORTED_MODELS = (Model.P1, Model.P3)


FRAME_IDLE_SLEEP_SECONDS = 0.01


@dataclasses.dataclass(frozen=True)
class CameraSettings:
    """What gets sent to the hardware, as opposed to what is done with what comes back.

    Separate from RenderSettings, and with a revision of its own, because these travel over USB and
    so can only be applied by the thread that owns the camera. Emissivity is deliberately not here:
    it never reaches the device, it is arithmetic applied to the numbers on the way out, so it
    belongs with the rendering.
    """

    gain: str = DEFAULT_GAIN


def fire_shutter(camera: P3Camera) -> None:
    """Fire the calibration shutter, without asking the driver to read the frame that follows.

    The driver has its own `trigger_shutter`, and it cannot be used on a P1. It sends this command
    and then reads back the mistimed frame the camera emits afterwards, reassembling it from two
    segments whose offsets are absolute line counts measured on a P3. `shutter_seg_2` is 800 lines:
    on a 256 wide sensor that is 204,812 bytes and lands inside the 206,872 byte frame buffer, and
    on a 160 wide P1 it is 128,012 bytes into a buffer of 83,224. The read overruns the buffer
    first, which on hardware surfaced as "memoryview assignment: lvalue and rvalue have different
    structures" and no calibration. Reported upstream. Not patched here: the vendored driver is
    pinned and is not ours to edit (VENDORING.md).

    The command itself is model independent, a fixed control transfer and its acknowledgement, and
    those two lines are all a calibration actually needs. The frame afterwards is the part we did
    not want anyway, and the driver's ordinary reader already copes with it: `read_frame` spots an
    end marker arriving before the end of a frame, drops what it has and starts again, so the next
    read or two resynchronises by itself.

    Reaching past the underscore is deliberate and is the smaller of two couplings. The alternative
    is issuing the control transfers here, which would copy the endpoint, the request numbers and
    the timeout out of the driver and leave them to rot when the pin moves.
    """

    camera._send_command(COMMANDS["shutter"])
    camera._read_status()


class DeviceController:
    """Everything that has to be said to the camera, said on the thread that owns it.

    `trigger_shutter` and `set_gain_mode` are not side channels: they write a control transfer and
    then read the same bulk endpoint the frame loop reads. Called from an HTTP handler, one of two
    things happens, and which one is a matter of timing: the command's acknowledgement is consumed
    as pixels, or a frame is consumed as the acknowledgement. Either desynchronises the stream for
    as long as the session lasts. So a request from the network only sets a flag here, and the
    capture thread acts on it between two frames, where nothing else is in flight.

    The gain is a setting and the shutter is an action, and they need different handling.

    A setting is declarative: the store holds what the gain should be, and this compares its
    revision against the last one applied. That makes it idempotent, so it can be checked every
    frame for the cost of an integer comparison, and it makes a reconnect self-healing, because a
    camera that has just been replugged comes up in its own default and `forget_session` is enough
    to have the user's choice re-sent.

    An action has no such resting state, so it is a flag that is cleared as it is taken. Repeated
    presses coalesce into one pending shutter rather than queueing: a person clicking three times
    wants a calibration, not three of them, and each one costs a dropped frame.
    """

    def __init__(self, settings_store: SettingsStore) -> None:
        self._settings_store = settings_store
        self._lock = threading.Lock()
        self._shutter_requested = False
        self._shutter_state = SHUTTER_IDLE
        self._shutter_detail: str | None = None
        self._applied_gain_revision: int | None = None
        self._gain_in_effect: str | None = None

    def request_shutter(self) -> None:
        """Ask for a calibration. Safe from any thread; nothing here touches the camera."""

        with self._lock:
            self._shutter_requested = True
            self._shutter_state = SHUTTER_PENDING

    def forget_session(self) -> None:
        """A camera that has just been opened is in its own default gain, not the chosen one."""

        with self._lock:
            self._applied_gain_revision = None
            self._gain_in_effect = None

    def apply(self, camera: P3Camera) -> None:
        """Called by the capture thread between frames. The only place commands are sent."""

        self._apply_gain(camera)
        self._apply_shutter(camera)

    def _apply_gain(self, camera: P3Camera) -> None:
        revision, settings = self._settings_store.camera_snapshot()
        with self._lock:
            if revision == self._applied_gain_revision:
                return
        camera.set_gain_mode(GAIN_MODES[settings.gain])
        with self._lock:
            self._applied_gain_revision = revision
            self._gain_in_effect = settings.gain

    def _apply_shutter(self, camera: P3Camera) -> None:
        with self._lock:
            if not self._shutter_requested:
                return
            self._shutter_requested = False
        try:
            fire_shutter(camera)
        except Exception as error:  # noqa: BLE001 - recorded for the page, then re-raised
            with self._lock:
                self._shutter_state = SHUTTER_FAILED
                self._shutter_detail = str(error)
            # A control transfer that fails is a camera that has gone away, not a slow frame, so
            # this belongs on the reconnect path rather than being swallowed into a stuck stream.
            raise
        with self._lock:
            self._shutter_state = SHUTTER_DONE
            self._shutter_detail = None

    def status(self) -> dict:
        """What the device is actually doing, as opposed to what it has been asked to do."""

        with self._lock:
            return {
                "gain": self._gain_in_effect,
                "shutter": {"state": self._shutter_state, "detail": self._shutter_detail},
            }


class CameraStalledError(Exception):
    """The camera is still connected but has stopped producing usable frames."""


class CameraNotFoundError(Exception):
    """No supported thermal camera is on the USB bus."""


def detect_camera_model() -> Model | None:
    """Which supported camera is plugged in, or None if none is.

    Probing beats configuring: the P1 and the P3 differ only in sensor size, the driver already
    knows both, and a user who has to pick from a list is a user who can pick wrong and get a plugin
    that fails in a way the printer cannot explain.
    """

    for model in SUPPORTED_MODELS:
        product_id = get_model_config(model).pid
        if usb.core.find(idVendor=VID, idProduct=product_id) is not None:
            return model
    return None


def next_thermal_frame(camera: P3Camera) -> np.ndarray | None:
    """One thermal frame, or None when the camera returned nothing usable.

    A marker mismatch is a glitched frame, not a dead camera: the driver raises on it, and letting
    that reach the reconnect path costs seconds of dead video for a fault the next read clears.
    """

    try:
        _, thermal_raw = camera.read_frame_both()
    except FrameMarkerMismatchError:
        return None
    # The driver carries no annotations, so this is the boundary where its Any becomes a frame.
    return cast("np.ndarray | None", thermal_raw)


def stream_frames(
    camera: P3Camera,
    frame_store: LatestFrame,
    renderer_source: RendererSource,
    shutdown: threading.Event,
    device: DeviceController | None = None,
) -> None:
    """Publish frames until the camera stalls or a shutdown is asked for.

    Empty reads are tolerated in ones and twos, because that is what a glitch looks like, and
    raised as a stall once there have been enough in a row. The distinction matters: the driver
    returns nothing at all when streaming has silently stopped, and the previous version of this
    loop treated that as a slow frame, so the stream froze on its last good image with the
    process still reporting itself healthy.
    """

    consecutive_failures = 0
    while not shutdown.is_set():
        # Before the read rather than after it, so the first thing a fresh session does is put the
        # camera into the gain the user chose, ahead of any frame being published from the default.
        if device is not None:
            device.apply(camera)
        thermal_raw = next_thermal_frame(camera)
        if thermal_raw is not None:
            consecutive_failures = 0
            rendered = renderer_source.current().render_frame(thermal_raw)
            frame_store.publish(rendered.jpeg, rendered.stats)
            continue
        consecutive_failures += 1
        if consecutive_failures >= MAX_CONSECUTIVE_FRAME_FAILURES:
            raise CameraStalledError(f"no usable frame in {consecutive_failures} reads")
        time.sleep(FRAME_IDLE_SLEEP_SECONDS)


def release_camera(camera: P3Camera) -> None:
    """Hand the USB interface back.

    stop_streaming resets the interface's alternate setting and disconnect releases the claim.
    Skipping either leaves the device claimed, and the next start cannot open it: from the outside
    that looks like a camera that needs unplugging. Both are best-effort, because this runs while
    unwinding from a failure and a camera that has already been pulled raises from both.
    """

    with contextlib.suppress(Exception):
        camera.stop_streaming()
    with contextlib.suppress(Exception):
        camera.disconnect()


def run_capture_session(
    frame_store: LatestFrame,
    renderer_source: RendererSource,
    shutdown: threading.Event,
    device: DeviceController | None = None,
) -> None:
    model = detect_camera_model()
    if model is None:
        raise CameraNotFoundError("no Thermal Master camera on the USB bus")
    camera = P3Camera(config=get_model_config(model))
    camera.connect()
    camera.init()
    camera.start_streaming()
    if device is not None:
        device.forget_session()
    try:
        stream_frames(camera, frame_store, renderer_source, shutdown, device)
    finally:
        release_camera(camera)


def next_reconnect_delay(current_delay: float) -> float:
    """Back off after a failed session, up to a ceiling.

    A camera that is simply not plugged in fails instantly and forever, and a fixed retry turns that
    into a log line every three seconds until someone notices.
    """

    return min(current_delay * RECONNECT_BACKOFF_FACTOR, MAX_RECONNECT_DELAY_SECONDS)


def capture_loop(
    frame_store: LatestFrame,
    renderer_source: RendererSource,
    shutdown: threading.Event,
    device: DeviceController | None = None,
) -> None:
    """Keep a capture session running: an unplug or a read error reconnects, it never exits."""

    reconnect_delay = INITIAL_RECONNECT_DELAY_SECONDS
    while not shutdown.is_set():
        frames_before_session = frame_store.published_count
        try:
            run_capture_session(frame_store, renderer_source, shutdown, device)
            return
        except Exception as error:  # noqa: BLE001
            print(f"thermal-master: capture error: {error}", file=sys.stderr, flush=True)
        if frame_store.published_count > frames_before_session:
            reconnect_delay = INITIAL_RECONNECT_DELAY_SECONDS
        shutdown.wait(reconnect_delay)
        reconnect_delay = next_reconnect_delay(reconnect_delay)
