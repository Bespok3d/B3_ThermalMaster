# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""How the capture loop behaves when the camera misbehaves.

Every case here was a real defect: a glitched frame that tore down the whole camera, an empty read
that froze the stream forever while the process looked healthy, a stop that left the USB interface
claimed so the next start could not open the device.
"""

from __future__ import annotations

import threading

import fake_camera
import pytest
from fake_camera import (
    P1_PRODUCT_ID,
    ScriptExhaustedError,
    StandInCamera,
    thermal_frame,
)


@pytest.fixture
def frame_store(thermal_streamer):
    """A frame store somebody is watching.

    Since 0.23.0 the capture loop renders only when something has asked for a picture, so a store
    nobody has asked anything of produces no frames at all. Every test in this file is about what
    the loop does while it is working, so they all start from somebody watching; the tests about
    the idling itself say so for themselves.
    """

    store = thermal_streamer.LatestFrame()
    store.note_interest()
    return store


def run_until_script_ends(thermal_streamer, camera, frame_store, renderer_source):
    shutdown = threading.Event()
    with pytest.raises(ScriptExhaustedError):
        thermal_streamer.stream_frames(camera, frame_store, renderer_source, shutdown)


def test_a_glitched_frame_does_not_end_the_session(thermal_streamer, frame_store, renderer_source):
    """A marker mismatch is one bad frame. Reconnecting over it costs seconds of dead video."""

    marker_mismatch = thermal_streamer.FrameMarkerMismatchError("cnt1 mismatch")
    camera = StandInCamera(scripted_frames=[marker_mismatch, thermal_frame(), thermal_frame()])

    run_until_script_ends(thermal_streamer, camera, frame_store, renderer_source)

    assert frame_store.published_count == 2


def test_a_camera_that_stops_producing_frames_ends_the_session(
    thermal_streamer, frame_store, renderer_source
):
    """The old loop slept and retried forever here, so the stream froze on its last good image."""

    empty_reads = [None] * thermal_streamer.MAX_CONSECUTIVE_FRAME_FAILURES
    camera = StandInCamera(scripted_frames=empty_reads)
    shutdown = threading.Event()

    with pytest.raises(thermal_streamer.CameraStalledError):
        thermal_streamer.stream_frames(camera, frame_store, renderer_source, shutdown)


def test_a_good_frame_forgives_the_failures_before_it(
    thermal_streamer, frame_store, renderer_source
):
    """Occasional empty reads are not a stall, however many there are in total."""

    almost_stalled = [None] * (thermal_streamer.MAX_CONSECUTIVE_FRAME_FAILURES - 1)
    camera = StandInCamera(scripted_frames=[*almost_stalled, thermal_frame(), *almost_stalled])

    run_until_script_ends(thermal_streamer, camera, frame_store, renderer_source)

    assert frame_store.published_count == 1


def test_a_requested_shutdown_stops_the_loop_without_reading(
    thermal_streamer, frame_store, renderer_source
):
    camera = StandInCamera(scripted_frames=[thermal_frame()])
    shutdown = threading.Event()
    shutdown.set()

    thermal_streamer.stream_frames(camera, frame_store, renderer_source, shutdown)

    assert camera.frames_read == 0
    assert frame_store.published_count == 0


def test_the_camera_is_released_when_a_session_ends(thermal_streamer, frame_store, renderer_source):
    """Both halves matter: stop_streaming resets the alternate setting, disconnect drops the
    claim."""

    fake_camera.PRESENT_PRODUCT_IDS.add(P1_PRODUCT_ID)
    shutdown = threading.Event()

    with pytest.raises(ScriptExhaustedError):
        thermal_streamer.run_capture_session(frame_store, renderer_source, shutdown)

    camera = fake_camera.StandInCamera.instances[-1]
    assert camera.stop_streaming_calls == 1
    assert camera.disconnect_calls == 1


def test_a_session_that_reaches_the_camera_says_so_in_the_log(
    thermal_streamer, frame_store, renderer_source, capsys
):
    """The log recorded when capture failed and never when it came back, so every outage had a
    start and no end. One line once a session is streaming gives it the end (F-72)."""

    fake_camera.PRESENT_PRODUCT_IDS.add(P1_PRODUCT_ID)
    shutdown = threading.Event()

    with pytest.raises(ScriptExhaustedError):
        thermal_streamer.run_capture_session(frame_store, renderer_source, shutdown)

    written = capsys.readouterr().err.splitlines()
    connected = [line for line in written if "camera connected" in line]
    assert len(connected) == 1
    assert connected[0].endswith("bespok3d/thermal-master: camera connected: P1, streaming")


def test_a_camera_pulled_mid_session_is_still_released(
    thermal_streamer, frame_store, renderer_source
):
    """Release runs while unwinding from a failure, so it cannot assume the device is there."""

    class VanishedCamera(StandInCamera):
        def stop_streaming(self) -> None:
            super().stop_streaming()
            raise OSError("no such device")

        def disconnect(self) -> None:
            super().disconnect()
            raise OSError("no such device")

    camera = VanishedCamera(scripted_frames=[])

    thermal_streamer.release_camera(camera)

    assert camera.stop_streaming_calls == 1
    assert camera.disconnect_calls == 1


def test_the_reconnect_delay_backs_off_and_stops_at_the_ceiling(thermal_streamer):
    initial_delay = thermal_streamer.INITIAL_RECONNECT_DELAY_SECONDS
    doubled = thermal_streamer.next_reconnect_delay(initial_delay)

    assert doubled == initial_delay * 2
    assert thermal_streamer.next_reconnect_delay(thermal_streamer.MAX_RECONNECT_DELAY_SECONDS) == (
        thermal_streamer.MAX_RECONNECT_DELAY_SECONDS
    )
