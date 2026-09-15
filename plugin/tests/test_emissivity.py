# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Emissivity: the one setting that changes the numbers without touching the camera or the picture.

These assert direction and identity rather than figures. The correction's arithmetic lives in the
vendored driver, and the tests run against a stand-in, so pinning a decimal here would only pin the
stand-in to itself. What has to hold is the physics: a surface that emits less of its own heat is
hotter than it appears, the correction leaves a perfect emitter alone, and none of it moves a pixel.
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

# The correction is against reflected surroundings the driver assumes are at 25 C, so a test object
# has to be well above that for the direction to be unambiguous.
OBJECT_CELSIUS = 80.0


def raw_for(celsius: float) -> int:
    return int((celsius + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN)


@pytest.fixture
def warm_frame():
    return np.full((P1_SENSOR_HEIGHT, P1_SENSOR_WIDTH), raw_for(OBJECT_CELSIUS), dtype=np.uint16)


def maximum_at(thermal_streamer, frame, emissivity):
    stats = thermal_streamer.frame_statistics(frame, (0.0, 1.0), 0, (False, False), emissivity)
    return stats.maximum_celsius


def test_a_perfect_emitter_is_left_alone(thermal_streamer, warm_frame):
    assert maximum_at(thermal_streamer, warm_frame, 1.0) == pytest.approx(OBJECT_CELSIUS, abs=0.1)


def test_a_poor_emitter_is_hotter_than_it_looks(thermal_streamer, warm_frame):
    """A shiny surface shows you the room, so the same reading means a hotter object."""

    assert maximum_at(thermal_streamer, warm_frame, 0.5) > maximum_at(
        thermal_streamer, warm_frame, 1.0
    )


def test_the_correction_grows_as_emissivity_falls(thermal_streamer, warm_frame):
    readings = [maximum_at(thermal_streamer, warm_frame, e) for e in (1.0, 0.9, 0.7, 0.5, 0.3)]

    assert readings == sorted(readings)


def test_the_default_is_a_correction_and_not_the_identity(thermal_streamer, warm_frame):
    """0.95 rather than 1.0, so the plugin is right about matte plastic out of the box."""

    assert thermal_streamer.DEFAULT_EMISSIVITY == 0.95
    assert maximum_at(thermal_streamer, warm_frame, thermal_streamer.DEFAULT_EMISSIVITY) > (
        maximum_at(thermal_streamer, warm_frame, 1.0)
    )


def test_emissivity_does_not_move_a_single_pixel(thermal_streamer):
    """It changes what the numbers mean, never what the sensor saw, so the image must not shift."""

    frame = thermal_frame()
    palettes = thermal_streamer.build_palettes()
    settings = thermal_streamer.RenderSettings
    plain = thermal_streamer.ThermalRenderer(
        palettes["ironbow"], settings(colorbar=False, markers=False, emissivity=1.0)
    )
    shiny = thermal_streamer.ThermalRenderer(
        palettes["ironbow"], settings(colorbar=False, markers=False, emissivity=0.3)
    )

    first = np.asarray(Image.open(io.BytesIO(plain.render_jpeg(frame))))
    second = np.asarray(Image.open(io.BytesIO(shiny.render_jpeg(frame))))

    assert np.array_equal(first, second)


def test_the_hottest_pixel_is_the_same_pixel_whatever_the_emissivity(thermal_streamer):
    """The correction is monotonic, which is the licence for applying it after the reduction."""

    frame = np.full((P1_SENSOR_HEIGHT, P1_SENSOR_WIDTH), raw_for(30.0), dtype=np.uint16)
    frame[11, 22] = raw_for(90.0)

    for emissivity in (1.0, 0.6, 0.2):
        stats = thermal_streamer.frame_statistics(
            frame, (0.0, 1.0), 0, (False, False), emissivity
        )
        assert stats.hotspot == (22, 11)


def test_a_posted_emissivity_is_taken(thermal_streamer):
    palettes = thermal_streamer.build_palettes()
    current = thermal_streamer.RenderSettings()

    _, settings = thermal_streamer.settings_from_form(
        {"emissivity": ["0.30"]}, palettes, current
    )

    assert settings.emissivity == pytest.approx(0.30)


def test_a_nonsense_emissivity_leaves_the_setting_alone(thermal_streamer):
    palettes = thermal_streamer.build_palettes()
    current = thermal_streamer.RenderSettings(emissivity=0.85)

    _, settings = thermal_streamer.settings_from_form(
        {"emissivity": ["shiny"]}, palettes, current
    )

    assert settings.emissivity == pytest.approx(0.85)


@pytest.mark.parametrize(("posted", "expected"), [("0.0", 0.05), ("-3", 0.05), ("7", 1.0)])
def test_an_out_of_range_emissivity_is_clamped(thermal_streamer, posted, expected):
    """Zero would divide by zero inside the correction, so the floor is not zero."""

    palettes = thermal_streamer.build_palettes()

    _, settings = thermal_streamer.settings_from_form(
        {"emissivity": [posted]}, palettes, thermal_streamer.RenderSettings()
    )

    assert settings.emissivity == pytest.approx(expected)


def test_the_emissivity_survives_a_restart(thermal_streamer, tmp_path):
    state_file = tmp_path / "settings.json"
    first = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    first.update("ironbow", thermal_streamer.RenderSettings(emissivity=0.3))
    second = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert second.as_dict()["emissivity"] == pytest.approx(0.3)


def test_the_presets_cover_skin(thermal_streamer):
    """0.98 is about right for skin, water and matte paint, which is what people point it at."""

    assert 0.98 in [value for value, _ in thermal_streamer.EMISSIVITY_PRESETS]


def test_every_preset_is_offered_by_the_page(thermal_streamer):
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    for value, _ in thermal_streamer.EMISSIVITY_PRESETS:
        assert f'value="{value:.2f}"' in page


def test_the_presets_are_distinct_and_ordered(thermal_streamer):
    """Two presets a hundredth apart would both match the same stored value on the page."""

    values = [value for value, _ in thermal_streamer.EMISSIVITY_PRESETS]

    assert values == sorted(values, reverse=True)
    assert len(set(values)) == len(values)
    assert min(abs(a - b) for a, b in zip(values, values[1:])) > thermal_streamer.EMISSIVITY_MATCH


def test_every_preset_is_in_range(thermal_streamer):
    for value, _ in thermal_streamer.EMISSIVITY_PRESETS:
        assert thermal_streamer.MIN_EMISSIVITY <= value <= thermal_streamer.MAX_EMISSIVITY
