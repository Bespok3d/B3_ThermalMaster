# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the thermal streamer tests.

Two things make the module under test awkward to import, and both are handled here.

It is named `thermal-master-stream.py`, which is a legal program name and an illegal module name, so
it is loaded by path rather than by `import`. And it imports the vendored `p3_camera` driver at
module scope. The real driver is present and would import, but it models a USB device, so a stand-in
is registered under that name before the load instead. The stand-in is the fake camera: it reads
from a scripted list of frames, where an entry is either a frame to return or an exception to raise,
which is how a glitch or a vanished device is described to a test as plain data.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import ModuleType

import fake_camera
import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
STREAMER_PATH = PLUGIN_DIR / "files" / "bin" / "thermal-master-stream.py"


def install_stand_in_usb() -> None:
    """usb.core.find is the only USB call the streamer makes without going through the driver."""

    usb_package = types.ModuleType("usb")
    usb_core = types.ModuleType("usb.core")
    usb_core.find = fake_camera.find_usb_device
    usb_package.core = usb_core
    sys.modules["usb"] = usb_package
    sys.modules["usb.core"] = usb_core


def install_stand_in_driver() -> ModuleType:
    install_stand_in_usb()
    driver = types.ModuleType("p3_camera")
    driver.VID = fake_camera.THERMAL_MASTER_VENDOR_ID
    driver.Model = types.SimpleNamespace(P1="p1", P3="p3")
    driver.P3Camera = fake_camera.StandInCamera
    driver.get_model_config = fake_camera.get_model_config
    driver.raw_to_celsius = fake_camera.raw_to_celsius
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
def thermal_ramp_frame():
    """A P1-shaped frame ramping from about 24 C to about 40 C.

    A ramp rather than a flat field, so percentile-based auto-ranging has a real distribution to
    work against and the coldest and hottest pixels are unambiguous.
    """

    return fake_camera.thermal_frame()


@pytest.fixture(autouse=True)
def forget_stand_in_cameras():
    """Each test sees only the cameras its own code constructed, on an empty USB bus."""

    fake_camera.StandInCamera.instances.clear()
    fake_camera.PRESENT_PRODUCT_IDS.clear()
    yield
    fake_camera.StandInCamera.instances.clear()
    fake_camera.PRESENT_PRODUCT_IDS.clear()
