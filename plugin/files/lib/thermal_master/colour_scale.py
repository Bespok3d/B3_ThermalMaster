# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""How a temperature becomes a colour.

Four scales, chosen on the settings page. The stretch is what the picture has always been: the
palette over the middle 96% of the scene, or over the temperatures somebody held, and everything
past that in the end colours. The other three spread the palette unevenly so that less of the
scene is lost to them. A knee is the stretch below a bend and the rest of the scene squeezed into
the top of the palette above it; a log spreads the whole scene, most of the colours at its cool
end. They were chosen on the U1 in 2026-09 from seven, recorded side by side on the same scenes
(ROADMAP Phase 9).

Worked on raw counts, as the stretch is, because converting a whole frame to degrees is the one
thing the printer's processor cannot afford at frame rate (F-56). The softness of a curve is
chosen in degrees and turned into counts at sixty-four to the degree, which is what a count is:
emissivity bends that a little, and the softness is a shape rather than a measurement.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from .palettes import PALETTE_STEPS
from .temperature import DEFAULT_EMISSIVITY, celsius_for_raw

# Raw sensor counts are sixty-fourths of a Kelvin, so this floor is half a degree. Without it, a
# camera staring at a uniform surface divides by a span of zero.
MIN_SPAN_RAW_COUNTS = 32.0


RAW_COUNTS_PER_KELVIN = 64.0


# The highest raw count there is, and so the end of any table of one.
RAW_COUNT_TOP = 65535


# A log is looked up in a table rather than worked out at every pixel: on the U1 the log over all
# 19,200 pixels of a P1 frame cost 2 to 2.8 ms of a 14.5 ms frame. Each entry covers a power of two
# of counts, so a pixel finds its entry with a shift, and the power is the largest that keeps the
# curve from climbing more than a step of the palette across one entry where it is steepest. The
# entry holds the curve at its middle, so a pixel is never more than one step from the curve.
LOOKUP_PALETTE_STEPS_PER_ENTRY = 1.0


SCALE_STRETCH = "stretch"
SCALE_KNEE = "knee"
SCALE_LOG_MILD = "log-mild"
SCALE_LOG_STRONG = "log-strong"
VALID_COLOUR_SCALES = (SCALE_STRETCH, SCALE_KNEE, SCALE_LOG_MILD, SCALE_LOG_STRONG)
DEFAULT_COLOUR_SCALE = SCALE_STRETCH


# What each is called where a person reads it: on a clip, in the list of clips.
SCALE_NAMES = {
    SCALE_STRETCH: "Stretch",
    SCALE_KNEE: "Knee",
    SCALE_LOG_MILD: "Log, gentle",
    SCALE_LOG_STRONG: "Log, strong",
}


KNEE_CURVE = "knee"
LOG_CURVE = "log"


@dataclasses.dataclass(frozen=True)
class CurveShape:
    """One of the curves: its kind, how soft its log part is in degrees, and where a knee bends.

    The knee is the share of the palette the stretch keeps below the bend. The rest goes to
    everything from the top of the stretch to the hottest thing in view.
    """

    curve: str
    softness_celsius: float
    knee: float = 1.0


CURVE_SHAPES = {
    SCALE_KNEE: CurveShape(KNEE_CURVE, 5.0, 0.85),
    SCALE_LOG_MILD: CurveShape(LOG_CURVE, 10.0),
    SCALE_LOG_STRONG: CurveShape(LOG_CURVE, 3.0),
}


def known_scale(value: object) -> str:
    """A saved scale, or the stretch for one this version does not have.

    The test builds of 0.28.1 to 0.28.4 offered seven. Of the three that went, `today` and
    `option-a` were the stretch already, and `linear` and `knee-soft` are nearest to it.
    """

    return value if isinstance(value, str) and value in VALID_COLOUR_SCALES else SCALE_STRETCH


@dataclasses.dataclass(frozen=True)
class ToldScene:
    """The coldest and hottest count of a whole print, which a clip's curve runs over.

    `range_measured` says whether the clip's range was measured from the print too, rather than
    held at temperatures somebody chose. A held range keeps its meaning under a curve: a log runs
    over the held temperatures, and only a knee reaches past them, to the print's hottest.
    """

    coldest: float
    hottest: float
    range_measured: bool


def span_of(low: float, high: float) -> float:
    return max(high - low, MIN_SPAN_RAW_COUNTS)


def linear_fractions(counts: np.ndarray, low: float, high: float) -> np.ndarray:
    """Where each count falls between two others, as a share of the palette."""

    fractions: np.ndarray = np.clip((counts - low) / span_of(low, high), 0.0, 1.0)
    return fractions


def log_fractions(counts: np.ndarray, low: float, high: float, softness: float) -> np.ndarray:
    """The same, on a log curve: steep just above the low end, and flatter the hotter it gets."""

    lifted = np.log1p(np.maximum(counts - low, 0.0) / softness)
    fractions: np.ndarray = np.clip(lifted / math.log1p(span_of(low, high) / softness), 0.0, 1.0)
    return fractions


def linear_raw(fraction: float, low: float, high: float) -> float:
    """The count at this share of the palette, the other way round from `linear_fractions`."""

    return low + fraction * span_of(low, high)


def log_raw(fraction: float, low: float, high: float, softness: float) -> float:
    return low + softness * math.expm1(fraction * math.log1p(span_of(low, high) / softness))


@dataclasses.dataclass(frozen=True)
class ColourMapping:
    """A curve, and the counts it runs between for one frame.

    The display range is the stretch's: the middle 96% of the scene, or the held temperatures. The
    scene is what a log runs over, and its hottest is where a knee's top ends. The picture and the
    ruler both ask this, which is what keeps them from disagreeing.
    """

    shape: CurveShape
    display: tuple[float, float]
    scene: tuple[float, float]
    emissivity: float = DEFAULT_EMISSIVITY

    @property
    def softness(self) -> float:
        return self.shape.softness_celsius * RAW_COUNTS_PER_KELVIN

    @property
    def ticks(self) -> tuple[float, ...]:
        """Where the ruler is marked with a temperature besides its ends: the bend, or halfway."""

        return (self.shape.knee,) if self.shape.curve == KNEE_CURVE else (0.5,)

    def fractions(self, frame: np.ndarray) -> np.ndarray:
        """The share of the palette each count gets, worked out exactly. The ruler's question."""

        counts = frame.astype(np.float32)
        if self.shape.curve == LOG_CURVE:
            return log_fractions(counts, *self.scene, self.softness)
        knee = self.shape.knee
        bottom, bend = self.display
        below = knee * linear_fractions(counts, bottom, bend)
        above = knee + (1.0 - knee) * log_fractions(counts, bend, self.scene[1], self.softness)
        chosen: np.ndarray = np.where(counts <= bend, below, above)
        return chosen

    def indices(self, frame: np.ndarray) -> np.ndarray:
        """Palette indices for a frame, each the cheapest way its curve allows."""

        if self.shape.curve == LOG_CURVE:
            return self._looked_up(frame)
        return self._knee_indices(frame)

    def _knee_indices(self, frame: np.ndarray) -> np.ndarray:
        """The stretch scaled to the knee's share, and the log only where a pixel is above it.

        Above the bend is the nozzle and little else, a percent or two of the frame, so the log is
        worked out for those pixels alone and the rest costs what the stretch costs.
        """

        knee = self.shape.knee
        top_step = PALETTE_STEPS - 1
        bottom, bend = self.display
        scaled = (frame.astype(np.float32) - bottom) * (knee * top_step / span_of(bottom, bend))
        np.clip(scaled, 0.0, knee * top_step, out=scaled)
        hot = frame > bend
        if hot.any():
            above = log_fractions(frame[hot].astype(np.float32), bend, self.scene[1], self.softness)
            scaled[hot] = (knee + (1.0 - knee) * above) * top_step
        indices: np.ndarray = scaled.astype(np.uint8)
        return indices

    def _looked_up(self, frame: np.ndarray) -> np.ndarray:
        """Palette indices from a table of the curve, done on sixteen bit counts in place.

        The table runs from the count drawn in the bottom colour to the one drawn in the top one.
        On the U1 the passes that find a pixel's entry cost more than the curve's table saved when
        they were done on 32 bit copies of the frame, so they are done on one 16 bit copy.
        """

        low = max(0, math.floor(self.raw_at(0.0)))
        high = min(RAW_COUNT_TOP, max(math.ceil(self.raw_at(1.0)), low + 1))
        widest = LOOKUP_PALETTE_STEPS_PER_ENTRY / ((PALETTE_STEPS - 1) * self.steepest())
        shift = max(0, math.floor(math.log2(widest))) if widest >= 1.0 else 0
        width = 1 << shift
        entries = ((high - low) >> shift) + 1
        middles = low + np.arange(entries, dtype=np.float32) * width + (width - 1) / 2.0
        table = (self.fractions(middles) * (PALETTE_STEPS - 1)).astype(np.uint8)
        counts = np.clip(frame, low, high).astype(np.uint16, copy=False)
        counts -= np.uint16(low)
        counts >>= np.uint16(shift)
        indices: np.ndarray = np.take(table, counts)
        return indices

    def steepest(self) -> float:
        """The most of the palette one count climbs anywhere on a log: at its bottom."""

        return 1.0 / (self.softness * math.log1p(span_of(*self.scene) / self.softness))

    def raw_at(self, fraction: float) -> float:
        """The count drawn at this share of the palette."""

        knee = self.shape.knee
        if self.shape.curve == LOG_CURVE:
            return log_raw(fraction, *self.scene, self.softness)
        if fraction <= knee:
            return linear_raw(fraction / knee, *self.display)
        return log_raw((fraction - knee) / (1.0 - knee), self.display[1], self.scene[1],
                       self.softness)

    def celsius_at(self, fraction: float) -> float:
        return celsius_for_raw(self.raw_at(fraction), self.emissivity)
