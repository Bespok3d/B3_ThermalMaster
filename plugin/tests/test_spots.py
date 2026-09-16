# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Placed spots: the mapping back to the sensor, the patch, and what a change does to them.

A spot names a place on the picture as displayed, and the temperature it reports comes from a frame
that has not been turned. The two ends therefore speak different coordinate spaces, and a spot that
reads the wrong pixel is the same failure as a marker drawn in the wrong place: confident and
wrong. Every test here marks one pixel, so a mapping that is off by a rotation reads the background
instead and the assertion fails rather than passing on an average that looks plausible.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from fake_camera import (
    KELVIN_AT_ZERO_CELSIUS,
    P1_SENSOR_HEIGHT,
    P1_SENSOR_WIDTH,
    RAW_UNITS_PER_KELVIN,
)

ORIENTATIONS = [
    (rotation, mirrors)
    for rotation in (0, 90, 180, 270)
    for mirrors in ((False, False), (True, False), (False, True), (True, True))
]


def raw_for(celsius: float) -> int:
    return int((celsius + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN)


@pytest.fixture
def palettes(thermal_streamer):
    return thermal_streamer.build_palettes()


@pytest.fixture
def warm_frame():
    """A uniform 30 C field to place a hot pixel into."""

    return np.full((P1_SENSOR_HEIGHT, P1_SENSOR_WIDTH), raw_for(30.0), dtype=np.uint16)


@pytest.mark.parametrize(("rotation", "mirrors"), ORIENTATIONS)
def test_a_point_survives_the_round_trip_through_both_orientations(thermal_streamer, rotation,
                                                                   mirrors):
    """`unorient_point` is only useful if it undoes `orient_point` exactly, everywhere."""

    size = (P1_SENSOR_WIDTH, P1_SENSOR_HEIGHT)
    for sensor in ((0, 0), (1, 0), (0, 1), (43, 17), (159, 119), (80, 60)):
        placed, oriented_size = thermal_streamer.orient_point(sensor, size, rotation, mirrors)
        back, sensor_size = thermal_streamer.unorient_point(
            placed, oriented_size, rotation, mirrors
        )

        assert back == sensor, (sensor, rotation, mirrors)
        assert sensor_size == size


@pytest.mark.parametrize(("rotation", "mirrors"), ORIENTATIONS)
def test_a_spot_reads_the_pixel_it_was_placed_on(thermal_streamer, warm_frame, rotation, mirrors):
    """Placed in the displayed orientation, measured in the sensor's. A cross-check, not maths.

    The patch is 3 by 3 around one 90 C pixel in a 30 C field, so a correct mapping reports the
    mean of the two and any other mapping reports 30 C exactly.
    """

    marked = warm_frame.copy()
    marked[17, 43] = raw_for(90.0)
    placed, _ = thermal_streamer.orient_point(
        (43, 17), (P1_SENSOR_WIDTH, P1_SENSOR_HEIGHT), rotation, mirrors
    )

    readings = thermal_streamer.spot_readings(marked, (placed,), rotation, mirrors, 1.0)

    assert len(readings) == 1
    assert readings[0].spot == placed
    assert readings[0].celsius > 35.0


def test_a_spot_is_a_patch_rather_than_a_pixel(thermal_streamer, warm_frame):
    """One pixel of this sensor is noisy, so the number is a small square averaged.

    Which is visible in the answer: nine pixels, one of them 90 C in a 30 C field, averages well
    below the hot pixel and well above the background.
    """

    marked = warm_frame.copy()
    marked[17, 43] = raw_for(90.0)

    reading = thermal_streamer.spot_readings(marked, ((43, 17),), 0, (False, False), 1.0)[0]

    assert 30.0 < reading.celsius < 90.0
    assert reading.celsius == pytest.approx(30.0 + 60.0 / 9, abs=0.5)


def test_a_spot_at_the_edge_averages_what_is_there(thermal_streamer, warm_frame):
    """The patch is clipped to the frame rather than reading off the end of it."""

    marked = warm_frame.copy()
    marked[0, 0] = raw_for(90.0)

    reading = thermal_streamer.spot_readings(marked, ((0, 0),), 0, (False, False), 1.0)[0]

    assert reading.celsius == pytest.approx(30.0 + 60.0 / 4, abs=0.5)


def test_a_spot_outside_the_picture_is_pulled_back_into_it(thermal_streamer, warm_frame):
    """A hand-edited settings file is a supported way in, so out of range clamps rather than
    crashes."""

    readings = thermal_streamer.spot_readings(
        warm_frame, ((10_000, 10_000),), 0, (False, False), 1.0
    )

    assert readings[0].spot == (P1_SENSOR_WIDTH - 1, P1_SENSOR_HEIGHT - 1)


def test_no_spots_means_no_work(thermal_streamer, warm_frame):
    assert thermal_streamer.spot_readings(warm_frame, (), 0, (False, False), 1.0) == ()


def test_the_readout_counts_a_spot_even_with_everything_else_off(thermal_streamer):
    """The encode size keys off the readout, and a spot's label is text like any other."""

    bare = thermal_streamer.RenderSettings(
        colorbar=False, reticle=False, hotspot=False, coldspot=False
    )

    assert bare.readout is False
    assert dataclasses.replace(bare, spots=((10, 10),)).readout is True


def test_a_spot_gets_a_marker_before_the_automatic_ones(thermal_streamer, warm_frame):
    """Somebody asked for it, so it gets first refusal on a label position."""

    stats = thermal_streamer.frame_statistics(warm_frame, (0.0, 1.0), 0, (False, False), 1.0)
    reading = thermal_streamer.SpotReading(spot=(20, 30), celsius=42.0)
    overlay = thermal_streamer.Overlay(
        thermal_streamer.build_palettes()["ironbow"],
        dataclasses.replace(stats, spots=(reading,)),
        "celsius",
    )

    markers = thermal_streamer.markers_for(overlay)

    assert markers[0].spot == (20, 30)
    assert markers[0].colour == thermal_streamer.SPOT_RGB
    assert len(markers) == 4


def test_the_rendered_frame_reports_what_the_spots_read(thermal_streamer, warm_frame):
    """The whole path, from a setting to a number beside a cross."""

    marked = warm_frame.copy()
    marked[17, 43] = raw_for(90.0)
    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"],
        thermal_streamer.RenderSettings(spots=((43, 17),), emissivity=1.0),
    )

    rendered = renderer.render_frame(marked)

    assert len(rendered.stats.spots) == 1
    assert rendered.stats.spots[0].celsius > 30.0
    assert rendered.stats.as_dict("celsius")["spots"][0]["x"] == 43


def test_only_so_many_spots_are_accepted(thermal_streamer):
    """The ceiling is a cost ceiling: every spot is a patch and a label drawn at frame rate."""

    asked = [[x, 10] for x in range(thermal_streamer.MAX_SPOTS + 3)]

    assert len(thermal_streamer.clean_spots(asked)) == thermal_streamer.MAX_SPOTS


@pytest.mark.parametrize("rubbish", [None, "spots", [["a", "b"]], [[1]], [[1, 2, 3]], [[-1, 2]]])
def test_anything_that_is_not_a_pair_of_numbers_is_dropped(thermal_streamer, rubbish):
    assert thermal_streamer.clean_spots(rubbish) == ()


def test_a_posted_list_replaces_the_whole_set(thermal_streamer, palettes):
    """Placing, moving and clearing are all the same request: here is the list I want."""

    current = thermal_streamer.RenderSettings(spots=((10, 10), (20, 20)))

    _, updated = thermal_streamer.settings_from_json({"spots": [[5, 5]]}, palettes, current)

    assert updated.spots == ((5, 5),)


def test_an_empty_list_clears_them(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(spots=((10, 10),))

    _, updated = thermal_streamer.settings_from_json({"spots": []}, palettes, current)

    assert updated.spots == ()


def test_a_change_that_does_not_mention_spots_keeps_them(thermal_streamer, palettes):
    """The one dialect where absent means absent. Changing a palette must not clear the spots."""

    current = thermal_streamer.RenderSettings(spots=((10, 10),))

    _, updated = thermal_streamer.settings_from_json({"units": "fahrenheit"}, palettes, current)

    assert updated.spots == ((10, 10),)


def test_turning_the_picture_drops_the_spots(thermal_streamer, palettes):
    """They name places on a picture that just moved. Dropped visibly beats moved silently."""

    current = thermal_streamer.RenderSettings(spots=((10, 10),))

    _, updated = thermal_streamer.settings_from_json({"rotation": 90}, palettes, current)

    assert updated.spots == ()


def test_mirroring_the_picture_drops_them_too(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(spots=((10, 10),))

    _, updated = thermal_streamer.settings_from_json(
        {"flip_horizontal": True}, palettes, current
    )

    assert updated.spots == ()


def test_the_control_page_form_leaves_them_alone(thermal_streamer, palettes):
    """It posts every field on every apply, and none of those fields is a spot."""

    current = thermal_streamer.RenderSettings(spots=((10, 10),), rotation=90)
    form = {"palette": ["ironbow"], "rotation": ["90"], "units": ["celsius"],
            "emissivity": ["0.95"], "colorbar": ["on"]}

    _, updated = thermal_streamer.settings_from_form(form, palettes, current)

    assert updated.spots == ((10, 10),)


def test_but_rotating_from_that_form_still_drops_them(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(spots=((10, 10),), rotation=0)
    form = {"palette": ["ironbow"], "rotation": ["180"], "units": ["celsius"],
            "emissivity": ["0.95"]}

    _, updated = thermal_streamer.settings_from_form(form, palettes, current)

    assert updated.spots == ()


def test_spots_survive_a_restart_as_pairs(thermal_streamer, tmp_path):
    """JSON hands back lists, and the rest of the plugin is written against pairs."""

    state_file = tmp_path / "settings.json"
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )
    store.update("ironbow", thermal_streamer.RenderSettings(spots=((11, 22), (33, 44))))

    reopened = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert reopened.snapshot()[2].spots == ((11, 22), (33, 44))


def test_a_settings_change_does_not_make_the_picture_re_settle(thermal_streamer, warm_frame):
    """Placing four spots is four settings changes, and the picture must not breathe on each one.

    The renderer is rebuilt rather than mutated, which used to start it with no smoothed bounds and
    no previous frame, worth about a second of visible re-ranging. The replacement inherits both.
    """

    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    source = thermal_streamer.RendererSource(store, thermal_streamer.build_palettes())
    first = source.current()
    first.render_frame(warm_frame)
    settled = first.bounds

    store.update("ironbow", thermal_streamer.RenderSettings(spots=((10, 10),)))
    replacement = source.current()

    assert replacement is not first
    assert replacement.bounds == settled
