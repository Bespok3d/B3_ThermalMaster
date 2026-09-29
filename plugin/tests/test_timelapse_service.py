# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The timelapse as it runs, one look at the print at a time, against a stand-in Moonraker.

Each test is a sequence of what Moonraker says and what the camera does, and then what is on the
disk. The cases are the ones the plan names: a print from start to finish, a camera that goes
missing or is switched off, a timelapse switched off mid-print, a recording left open by a power
cut, and one left open by a crash while its print carries on.
"""

from __future__ import annotations

import dataclasses

import fake_camera
import pytest

STARTED = 1_790_000_000.0


class StandInClient:
    """Answers from a list, one per look, and the newest job from a field."""

    base_url = "http://127.0.0.1:7125"

    def __init__(self, statuses, job=None, refused=False):
        self.statuses = list(statuses)
        self.job = job
        self.refused = refused

    def print_status(self):
        if self.refused:
            raise self.refusal
        return self.statuses.pop(0) if self.statuses else None

    def newest_job(self):
        return self.job


class StandInTap:
    """A camera that sends a frame whenever one is asked for, unless it has gone."""

    def __init__(self, present=True):
        self.present = present
        self.requests = 0

    def request(self):
        self.requests += 1

    def collect(self, _timeout):
        if not self.present:
            return None
        return StandInTap.frame_type(fake_camera.thermal_frame(), "high", STARTED)


@pytest.fixture
def service_for(thermal_streamer, tmp_path, palettes):
    StandInTap.frame_type = thermal_streamer.CapturedFrame
    StandInClient.refusal = thermal_streamer.MoonrakerRefusedError("401")

    def build(statuses, job=None, tap=None, streaming=True, free=10 * 1024 ** 3, refused=False):
        store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
        store.update_timelapse(
            dataclasses.replace(thermal_streamer.TimelapseSettings(), timelapse=True)
        )
        client = StandInClient(statuses, job, refused)
        service = thermal_streamer.TimelapseService(
            thermal_streamer.TimelapseWiring(
                root=tmp_path, client=client, settings_store=store, tap=tap or StandInTap(),
                streaming=lambda: streaming, palettes=palettes, ffmpeg=None,
                free=lambda _folder: free,
            )
        )
        return service, store

    return build


def printing(streamer, layer, total=4, filename="cube.gcode"):
    return streamer.PrintStatus("printing", filename, layer, total)


def run_all(service, looks):
    for _ in range(looks):
        service.step()


def only_recording(streamer, root):
    (recording,) = streamer.recordings(root)
    return recording


def test_a_print_gets_a_frame_per_layer_and_one_for_the_finished_part(
    thermal_streamer, service_for, tmp_path
):
    statuses = [printing(thermal_streamer, layer) for layer in (1, 2, 2, 3)]
    statuses.append(thermal_streamer.PrintStatus("complete", "cube.gcode", 3, 4))
    service, _ = service_for(statuses, thermal_streamer.JobInfo("0002F1", "cube.gcode", STARTED))

    run_all(service, 5)

    recording = only_recording(thermal_streamer, tmp_path)
    assert [record.layer for record in recording.records()] == [1, 2, 3, 4]
    assert recording.state == "complete"
    assert recording.recording_id == "20260921-141320-0002F1"


def test_a_layer_the_camera_missed_is_recorded_as_missed(thermal_streamer, service_for, tmp_path):
    service, _ = service_for([printing(thermal_streamer, 1)], tap=StandInTap(present=False))

    service.step()

    (record,) = only_recording(thermal_streamer, tmp_path).records()
    assert record.kind == thermal_streamer.CAMERA_MISSING_RECORD


def test_a_layer_with_the_camera_switched_off_is_recorded_as_off(
    thermal_streamer, service_for, tmp_path
):
    service, _ = service_for(
        [printing(thermal_streamer, 1)], tap=StandInTap(present=False), streaming=False
    )

    service.step()

    (record,) = only_recording(thermal_streamer, tmp_path).records()
    assert record.kind == thermal_streamer.CAMERA_OFF_RECORD


def test_switching_the_timelapse_off_mid_print_keeps_what_was_taken(
    thermal_streamer, service_for, tmp_path
):
    service, store = service_for([printing(thermal_streamer, 1), printing(thermal_streamer, 2)])
    run_all(service, 2)

    store.update_timelapse(thermal_streamer.TimelapseSettings(timelapse=False))
    service.step()

    recording = only_recording(thermal_streamer, tmp_path)
    assert recording.state == thermal_streamer.STOPPED
    assert recording.meta["frames"] == 2


def test_a_recording_left_open_with_no_print_running_was_interrupted(
    thermal_streamer, service_for, tmp_path
):
    left = thermal_streamer.Recording.create(tmp_path, STARTED, "0002F1", "cube.gcode")
    service, _ = service_for([thermal_streamer.PrintStatus("standby", "", None, None)])

    service.step()

    assert thermal_streamer.Recording.open(left.folder).state == thermal_streamer.INTERRUPTED


def test_a_recording_left_open_by_a_crash_is_carried_on(thermal_streamer, service_for, tmp_path):
    left = thermal_streamer.Recording.create(tmp_path, STARTED, "0002F1", "cube.gcode")
    left.append(thermal_streamer.LayerRecord(1, thermal_streamer.FRAME_RECORD, "high", STARTED,
                                             fake_camera.thermal_frame()))
    service, _ = service_for(
        [printing(thermal_streamer, 3)], thermal_streamer.JobInfo("0002F1", "cube.gcode", STARTED)
    )

    service.step()

    recording = only_recording(thermal_streamer, tmp_path)
    assert recording.folder == left.folder
    assert [record.layer for record in recording.records()] == [1, 3]


def test_a_recording_left_by_another_print_is_closed_when_a_new_one_starts(
    thermal_streamer, service_for, tmp_path
):
    left = thermal_streamer.Recording.create(tmp_path, STARTED - 3600, "0002F0", "old.gcode")
    service, _ = service_for(
        [printing(thermal_streamer, 1)], thermal_streamer.JobInfo("0002F1", "cube.gcode", STARTED)
    )

    service.step()

    assert thermal_streamer.Recording.open(left.folder).state == thermal_streamer.INTERRUPTED
    assert len(thermal_streamer.recordings(tmp_path)) == 2


def test_below_the_free_space_floor_no_frame_is_kept(thermal_streamer, service_for, tmp_path):
    service, _ = service_for([printing(thermal_streamer, 1)], free=0)

    service.step()

    assert only_recording(thermal_streamer, tmp_path).meta["frames"] == 0
    assert "less than 200 MB free" in service.status_line()


def test_a_slicer_that_sends_no_layers_is_named_as_the_reason(thermal_streamer, service_for):
    service, _ = service_for([thermal_streamer.PrintStatus("printing", "cube.gcode", None, None)])

    service.step()

    assert "not sending layer numbers" in service.status_line()


def test_a_refusing_moonraker_asks_for_the_key(thermal_streamer, service_for):
    service, _ = service_for([], refused=True)

    service.step()

    assert service.status_line() == thermal_streamer.REFUSED_SENTENCE


def test_a_missing_moonraker_is_named(thermal_streamer, service_for):
    service, _ = service_for([])

    service.step()

    assert "Moonraker is not answering" in service.status_line()


def test_off_says_so_and_asks_nothing(thermal_streamer, service_for):
    service, store = service_for([printing(thermal_streamer, 1)])
    store.update_timelapse(thermal_streamer.TimelapseSettings(timelapse=False))

    service.step()

    assert service.status_line().startswith("Off.")


def test_the_print_being_recorded_cannot_be_deleted(thermal_streamer, service_for, tmp_path):
    service, _ = service_for([printing(thermal_streamer, 1)])
    service.step()
    recording = only_recording(thermal_streamer, tmp_path)

    assert not service.delete(recording.recording_id)
    assert recording.folder.is_dir()


def test_a_finished_print_can_be_deleted(thermal_streamer, service_for, tmp_path):
    service, _ = service_for([])
    done = thermal_streamer.Recording.create(tmp_path, STARTED, None, "cube.gcode")
    done.finish("complete")

    assert service.delete(done.recording_id)
    assert not done.folder.exists()


def test_a_finished_print_without_a_clip_is_queued_and_its_failure_noted(
    thermal_streamer, service_for, tmp_path
):
    service, _ = service_for([])
    done = thermal_streamer.Recording.create(tmp_path, STARTED, None, "cube.gcode")
    done.append(thermal_streamer.LayerRecord(1, thermal_streamer.FRAME_RECORD, "high", STARTED,
                                             fake_camera.thermal_frame()))
    done.finish("complete")

    service.make_clip_now(done.recording_id)

    noted = thermal_streamer.Recording.open(done.folder).summary()
    assert "ffmpeg is not installed" in noted["error"]
