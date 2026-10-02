# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The timelapse's switch to wide range when something in view passes a temperature.

High sensitivity reads nothing above about 205 C, so a hotter nozzle in view stops there in every
layer of a clip. While a print is recorded, the first frame past the threshold switches the camera
to wide range for the rest of that print, one way, without changing the gain somebody chose; no
frame is taken for five seconds after, while the camera recalibrates; and the chosen gain comes
back when the print ends, however it ends.
"""

from __future__ import annotations

import dataclasses

import fake_camera
import pytest
from fake_camera import GainMode

STARTED = 1_790_000_000.0


def raw_for(celsius):
    return (celsius + fake_camera.KELVIN_AT_ZERO_CELSIUS) * fake_camera.RAW_UNITS_PER_KELVIN


def frame_with(celsius):
    frame = fake_camera.thermal_frame(20.0, 40.0)
    frame[10, 10] = int(raw_for(celsius))
    return frame


@pytest.fixture
def store(thermal_streamer):
    return thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)


def test_the_override_wins_without_changing_the_chosen_gain(thermal_streamer, store):
    device = thermal_streamer.DeviceController(store)
    camera = fake_camera.StandInCamera()
    device.apply(camera)

    device.override_gain(thermal_streamer.GAIN_LOW)
    device.apply(camera)

    assert camera.gain_modes_set == [GainMode.HIGH, GainMode.LOW]
    assert store.camera_snapshot()[1].gain == thermal_streamer.GAIN_HIGH
    assert device.status()["gain"] == thermal_streamer.GAIN_LOW
    assert device.status()["gain_overridden"] is True


def test_a_reconnect_sends_the_override_again_and_clearing_it_sends_the_chosen_gain(
    thermal_streamer, store
):
    device = thermal_streamer.DeviceController(store)
    device.override_gain(thermal_streamer.GAIN_LOW)
    first = fake_camera.StandInCamera()
    device.apply(first)
    device.forget_session()
    replugged = fake_camera.StandInCamera()
    device.apply(replugged)
    device.apply(replugged)

    device.override_gain(None)
    device.apply(replugged)

    assert first.gain_modes_set == [GainMode.LOW]
    assert replugged.gain_modes_set == [GainMode.LOW, GainMode.HIGH]
    assert device.gain_overridden is False


def test_the_tap_calls_once_when_a_frame_passes_the_threshold(thermal_streamer):
    tap = thermal_streamer.FrameTap()
    calls = []
    tap.arm(raw_for(145.0), thermal_streamer.GAIN_HIGH, lambda: calls.append(1))

    tap.offer(frame_with(140.0), thermal_streamer.GAIN_HIGH)
    tap.offer(frame_with(150.0), thermal_streamer.GAIN_HIGH)
    tap.offer(frame_with(160.0), thermal_streamer.GAIN_HIGH)

    assert calls == [1]


def test_the_tap_ignores_frames_taken_in_another_gain(thermal_streamer):
    tap = thermal_streamer.FrameTap()
    calls = []
    tap.arm(raw_for(145.0), thermal_streamer.GAIN_HIGH, lambda: calls.append(1))

    tap.offer(frame_with(300.0), thermal_streamer.GAIN_LOW)

    assert calls == []


def test_no_frame_is_taken_while_the_camera_settles_after_a_switch(thermal_streamer, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(thermal_streamer.timelapse.time, "monotonic", lambda: clock[0])
    tap = thermal_streamer.FrameTap()
    tap.offer(frame_with(40.0), thermal_streamer.GAIN_HIGH)
    tap.request()

    tap.offer(frame_with(40.0), thermal_streamer.GAIN_LOW)
    clock[0] += thermal_streamer.GAIN_SETTLE_SECONDS - 0.1
    tap.offer(frame_with(40.0), thermal_streamer.GAIN_LOW)
    unsettled = tap.settling()
    clock[0] += 0.2
    tap.offer(frame_with(41.0), thermal_streamer.GAIN_LOW)

    assert unsettled is True
    frame = tap.collect(0.0)
    assert frame is not None and frame.gain == thermal_streamer.GAIN_LOW


def test_a_camera_coming_up_in_its_gain_is_not_a_switch(thermal_streamer):
    tap = thermal_streamer.FrameTap()

    tap.offer(frame_with(40.0), None)
    tap.offer(frame_with(40.0), thermal_streamer.GAIN_HIGH)

    assert tap.settling() is False


def test_the_threshold_is_a_setting_kept_in_range(thermal_streamer):
    current = thermal_streamer.TimelapseSettings()

    assert current.timelapse_auto_gain is True
    assert current.timelapse_auto_gain_celsius == 195.0
    assert thermal_streamer.clamped_threshold("120", 145.0) == 120.0
    assert thermal_streamer.clamped_threshold("5", 145.0) == thermal_streamer.MIN_AUTO_GAIN_CELSIUS
    assert thermal_streamer.clamped_threshold(9000, 145.0) == (
        thermal_streamer.MAX_AUTO_GAIN_CELSIUS
    )
    assert thermal_streamer.clamped_threshold("hot", 145.0) == 145.0


def test_the_form_and_json_carry_the_switch(thermal_streamer):
    current = thermal_streamer.TimelapseSettings()

    sent = thermal_streamer.timelapse_settings_from_json(
        {"timelapse_auto_gain": False, "timelapse_auto_gain_celsius": 130}, current
    )
    posted = thermal_streamer.timelapse_settings_from_form(
        {"timelapse_auto_gain_celsius": ["140"]}, current
    )

    assert (sent.timelapse_auto_gain, sent.timelapse_auto_gain_celsius) == (False, 130.0)
    assert (posted.timelapse_auto_gain, posted.timelapse_auto_gain_celsius) == (False, 140.0)


class Tap:
    """The frame tap's watching half, and a camera that sends a frame whenever one is asked for."""

    def __init__(self, streamer):
        self.streamer = streamer
        self.armed = None

    def arm(self, threshold, gain=None, on_hot=None):
        self.armed = (threshold, gain, on_hot) if threshold is not None else None

    def request(self):
        pass

    def collect(self, _timeout):
        return self.streamer.CapturedFrame(fake_camera.thermal_frame(), "high", STARTED)


class Client:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.base_url = "http://stand-in"

    def print_status(self):
        return self.statuses.pop(0) if self.statuses else None

    def newest_job(self):
        return None

    def has_root(self, _root):
        return False


def service_with(streamer, root, palettes, statuses, **timelapse):
    store = streamer.SettingsStore("ironbow", streamer.RenderSettings(), None)
    store.update_timelapse(
        dataclasses.replace(streamer.TimelapseSettings(), timelapse=True, **timelapse)
    )
    overrides = []
    tap = Tap(streamer)
    service = streamer.TimelapseService(streamer.TimelapseWiring(
        root=root, client=Client(statuses), settings_store=store, tap=tap,
        streaming=lambda: True, palettes=palettes, ffmpeg=None, override_gain=overrides.append,
    ))
    return service, tap, overrides


def printing(streamer, layer):
    return streamer.PrintStatus("printing", "cube.gcode", layer, 3)


def test_a_recorded_print_arms_the_switch_and_a_hot_frame_switches_it_for_the_print(
    thermal_streamer, palettes, tmp_path
):
    statuses = [printing(thermal_streamer, layer) for layer in (0, 0, 1, 2)]
    service, tap, overrides = service_with(thermal_streamer, tmp_path, palettes, statuses)
    service.step()
    threshold, gain, on_hot = tap.armed

    on_hot()
    service.step()
    service.step()

    (recording,) = thermal_streamer.recordings(tmp_path)
    assert threshold == pytest.approx(
        thermal_streamer.raw_for_celsius(thermal_streamer.DEFAULT_AUTO_GAIN_CELSIUS), abs=0.5
    )
    assert gain == thermal_streamer.GAIN_HIGH
    assert overrides[0] == thermal_streamer.GAIN_LOW
    assert recording.gain_switch["layer"] == 0
    assert tap.armed is None
    assert "Switched to wide range before the first layer" in service.status_line()


def test_the_chosen_gain_comes_back_when_the_print_ends(thermal_streamer, palettes, tmp_path):
    statuses = [printing(thermal_streamer, 1), printing(thermal_streamer, 1),
                thermal_streamer.PrintStatus("cancelled", "cube.gcode", 1, 3)]
    service, tap, overrides = service_with(thermal_streamer, tmp_path, palettes, statuses)
    service.step()
    tap.armed[2]()
    service.step()

    service.step()

    assert overrides[-1] is None
    assert tap.armed is None


def test_a_switched_print_carried_on_after_a_restart_switches_again(
    thermal_streamer, palettes, tmp_path
):
    left = thermal_streamer.Recording.create(tmp_path, STARTED, None, "cube.gcode")
    left.note_gain_switch(3, STARTED + 60, 145.0)
    service, tap, overrides = service_with(
        thermal_streamer, tmp_path, palettes, [printing(thermal_streamer, 5)]
    )

    service.step()

    assert overrides == [thermal_streamer.GAIN_LOW]
    assert tap.armed is None


def test_with_the_switch_off_nothing_is_armed(thermal_streamer, palettes, tmp_path):
    service, tap, overrides = service_with(
        thermal_streamer, tmp_path, palettes, [printing(thermal_streamer, 1)],
        timelapse_auto_gain=False,
    )

    service.step()

    assert tap.armed is None
    assert overrides == []


def test_a_threshold_of_its_own_is_armed_at_its_own_count(thermal_streamer, palettes, tmp_path):
    service, tap, _ = service_with(
        thermal_streamer, tmp_path, palettes, [printing(thermal_streamer, 1)],
        timelapse_auto_gain_celsius=120.0,
    )

    service.step()

    assert tap.armed[0] == pytest.approx(thermal_streamer.raw_for_celsius(120.0), abs=0.5)


def test_the_list_and_the_camera_line_say_so(thermal_streamer):
    said = thermal_streamer.gain_switch_sentence({"layer": 12, "at": STARTED, "celsius": 145.0})
    line = thermal_streamer.describe_device(
        {"streaming": True, "gain_overridden": True, "shutter": {"state": "idle"}}
    )

    assert said == " Switched to wide range at layer 12, when something passed 145 C."
    assert "Wide range for this print" in line
