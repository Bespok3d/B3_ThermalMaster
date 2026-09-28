# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The optimised steps, pinned against the implementations they replaced.

Every change in this round was taken on the printer's numbers and on the promise that the picture
does not change, so each one is checked here against the straightforward version written out in
full. A faster step that quietly draws something else is not an optimisation, and these are exactly
the kind of change that no other test would notice: the picture still appears, the numbers still
look plausible, and nothing raises.
"""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def scene():
    """A ramp with a hot blob and some noise, so percentiles and edges both have work to do."""

    generator = np.random.default_rng(7)
    ramp = np.linspace(19000.0, 21000.0, 160 * 120, dtype=np.float32).reshape((120, 160))
    rows, columns = np.ogrid[:120, :160]
    ramp[((rows - 40) ** 2 + (columns - 50) ** 2) < 400] = 23000.0
    return (ramp + generator.normal(0.0, 30.0, ramp.shape)).astype(np.uint16)


def test_the_bounds_are_what_two_separate_percentile_calls_said(thermal_streamer, scene):
    """One call for both percentiles, and the same two numbers to the last bit."""

    separately = (
        float(np.percentile(scene, thermal_streamer.NORMALIZE_LOW_PERCENTILE)),
        float(np.percentile(scene, thermal_streamer.NORMALIZE_HIGH_PERCENTILE)),
    )

    assert thermal_streamer.frame_bounds(scene) == separately


def test_the_integer_blend_is_the_float_blend(thermal_streamer, scene):
    """The half-and-half average, in integers, is the average the float path computed."""

    previous = np.roll(scene, 3, axis=1)
    weight = thermal_streamer.EVEN_BLEND_WEIGHT
    as_floats = (
        weight * scene.astype(np.float32) + (1.0 - weight) * previous.astype(np.float32)
    ).astype(np.uint16)

    blended = thermal_streamer.reduce_temporal_noise(scene, previous, weight)

    assert np.array_equal(blended, as_floats)


def test_another_weight_still_goes_the_long_way(thermal_streamer, scene):
    """The integer path is for one weight. Anything else keeps the arithmetic it had."""

    previous = np.roll(scene, 3, axis=1)
    as_floats = (0.25 * scene.astype(np.float32) + 0.75 * previous.astype(np.float32)).astype(
        np.uint16
    )

    assert np.array_equal(
        thermal_streamer.reduce_temporal_noise(scene, previous, 0.25), as_floats
    )


def test_a_blend_of_the_hottest_frames_does_not_wrap(thermal_streamer):
    """Two uint16 frames added together overflow uint16, and would wrap to cold rather than hot."""

    hot = np.full((4, 4), 65535, dtype=np.uint16)

    blended = thermal_streamer.reduce_temporal_noise(hot, hot, 0.5)

    assert blended.dtype == np.uint16
    assert int(blended.max()) == 65535


def test_the_palette_lookup_is_the_one_it_replaced(thermal_streamer, scene):
    """`take` against fancy indexing, byte for byte, over a whole rendered frame."""

    palette = thermal_streamer.build_palettes()["ironbow"]
    settings = thermal_streamer.RenderSettings(rotation=90)
    renderer = thermal_streamer.ThermalRenderer(palette, settings)

    image, _, denoised = renderer.render_image(scene)

    bounds = renderer.bounds
    assert bounds is not None
    normalized = thermal_streamer.normalize_to_bytes(denoised, *bounds)
    detailed = thermal_streamer.enhance_detail(normalized, settings.detail_strength)
    expected = thermal_streamer.orient(palette[detailed], 90, False, False)

    assert np.array_equal(image, expected)
