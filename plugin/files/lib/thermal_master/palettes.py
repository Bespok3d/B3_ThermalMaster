# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The colour tables the thermal image is drawn with.

Two shapes, because palettes come in two. A ramp interpolates between colour stops, which is what
makes ironbow and rainbow. A tint scales a grey ramp per channel, which is what makes military green
and sepia warm without inventing a curve for each.
"""

from __future__ import annotations

import numpy as np

PALETTE_STEPS = 256


# Palettes defined two ways, because they come in two shapes. A ramp interpolates between colour
# stops; a tint scales a grey ramp per channel, which is what makes military green and sepia warm.
PALETTE_RAMPS = {
    "ironbow": (
        (0.0, (0, 0, 0)),
        (0.25, (60, 0, 110)),
        (0.5, (180, 40, 90)),
        (0.75, (250, 150, 30)),
        (1.0, (255, 255, 200)),
    ),
    "rainbow": (
        (0.0, (0, 0, 140)),
        (0.25, (0, 180, 255)),
        (0.5, (0, 200, 60)),
        (0.75, (255, 220, 0)),
        (1.0, (190, 0, 0)),
    ),
}


PALETTE_TINTS = {
    "white-hot": (1.0, 1.0, 1.0),
    "military": (0.3, 1.0, 0.2),
    "sepia": (1.0, 0.7, 0.4),
}


DEFAULT_PALETTE = "ironbow"


def build_ramp_palette(stops: tuple) -> np.ndarray:
    stop_positions = np.array([position for position, _ in stops], dtype=np.float32)
    stop_colors = np.array([color for _, color in stops], dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, PALETTE_STEPS, dtype=np.float32)
    palette = np.empty((PALETTE_STEPS, 3), dtype=np.uint8)
    for channel in range(3):
        ramped_channel = np.interp(ramp, stop_positions, stop_colors[:, channel])
        palette[:, channel] = ramped_channel.astype(np.uint8)
    return palette


def build_tint_palette(tint: tuple) -> np.ndarray:
    grey = np.linspace(0.0, 255.0, PALETTE_STEPS, dtype=np.float32)
    palette = np.empty((PALETTE_STEPS, 3), dtype=np.uint8)
    for channel, scale in enumerate(tint):
        palette[:, channel] = (grey * scale).astype(np.uint8)
    return palette


def build_palettes() -> dict:
    """Every palette by name. Black hot is white hot read backwards, which is all it ever was."""

    palettes = {name: build_ramp_palette(stops) for name, stops in PALETTE_RAMPS.items()}
    palettes.update({name: build_tint_palette(tint) for name, tint in PALETTE_TINTS.items()})
    palettes["black-hot"] = palettes["white-hot"][::-1].copy()
    return palettes
