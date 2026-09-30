# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A print's timelapse on the disk, and what is kept of it.

The case the file format has to survive is the printer losing power mid-print, so the torn tail is
tested as its own case: everything written before the cut comes back, and the half record does not
take the rest with it. Retention is tested for the order things go in, because the order is the
decision: the count first, then temperatures of older prints, and a free space floor over both.
"""

from __future__ import annotations

import fake_camera
import numpy as np

STARTED = 1_790_000_000.0


def frame_record(streamer, layer, gain="high"):
    return streamer.LayerRecord(layer, streamer.FRAME_RECORD, gain, STARTED + layer,
                                fake_camera.thermal_frame())


def test_layers_come_back_as_they_were_written(thermal_streamer, tmp_path):
    recording = thermal_streamer.Recording.create(tmp_path, STARTED, "0001A2", "cube.gcode")
    recording.append(frame_record(thermal_streamer, 1, "high"))
    recording.append(thermal_streamer.LayerRecord(2, thermal_streamer.CAMERA_MISSING_RECORD,
                                                  None, STARTED + 2))
    recording.append(frame_record(thermal_streamer, 3, "low"))

    records = list(thermal_streamer.Recording.open(recording.folder).records())

    assert [record.layer for record in records] == [1, 2, 3]
    assert [record.kind for record in records] == [
        thermal_streamer.FRAME_RECORD,
        thermal_streamer.CAMERA_MISSING_RECORD,
        thermal_streamer.FRAME_RECORD,
    ]
    assert records[0].gain == "high" and records[2].gain == "low"
    assert records[1].counts is None
    assert np.array_equal(records[0].counts, fake_camera.thermal_frame())


def test_a_record_torn_by_a_power_cut_is_ignored_and_the_rest_survive(thermal_streamer, tmp_path):
    recording = thermal_streamer.Recording.create(tmp_path, STARTED, None, "cube.gcode")
    recording.append(frame_record(thermal_streamer, 1))
    recording.append(frame_record(thermal_streamer, 2))
    whole = recording.frames_path.read_bytes()
    recording.frames_path.write_bytes(whole[: len(whole) - 1000])

    assert [record.layer for record in recording.records()] == [1]


def test_the_folder_is_named_for_the_start_and_the_job(thermal_streamer, tmp_path):
    recording = thermal_streamer.Recording.create(tmp_path, STARTED, "00/1A", "cube.gcode")

    assert recording.recording_id == "20260921-141320-001A"
    assert recording.clip_name == "cube_20260921141320_thermal.mp4"


def test_a_request_can_only_name_a_folder_the_plugin_made(thermal_streamer, tmp_path):
    thermal_streamer.Recording.create(tmp_path, STARTED, None, "cube.gcode")

    assert thermal_streamer.find_recording(tmp_path, "20260921-141320") is not None
    assert thermal_streamer.find_recording(tmp_path, "../20260921-141320") is None
    assert thermal_streamer.find_recording(tmp_path, "20260921-141320/..") is None
    assert thermal_streamer.find_recording(tmp_path, "") is None


def test_recordings_are_listed_newest_first(thermal_streamer, tmp_path):
    for offset in (0, 7200, 3600):
        thermal_streamer.Recording.create(tmp_path, STARTED + offset, None, "cube.gcode")

    starts = [recording.started_at for recording in thermal_streamer.recordings(tmp_path)]

    assert starts == [STARTED + 7200, STARTED + 3600, STARTED]


def finished_recording(streamer, root, offset, with_clip=True):
    recording = streamer.Recording.create(root, STARTED + offset, None, "cube.gcode")
    recording.append(frame_record(streamer, 1))
    recording.finish("complete")
    if with_clip:
        recording.clip_path.write_bytes(b"not really a clip")
    return recording


def plenty_of_room(_folder):
    return 10 * 1024 ** 3


def test_prints_past_the_count_are_removed_oldest_first(thermal_streamer, tmp_path):
    made = [finished_recording(thermal_streamer, tmp_path, hour * 3600) for hour in range(5)]

    thermal_streamer.Retention(tmp_path, 3, free=plenty_of_room).prune()

    left = {recording.recording_id for recording in thermal_streamer.recordings(tmp_path)}
    assert left == {recording.recording_id for recording in made[2:]}


def test_temperatures_are_kept_for_the_newest_prints_only(thermal_streamer, tmp_path):
    made = [finished_recording(thermal_streamer, tmp_path, hour * 3600) for hour in range(4)]

    thermal_streamer.Retention(tmp_path, 10, free=plenty_of_room).prune()

    newest_first = list(reversed(made))
    assert [recording.has_frames for recording in newest_first] == [True, True, False, False]
    assert all(recording.has_clip for recording in newest_first)


def test_temperatures_without_a_clip_yet_are_not_dropped(thermal_streamer, tmp_path):
    """A clip still to be made is made from them."""

    made = [finished_recording(thermal_streamer, tmp_path, hour * 3600, with_clip=False)
            for hour in range(4)]

    thermal_streamer.Retention(tmp_path, 10, free=plenty_of_room).prune()

    assert all(recording.has_frames for recording in made)


def test_the_print_being_recorded_is_never_pruned(thermal_streamer, tmp_path):
    for hour in range(3):
        finished_recording(thermal_streamer, tmp_path, hour * 3600)
    oldest_open = thermal_streamer.Recording.create(tmp_path, STARTED - 3600, None, "cube.gcode")

    thermal_streamer.Retention(tmp_path, 1, free=plenty_of_room).prune()

    assert oldest_open.folder.is_dir()


class ShrinkingDisk:
    """A disk that is short of space until this many things have been deleted from it."""

    def __init__(self, deletions_needed):
        self.deletions_needed = deletions_needed
        self.root = None

    def __call__(self, folder):
        self.root = folder
        gone = sum(1 for recording in folder.iterdir() if not (recording / "frames.bin").exists())
        return 10 * 1024 ** 3 if gone >= self.deletions_needed else 0


def test_below_the_floor_the_oldest_temperatures_go_first(thermal_streamer, tmp_path):
    made = [finished_recording(thermal_streamer, tmp_path, hour * 3600) for hour in range(2)]

    thermal_streamer.Retention(tmp_path, 10, free=ShrinkingDisk(1)).prune()

    oldest, newest = made
    assert not oldest.has_frames and oldest.has_clip
    assert newest.has_frames


def test_below_the_floor_whole_prints_go_once_the_temperatures_are_gone(thermal_streamer, tmp_path):
    made = [finished_recording(thermal_streamer, tmp_path, hour * 3600) for hour in range(2)]

    thermal_streamer.Retention(tmp_path, 10, free=lambda _folder: 0).prune()

    assert not any(recording.folder.exists() for recording in made)
