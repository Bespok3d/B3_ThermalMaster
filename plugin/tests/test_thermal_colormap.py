# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The display pipeline's contract: raw sensor counts in, a colormapped RGB image out.

These pin the behaviour the image pipeline work will refactor (ROADMAP phase 5), so that swapping in
smoothed auto-gain, a different palette, or a cheaper normalization path has to keep meaning the
same thing.
"""

from __future__ import annotations

import numpy as np

PALETTE_STEPS = 256


def test_palette_has_one_rgb_triple_per_step(thermal_streamer):
    palette = thermal_streamer.build_ironbow_palette()

    assert palette.shape == (PALETTE_STEPS, 3)
    assert palette.dtype == np.uint8


def test_palette_runs_dark_to_bright(thermal_streamer):
    palette = thermal_streamer.build_ironbow_palette()

    assert int(palette[0].sum()) < int(palette[-1].sum())


def test_colormap_returns_an_rgb_image_shaped_like_the_frame(thermal_streamer, thermal_ramp_frame):
    palette = thermal_streamer.build_ironbow_palette()

    image = thermal_streamer.colormap_thermal(thermal_ramp_frame, palette)

    assert image.shape == (*thermal_ramp_frame.shape, 3)
    assert image.dtype == np.uint8


def test_colormap_puts_the_coldest_and_hottest_pixels_at_the_palette_ends(
    thermal_streamer, thermal_ramp_frame
):
    palette = thermal_streamer.build_ironbow_palette()

    image = thermal_streamer.colormap_thermal(thermal_ramp_frame, palette)

    coldest = np.unravel_index(int(np.argmin(thermal_ramp_frame)), thermal_ramp_frame.shape)
    hottest = np.unravel_index(int(np.argmax(thermal_ramp_frame)), thermal_ramp_frame.shape)
    assert np.array_equal(image[coldest], palette[0])
    assert np.array_equal(image[hottest], palette[PALETTE_STEPS - 1])


def test_colormap_survives_a_flat_frame_without_dividing_by_zero(thermal_streamer):
    """A lens cap, a warm-up frame, or a camera staring at a uniform surface.

    Every pixel is identical, so the auto-range span is zero. The floor on that span is what keeps
    this from producing NaNs, and a NaN here reaches the JPEG encoder as a crash.
    """

    palette = thermal_streamer.build_ironbow_palette()
    flat_frame = np.full((120, 160), 19000, dtype=np.uint16)

    image = thermal_streamer.colormap_thermal(flat_frame, palette)

    assert image.shape == (120, 160, 3)
    assert np.isfinite(image).all()
