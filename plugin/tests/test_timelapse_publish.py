# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A finished clip copied to the Timelapse page, named after the firmware's clip of the same print.

The name is the part with a decision in it. On the U1 the firmware names its clips after the
G-code and the print's start, to the second, so the thermal clip follows it and the two sort side
by side; on mainline Klipper there is no such clip, and the plugin must not wait ten minutes for
one that is never coming.
"""

from __future__ import annotations

import pytest

STARTED = 1_790_000_000.0  # 2026-09-21 14:13:20 UTC


class StandInFolder:
    """Moonraker's `timelapse` folder: its files, its free space, what went in and came out."""

    def __init__(self, names=(), free=10 * 1024 ** 3, arriving=None):
        self.names = list(names)
        self.free = free
        self.arriving = list(arriving or [])
        self.uploaded = []
        self.deleted = []

    def has_root(self, root):
        return root == "timelapse"

    def file_names(self, _root):
        return list(self.names)

    def free_space(self, _root):
        return self.free

    def upload(self, root, name, source):
        self.uploaded.append((root, name, source.read_bytes()))
        return True

    def delete_file(self, root, name):
        self.deleted.append((root, name))
        return True


def waiting_that_lets(folder):
    """A wait that, each time it is called, lets the next file the firmware writes arrive."""

    calls = []

    def wait(seconds):
        calls.append(seconds)
        if folder.arriving:
            folder.names.append(folder.arriving.pop(0))
        return False

    return wait, calls


def made_clip(streamer, root):
    recording = streamer.Recording.create(root, STARTED, None, "Voron_Cube.gcode")
    recording.finish("complete")
    recording.clip_path.write_bytes(b"clip bytes")
    recording.thumbnail_path.write_bytes(b"thumbnail bytes")
    return recording


def test_the_firmwares_clip_of_the_same_print_is_found(thermal_streamer):
    names = [
        "Voron_Cube_PLA_23m7s_20260921141321.mp4",
        "Voron_Cube_PLA_23m7s_20260921141321.jpg",
        "Other_PLA_1h2m_20260921101500.mp4",
        "Voron_Cube_PLA_23m7s_20260921141321_thermal.mp4",
    ]

    assert thermal_streamer.firmware_base_for(names, STARTED) == (
        "Voron_Cube_PLA_23m7s_20260921141321"
    )


def test_a_clip_started_minutes_apart_is_not_the_same_print(thermal_streamer):
    assert thermal_streamer.firmware_base_for(["Cube_20260921141920.mp4"], STARTED) is None


def test_mainline_names_are_not_taken_for_the_firmwares(thermal_streamer):
    """moonraker-timelapse writes the date and time apart, so nothing there is waited for."""

    assert thermal_streamer.firmware_clips(["timelapse_cube_20260921_1413.mp4"]) == {}


def test_without_firmware_clips_the_name_is_ours_and_nothing_waits(thermal_streamer, tmp_path):
    folder = StandInFolder(["timelapse_cube_20260921_1413.mp4"])
    wait, calls = waiting_that_lets(folder)
    publisher = thermal_streamer.Publisher(folder, wait)

    base = publisher.base_name(made_clip(thermal_streamer, tmp_path))

    assert base == "Voron_Cube_20260921141320"
    assert calls == []


def test_the_firmwares_clip_is_waited_for(thermal_streamer, tmp_path):
    folder = StandInFolder(
        ["Older_PLA_1h_20260920090000.mp4"], arriving=["Voron_Cube_PLA_8m_20260921141320.mp4"]
    )
    wait, calls = waiting_that_lets(folder)
    publisher = thermal_streamer.Publisher(folder, wait)

    base = publisher.base_name(made_clip(thermal_streamer, tmp_path))

    assert base == "Voron_Cube_PLA_8m_20260921141320"
    assert calls == [thermal_streamer.FIRMWARE_POLL_SECONDS]


def test_a_firmware_clip_that_never_comes_leaves_our_own_name(thermal_streamer, tmp_path):
    folder = StandInFolder(["Older_PLA_1h_20260920090000.mp4"])
    wait, _ = waiting_that_lets(folder)
    publisher = thermal_streamer.Publisher(folder, wait, patience=0.0)

    assert publisher.base_name(made_clip(thermal_streamer, tmp_path)) == "Voron_Cube_20260921141320"


def test_stopping_the_service_ends_the_wait(thermal_streamer, tmp_path):
    folder = StandInFolder(["Older_PLA_1h_20260920090000.mp4"])
    publisher = thermal_streamer.Publisher(folder, lambda _seconds: True)

    assert publisher.base_name(made_clip(thermal_streamer, tmp_path)) == "Voron_Cube_20260921141320"


def test_the_clip_and_its_thumbnail_are_copied_under_the_thermal_name(thermal_streamer, tmp_path):
    folder = StandInFolder()
    publisher = thermal_streamer.Publisher(folder, lambda _seconds: False)

    outcome = publisher.publish(made_clip(thermal_streamer, tmp_path), "Cube_PLA_20260921141320")

    assert outcome == {
        "root": "timelapse",
        "files": ["Cube_PLA_20260921141320_thermal.mp4", "Cube_PLA_20260921141320_thermal.jpg"],
    }
    assert [(root, name) for root, name, _ in folder.uploaded] == [
        ("timelapse", "Cube_PLA_20260921141320_thermal.mp4"),
        ("timelapse", "Cube_PLA_20260921141320_thermal.jpg"),
    ]
    assert folder.uploaded[0][2] == b"clip bytes"


def test_a_folder_short_of_space_gets_nothing(thermal_streamer, tmp_path):
    folder = StandInFolder(free=thermal_streamer.FREE_SPACE_FLOOR_BYTES)
    publisher = thermal_streamer.Publisher(folder, lambda _seconds: False)

    outcome = publisher.publish(made_clip(thermal_streamer, tmp_path), "Cube")

    assert "less than 200 MB free" in outcome["error"]
    assert folder.uploaded == []


def test_a_refusing_moonraker_is_named(thermal_streamer, tmp_path):
    class Refusing(StandInFolder):
        def free_space(self, _root):
            raise thermal_streamer.MoonrakerRefusedError("401")

    publisher = thermal_streamer.Publisher(Refusing(), lambda _seconds: False)

    outcome = publisher.publish(made_clip(thermal_streamer, tmp_path), "Cube")

    assert "asks for a login" in outcome["error"]


@pytest.mark.parametrize("recorded", [["a_thermal.mp4", "a_thermal.jpg"], []])
def test_taking_a_print_off_the_page_removes_what_was_copied(thermal_streamer, tmp_path, recorded):
    folder = StandInFolder()
    recording = made_clip(thermal_streamer, tmp_path)
    recording.note_clip({"published": {"root": "timelapse", "files": recorded}})

    thermal_streamer.Publisher(folder, lambda _seconds: False).unpublish(recording)

    assert folder.deleted == [("timelapse", name) for name in recorded]
