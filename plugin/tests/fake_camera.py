# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A camera that reads from a script instead of a USB endpoint.

The real driver models a device, and the failures worth testing are the ones a device produces at
awkward moments: a frame whose markers disagree, a read that returns nothing because streaming
stopped without saying so, a camera pulled out mid-session. Each of those is one entry in a list
here, so a test describes a fault as data rather than by patching.

The script is played two ways, because the plugin reads the camera two ways. Its own reader asks
the USB endpoint for a whole frame at a time, so `dev` is a stand-in endpoint that turns each
entry into the transfers a camera would send, packet by packet. The driver's reader, which the
plugin falls back on, is `read_frame_both`, and that takes the same entries as the frames they
stand for. Both move the same place in the script, so a session that changes from one to the
other mid-script carries on where it was.
"""

from __future__ import annotations

import struct
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar

import numpy as np

# The driver's own scaling, reproduced so this converts exactly as the real one does: raw values are
# sixty-fourths of a Kelvin.
RAW_UNITS_PER_KELVIN = 64
KELVIN_AT_ZERO_CELSIUS = 273.15

P1_SENSOR_WIDTH = 160
P1_SENSOR_HEIGHT = 120


THERMAL_MASTER_VENDOR_ID = 0x3474
P1_PRODUCT_ID = 0x45C2
P3_PRODUCT_ID = 0x45A2

# Which cameras the fake USB bus currently has plugged in. A test sets this to say what hardware it
# is standing in front of.
PRESENT_PRODUCT_IDS: set[int] = set()

# Stand-ins for the driver's command table. Only the shutter is used, and only by identity: the
# test asserts that the bytes the streamer sent are the ones the driver calls "shutter", which a
# streamer that invented its own payload would fail.
COMMANDS = {
    "shutter": b"stand-in-shutter-command",
    "gain_high": b"stand-in-gain-high",
    "gain_low": b"stand-in-gain-low",
}


def find_usb_device(idVendor: int, idProduct: int):  # noqa: N803 - pyusb's own parameter names
    """Stand in for usb.core.find, which is the only USB call the streamer makes itself."""

    if idVendor != THERMAL_MASTER_VENDOR_ID:
        return None
    return object() if idProduct in PRESENT_PRODUCT_IDS else None


class ScriptExhaustedError(Exception):
    """The scripted frames ran out. How a test says "the session ends here"."""


class FrameMarkerMismatchError(Exception):
    """The driver's own error for a frame whose two markers carry different counters.

    As a script entry it is that frame. The driver's reader raises it; on the wire it is a frame
    of the right size whose end marker belongs to the frame after.
    """


def raw_to_celsius(raw: np.ndarray) -> np.ndarray:
    return np.asarray(raw, dtype=np.float32) / RAW_UNITS_PER_KELVIN - KELVIN_AT_ZERO_CELSIUS


class GainMode(IntEnum):
    """The driver's own gain modes. AUTO exists in the enum and not in the protocol."""

    LOW = 0
    HIGH = 1
    AUTO = 2


@dataclass
class EnvParams:
    """The driver's environmental parameters, of which only emissivity is exposed so far."""

    emissivity: float = 0.95
    ambient_temp: float = 25.0
    reflected_temp: float = 25.0
    distance: float = 1.0
    humidity: float = 0.5


def raw_to_celsius_corrected(raw, env: EnvParams):
    """The driver's emissivity correction, reproduced here rather than imported.

    The tests that use it assert the direction and the identity case, never an exact figure, so a
    number that only this file agrees with cannot pass: a wrong sign or a swapped term fails, and
    nothing depends on this arithmetic matching the driver digit for digit.
    """

    apparent_k = np.asarray(raw, dtype=np.float32) / RAW_UNITS_PER_KELVIN
    reflected_k = env.reflected_temp + KELVIN_AT_ZERO_CELSIUS
    object_k4 = (apparent_k**4 - (1.0 - env.emissivity) * reflected_k**4) / env.emissivity
    return np.maximum(object_k4, 0.0) ** 0.25 - KELVIN_AT_ZERO_CELSIUS


@dataclass
class StandInModelConfig:
    """The driver's ModelConfig, with the names the driver gives things.

    They have to be the driver's: the plugin's own reader takes its geometry from here, and a
    stand-in that called the width something else would pass every test and fail on a printer.
    `test_frame_reader.py` holds these numbers against the vendored driver's.
    """

    model: str
    sensor_w: int
    sensor_h: int
    pid: int = P1_PRODUCT_ID

    @property
    def frame_rows(self) -> int:
        """The infrared picture, two rows of metadata, and the temperatures."""

        return 2 * self.sensor_h + 2

    @property
    def frame_size(self) -> int:
        return 2 * self.frame_rows * self.sensor_w

    @property
    def frame_read_size(self) -> int:
        return self.frame_size + 2 * FRAME_MARKER.size

    @property
    def thermal_row_start(self) -> int:
        return self.sensor_h + 2


def get_model_config(model: str = "p1") -> StandInModelConfig:
    if model == "p1":
        return StandInModelConfig(
            model="p1", sensor_w=P1_SENSOR_WIDTH, sensor_h=P1_SENSOR_HEIGHT, pid=P1_PRODUCT_ID
        )
    return StandInModelConfig(model="p3", sensor_w=256, sensor_h=192, pid=P3_PRODUCT_ID)


# --------------------------------------------------------------------------------------------
# The wire: what a camera sends, and an endpoint that hands it over as Linux does.
# --------------------------------------------------------------------------------------------

# What a bulk endpoint carries on a high speed link, which is what both cameras negotiate.
USB_PACKET_BYTES = 512

# A frame marker: its own length, a sync byte, the frame's counter, a second counter, and one
# that wraps. The same layout as the driver's MARKER_DTYPE.
FRAME_MARKER = struct.Struct("<BBIIH")

START_SYNC = 0x8C
END_SYNC = 0x8E
WRAPPING_COUNT_A_FRAME = 40
WRAPPING_COUNT_WRAPS_AT = 2048

# What the two parts of a frame the plugin does not want are filled with. Not zero and not each
# other, so temperatures taken from a row too early or too late are plainly not temperatures.
INFRARED_FILL = 0xAAAA
METADATA_FILL = 0x5555


def frame_marker(sync: int, frame_number: int) -> bytes:
    return FRAME_MARKER.pack(
        FRAME_MARKER.size,
        sync + frame_number % 2,
        frame_number,
        0,
        frame_number * WRAPPING_COUNT_A_FRAME % WRAPPING_COUNT_WRAPS_AT,
    )


def frame_transfers(
    config, temperatures: np.ndarray, frame_number: int, ended_as: int | None = None
) -> list[bytes]:
    """One frame as the camera sends it: the start marker and the pixels, then the end marker.

    Two transfers, which is what upstream's P3_PROTOCOL.md says and what both cameras were
    counted doing on the printer on 2026-11-13. `ended_as` gives the end marker another frame's
    counter, which is the glitch the driver calls a marker mismatch.
    """

    pixels = np.full((config.frame_rows, config.sensor_w), INFRARED_FILL, dtype="<u2")
    pixels[config.sensor_h : config.thermal_row_start] = METADATA_FILL
    pixels[config.thermal_row_start :] = temperatures
    closing_number = frame_number if ended_as is None else ended_as
    return [
        frame_marker(START_SYNC, frame_number) + pixels.tobytes(),
        frame_marker(END_SYNC, closing_number),
    ]


@dataclass
class Emission:
    """A script entry for something that is not one sound frame: its transfers, byte for byte.

    The mistimed frame after a calibration, or the tail of a frame the stream was joined in the
    middle of. Only the wire can say these, so the driver's reader is given nothing for one.
    """

    transfers: list[bytes]


class EndpointOverflowError(Exception):
    """A packet arrived that the read had no room left for.

    Linux fails such a read with an overflow, which is why every request the plugin makes is a
    whole number of packets, and why a request that is not fails here too.
    """


class StandInEndpoint:
    """The stream's endpoint, handed over the way Linux hands one to libusb.

    Data arrives in packets. A read ends when its buffer is full or when a packet shorter than a
    full one arrives, whichever is first, so a transfer that is an exact number of packets long
    runs on into the next one. `requested_sizes` keeps what each read asked for.
    """

    def __init__(self, next_transfers: Callable[[], list[bytes]]) -> None:
        self._next_transfers = next_transfers
        self._packets: deque[bytes] = deque()
        self.requested_sizes: list[int] = []

    def _next_packet(self, room_left: int) -> bytes:
        if not self._packets:
            for transfer in self._next_transfers():
                self._packets.extend(
                    transfer[sent : sent + USB_PACKET_BYTES]
                    for sent in range(0, len(transfer), USB_PACKET_BYTES)
                )
        if len(self._packets[0]) > room_left:
            raise EndpointOverflowError(
                f"a packet of {len(self._packets[0])} bytes with room for {room_left}"
            )
        return self._packets.popleft()

    def read(self, endpoint: int, receive, timeout: int) -> int:
        self.requested_sizes.append(len(receive))
        room = memoryview(receive)
        filled = 0
        while filled < len(receive):
            packet = self._next_packet(len(receive) - filled)
            room[filled : filled + len(packet)] = packet
            filled += len(packet)
            if len(packet) < USB_PACKET_BYTES:
                break
        return filled


@dataclass
class StandInCamera:
    """Each entry in `scripted_frames` is a frame to return, an exception to raise, or None.

    None is the driver's own way of saying it read nothing usable, which is not the same as an
    error and is why it has to be expressible separately.
    """

    instances: ClassVar[list[StandInCamera]] = []

    config: StandInModelConfig = field(default_factory=get_model_config)
    scripted_frames: list = field(default_factory=list)
    connected: bool = False
    streaming: bool = False
    frames_read: int = 0
    stop_streaming_calls: int = 0
    disconnect_calls: int = 0
    # What the device was told to do, in order, so a test can assert both that a command was sent
    # and that it was sent on the thread that owns the camera rather than from an HTTP handler.
    gain_modes_set: list = field(default_factory=list)
    commands_sent: list = field(default_factory=list)
    status_reads: int = 0
    fail_next_shutter: BaseException | None = None
    # The same script as the USB endpoint plays it, for the plugin's own reader.
    dev: StandInEndpoint = field(init=False, repr=False)

    @property
    def shutter_triggers(self) -> int:
        return self.commands_sent.count(COMMANDS["shutter"])

    def __post_init__(self) -> None:
        StandInCamera.instances.append(self)
        self.dev = StandInEndpoint(self._next_transfers)

    def _next_scripted(self):
        if self.frames_read >= len(self.scripted_frames):
            raise ScriptExhaustedError("the test ran out of scripted frames")
        scripted = self.scripted_frames[self.frames_read]
        self.frames_read += 1
        return scripted

    def _next_transfers(self) -> list[bytes]:
        """The next script entry as the camera would put it on the wire."""

        scripted = self._next_scripted()
        frame_number = self.frames_read
        if isinstance(scripted, FrameMarkerMismatchError):
            blank = np.zeros((self.config.sensor_h, self.config.sensor_w), dtype=np.uint16)
            return frame_transfers(self.config, blank, frame_number, ended_as=frame_number + 1)
        if isinstance(scripted, BaseException):
            raise scripted
        if scripted is None:
            # Nothing usable: an end marker with no frame in front of it.
            return [frame_marker(END_SYNC, frame_number)]
        if isinstance(scripted, Emission):
            return scripted.transfers
        return frame_transfers(self.config, scripted, frame_number)

    def connect(self) -> None:
        self.connected = True

    def init(self) -> tuple[str, str]:
        return ("stand-in", "0.0.0")

    def start_streaming(self) -> None:
        self.streaming = True

    def stop_streaming(self) -> None:
        self.stop_streaming_calls += 1
        self.streaming = False

    def read_frame_both(self):
        """The next script entry as the driver's own reader would return it."""

        scripted = self._next_scripted()
        if isinstance(scripted, BaseException):
            raise scripted
        if isinstance(scripted, Emission):
            return (None, None)
        return (None, scripted)

    def set_gain_mode(self, mode: GainMode) -> None:
        self.gain_modes_set.append(mode)

    def _send_command(self, command: bytes) -> None:
        if self.fail_next_shutter is not None:
            failure, self.fail_next_shutter = self.fail_next_shutter, None
            raise failure
        self.commands_sent.append(command)

    def _read_status(self) -> int:
        self.status_reads += 1
        return 0x02

    def trigger_shutter(self, return_partial: bool = False) -> None:
        """Present, and deliberately broken, because the real one is broken on a P1.

        The streamer must not call this. If it ever does again, this says so here rather than on
        hardware, where it presents as an obscure memoryview error and a calibration that silently
        never happened.
        """

        raise TypeError("memoryview assignment: lvalue and rvalue have different structures")

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False


def thermal_frame(celsius_low: float = 24.0, celsius_high: float = 40.0) -> np.ndarray:
    """A P1-shaped frame ramping smoothly between two temperatures."""

    coldest_raw = (celsius_low + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN
    hottest_raw = (celsius_high + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN
    pixel_count = P1_SENSOR_WIDTH * P1_SENSOR_HEIGHT
    ramp = np.linspace(coldest_raw, hottest_raw, pixel_count, dtype=np.float32)
    return ramp.reshape((P1_SENSOR_HEIGHT, P1_SENSOR_WIDTH)).astype(np.uint16)
