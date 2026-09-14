# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Turning raw sensor counts into temperatures, and saying where they were measured.

Raw values are sixty-fourths of a Kelvin. Everything here is Celsius, because the sensor has one
scale and a display unit is a presentation choice made at the last moment.

The emissivity correction lives here too, applied to the handful of numbers reported rather than to
the frame: it is monotonic, so the hottest raw pixel is the hottest corrected pixel, and a
full-frame float pass is the one thing the printer's processor cannot afford (F-56).
"""

from __future__ import annotations

import dataclasses

import numpy as np
from p3_camera import (  # type: ignore[import-not-found]
    EnvParams,
    raw_to_celsius_corrected,
)

CELSIUS = "celsius"


FAHRENHEIT = "fahrenheit"


VALID_UNITS = (CELSIUS, FAHRENHEIT)


DEFAULT_UNITS = CELSIUS


# Emissivity is how much of what a surface radiates is its own temperature rather than a reflection
# of the room. A shiny surface reads cold because it is showing you the wall. The correction is
# applied to the handful of temperatures reported, never to the frame: it is monotonic, so the
# hottest pixel is the hottest pixel either way, and a full-frame pass is the one thing this
# processor cannot afford (F-56).
MIN_EMISSIVITY = 0.05


MAX_EMISSIVITY = 1.0


DEFAULT_EMISSIVITY = 0.95


# Close enough to count as the same preset. The stored value is a float and the page's options are
# strings, so they are compared by distance rather than by equality.
EMISSIVITY_MATCH = 0.005


EMISSIVITY_PRESETS = (
    (1.00, "1.00 perfect emitter"),
    (0.98, "0.98 skin, water, matte paint"),
    (0.95, "0.95 matte plastic, PLA, painted"),
    (0.90, "0.90 rough surfaces, ceramic"),
    (0.85, "0.85 glossy plastic, PETG"),
    (0.60, "0.60 oxidised steel"),
    (0.30, "0.30 anodised aluminium"),
    (0.10, "0.10 bare shiny metal"),
)


def to_display_temperature(celsius: float, units: str) -> float:
    """Celsius unless Fahrenheit was asked for. The sensor only ever speaks the one."""

    if units == FAHRENHEIT:
        return celsius * 9.0 / 5.0 + 32.0
    return celsius


def format_temperature(celsius: float, units: str) -> str:
    return f"{to_display_temperature(celsius, units):.1f}{'F' if units == FAHRENHEIT else 'C'}"


def rotate_point_clockwise(
    point: tuple[int, int], size: tuple[int, int]
) -> tuple[tuple[int, int], tuple[int, int]]:
    """One quarter turn clockwise. The frame's width and height trade places with it."""

    x, y = point
    width, height = size
    return (height - 1 - y, x), (height, width)


def orient_point(
    point: tuple[int, int],
    size: tuple[int, int],
    rotation: int,
    mirrors: tuple[bool, bool],
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Where a sensor pixel ends up once the frame has been rotated and mirrored.

    Written to follow `orient` step for step rather than as one derived transform, so the two
    cannot drift apart. A marker that lands somewhere other than the pixel it names is worse than
    no marker at all, because it looks authoritative.
    """

    flip_horizontal, flip_vertical = mirrors
    for _ in range(rotation // 90):
        point, size = rotate_point_clockwise(point, size)
    x, y = point
    width, height = size
    if flip_horizontal:
        x = width - 1 - x
    if flip_vertical:
        y = height - 1 - y
    return (x, y), (width, height)


@dataclasses.dataclass(frozen=True)
class FrameStats:
    """What one frame says about temperature, in the orientation it is displayed in.

    Celsius throughout, because the sensor has one scale and a display unit is a presentation
    choice; the conversion happens where the number is written out, once.
    """

    minimum_celsius: float
    maximum_celsius: float
    average_celsius: float
    centre_celsius: float
    range_low_celsius: float
    range_high_celsius: float
    hotspot: tuple[int, int]
    coldspot: tuple[int, int]
    width: int
    height: int

    def as_dict(self, units: str) -> dict:
        def shown(celsius: float) -> float:
            return round(to_display_temperature(celsius, units), 2)

        return {
            "units": units,
            "minimum": shown(self.minimum_celsius),
            "maximum": shown(self.maximum_celsius),
            "average": shown(self.average_celsius),
            "centre": shown(self.centre_celsius),
            "range_low": shown(self.range_low_celsius),
            "range_high": shown(self.range_high_celsius),
            "hotspot": {"x": self.hotspot[0], "y": self.hotspot[1]},
            "coldspot": {"x": self.coldspot[0], "y": self.coldspot[1]},
            "width": self.width,
            "height": self.height,
        }


def frame_statistics(
    frame: np.ndarray,
    bounds: tuple[float, float],
    rotation: int,
    mirrors: tuple[bool, bool],
    emissivity: float = DEFAULT_EMISSIVITY,
) -> FrameStats:
    """Read the temperatures out of a frame, and say where the extremes ended up on screen.

    Computed for every frame whether or not the overlay is on. Five reductions over a frame this
    small cost about as much as one row of the colormap, and the alternative is a /stats endpoint
    that answers about a frame nobody is looking at.

    The emissivity correction is applied to the six numbers this returns rather than to the frame.
    It is monotonic, so correcting the hottest raw pixel gives the same answer as correcting every
    pixel and then taking the hottest, for a millionth of the work. The average is the one
    approximation: the correction is a fourth-power curve, so the corrected mean is not the mean of
    the corrected pixels. It is close enough to report and not close enough to leave undocumented.
    """

    height, width = frame.shape
    hottest_y, hottest_x = divmod(int(np.argmax(frame)), width)
    coldest_y, coldest_x = divmod(int(np.argmin(frame)), width)
    hotspot, oriented = orient_point((hottest_x, hottest_y), (width, height), rotation, mirrors)
    coldspot, _ = orient_point((coldest_x, coldest_y), (width, height), rotation, mirrors)
    environment = EnvParams(emissivity=emissivity)

    def celsius(raw_value: float) -> float:
        return float(raw_to_celsius_corrected(float(raw_value), environment))

    return FrameStats(
        minimum_celsius=celsius(float(frame.min())),
        maximum_celsius=celsius(float(frame.max())),
        average_celsius=celsius(float(frame.mean())),
        centre_celsius=celsius(float(frame[height // 2, width // 2])),
        range_low_celsius=celsius(bounds[0]),
        range_high_celsius=celsius(bounds[1]),
        hotspot=hotspot,
        coldspot=coldspot,
        width=oriented[0],
        height=oriented[1],
    )
