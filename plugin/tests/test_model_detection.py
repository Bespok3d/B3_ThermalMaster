# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Which camera the plugin decides it is looking at.

The P1 and the P3 speak the same protocol and differ only in sensor size, so the plugin probes the
USB bus rather than being configured. Getting this wrong is not a graceful failure: the frame size
would be read against the wrong geometry, and every frame after that is nonsense.
"""

from __future__ import annotations

import threading

import fake_camera
import pytest
from fake_camera import P1_PRODUCT_ID, P3_PRODUCT_ID, ScriptExhaustedError


@pytest.fixture
def frame_store(thermal_streamer):
    return thermal_streamer.LatestFrame()


def test_a_p1_on_the_bus_is_detected_as_a_p1(thermal_streamer):
    fake_camera.PRESENT_PRODUCT_IDS.add(P1_PRODUCT_ID)

    assert thermal_streamer.detect_camera_model() == "p1"


def test_a_p3_on_the_bus_is_detected_as_a_p3(thermal_streamer):
    fake_camera.PRESENT_PRODUCT_IDS.add(P3_PRODUCT_ID)

    assert thermal_streamer.detect_camera_model() == "p3"


def test_an_empty_bus_detects_nothing(thermal_streamer):
    assert thermal_streamer.detect_camera_model() is None


def test_an_unrelated_usb_device_is_not_mistaken_for_a_camera(thermal_streamer):
    """The printer's own USB bus carries MCU links and a hub. None of them are cameras."""

    fake_camera.PRESENT_PRODUCT_IDS.add(0x606F)

    assert thermal_streamer.detect_camera_model() is None


def test_a_session_without_a_camera_says_so_rather_than_guessing(
    thermal_streamer, frame_store, renderer_source
):
    """The old code assumed a P1 and let the driver fail somewhere further in."""

    shutdown = threading.Event()

    with pytest.raises(thermal_streamer.CameraNotFoundError):
        thermal_streamer.run_capture_session(frame_store, renderer_source, shutdown)


def test_a_session_uses_the_geometry_of_the_camera_it_found(
    thermal_streamer, frame_store, renderer_source
):
    fake_camera.PRESENT_PRODUCT_IDS.add(P3_PRODUCT_ID)
    shutdown = threading.Event()

    with pytest.raises(ScriptExhaustedError):
        thermal_streamer.run_capture_session(frame_store, renderer_source, shutdown)

    camera = fake_camera.StandInCamera.instances[-1]
    assert camera.config.model == "p3"
    assert (camera.config.sensor_width, camera.config.sensor_height) == (256, 192)
