# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the thermal streamer tests.

Two things make the module under test awkward to import, and both are handled here.

It is named `thermal-p1-stream.py`, which is a legal program name and an illegal module name, so it
is loaded by path rather than by `import`. And it imports the vendored `p3_camera` driver at module
scope, which a fresh clone does not have, because that file is fetched at build time and gitignored.
So a stand-in driver is registered before the load. The stand-in doubles as the fake camera: no USB
device is involved anywhere in this suite, and none of these tests need one.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
STREAMER_PATH = PLUGIN_DIR / "files" / "bin" / "thermal-p1-stream.py"

# The driver's own scaling, reproduced so the stand-in converts exactly as the real one does:
# raw values are sixty-fourths of a Kelvin.
RAW_UNITS_PER_KELVIN = 64
KELVIN_AT_ZERO_CELSIUS = 273.15

P1_SENSOR_WIDTH = 160
P1_SENSOR_HEIGHT = 120


def raw_to_celsius(raw: np.ndarray) -> np.ndarray:
    return np.asarray(raw, dtype=np.float32) / RAW_UNITS_PER_KELVIN - KELVIN_AT_ZERO_CELSIUS


@dataclass
class StandInModelConfig:
    model: str
    sensor_width: int
    sensor_height: int


def get_model_config(model: str = "p1") -> StandInModelConfig:
    if model == "p1":
        return StandInModelConfig(
            model="p1", sensor_width=P1_SENSOR_WIDTH, sensor_height=P1_SENSOR_HEIGHT
        )
    return StandInModelConfig(model="p3", sensor_width=256, sensor_height=192)


@dataclass
class StandInCamera:
    """A camera that reads from a scripted list instead of a USB endpoint.

    Each entry in `scripted_frames` is either a thermal frame to return or an exception to raise, so
    a test can describe a run of good frames, a glitch, or a dropped device as plain data.
    """

    config: StandInModelConfig = field(default_factory=get_model_config)
    scripted_frames: list = field(default_factory=list)
    connected: bool = False
    streaming: bool = False
    frames_read: int = 0
    disconnect_calls: int = 0

    def connect(self) -> None:
        self.connected = True

    def init(self) -> tuple[str, str]:
        return ("stand-in", "0.0.0")

    def start_streaming(self) -> None:
        self.streaming = True

    def stop_streaming(self) -> None:
        self.streaming = False

    def read_frame_both(self):
        if self.frames_read >= len(self.scripted_frames):
            raise AssertionError("the test ran out of scripted frames")
        scripted = self.scripted_frames[self.frames_read]
        self.frames_read += 1
        if isinstance(scripted, BaseException):
            raise scripted
        return (None, scripted)

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False


def install_stand_in_driver() -> ModuleType:
    driver = types.ModuleType("p3_camera")
    driver.Model = types.SimpleNamespace(P1="p1", P3="p3")
    driver.P3Camera = StandInCamera
    driver.get_model_config = get_model_config
    driver.raw_to_celsius = raw_to_celsius
    driver.FrameMarkerMismatchError = type("FrameMarkerMismatchError", (Exception,), {})
    sys.modules["p3_camera"] = driver
    return driver


def load_streamer() -> ModuleType:
    install_stand_in_driver()
    spec = importlib.util.spec_from_file_location("thermal_stream_under_test", STREAMER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the streamer from {STREAMER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def thermal_streamer() -> ModuleType:
    return load_streamer()


@pytest.fixture
def thermal_ramp_frame() -> np.ndarray:
    """A P1-shaped frame ramping smoothly from about 24 C to about 40 C.

    A ramp rather than a constant field, so percentile-based auto-ranging has a real distribution to
    work against and the coldest and hottest pixels are unambiguous.
    """

    coldest_raw = (24.0 + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN
    hottest_raw = (40.0 + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN
    pixel_count = P1_SENSOR_WIDTH * P1_SENSOR_HEIGHT
    ramp = np.linspace(coldest_raw, hottest_raw, pixel_count, dtype=np.float32)
    return ramp.reshape((P1_SENSOR_HEIGHT, P1_SENSOR_WIDTH)).astype(np.uint16)
