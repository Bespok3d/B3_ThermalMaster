# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Asking the C library to keep freed memory (F-79).

What it buys is a measurement on the printer and cannot be tested here. What can be is the asking:
that both settings are sent, with glibc's own numbers, and that a system with nothing to ask is
left alone rather than failed on. The gate runs on macOS, which has no `mallopt`, so everything
here goes through a stand-in and nothing depends on which C library the tests run under.
"""

from __future__ import annotations


class RecordingMallopt:
    """A `mallopt` that writes down what it was asked and answers as it was told to."""

    def __init__(self, refused: tuple[int, ...] = ()) -> None:
        self.asked: list[tuple[int, int]] = []
        self._refused = refused

    def __call__(self, setting: int, value: int) -> int:
        self.asked.append((setting, value))
        return 0 if setting in self._refused else 1


def test_both_thresholds_are_set_with_glibcs_own_numbers(thermal_streamer):
    """-3 and -1 are M_MMAP_THRESHOLD and M_TRIM_THRESHOLD in glibc's <malloc.h>."""

    mallopt = RecordingMallopt()

    assert thermal_streamer.keep_freed_memory(mallopt) is True
    assert mallopt.asked == [
        (-3, thermal_streamer.KEPT_BLOCK_BYTES),
        (-1, thermal_streamer.HEAP_SLACK_BYTES),
    ]


def test_a_whole_frame_array_is_well_inside_what_is_kept(thermal_streamer):
    """A P3 frame as float32 is 196,608 bytes, and its doubled picture in Pillow about 786 KB."""

    p3_frame_as_floats = 256 * 192 * 4
    p3_doubled_picture = 512 * 384 * 4

    assert thermal_streamer.KEPT_BLOCK_BYTES > p3_doubled_picture > p3_frame_as_floats
    assert thermal_streamer.HEAP_SLACK_BYTES > thermal_streamer.KEPT_BLOCK_BYTES


def test_where_there_is_no_mallopt_nothing_is_asked_and_nothing_fails(thermal_streamer):
    """macOS and musl have none. The plugin runs there as it always did."""

    assert thermal_streamer.keep_freed_memory(None) is False


def test_a_setting_glibc_refuses_is_reported_and_the_other_is_still_sent(thermal_streamer):
    refusing = RecordingMallopt(refused=(thermal_streamer.M_MMAP_THRESHOLD,))

    assert thermal_streamer.keep_freed_memory(refusing) is False
    assert [setting for setting, _ in refusing.asked] == [
        thermal_streamer.M_MMAP_THRESHOLD,
        thermal_streamer.M_TRIM_THRESHOLD,
    ]


def test_looking_for_mallopt_never_raises(thermal_streamer):
    """Whatever this machine's C library is, the answer is a function or None."""

    found = thermal_streamer.glibc_mallopt()

    assert found is None or callable(found)
