# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the thermal streamer tests.

The plugin imports the vendored `p3_camera` driver at module scope. The real driver is present and
would import, but it models a USB device, so a stand-in is registered under that name first. The
stand-in is the fake camera: it reads from a scripted list of frames, where an entry is either a
frame to return or an exception to raise, which is how a glitch or a vanished device is described
to a test as plain data. It also records what it was told, so a command sent from the wrong thread
fails here rather than on hardware.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import ModuleType

import fake_camera
import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
LIB_DIR = PLUGIN_DIR / "files" / "lib"


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
    driver.COMMANDS = fake_camera.COMMANDS
    driver.raw_to_celsius = fake_camera.raw_to_celsius
    driver.raw_to_celsius_corrected = fake_camera.raw_to_celsius_corrected
    driver.EnvParams = fake_camera.EnvParams
    driver.GainMode = fake_camera.GainMode
    driver.FrameMarkerMismatchError = type("FrameMarkerMismatchError", (Exception,), {})
    sys.modules["p3_camera"] = driver
    return driver


def load_streamer() -> ModuleType:
    """The plugin's package, with the stand-in driver already in place of the real one.

    The package re-exports everything public, so this returns one namespace with the whole plugin
    on it. That is what let the split into modules happen without touching a single test: the tests
    ask for names, not for files, and a test suite that passes unchanged is the only real evidence
    that a refactor changed nothing.
    """

    install_stand_in_driver()
    sys.path.insert(0, str(LIB_DIR))
    import thermal_master

    return thermal_master


@pytest.fixture(scope="session")
def thermal_streamer() -> ModuleType:
    return load_streamer()


@pytest.fixture
def renderer_source(thermal_streamer):
    """A source wired to a defaults-only store, which is what the capture path actually consumes."""

    palettes = thermal_streamer.build_palettes()
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    return thermal_streamer.RendererSource(store, palettes)


@pytest.fixture
def palettes(thermal_streamer):
    """The built palettes. Four test files had written this line for themselves."""

    return thermal_streamer.build_palettes()


@pytest.fixture
def settings_dict(thermal_streamer):
    """A settings dictionary shaped exactly like the one the plugin hands its own page.

    Hand written literals kept going stale here: every new setting broke two tests that had no
    opinion about it, and the fix each time was to paste one more key. Built from the real
    dataclasses instead, so a test says only what it cares about and the rest comes along.
    """

    import dataclasses

    def build(palette: str = "ironbow", **overrides):
        camera_fields = {field.name for field in
                         dataclasses.fields(thermal_streamer.CameraSettings())}
        camera = {key: overrides.pop(key) for key in list(overrides) if key in camera_fields}
        store = thermal_streamer.SettingsStore(
            palette,
            dataclasses.replace(thermal_streamer.RenderSettings(), **overrides),
            None,
            dataclasses.replace(thermal_streamer.CameraSettings(), **camera),
        )
        return store.as_dict()

    return build


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
