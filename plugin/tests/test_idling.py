# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Not rendering into an empty room.

Measured on the printer before any of this existed: 40.6% of a core with nothing watching, against
45.1% with somebody pointing at the picture. Nine tenths of what the plugin cost was work nobody
had asked for, and the camera streams whether or not anyone is listening, so the loop has to keep
reading while it stops rendering.

The tests here are about the decision rather than about the saving: that the loop skips the
expensive half, that anything asking for a picture brings it back, and that coming back does not
show a frame from before the silence.
"""

from __future__ import annotations

import threading

import pytest
from fake_camera import StandInCamera, thermal_frame


class CountingRendererSource:
    """Stands in for the real one and counts what the capture loop asks it for.

    The loop reaches for a renderer once per frame it intends to render, so counting the requests
    is counting the renders, without subclassing anything to find out.
    """

    def __init__(self, thermal_streamer) -> None:
        self._streamer = thermal_streamer
        self.renders = 0
        self.restarts = 0
        self._renderer = self._build()

    def _build(self):
        return self._streamer.ThermalRenderer(
            self._streamer.build_palettes()["ironbow"], self._streamer.RenderSettings()
        )

    def restart(self) -> None:
        self.restarts += 1
        self._renderer = self._build()

    def current(self):
        self.renders += 1
        return self._renderer


@pytest.fixture
def counting(thermal_streamer):
    return CountingRendererSource(thermal_streamer)


def run_session(thermal_streamer, frames, store, renderer_source):
    """One capture session over a scripted camera, until the script runs out."""

    camera = StandInCamera(scripted_frames=frames)
    shutdown = threading.Event()
    with pytest.raises(Exception, match="scripted frames"):
        thermal_streamer.stream_frames(camera, store, renderer_source, shutdown)


def test_a_store_nobody_has_asked_anything_of_is_not_wanted(thermal_streamer):
    assert thermal_streamer.LatestFrame().wanted() is False


def test_nobody_asking_is_not_wanted_in_the_first_minute_after_boot(thermal_streamer, monkeypatch):
    """The monotonic clock counts from boot, and CI's runner had been up for less than a minute.

    A store that took "never asked" to mean "asked at zero" was wanted until the clock passed its
    idle minute, which only CI noticed, because every other machine here had been up for longer
    (F-75).
    """

    import time

    monkeypatch.setattr(time, "monotonic", lambda: 5.0)

    assert thermal_streamer.LatestFrame().wanted() is False


def test_asking_for_a_picture_makes_it_wanted(thermal_streamer):
    store = thermal_streamer.LatestFrame()

    store.note_interest()

    assert store.wanted() is True


def test_interest_runs_out(thermal_streamer):
    """A minute of quiet, and the plugin is entitled to stop working."""

    store = thermal_streamer.LatestFrame()
    store.note_interest()

    assert store.wanted(within=0.0) is False
    assert thermal_streamer.IDLE_AFTER_SECONDS == 60.0


def test_nothing_is_rendered_for_nobody(thermal_streamer, counting):
    store = thermal_streamer.LatestFrame()

    run_session(thermal_streamer, [thermal_frame(), thermal_frame()], store, counting)

    assert counting.renders == 0
    assert store.published_count == 0


def test_the_frames_are_still_read(thermal_streamer, counting):
    """The camera streams regardless, and a reader that stops reading falls out of step with it."""

    store = thermal_streamer.LatestFrame()

    run_session(thermal_streamer, [thermal_frame(), thermal_frame()], store, counting)

    assert store.seen_count == 2


def test_somebody_watching_gets_frames(thermal_streamer, counting):
    store = thermal_streamer.LatestFrame()
    store.note_interest()

    run_session(thermal_streamer, [thermal_frame(), thermal_frame()], store, counting)

    assert counting.renders == 2
    assert store.published_count == 2


def test_coming_back_starts_the_renderer_again(thermal_streamer, counting):
    """What a renderer carries between frames describes a scene that has moved on."""

    store = thermal_streamer.LatestFrame()
    camera = StandInCamera(scripted_frames=[thermal_frame(), thermal_frame(), thermal_frame()])
    shutdown = threading.Event()

    class WakingStore(type(store)):  # type: ignore[misc]
        """Wanted from the second frame onwards, which is what waking up looks like here."""

        def __init__(self) -> None:
            super().__init__()
            self.asked = 0

        def wanted(self, within: float = 60.0) -> bool:
            self.asked += 1
            return self.asked > 1

    waking = WakingStore()
    with pytest.raises(Exception, match="scripted frames"):
        thermal_streamer.stream_frames(camera, waking, counting, shutdown)

    assert counting.restarts == 1
    assert waking.published_count == 2


def test_a_session_that_only_idled_still_counts_as_working(thermal_streamer):
    """The reconnect backoff counts frames read, not frames published.

    A session that idled for an hour and then hit an unplug read plenty of frames and published
    none, and treating that as a camera that never worked would make the unplug take minutes to
    notice.
    """

    store = thermal_streamer.LatestFrame()
    store.note_read()
    store.note_read()

    assert store.seen_count == 2
    assert store.published_count == 0


def test_a_fresh_frame_is_not_waited_for(thermal_streamer):
    """Waking is for a stale store. A live one answers at once."""

    store = thermal_streamer.LatestFrame()
    store.publish(b"jpeg")

    assert store.fresh() is True
    store.wake(timeout=5.0)  # returns immediately, or this test takes five seconds


def test_a_stale_store_is_worth_waiting_for(thermal_streamer):
    store = thermal_streamer.LatestFrame()

    assert store.fresh() is False
    assert store.fresh(within=0.0) is False


def test_waking_asks_for_frames(thermal_streamer):
    store = thermal_streamer.LatestFrame()

    store.wake(timeout=0.01)

    assert store.wanted() is True
