# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The timelapse's plain logic: its settings, following a print, and taking a frame per layer.

ROADMAP Phase 9. Kept apart from the parts that touch the disk, Moonraker and ffmpeg, so that what
decides when a frame is taken can be tested with nothing but values.

The frame is taken the moment Klipper's layer number changes, which is the moment the slicer's
layer change G-code runs: the part as it stands after the layer before. Some layers are over in
seconds, so nothing waits for a park.
"""

from __future__ import annotations

import dataclasses
import threading
import time

import numpy as np

# How the clip maps temperatures to colours. The live picture's own range, a range of its own, the
# whole print's coldest to hottest, or the range the print had once it had started. The last is the
# candidate default: what the bed and the nozzle do before the first layer is down is not what the
# clip is about. Step 4 of the plan compares them on real clips before a default is final.
TIMELAPSE_RANGE_AS_DISPLAYED = "as-displayed"


TIMELAPSE_RANGE_FIXED = "fixed"


TIMELAPSE_RANGE_WHOLE_PRINT = "whole-print"


TIMELAPSE_RANGE_FROM_START = "from-start"


VALID_TIMELAPSE_RANGES = (
    TIMELAPSE_RANGE_FROM_START,
    TIMELAPSE_RANGE_WHOLE_PRINT,
    TIMELAPSE_RANGE_FIXED,
    TIMELAPSE_RANGE_AS_DISPLAYED,
)


DEFAULT_TIMELAPSE_RANGE = TIMELAPSE_RANGE_FROM_START


# Counted in prints: when one falls off the end its clip, its thumbnail and its temperatures go
# together, so there is one number to think about.
DEFAULT_TIMELAPSE_KEEP = 10


MIN_TIMELAPSE_KEEP = 1


MAX_TIMELAPSE_KEEP = 100


# A fixed range wide enough for a heated bed and a part coming off a hot nozzle.
DEFAULT_TIMELAPSE_LOW_CELSIUS = 20.0


DEFAULT_TIMELAPSE_HIGH_CELSIUS = 120.0


@dataclasses.dataclass(frozen=True)
class TimelapseSettings:
    """What the timelapse does, saved with the rest of the settings.

    Every name is prefixed, because the settings file is flat and a key belongs to whichever
    settings class declares it: `range_mode` is already the live picture's.

    Off until switched on. It writes to the disk and talks to Moonraker, and neither is something
    a camera plugin should start doing to a printer unasked.

    The Moonraker key is here because it is a setting like any other as far as the file goes, and
    nowhere else: it is never handed back to a browser, which is told only whether one is set.
    """

    timelapse: bool = False
    timelapse_keep: int = DEFAULT_TIMELAPSE_KEEP
    timelapse_range_mode: str = DEFAULT_TIMELAPSE_RANGE
    timelapse_range_low_celsius: float = DEFAULT_TIMELAPSE_LOW_CELSIUS
    timelapse_range_high_celsius: float = DEFAULT_TIMELAPSE_HIGH_CELSIUS
    # The readout drawn into a clip, apart from the live picture's, so a clip can come out plain
    # while the tile keeps its numbers, or the other way round. On by default, as the live one is.
    timelapse_colorbar: bool = True
    timelapse_reticle: bool = True
    timelapse_hotspot: bool = True
    timelapse_coldspot: bool = True
    timelapse_spots: bool = True
    # Which colour scale a clip was made with, in its corner and in its name. Off by default: the
    # list on the settings page always says, and a name without it sorts beside the firmware's.
    timelapse_scale_label: bool = False
    timelapse_scale_in_name: bool = False
    moonraker_api_key: str = ""

    def public(self) -> dict:
        """Everything a page may be shown, which is everything but the key."""

        shown = dataclasses.asdict(self)
        del shown["moonraker_api_key"]
        shown["moonraker_api_key_set"] = bool(self.moonraker_api_key)
        return shown


# The button that forgets the saved Moonraker key. A command, like calibrating, rather than a
# setting: there is nothing to show a person about a key except whether one is saved.
FORGET_KEY_ACTION = "forget-moonraker-key"


# The button that sets the clips' readout to whatever the live picture's is.
COPY_READOUT_ACTION = "copy-live-readout"


# The timelapse's readout switches, and the live ones they stand in for in a clip.
CLIP_READOUT_SWITCHES = {
    "timelapse_colorbar": "colorbar",
    "timelapse_reticle": "reticle",
    "timelapse_hotspot": "hotspot",
    "timelapse_coldspot": "coldspot",
}


# The switches that are plain checkboxes, on the form and in JSON.
TIMELAPSE_SWITCHES = (
    *CLIP_READOUT_SWITCHES,
    "timelapse_spots",
    "timelapse_scale_label",
    "timelapse_scale_in_name",
)


def clamped_keep(value: object, current: int) -> int:
    """How many prints to keep, pulled into range rather than refused."""

    try:
        posted = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return current
    return min(max(posted, MIN_TIMELAPSE_KEEP), MAX_TIMELAPSE_KEEP)


# Klipper's print_stats states. Paused is still a print: the layer does not move, and nothing ends.
PRINTING = "printing"


PAUSED = "paused"


ACTIVE_PRINT_STATES = (PRINTING, PAUSED)


# How a print can end, as Klipper says it, and one more for the ways Klipper does not: a print that
# went from printing to standby was cut off by a restart, and a recording the plugin finds still
# open after its own restart was cut off by a reboot or a power loss.
ENDED_PRINT_STATES = ("complete", "cancelled", "error")


INTERRUPTED = "interrupted"


# Switched off on the settings page mid-print. What was taken is kept and gets its clip.
STOPPED = "stopped"


def ended_state(klipper_state: str) -> str:
    """What to call the end of a print, from the state Klipper left it in."""

    return klipper_state if klipper_state in ENDED_PRINT_STATES else INTERRUPTED


@dataclasses.dataclass(frozen=True)
class PrintStatus:
    """The part of Klipper's `print_stats` the timelapse follows.

    The layer numbers are None unless the slicer sends `SET_PRINT_STATS_INFO`, which is the one
    thing this feature asks of the slicer.
    """

    state: str
    filename: str
    current_layer: int | None
    total_layer: int | None
    # Whether a Snapmaker's firmware is recording its own clip of this print. None on a printer that
    # has no such thing to say, which is every printer that is not a Snapmaker.
    firmware_timelapse: bool | None = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_PRINT_STATES


@dataclasses.dataclass(frozen=True)
class PrintStarted:
    filename: str


@dataclasses.dataclass(frozen=True)
class LayerReached:
    layer: int


@dataclasses.dataclass(frozen=True)
class PrintEnded:
    state: str


PrintEvent = PrintStarted | LayerReached | PrintEnded


class PrintTracker:
    """Klipper's state in, what happened to the print out.

    Plain logic, fed one status at a time, so that every way a print can start, move and end is a
    list of values in a test. A tracker that first sees a print already running reports it as
    started: whether that is a new recording or the continuation of one is the caller's question,
    because only the caller can look at what is on the disk.
    """

    def __init__(self) -> None:
        self._active = False
        self._layer: int | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def layer(self) -> int | None:
        """The last layer reported, or None before the first."""

        return self._layer

    def update(self, status: PrintStatus) -> list[PrintEvent]:
        if not self._active:
            return self._while_idle(status)
        return self._while_printing(status)

    def _while_idle(self, status: PrintStatus) -> list[PrintEvent]:
        if not status.active:
            return []
        self._active = True
        self._layer = None
        return [PrintStarted(status.filename), *self._layer_events(status)]

    def _while_printing(self, status: PrintStatus) -> list[PrintEvent]:
        if status.active:
            return self._layer_events(status)
        self._active = False
        return [PrintEnded(ended_state(status.state))]

    def _layer_events(self, status: PrintStatus) -> list[PrintEvent]:
        layer = status.current_layer
        if layer is None or layer == self._layer:
            return []
        self._layer = layer
        return [LayerReached(layer)]


@dataclasses.dataclass(frozen=True)
class CapturedFrame:
    """One frame the capture loop handed over, with what is needed to read it later."""

    counts: np.ndarray
    gain: str | None
    taken_at: float


class FrameTap:
    """Where the capture loop hands over the one frame a layer change asks for.

    The capture loop reads every frame the camera sends, rendered or not (Phase 7g), so taking one
    per layer needs nothing woken: the timelapse asks, and the next frame read is copied here. The
    copy is the whole cost, 38.4 KB once a layer on a P1.

    Asked for on one thread and offered on another, so it is a condition rather than a flag. The
    offer is made for every frame the camera sends, and nearly all of them are not wanted, so that
    case is one attribute read with no lock taken.
    """

    def __init__(self) -> None:
        self._wanted = False
        self._frame: CapturedFrame | None = None
        self._ready = threading.Condition()

    def request(self) -> None:
        """Ask for the next frame. A request not yet answered is simply asked again."""

        with self._ready:
            self._frame = None
            self._wanted = True

    def asked(self) -> bool:
        """Whether a frame is wanted, read without the lock: the check every frame pays for."""

        return self._wanted

    def offer(self, counts: np.ndarray, gain: str | None) -> None:
        """Called by the capture loop with every frame it reads."""

        if not self.asked():
            return
        with self._ready:
            # Asked again under the lock: the answer outside it can be a frame out of date.
            if not self._wanted:
                return
            # Copied, because the array belongs to the driver and the next read may reuse it.
            self._frame = CapturedFrame(np.array(counts, copy=True), gain, time.time())
            self._wanted = False
            self._ready.notify_all()

    def collect(self, timeout: float) -> CapturedFrame | None:
        """The frame asked for, or None when none came in time and the camera is not sending."""

        deadline = time.monotonic() + timeout
        with self._ready:
            while self._frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._ready.wait(remaining)
            frame = self._frame
            self._frame = None
            self._wanted = False
            return frame
