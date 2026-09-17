# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Switching the camera off, which is deeper than idling.

Idling stops the rendering and keeps reading the camera, so waking costs one frame. Off releases
the device: nothing is read, the camera can be unplugged, and the printer pays nothing at all for
having the plugin installed. That makes it a state a person chooses rather than one the plugin
falls into, so it has a switch, it survives a restart, and it says so in the picture itself.

The placeholder travelling the ordinary frame path is the load-bearing decision here. Everything
showing the camera then shows the switched off state with no special case anywhere, and the
temperatures behind it go away on their own, because publishing a picture with no measurements is
exactly what clears them.
"""

from __future__ import annotations

import dataclasses
import json
import threading

import pytest
from fake_camera import StandInCamera, thermal_frame


class StandInDevice:
    """A controller that answers about streaming and nothing else.

    The capture loop asks one question of the device on this path, so a stand-in that scripts the
    answers describes "switched off, then switched back on" as data rather than by driving a
    settings store from another thread.
    """

    def __init__(self, answers: list[bool], shutdown: threading.Event | None = None) -> None:
        self._answers = list(answers)
        self._shutdown = shutdown
        self.asked = 0

    @property
    def streaming(self) -> bool:
        answer = self._answers[min(self.asked, len(self._answers) - 1)]
        self.asked += 1
        if self._shutdown is not None and self.asked >= len(self._answers):
            self._shutdown.set()
        return answer

    def apply(self, camera) -> None:
        raise AssertionError("a switched off camera must never be commanded")

    def forget_session(self) -> None:
        return


class StoppingStore:
    """A frame store that ends the loop as soon as it has been given a picture."""

    def __init__(self, shutdown: threading.Event) -> None:
        self._shutdown = shutdown
        self.published: list[bytes] = []
        self.seen_count = 0

    def publish(self, jpeg, stats=None, thermal=None) -> None:
        self.published.append(jpeg)
        self._shutdown.set()


def test_streaming_is_on_to_begin_with(thermal_streamer):
    assert thermal_streamer.CameraSettings().streaming is True


def test_the_stop_button_switches_it_off(thermal_streamer):
    current = thermal_streamer.CameraSettings()

    updated = thermal_streamer.camera_settings_from_form({"command": ["stop-stream"]}, current)

    assert updated.streaming is False


def test_the_start_button_switches_it_on(thermal_streamer):
    off = dataclasses.replace(thermal_streamer.CameraSettings(), streaming=False)

    updated = thermal_streamer.camera_settings_from_form({"command": ["start-stream"]}, off)

    assert updated.streaming is True


def test_an_ordinary_apply_leaves_the_switch_alone(thermal_streamer):
    """The control page posts every field it has on every Apply, and none of them is this."""

    off = dataclasses.replace(thermal_streamer.CameraSettings(), streaming=False)

    updated = thermal_streamer.camera_settings_from_form({"gain": ["low"]}, off)

    assert updated.streaming is False
    assert updated.gain == "low"


def test_a_body_asking_for_both_stops(thermal_streamer):
    """A confused request is read the way that does less work."""

    current = thermal_streamer.CameraSettings()

    updated = thermal_streamer.camera_settings_from_form(
        {"command": ["start-stream", "stop-stream"]}, current
    )

    assert updated.streaming is False


def test_the_viewer_says_it_outright(thermal_streamer):
    """The toolbar toggle knows which way it is going, so it sends the state rather than a press."""

    current = thermal_streamer.CameraSettings()

    updated = thermal_streamer.camera_settings_from_json({"streaming": False}, current)

    assert updated.streaming is False


def test_a_json_body_that_says_nothing_about_it_changes_nothing(thermal_streamer):
    off = dataclasses.replace(thermal_streamer.CameraSettings(), streaming=False)

    assert thermal_streamer.camera_settings_from_json({"units": "celsius"}, off).streaming is False


def test_off_survives_a_restart(thermal_streamer, tmp_path):
    """A reboot must not quietly start burning CPU somebody had turned off."""

    state_file = tmp_path / "settings.json"
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    store.update_camera(dataclasses.replace(thermal_streamer.CameraSettings(), streaming=False))
    restarted = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert json.loads(state_file.read_text())["streaming"] is False
    assert restarted.camera_snapshot()[1].streaming is False


def test_a_switched_off_session_ends_without_reading(thermal_streamer, renderer_source):
    """Returning unwinds through run_capture_session, which is what releases the device."""

    camera = StandInCamera(scripted_frames=[thermal_frame()])
    store = thermal_streamer.LatestFrame()
    store.note_interest()

    thermal_streamer.stream_frames(
        camera, store, renderer_source, threading.Event(), StandInDevice([False])
    )

    assert camera.frames_read == 0
    assert store.published_count == 0


def test_no_device_means_nothing_can_have_turned_it_off(thermal_streamer):
    assert thermal_streamer.streaming_wanted(None) is True


def test_the_loop_publishes_the_placeholder_instead_of_opening_the_camera(thermal_streamer):
    shutdown = threading.Event()
    store = StoppingStore(shutdown)

    thermal_streamer.capture_loop(store, None, shutdown, StandInDevice([False]))

    assert store.published == [thermal_streamer.stream_off_jpeg()]
    assert StandInCamera.instances == []


def test_the_placeholder_says_what_has_happened(thermal_streamer):
    """A picture rather than an error, because it has to travel the frame path to be seen at all."""

    picture = thermal_streamer.stream_off_picture()

    assert thermal_streamer.STREAM_OFF_TITLE == "Stream off"
    assert "Start" in thermal_streamer.STREAM_OFF_HINT
    assert picture.size == thermal_streamer.STREAM_OFF_SIZE
    assert thermal_streamer.stream_off_jpeg()[:2] == b"\xff\xd8"


def test_the_placeholder_is_encoded_once(thermal_streamer):
    """It is republished twice a second for as long as the camera is off."""

    assert thermal_streamer.stream_off_jpeg() is thermal_streamer.stream_off_jpeg()


def test_the_placeholder_carries_no_temperatures(thermal_streamer):
    """There are none behind a picture of words, so frame.bin has to stop offering them."""

    store = thermal_streamer.LatestFrame()
    store.publish(b"a real frame", stats="measurements", thermal="values")

    store.publish(thermal_streamer.stream_off_jpeg())

    assert store.latest_thermal() is None
    assert store.latest_stats() is None


def test_coming_back_starts_the_picture_clean(thermal_streamer, renderer_source):
    """What the renderer is holding describes the scene before the camera was switched off."""

    shutdown = threading.Event()
    restarts = []
    renderer_source.restart = lambda: restarts.append(True)  # type: ignore[method-assign]
    store = thermal_streamer.LatestFrame()

    thermal_streamer.capture_loop(
        store, renderer_source, shutdown, StandInDevice([False, True], shutdown)
    )

    assert restarts == [True]


def test_the_status_reports_the_switch(thermal_streamer):
    """The page renders the button from this, so it has to be in what the device reports."""

    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    device = thermal_streamer.DeviceController(store)

    assert device.streaming is True
    assert device.status()["streaming"] is True
    store.update_camera(dataclasses.replace(thermal_streamer.CameraSettings(), streaming=False))
    assert device.status()["streaming"] is False


def test_the_status_line_says_the_camera_is_off(thermal_streamer):
    said = thermal_streamer.describe_device(
        {"streaming": False, "shutter": {"state": "idle", "detail": None}}
    )

    assert "switched off" in said


@pytest.mark.parametrize(
    ("streaming", "label", "command"),
    [(True, "Stop the camera", "stop-stream"), (False, "Start the camera", "start-stream")],
)
def test_the_control_page_offers_the_other_state(
    thermal_streamer, settings_dict, streaming, label, command
):
    page = thermal_streamer.render_control_page(
        settings_dict(streaming=streaming), ["ironbow"], None
    )

    assert f'value="{command}"' in page
    assert f">{label}</button>" in page


def test_the_viewer_has_the_switch_too(thermal_streamer):
    """Agreed with the maintainer: the same button, where the camera actually is."""

    page = thermal_streamer.render_viewer_page()

    assert 'id="stream"' in page
    assert '.tools .stream {{ display: none; }}' not in page
    assert ".tools .stream" in page
