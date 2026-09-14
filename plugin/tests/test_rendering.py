# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The display pipeline: raw sensor counts in, a colourmapped RGB image out.

The pipeline is four steps and each one has a way of being subtly wrong that still produces a
picture: noise reduction that ignores its history, bounds that snap instead of easing, a
normalisation that divides by zero on a flat scene, an unsharp mask that inverts. These pin the
behaviour rather than the appearance.
"""

from __future__ import annotations

import numpy as np
import pytest
from fake_camera import thermal_frame

PALETTE_STEPS = 256
EXPECTED_PALETTES = {"ironbow", "rainbow", "white-hot", "black-hot", "military", "sepia"}


@pytest.fixture
def palettes(thermal_streamer):
    return thermal_streamer.build_palettes()


@pytest.fixture
def renderer(thermal_streamer, palettes):
    return thermal_streamer.ThermalRenderer(palettes["ironbow"])


def test_every_advertised_palette_is_built(palettes):
    assert set(palettes) == EXPECTED_PALETTES


def test_every_palette_is_a_full_rgb_table(palettes):
    for name, palette in palettes.items():
        assert palette.shape == (PALETTE_STEPS, 3), name
        assert palette.dtype == np.uint8, name


def test_hot_reads_brighter_than_cold_except_where_it_should_not(palettes):
    """Black hot is the one palette where the coldest pixel is the brightest."""

    for name in EXPECTED_PALETTES - {"black-hot"}:
        assert int(palettes[name][0].sum()) < int(palettes[name][-1].sum()), name
    assert int(palettes["black-hot"][0].sum()) > int(palettes["black-hot"][-1].sum())


def test_military_is_green_and_sepia_is_warm(palettes):
    military_red, military_green, military_blue = palettes["military"][-1]
    assert military_green > military_red and military_green > military_blue

    sepia_red, sepia_green, sepia_blue = palettes["sepia"][-1]
    assert sepia_red > sepia_green > sepia_blue


def test_a_rendered_frame_is_an_rgb_image_of_the_frame_shape(renderer):
    frame = thermal_frame()

    image = renderer.render(frame)

    assert image.shape == (*frame.shape, 3)
    assert image.dtype == np.uint8


def test_a_flat_scene_does_not_divide_by_zero(renderer):
    """A lens cap, or a camera staring at a uniform surface. The span floor is what saves this."""

    image = renderer.render(np.full((120, 160), 19000, dtype=np.uint16))

    assert np.isfinite(image).all()


def test_the_display_range_eases_towards_a_changed_scene_instead_of_snapping(thermal_streamer):
    """The flicker fix. Recomputing bounds per frame re-scaled the whole image constantly."""

    smoothing = thermal_streamer.BOUNDS_SMOOTHING
    eased = thermal_streamer.smooth_bounds((0.0, 100.0), (0.0, 200.0), smoothing)

    assert eased[1] == pytest.approx(100.0 + smoothing * 100.0)
    assert eased[1] < 200.0


def test_the_first_frame_sets_the_range_outright(thermal_streamer):
    """With no history there is nothing to ease from, so the first frame must not be dimmed."""

    assert thermal_streamer.smooth_bounds(None, (10.0, 90.0), 0.15) == (10.0, 90.0)


def test_the_range_converges_on_a_steady_scene(renderer):
    frame = thermal_frame()

    for _ in range(200):
        renderer.render(frame)
    settled = renderer.bounds

    renderer.render(frame)
    assert renderer.bounds[0] == pytest.approx(settled[0], abs=1.0)
    assert renderer.bounds[1] == pytest.approx(settled[1], abs=1.0)


def test_noise_reduction_averages_against_the_previous_frame(thermal_streamer):
    previous = np.full((4, 4), 1000, dtype=np.uint16)
    current = np.full((4, 4), 2000, dtype=np.uint16)

    blended = thermal_streamer.reduce_temporal_noise(current, previous, 0.5)

    assert blended.dtype == np.uint16
    assert int(blended[0, 0]) == 1500


def test_noise_reduction_passes_the_first_frame_through(thermal_streamer):
    current = np.full((4, 4), 2000, dtype=np.uint16)

    assert np.array_equal(thermal_streamer.reduce_temporal_noise(current, None, 0.5), current)


def test_normalisation_puts_the_bounds_at_the_ends_of_the_palette(thermal_streamer):
    frame = np.array([[1000, 1500, 2000]], dtype=np.uint16)

    normalized = thermal_streamer.normalize_to_bytes(frame, 1000.0, 2000.0)

    assert normalized[0, 0] == 0
    assert normalized[0, 2] == PALETTE_STEPS - 1


def test_anything_beyond_the_bounds_is_clipped_not_wrapped(thermal_streamer):
    """Wrapping here would paint the hottest thing in frame with the coldest colour."""

    frame = np.array([[500, 5000]], dtype=np.uint16)

    normalized = thermal_streamer.normalize_to_bytes(frame, 1000.0, 2000.0)

    assert normalized[0, 0] == 0
    assert normalized[0, 1] == PALETTE_STEPS - 1


def test_detail_enhancement_sharpens_an_edge_rather_than_softening_it(thermal_streamer):
    edge = np.zeros((5, 6), dtype=np.uint8)
    edge[:, 3:] = 200

    sharpened = thermal_streamer.enhance_detail(edge, 0.8)

    assert int(sharpened[2, 3]) >= int(edge[2, 3])
    assert int(sharpened[2, 2]) <= int(edge[2, 2])


def test_detail_enhancement_off_is_a_passthrough(thermal_streamer):
    image = np.arange(24, dtype=np.uint8).reshape(4, 6)

    assert np.array_equal(thermal_streamer.enhance_detail(image, 0.0), image)


def test_the_blur_leaves_a_flat_field_flat(thermal_streamer):
    """Edge padding matters: zero padding would darken the border of every frame."""

    flat = np.full((6, 6), 100, dtype=np.uint8)

    assert np.allclose(thermal_streamer.blur_3x3(flat), 100.0)
