# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A camera that reads from a script instead of a USB endpoint.

The real driver models a device, and the failures worth testing are the ones a device produces at
awkward moments: a frame whose markers disagree, a read that returns nothing because streaming
stopped without saying so, a camera pulled out mid-session. Each of those is one entry in a list
here, so a test describes a fault as data rather than by patching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


def find_usb_device(idVendor: int, idProduct: int):  # noqa: N803 - pyusb's own parameter names
    """Stand in for usb.core.find, which is the only USB call the streamer makes itself."""

    if idVendor != THERMAL_MASTER_VENDOR_ID:
        return None
    return object() if idProduct in PRESENT_PRODUCT_IDS else None


class ScriptExhaustedError(Exception):
    """The scripted frames ran out. How a test says "the session ends here"."""


def raw_to_celsius(raw: np.ndarray) -> np.ndarray:
    return np.asarray(raw, dtype=np.float32) / RAW_UNITS_PER_KELVIN - KELVIN_AT_ZERO_CELSIUS


@dataclass
class StandInModelConfig:
    model: str
    sensor_width: int
    sensor_height: int
    pid: int = P1_PRODUCT_ID


def get_model_config(model: str = "p1") -> StandInModelConfig:
    if model == "p1":
        return StandInModelConfig(
            model="p1", sensor_width=P1_SENSOR_WIDTH, sensor_height=P1_SENSOR_HEIGHT,
            pid=P1_PRODUCT_ID,
        )
    return StandInModelConfig(
        model="p3", sensor_width=256, sensor_height=192, pid=P3_PRODUCT_ID
    )


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

    def __post_init__(self) -> None:
        StandInCamera.instances.append(self)

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
        if self.frames_read >= len(self.scripted_frames):
            raise ScriptExhaustedError("the test ran out of scripted frames")
        scripted = self.scripted_frames[self.frames_read]
        self.frames_read += 1
        if isinstance(scripted, BaseException):
            raise scripted
        return (None, scripted)

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
