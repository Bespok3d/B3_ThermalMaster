# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Commands that travel over USB, and the rule about which thread may send them.

`trigger_shutter` and `set_gain_mode` write a control transfer and then read the same bulk endpoint
the frame loop reads, so sending one from an HTTP handler desynchronises the stream for the rest of
the session. The rule is that a request from the network sets a flag and the capture thread acts on
it between frames. These tests pin that rule rather than the wiring that currently implements it:
the fake camera records what it was told and when, so a command sent from the wrong place fails
here instead of on hardware, where it looks like a camera that has started returning nonsense.
"""

from __future__ import annotations

import threading

import fake_camera
import pytest
from fake_camera import GainMode, ScriptExhaustedError, thermal_frame


@pytest.fixture
def store(thermal_streamer):
    return thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)


@pytest.fixture
def device(thermal_streamer, store):
    return thermal_streamer.DeviceController(store)


@pytest.fixture
def camera():
    return fake_camera.StandInCamera()


def run_one_session(thermal_streamer, device, frames):
    """One capture session over a scripted camera, ending when the script runs out."""

    fake_camera.PRESENT_PRODUCT_IDS.add(fake_camera.P1_PRODUCT_ID)
    frame_store = thermal_streamer.LatestFrame()
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    source = thermal_streamer.RendererSource(store, thermal_streamer.build_palettes())
    original = fake_camera.StandInCamera.__init__

    def scripted(self, **kwargs):
        original(self, **kwargs)
        self.scripted_frames = list(frames)

    fake_camera.StandInCamera.__init__ = scripted
    try:
        with pytest.raises(ScriptExhaustedError):
            thermal_streamer.run_capture_session(
                frame_store, source, threading.Event(), device
            )
    finally:
        fake_camera.StandInCamera.__init__ = original
    return fake_camera.StandInCamera.instances[-1]


def test_asking_for_a_shutter_sends_nothing_by_itself(thermal_streamer, device, camera):
    """The whole point: the request must not reach the camera on the requesting thread."""

    device.request_shutter()

    assert camera.shutter_triggers == 0


def test_the_shutter_fires_when_the_capture_thread_applies_it(device, camera):
    device.request_shutter()

    device.apply(camera)

    assert camera.shutter_triggers == 1


def test_repeated_presses_coalesce_into_one_calibration(device, camera):
    """Three clicks want a calibration, not three of them, and each one costs a frame."""

    device.request_shutter()
    device.request_shutter()
    device.request_shutter()

    device.apply(camera)

    assert camera.shutter_triggers == 1


def test_a_shutter_is_not_repeated_on_the_next_frame(device, camera):
    device.request_shutter()
    device.apply(camera)

    device.apply(camera)

    assert camera.shutter_triggers == 1


def test_the_chosen_gain_is_sent_on_the_first_apply(thermal_streamer, store, device, camera):
    store.update_camera(thermal_streamer.CameraSettings(gain="low"))

    device.apply(camera)

    assert camera.gain_modes_set == [GainMode.LOW]


def test_the_gain_is_not_re_sent_every_frame(device, camera):
    device.apply(camera)
    sent_once = list(camera.gain_modes_set)

    for _ in range(10):
        device.apply(camera)

    assert camera.gain_modes_set == sent_once


def test_changing_the_gain_sends_it_again(thermal_streamer, store, device, camera):
    device.apply(camera)

    store.update_camera(thermal_streamer.CameraSettings(gain="low"))
    device.apply(camera)

    assert camera.gain_modes_set == [GainMode.HIGH, GainMode.LOW]


def test_changing_a_palette_does_not_send_a_gain_command(thermal_streamer, store, device, camera):
    """Rendering settings and camera settings are counted separately for exactly this reason."""

    device.apply(camera)
    sent_once = list(camera.gain_modes_set)

    store.update("sepia", thermal_streamer.RenderSettings(rotation=90))
    device.apply(camera)

    assert camera.gain_modes_set == sent_once


def test_a_reconnected_camera_is_put_back_into_the_chosen_gain(
    thermal_streamer, store, device, camera
):
    """A replugged camera comes up in its own default, so the choice has to be re-sent."""

    store.update_camera(thermal_streamer.CameraSettings(gain="low"))
    device.apply(camera)
    replacement = fake_camera.StandInCamera()

    device.forget_session()
    device.apply(replacement)

    assert replacement.gain_modes_set == [GainMode.LOW]


def test_a_failed_shutter_reaches_the_reconnect_path(device, camera):
    """A control transfer that fails is a camera that has gone, not a frame that was slow."""

    camera.fail_next_shutter = OSError("device disconnected")
    device.request_shutter()

    with pytest.raises(OSError, match="device disconnected"):
        device.apply(camera)


def test_a_failed_shutter_is_reported_rather_than_only_raised(device, camera):
    camera.fail_next_shutter = OSError("device disconnected")
    device.request_shutter()
    with pytest.raises(OSError, match="device disconnected"):
        device.apply(camera)

    status = device.status()

    assert status["shutter"]["state"] == "failed"
    assert "disconnected" in status["shutter"]["detail"]


def test_status_reports_the_gain_actually_in_effect_not_the_one_asked_for(
    thermal_streamer, store, device, camera
):
    store.update_camera(thermal_streamer.CameraSettings(gain="low"))

    assert device.status()["gain"] is None
    device.apply(camera)
    assert device.status()["gain"] == "low"


def test_the_capture_session_applies_commands_between_frames(thermal_streamer, device):
    """End to end through the real capture path, which is where the ordering has to hold."""

    device.request_shutter()

    camera = run_one_session(thermal_streamer, device, [thermal_frame(), thermal_frame()])

    assert camera.shutter_triggers == 1
    assert camera.gain_modes_set == [GainMode.HIGH]


def test_a_session_that_starts_without_a_controller_still_streams(thermal_streamer):
    """The controller is optional wiring; the capture path must not require it."""

    camera = run_one_session(thermal_streamer, None, [thermal_frame()])

    assert camera.frames_read == 1


def test_the_form_changes_the_gain(thermal_streamer):
    current = thermal_streamer.CameraSettings()

    assert thermal_streamer.camera_settings_from_form({"gain": ["low"]}, current).gain == "low"


def test_an_unknown_gain_is_refused(thermal_streamer):
    current = thermal_streamer.CameraSettings(gain="low")

    assert thermal_streamer.camera_settings_from_form({"gain": ["turbo"]}, current).gain == "low"


def test_the_gain_survives_a_restart(thermal_streamer, tmp_path):
    state_file = tmp_path / "settings.json"
    first = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )
    first.update_camera(thermal_streamer.CameraSettings(gain="low"))

    second = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert second.camera_snapshot()[1].gain == "low"
    assert second.as_dict()["gain"] == "low"
