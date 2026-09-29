# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""How a temperature becomes a colour, when it is not the straight stretch the picture has always
used.

On the test/color-bar branch only, to record the same scenes with each mapping and choose one
(ROADMAP Phase 9, the colour bar question). The straight stretch puts the palette over the middle
96% of the scene and draws everything hotter in the top colour, which gives the bed and the part
every colour there is and leaves a ruler that is flat white from the part's temperature up to the
nozzle's. The curves here spread the palette over the whole scene instead, unevenly: the cool
part of the scene keeps more of the colours than a straight line would give it, and the hot part
keeps some rather than none.

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


# The seven being compared, named as the panels of the study in `Claude outputs/bar-study/` were.
# Today and Option A are the straight stretch, and differ only in the ruler: today's spans the
# scene, Option A's spans the colours and says with a triangle that the scene goes past them.
SCALE_TODAY = "today"
SCALE_OPTION_A = "option-a"
SCALE_LINEAR = "linear"
SCALE_LOG_MILD = "log-mild"
SCALE_LOG_STRONG = "log-strong"
SCALE_KNEE = "knee"
SCALE_KNEE_SOFT = "knee-soft"
VALID_COLOUR_SCALES = (
    SCALE_TODAY,
    SCALE_OPTION_A,
    SCALE_LINEAR,
    SCALE_LOG_MILD,
    SCALE_LOG_STRONG,
    SCALE_KNEE,
    SCALE_KNEE_SOFT,
)
DEFAULT_COLOUR_SCALE = SCALE_TODAY


LINEAR_CURVE = "linear"
LOG_CURVE = "log"
KNEE_CURVE = "knee"


@dataclasses.dataclass(frozen=True)
class CurveShape:
    """One of the curves: its kind, how soft its log part is in degrees, and where a knee bends.

    The knee is the share of the palette the stretch keeps below the bend. The rest goes to
    everything from the top of the stretch to the hottest thing in view.
    """

    curve: str
    softness_celsius: float = 0.0
    knee: float = 1.0


CURVE_SHAPES = {
    SCALE_LINEAR: CurveShape(LINEAR_CURVE),
    SCALE_LOG_MILD: CurveShape(LOG_CURVE, 10.0),
    SCALE_LOG_STRONG: CurveShape(LOG_CURVE, 3.0),
    SCALE_KNEE: CurveShape(KNEE_CURVE, 5.0, 0.85),
    SCALE_KNEE_SOFT: CurveShape(KNEE_CURVE, 5.0, 0.75),
}


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

    The display range is the stretch's, the middle 96% of the scene; the scene is its coldest and
    hottest. The log and linear curves run over the scene, and a knee runs the stretch up to the
    top of the display range and the log curve from there to the hottest thing in view.

    The picture and the ruler both ask this, which is what keeps them from disagreeing.
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
        counts = frame.astype(np.float32)
        low, high = self.scene
        if self.shape.curve == LINEAR_CURVE:
            return linear_fractions(counts, low, high)
        if self.shape.curve == LOG_CURVE:
            return log_fractions(counts, low, high, self.softness)
        return self._knee_fractions(counts)

    def _knee_fractions(self, counts: np.ndarray) -> np.ndarray:
        knee = self.shape.knee
        bottom, bend = self.display
        below = knee * linear_fractions(counts, bottom, bend)
        above = knee + (1.0 - knee) * log_fractions(counts, bend, self.scene[1], self.softness)
        chosen: np.ndarray = np.where(counts <= bend, below, above)
        return chosen

    def indices(self, frame: np.ndarray) -> np.ndarray:
        """Palette indices, truncated the way the stretch truncates them."""

        indices: np.ndarray = (self.fractions(frame) * (PALETTE_STEPS - 1)).astype(np.uint8)
        return indices

    def raw_at(self, fraction: float) -> float:
        """The count drawn at this share of the palette."""

        low, high = self.scene
        knee = self.shape.knee
        if self.shape.curve == LINEAR_CURVE:
            return linear_raw(fraction, low, high)
        if self.shape.curve == LOG_CURVE:
            return log_raw(fraction, low, high, self.softness)
        if fraction <= knee:
            return linear_raw(fraction / knee, *self.display)
        return log_raw((fraction - knee) / (1.0 - knee), self.display[1], high, self.softness)

    def celsius_at(self, fraction: float) -> float:
        return celsius_for_raw(self.raw_at(fraction), self.emissivity)
