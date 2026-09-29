# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Following a print from Klipper's state, and taking one frame out of the capture loop per layer.

The tracker is plain logic, so every way a print can start, move and end is a list of statuses
here. The tap is the one place the timelapse and the capture thread meet, so its tests are about
what crosses between them: one frame per request, a copy rather than the driver's own array, and
nothing at all when nothing was asked for.
"""

from __future__ import annotations

import threading

import numpy as np


def status(streamer, state, layer=None, total=None, filename="cube.gcode"):
    return streamer.PrintStatus(state, filename, layer, total)


def events_for(streamer, statuses):
    tracker = streamer.PrintTracker()
    return [event for each in statuses for event in tracker.update(each)]


def test_a_print_is_started_moved_through_and_ended(thermal_streamer):
    events = events_for(
        thermal_streamer,
        [
            status(thermal_streamer, "standby"),
            status(thermal_streamer, "printing", 1, 3),
            status(thermal_streamer, "printing", 1, 3),
            status(thermal_streamer, "printing", 2, 3),
            status(thermal_streamer, "printing", 3, 3),
            status(thermal_streamer, "complete", 3, 3),
            status(thermal_streamer, "complete", 3, 3),
        ],
    )

    assert events == [
        thermal_streamer.PrintStarted("cube.gcode"),
        thermal_streamer.LayerReached(1),
        thermal_streamer.LayerReached(2),
        thermal_streamer.LayerReached(3),
        thermal_streamer.PrintEnded("complete"),
    ]


def test_a_slicer_that_sends_no_layers_starts_and_ends_a_print_with_nothing_between(
    thermal_streamer,
):
    events = events_for(
        thermal_streamer,
        [status(thermal_streamer, "printing"), status(thermal_streamer, "printing"),
         status(thermal_streamer, "cancelled")],
    )

    assert events == [
        thermal_streamer.PrintStarted("cube.gcode"),
        thermal_streamer.PrintEnded("cancelled"),
    ]


def test_a_pause_is_still_the_same_print(thermal_streamer):
    events = events_for(
        thermal_streamer,
        [status(thermal_streamer, "printing", 4), status(thermal_streamer, "paused", 4),
         status(thermal_streamer, "printing", 5)],
    )

    assert events == [
        thermal_streamer.PrintStarted("cube.gcode"),
        thermal_streamer.LayerReached(4),
        thermal_streamer.LayerReached(5),
    ]


def test_a_print_that_drops_to_standby_was_interrupted(thermal_streamer):
    """A firmware restart ends a print without saying how, and it did not complete."""

    events = events_for(
        thermal_streamer,
        [status(thermal_streamer, "printing", 7), status(thermal_streamer, "standby")],
    )

    assert events[-1] == thermal_streamer.PrintEnded(thermal_streamer.INTERRUPTED)


def test_a_print_already_running_when_first_seen_is_reported_as_started(thermal_streamer):
    """What the tracker cannot know, the service decides: a new recording or the old one."""

    events = events_for(thermal_streamer, [status(thermal_streamer, "printing", 40, 75)])

    assert events == [
        thermal_streamer.PrintStarted("cube.gcode"),
        thermal_streamer.LayerReached(40),
    ]


def test_nothing_is_taken_when_nothing_was_asked_for(thermal_streamer):
    tap = thermal_streamer.FrameTap()

    tap.offer(np.zeros((2, 2), dtype=np.uint16), "high")

    assert tap.collect(0.0) is None


def test_an_asked_for_frame_is_a_copy_with_its_gain(thermal_streamer):
    tap = thermal_streamer.FrameTap()
    counts = np.full((2, 2), 18000, dtype=np.uint16)

    tap.request()
    tap.offer(counts, "low")
    counts[:] = 0
    frame = tap.collect(0.0)

    assert frame is not None
    assert frame.gain == "low"
    assert int(frame.counts[0, 0]) == 18000


def test_one_request_takes_one_frame(thermal_streamer):
    tap = thermal_streamer.FrameTap()
    tap.request()
    tap.offer(np.full((2, 2), 1, dtype=np.uint16), "high")
    tap.offer(np.full((2, 2), 2, dtype=np.uint16), "high")

    assert int(tap.collect(0.0).counts[0, 0]) == 1
    assert tap.collect(0.0) is None


def test_a_frame_offered_from_the_capture_thread_is_waited_for(thermal_streamer):
    tap = thermal_streamer.FrameTap()
    tap.request()
    offering = threading.Timer(0.05, tap.offer, (np.ones((2, 2), dtype=np.uint16), "high"))
    offering.start()

    frame = tap.collect(2.0)
    offering.join()

    assert frame is not None


def test_no_frame_in_time_is_none_and_the_request_is_dropped(thermal_streamer):
    """A camera not sending: the layer is recorded as missed, and a later frame is not it."""

    tap = thermal_streamer.FrameTap()
    tap.request()

    assert tap.collect(0.01) is None
    tap.offer(np.ones((2, 2), dtype=np.uint16), "high")
    assert tap.collect(0.0) is None


def test_the_latest_frame_hands_every_frame_to_its_tap(thermal_streamer):
    tap = thermal_streamer.FrameTap()
    frames = thermal_streamer.LatestFrame(tap)
    tap.request()

    frames.offer_raw(np.ones((2, 2), dtype=np.uint16), "high")

    assert tap.collect(0.0) is not None
