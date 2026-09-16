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

from .geometry import orient, orient_point

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


# The wire format for a frame of temperatures. Hundredths of a degree in a signed 16 bit integer
# covers -327 C to +327 C, which is well past what either sensor can report, and it is exact to the
# tenth of a degree the readout shows. Floats would double the bytes to carry precision the camera
# does not have.
THERMAL_FRAME_MAGIC = b"TMF1"
THERMAL_FRAME_HEADER = 16
CENTIDEGREES_PER_DEGREE = 100
MIN_CENTIDEGREES = -32000
MAX_CENTIDEGREES = 32000


@dataclasses.dataclass(frozen=True)
class ThermalFrame:
    """One frame of raw sensor counts, with everything needed to make sense of it later.

    Raw rather than converted, and unoriented, because converting a whole frame is four float
    passes and this processor cannot afford them at frame rate (F-56). The settings that were in
    force are carried along so a request arriving later converts the frame the way it was measured
    rather than the way the settings happen to read by then.
    """

    counts: np.ndarray
    rotation: int
    mirrors: tuple[bool, bool]
    emissivity: float


def encode_thermal_frame(frame: ThermalFrame) -> bytes:
    """A frame of temperatures as bytes a browser can index into.

    Self describing rather than relying on HTTP headers, so the body can be saved, replayed and
    tested on its own, and so a proxy that strips headers cannot silently break the reader.

    Oriented here rather than when it was captured, for the same reason it is stored as counts: the
    work happens once per request, and requests only happen while someone is looking.
    """

    oriented = orient(frame.counts, frame.rotation, *frame.mirrors)
    environment = EnvParams(emissivity=frame.emissivity)
    celsius = np.asarray(raw_to_celsius_corrected(oriented, environment), dtype=np.float32)
    centidegrees = np.clip(
        celsius * CENTIDEGREES_PER_DEGREE, MIN_CENTIDEGREES, MAX_CENTIDEGREES
    ).astype("<i2")
    height, width = centidegrees.shape
    header = (
        THERMAL_FRAME_MAGIC
        + np.array(
            [width, height, CENTIDEGREES_PER_DEGREE, 0, 0, 0], dtype="<u2"
        ).tobytes()
    )
    return header + centidegrees.tobytes()


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
