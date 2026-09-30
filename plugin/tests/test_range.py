# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A told display range: what it maps, what the ruler says, and what it stops doing.

The point of it is that a colour means the same temperature in every frame, so most of these are
about two frames rather than one: the same scene with something hot added to it has to come out
mapped the same way, and that is exactly what auto-ranging cannot promise.
"""

from __future__ import annotations

import numpy as np
import pytest
from fake_camera import KELVIN_AT_ZERO_CELSIUS, RAW_UNITS_PER_KELVIN


def raw_for(celsius: float) -> int:
    return int((celsius + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN)


@pytest.fixture
def bed(thermal_streamer):
    """A frame like the maintainer's bed: warm surface, cooler surroundings."""

    frame = np.full((120, 160), raw_for(28.0), dtype=np.uint16)
    frame[40:100, 30:130] = raw_for(95.0)
    return frame


def held(thermal_streamer, low: float, high: float, **overrides):
    return thermal_streamer.RenderSettings(
        range_mode=thermal_streamer.FIXED_RANGE,
        range_low_celsius=low,
        range_high_celsius=high,
        emissivity=1.0,
        **overrides,
    )


def test_a_temperature_and_its_raw_count_survive_the_round_trip(thermal_streamer):
    """The inverse is a search through the driver, so it is checked against the driver."""

    from p3_camera import EnvParams, raw_to_celsius_corrected

    for celsius in (-20.0, 0.0, 23.5, 100.0, 250.0):
        raw = thermal_streamer.raw_for_celsius(celsius, 1.0)
        back = float(raw_to_celsius_corrected(raw, EnvParams(emissivity=1.0)))

        assert back == pytest.approx(celsius, abs=0.01)


def test_a_lower_emissivity_needs_a_different_count_for_the_same_reading(thermal_streamer):
    """The correction is part of the conversion, which is why the bounds follow emissivity."""

    perfect = thermal_streamer.raw_for_celsius(100.0, 1.0)
    shiny = thermal_streamer.raw_for_celsius(100.0, 0.3)

    assert shiny != perfect


def test_a_told_range_is_the_same_two_counts_every_frame(thermal_streamer, bed):
    """The promise: the mapping does not move when the scene does."""

    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"], held(thermal_streamer, 20.0, 100.0)
    )
    renderer.render_image(bed)
    first = renderer.bounds

    hotter = bed.copy()
    hotter[0:10, 0:10] = raw_for(240.0)
    renderer.render_image(hotter)

    assert renderer.bounds == first


def test_an_auto_range_moves_when_the_scene_does(thermal_streamer, bed):
    """The other half of that promise, so the test is not passing for a boring reason."""

    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"],
        thermal_streamer.RenderSettings(emissivity=1.0),
    )
    renderer.render_image(bed)
    first = renderer.bounds

    hotter = bed.copy()
    hotter[0:60, 0:80] = raw_for(240.0)
    renderer.render_image(hotter)

    assert renderer.bounds != first


def test_the_same_pixel_gets_the_same_colour_in_both_frames(thermal_streamer, bed):
    """What a held range is actually for, stated as the picture rather than as the bounds."""

    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"], held(thermal_streamer, 20.0, 100.0)
    )
    quiet, _, _ = renderer.render_image(bed)
    hotter = bed.copy()
    hotter[0:10, 0:10] = raw_for(240.0)
    busy, _, _ = renderer.render_image(hotter)

    assert np.array_equal(quiet[60, 80], busy[60, 80])


def test_the_range_the_stats_report_is_the_one_that_was_asked_for(thermal_streamer, bed):
    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"], held(thermal_streamer, 20.0, 100.0)
    )

    _, stats, _ = renderer.render_image(bed)

    assert stats.range_low_celsius == pytest.approx(20.0, abs=0.05)
    assert stats.range_high_celsius == pytest.approx(100.0, abs=0.05)


def test_the_ruler_spans_the_held_range_rather_than_the_scene(thermal_streamer, bed):
    """A ruler that still followed the scene would be a moving reference on a fixed mapping."""

    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"], held(thermal_streamer, 20.0, 100.0)
    )
    _, stats, _ = renderer.render_image(bed)

    axis = thermal_streamer.bar_axis(stats)

    assert axis == (stats.range_low_celsius, stats.range_high_celsius)


def test_a_triangle_says_the_scene_has_left_the_scale(thermal_streamer, bed):
    """The meaning the triangles had before 0.15.0, back in the mode where it is true again."""

    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"], held(thermal_streamer, 20.0, 40.0)
    )
    _, stats, _ = renderer.render_image(bed)
    overlay = thermal_streamer.Overlay(
        thermal_streamer.build_palettes()["ironbow"], stats, "celsius"
    )
    axis = thermal_streamer.bar_axis(stats)

    assert thermal_streamer.marks_top(overlay, axis) is True
    assert thermal_streamer.marks_bottom(overlay, axis) is False


def test_a_scene_inside_the_scale_gets_no_triangles(thermal_streamer, bed):
    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"], held(thermal_streamer, 0.0, 200.0)
    )
    _, stats, _ = renderer.render_image(bed)
    overlay = thermal_streamer.Overlay(
        thermal_streamer.build_palettes()["ironbow"], stats, "celsius"
    )
    axis = thermal_streamer.bar_axis(stats)

    assert thermal_streamer.marks_top(overlay, axis) is False
    assert thermal_streamer.marks_bottom(overlay, axis) is False


def test_on_a_followed_range_the_triangles_say_the_same(thermal_streamer, bed):
    """Since 0.28.6 the ruler spans the colours either way, so the triangles mean one thing.

    The middle 96% of the scene is what the colours cover, so something hotter than it is past
    the top, whether or not the hottest pixel's marker is on.
    """

    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"],
        thermal_streamer.RenderSettings(emissivity=1.0, hotspot=False),
    )
    _, stats, _ = renderer.render_image(bed)
    overlay = thermal_streamer.Overlay(
        thermal_streamer.build_palettes()["ironbow"], stats, "celsius", hotspot=False
    )
    axis = thermal_streamer.bar_axis(stats)

    assert thermal_streamer.marks_top(overlay, axis) is (stats.maximum_celsius > axis[1])
    assert thermal_streamer.marks_bottom(overlay, axis) is (stats.minimum_celsius < axis[0])


def test_holding_the_range_does_not_measure_one(thermal_streamer, bed, monkeypatch):
    """The saving, stated as a fact about the code rather than as a benchmark.

    `frame_bounds` is the second most expensive step in the pipeline, and a told range has no use
    for it. If it is ever called again, the cost comes back silently.
    """

    def refuse(_frame):
        raise AssertionError("a told range must not measure the scene")

    monkeypatch.setattr(thermal_streamer.pipeline, "frame_bounds", refuse)
    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"], held(thermal_streamer, 20.0, 100.0)
    )

    renderer.render_image(bed)


def test_the_two_ends_are_put_the_right_way_round(thermal_streamer):
    assert thermal_streamer.ordered_range(80.0, 20.0) == (20.0, 80.0)


def test_a_range_of_nothing_is_widened_rather_than_dividing_by_zero(thermal_streamer):
    low, high = thermal_streamer.ordered_range(50.0, 50.0)

    assert high - low >= thermal_streamer.MIN_RANGE_SPAN_CELSIUS


def test_locking_takes_the_range_the_picture_is_using(thermal_streamer, bed):
    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"],
        thermal_streamer.RenderSettings(emissivity=1.0),
    )
    _, stats, _ = renderer.render_image(bed)

    locked = thermal_streamer.locked_range(thermal_streamer.RenderSettings(), stats)

    assert locked.range_mode == thermal_streamer.FIXED_RANGE
    assert locked.range_low_celsius == pytest.approx(stats.range_low_celsius, abs=0.05)
    assert locked.range_high_celsius == pytest.approx(stats.range_high_celsius, abs=0.05)


def test_locking_before_the_first_frame_changes_nothing(thermal_streamer):
    """There is nothing on screen to hold, and a guess would be worse than staying on auto."""

    settings = thermal_streamer.RenderSettings()

    assert thermal_streamer.locked_range(settings, None) == settings


def test_a_posted_form_can_set_the_range(thermal_streamer, palettes):
    form = {"palette": ["ironbow"], "rotation": ["0"], "units": ["celsius"],
            "emissivity": ["0.95"], "range_mode": ["fixed"],
            "range_low_celsius": ["30"], "range_high_celsius": ["110.5"]}

    _, updated = thermal_streamer.settings_from_form(
        form, palettes, thermal_streamer.RenderSettings()
    )

    assert updated.range_mode == thermal_streamer.FIXED_RANGE
    assert updated.range_low_celsius == 30.0
    assert updated.range_high_celsius == 110.5


def test_a_posted_range_that_is_not_a_number_leaves_the_setting_alone(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(range_low_celsius=30.0)
    form = {"palette": ["ironbow"], "rotation": ["0"], "units": ["celsius"],
            "emissivity": ["0.95"], "range_mode": ["fixed"], "range_low_celsius": ["warm"]}

    _, updated = thermal_streamer.settings_from_form(form, palettes, current)

    assert updated.range_low_celsius == 30.0


def test_json_can_change_the_mode_without_naming_the_ends(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(range_low_celsius=30.0, range_high_celsius=90.0)

    _, updated = thermal_streamer.settings_from_json({"range_mode": "fixed"}, palettes, current)

    assert updated.range_mode == thermal_streamer.FIXED_RANGE
    assert (updated.range_low_celsius, updated.range_high_celsius) == (30.0, 90.0)


def test_an_unknown_mode_is_ignored(thermal_streamer, palettes):
    _, updated = thermal_streamer.settings_from_json(
        {"range_mode": "psychic"}, palettes, thermal_streamer.RenderSettings()
    )

    assert updated.range_mode == thermal_streamer.AUTO_RANGE


def test_the_range_survives_a_restart(thermal_streamer, tmp_path):
    state_file = tmp_path / "settings.json"
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )
    store.update("ironbow", held(thermal_streamer, 25.5, 120.0))

    reopened = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )
    restored = reopened.snapshot()[2]

    assert restored.range_mode == thermal_streamer.FIXED_RANGE
    assert (restored.range_low_celsius, restored.range_high_celsius) == (25.5, 120.0)


def test_the_page_offers_the_range_controls(thermal_streamer, settings_dict):
    page = thermal_streamer.render_control_page(settings_dict(), ["ironbow"], None)

    assert 'name="range_mode"' in page
    assert 'name="range_low_celsius"' in page
    assert 'name="range_high_celsius"' in page
    assert 'value="lock-range"' in page
