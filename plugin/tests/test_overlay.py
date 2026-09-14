# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The temperature readout: the numbers, where the markers land, and what they cost.

A wrong marker is worse than no marker, because it looks authoritative. So the coordinate tests
here do not check the arithmetic against itself: they mark one pixel, orient the frame with the
same function the renderer uses, and assert the marked pixel is where `orient_point` said it would
be. Any drift between the two shows up as a failure rather than as a cross drawn on cold metal.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from fake_camera import (
    KELVIN_AT_ZERO_CELSIUS,
    P1_SENSOR_HEIGHT,
    P1_SENSOR_WIDTH,
    RAW_UNITS_PER_KELVIN,
    thermal_frame,
)
from PIL import Image

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
def flat_frame():
    """A uniform 30 C field, so a pixel set hotter than it is unambiguously the hottest."""

    return np.full((P1_SENSOR_HEIGHT, P1_SENSOR_WIDTH), raw_for(30.0), dtype=np.uint16)


def test_fahrenheit_converts_and_celsius_passes_through(thermal_streamer):
    assert thermal_streamer.to_display_temperature(100.0, "celsius") == 100.0
    assert thermal_streamer.to_display_temperature(100.0, "fahrenheit") == pytest.approx(212.0)


def test_a_formatted_temperature_names_its_scale(thermal_streamer):
    assert thermal_streamer.format_temperature(21.25, "celsius") == "21.2C"
    assert thermal_streamer.format_temperature(0.0, "fahrenheit") == "32.0F"


@pytest.mark.parametrize(("rotation", "mirrors"), ORIENTATIONS)
def test_a_marked_pixel_lands_where_orient_point_says(thermal_streamer, flat_frame, rotation,
                                                      mirrors):
    """The cross-check: `orient_point` against `orient`, for every orientation on offer."""

    marked = flat_frame.copy()
    marked[17, 43] = raw_for(90.0)

    (x, y), size = thermal_streamer.orient_point(
        (43, 17), (P1_SENSOR_WIDTH, P1_SENSOR_HEIGHT), rotation, mirrors
    )
    oriented = thermal_streamer.orient(marked, rotation, *mirrors)

    assert (oriented.shape[1], oriented.shape[0]) == size
    assert oriented[y, x] == raw_for(90.0)


def test_a_quarter_turn_clockwise_sends_the_top_left_pixel_to_the_top_right(thermal_streamer):
    (x, y), size = thermal_streamer.orient_point((0, 0), (160, 120), 90, (False, False))

    assert size == (120, 160)
    assert (x, y) == (119, 0)


def test_statistics_report_the_temperatures_in_the_frame(thermal_streamer, flat_frame):
    frame = flat_frame.copy()
    frame[10, 20] = raw_for(80.0)
    frame[100, 30] = raw_for(5.0)

    stats = thermal_streamer.frame_statistics(frame, (0.0, 1.0), 0, (False, False), 1.0)

    assert stats.maximum_celsius == pytest.approx(80.0, abs=0.05)
    assert stats.minimum_celsius == pytest.approx(5.0, abs=0.05)
    assert stats.average_celsius == pytest.approx(30.0, abs=0.05)
    assert stats.hotspot == (20, 10)
    assert stats.coldspot == (30, 100)


def test_statistics_report_the_centre_pixel(thermal_streamer, flat_frame):
    frame = flat_frame.copy()
    frame[P1_SENSOR_HEIGHT // 2, P1_SENSOR_WIDTH // 2] = raw_for(55.0)

    stats = thermal_streamer.frame_statistics(frame, (0.0, 1.0), 0, (False, False), 1.0)

    assert stats.centre_celsius == pytest.approx(55.0, abs=0.05)


def test_the_colorbar_ends_are_the_display_bounds_not_the_scene_extremes(thermal_streamer,
                                                                        flat_frame):
    """The bar explains the colour mapping, so it has to be labelled with what was mapped."""

    frame = flat_frame.copy()
    frame[0, 0] = raw_for(200.0)
    bounds = (float(raw_for(20.0)), float(raw_for(40.0)))

    stats = thermal_streamer.frame_statistics(frame, bounds, 0, (False, False), 1.0)

    assert stats.range_low_celsius == pytest.approx(20.0, abs=0.05)
    assert stats.range_high_celsius == pytest.approx(40.0, abs=0.05)
    assert stats.maximum_celsius == pytest.approx(200.0, abs=0.05)


def test_the_stats_payload_converts_every_temperature_it_carries(thermal_streamer, flat_frame):
    stats = thermal_streamer.frame_statistics(flat_frame, (0.0, 1.0), 0, (False, False), 1.0)

    payload = stats.as_dict("fahrenheit")

    assert payload["units"] == "fahrenheit"
    assert payload["average"] == pytest.approx(86.0, abs=0.1)
    assert payload["width"] == P1_SENSOR_WIDTH


def test_the_stats_payload_carries_marker_pixels_a_client_can_place(thermal_streamer, flat_frame):
    frame = flat_frame.copy()
    frame[7, 9] = raw_for(70.0)

    payload = thermal_streamer.frame_statistics(frame, (0.0, 1.0), 0, (False, False), 1.0).as_dict(
        "celsius"
    )

    assert payload["hotspot"] == {"x": 9, "y": 7}
    assert payload["height"] == P1_SENSOR_HEIGHT


def test_the_overlay_raises_the_encode_until_text_fits(thermal_streamer):
    assert thermal_streamer.encode_upscale((160, 120), 1, overlay_enabled=True) == 2
    assert thermal_streamer.encode_upscale((256, 192), 1, overlay_enabled=True) == 2


def test_a_rotated_frame_is_not_scaled_further_than_an_upright_one(thermal_streamer):
    """Measuring the width would triple a portrait frame for no more legibility than doubling it."""

    assert thermal_streamer.encode_upscale((120, 160), 1, overlay_enabled=True) == 2


def test_the_overlay_never_lowers_an_upscale_someone_asked_for(thermal_streamer):
    assert thermal_streamer.encode_upscale((160, 120), 4, overlay_enabled=True) == 4


def test_without_the_overlay_the_encode_is_left_exactly_as_asked(thermal_streamer):
    """The 43% of a core F-27 bought back stays bought for anyone who turns the readout off."""

    assert thermal_streamer.encode_upscale((160, 120), 1, overlay_enabled=False) == 1


def test_a_frame_with_the_overlay_is_still_a_jpeg(thermal_streamer, palettes):
    renderer = thermal_streamer.ThermalRenderer(
        palettes["ironbow"], thermal_streamer.RenderSettings(overlay=True)
    )

    rendered = renderer.render_frame(thermal_frame())

    assert rendered.jpeg[:2] == b"\xff\xd8"
    assert rendered.jpeg[-2:] == b"\xff\xd9"


def test_a_rendered_frame_carries_the_statistics_of_the_frame_it_encoded(thermal_streamer,
                                                                        palettes):
    renderer = thermal_streamer.ThermalRenderer(
        palettes["ironbow"], thermal_streamer.RenderSettings(emissivity=1.0)
    )

    rendered = renderer.render_frame(thermal_frame(celsius_low=24.0, celsius_high=40.0))

    assert rendered.stats.minimum_celsius == pytest.approx(24.0, abs=0.5)
    assert rendered.stats.maximum_celsius == pytest.approx(40.0, abs=0.5)


def test_the_overlay_actually_marks_the_picture(thermal_streamer, palettes):
    """Same frame, same encode size, overlay the only difference: the pixels must differ."""

    frame = thermal_frame()
    settings = thermal_streamer.RenderSettings
    plain = thermal_streamer.ThermalRenderer(palettes["ironbow"], settings(upscale=2,
                                                                          overlay=False))
    marked = thermal_streamer.ThermalRenderer(palettes["ironbow"], settings(upscale=2,
                                                                           overlay=True))

    plain_pixels = np.asarray(Image.open(io.BytesIO(plain.render_jpeg(frame))))
    marked_pixels = np.asarray(Image.open(io.BytesIO(marked.render_jpeg(frame))))

    assert plain_pixels.shape == marked_pixels.shape
    assert not np.array_equal(plain_pixels, marked_pixels)


def test_the_overlay_survives_a_frame_whose_hotspot_is_in_the_corner(thermal_streamer, palettes,
                                                                    flat_frame):
    """A label beside a corner marker would run off the picture if nothing clamped it."""

    frame = flat_frame.copy()
    frame[-1, -1] = raw_for(120.0)
    renderer = thermal_streamer.ThermalRenderer(palettes["ironbow"])

    assert renderer.render_frame(frame).jpeg[:2] == b"\xff\xd8"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_overlay_survives_every_rotation(thermal_streamer, palettes, rotation):
    settings = thermal_streamer.RenderSettings(rotation=rotation, flip_horizontal=True)
    renderer = thermal_streamer.ThermalRenderer(palettes["ironbow"], settings)

    rendered = renderer.render_frame(thermal_frame())
    size = Image.open(io.BytesIO(rendered.jpeg)).size

    assert size[0] == rendered.stats.width * thermal_streamer.encode_upscale(
        (rendered.stats.width, rendered.stats.height), 1, overlay_enabled=True
    )


def test_a_font_is_available_at_whatever_size_is_asked_for(thermal_streamer):
    assert thermal_streamer.overlay_font(14) is not None


def test_the_posted_form_can_turn_the_readout_off_and_change_its_units(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(overlay=True, units="celsius")

    _, settings = thermal_streamer.settings_from_form(
        {"units": ["fahrenheit"]}, palettes, current
    )

    assert settings.overlay is False
    assert settings.units == "fahrenheit"


def test_an_unknown_unit_leaves_the_setting_alone(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(units="fahrenheit")

    _, settings = thermal_streamer.settings_from_form(
        {"units": ["kelvin"], "overlay": ["on"]}, palettes, current
    )

    assert settings.units == "fahrenheit"
    assert settings.overlay is True


def test_a_glyph_is_rendered_once_and_reused(thermal_streamer):
    """Not an optimisation detail: uncached, the readout costs more than the JPEG under it."""

    first = thermal_streamer.glyph_tile("7", 14, (255, 255, 255))

    assert thermal_streamer.glyph_tile("7", 14, (255, 255, 255)) is first


def test_a_glyph_is_not_shared_between_colours(thermal_streamer):
    white = thermal_streamer.glyph_tile("7", 14, (255, 255, 255))
    red = thermal_streamer.glyph_tile("7", 14, (255, 0, 0))

    assert white is not red


def test_a_label_is_as_wide_as_the_font_says_its_string_is(thermal_streamer):
    """Summing per-character advances has to agree with measuring the whole string."""

    font = thermal_streamer.overlay_font(14)

    assert thermal_streamer.label_width("213.7C", 14) == pytest.approx(
        font.getlength("213.7C"), abs=1.0
    )


def test_a_marker_near_the_colorbar_labels_itself_on_its_other_side(thermal_streamer):
    """The hardware defect this fixes: a corner hotspot's label landed on the colorbar's own."""

    style = thermal_streamer.overlay_style((240, 320))

    left, _ = thermal_streamer.marker_label_position((225, 300), 7, "31.3C", style)

    assert left < 225
    assert left + thermal_streamer.label_width("31.3C", style.pixel_height) <= style.content_right


def test_a_marker_with_room_labels_itself_on_the_right(thermal_streamer):
    style = thermal_streamer.overlay_style((320, 240))

    left, _ = thermal_streamer.marker_label_position((60, 100), 9, "31.3C", style)

    assert left > 60


def test_a_marker_label_never_starts_off_the_left_edge(thermal_streamer):
    """A cramped picture must not push a flipped label to a negative position."""

    style = thermal_streamer.overlay_style((240, 320))

    left, _ = thermal_streamer.marker_label_position((4, 10), 7, "31.3C", style)

    assert left >= 0


def test_the_colorbar_box_leaves_room_for_content_beside_it(thermal_streamer):
    style = thermal_streamer.overlay_style((320, 240))
    bar_left, _, bar_width, _ = style.bar_box

    assert style.content_right < bar_left
    assert bar_left + bar_width <= 320


def test_a_hotspot_inside_the_range_is_ticked_on_the_bar(thermal_streamer):
    row, beyond = thermal_streamer.bar_position(30.0, 20.0, 40.0, 101)

    assert beyond == 0
    assert row == 50


def test_the_top_of_the_bar_is_the_high_end(thermal_streamer):
    row, beyond = thermal_streamer.bar_position(40.0, 20.0, 40.0, 101)

    assert (row, beyond) == (0, 0)


def test_a_hotspot_above_the_range_is_marked_as_past_the_top(thermal_streamer):
    """The case seen on hardware: a bar labelled 29.2 beside a marker reading 35.8."""

    _, beyond = thermal_streamer.bar_position(35.8, 20.9, 29.2, 100)

    assert beyond == 1


def test_a_value_below_the_range_is_marked_as_past_the_bottom(thermal_streamer):
    row, beyond = thermal_streamer.bar_position(10.0, 20.0, 40.0, 100)

    assert (row, beyond) == (99, -1)


def test_a_collapsed_range_does_not_divide_by_zero(thermal_streamer):
    row, beyond = thermal_streamer.bar_position(25.0, 25.0, 25.0, 100)

    assert (row, beyond) == (50, 0)


def test_the_reserved_column_is_wider_than_the_bar(thermal_streamer):
    """It has to clear the bar's labels, which are right-aligned and several times its width."""

    style = thermal_streamer.overlay_style((320, 240))
    bar_left, _, bar_width, _ = style.bar_box

    assert style.content_right < bar_left
    assert 320 - style.content_right > bar_width


def test_a_label_for_a_marker_inside_the_reserved_column_is_pulled_clear_of_it(thermal_streamer):
    """Flipping alone was not enough when the marker itself sits in the reserved column."""

    style = thermal_streamer.overlay_style((240, 320))

    left, _ = thermal_streamer.marker_label_position((236, 300), 7, "31.3C", style)

    assert left + thermal_streamer.label_width("31.3C", style.pixel_height) <= style.content_right
