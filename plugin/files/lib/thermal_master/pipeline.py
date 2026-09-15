# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Raw sensor frames in, a colourmapped JPEG out.

Four steps, each with a way of being subtly wrong that still produces a picture: noise reduction
that ignores its history, bounds that snap instead of easing, a normalisation that divides by zero
on a flat scene, an unsharp mask that inverts.

Measured on the printer, this is where the time goes: about half the frame is this module and a
tenth is the encode, which is the opposite of what every development machine reports (F-56).
"""

from __future__ import annotations

import dataclasses
import io

import numpy as np
from PIL import Image

from .overlay import Overlay, draw_overlay, encode_upscale
from .palettes import PALETTE_STEPS
from .temperature import (
    DEFAULT_EMISSIVITY,
    DEFAULT_UNITS,
    FrameStats,
    frame_statistics,
)

# Encode at the sensor's own size and let the browser scale it. Upscaling here cost four fifths of
# the plugin's CPU: measured on hardware at 62.5% of a core, of which the 4x resize and the 640x480
# encode it produced were about 49 points. The browser scales the tile to fit regardless, so the
# extra pixels bought nothing but a second lossy resampling step. Quality rises to compensate for
# the smaller image, which costs nothing measurable.
DEFAULT_UPSCALE = 1


DEFAULT_JPEG_QUALITY = 88


# Orientation is applied here rather than left to the viewer. Fluidd can rotate a camera itself, but
# only from a config file this plugin owns, which means a reinstall to change it, and the installer
# does not reliably carry the choice through. Owning it makes it a live setting instead. A rotation
# is a memory shuffle of a frame this small, far below the cost of the encode.
VALID_ROTATIONS = (0, 90, 180, 270)


NORMALIZE_LOW_PERCENTILE = 2.0


NORMALIZE_HIGH_PERCENTILE = 98.0


# Raw sensor counts are sixty-fourths of a Kelvin, so this floor is half a degree. Without it, a
# camera staring at a uniform surface divides by a span of zero.
MIN_SPAN_RAW_COUNTS = 32.0


# How fast the displayed range chases the scene, per frame. The bounds used to be recomputed from
# scratch every frame, so the whole image re-scaled whenever anything warm entered or left the view,
# which reads as a constant flicker. At 25 fps this settles a full swing in about a second.
BOUNDS_SMOOTHING = 0.15


# Averaging each frame with the one before halves the per-pixel sensor noise. Higher is more
# responsive and noisier; 1.0 disables it.
NOISE_REDUCTION_WEIGHT = 0.5


# Unsharp mask strength. Thermal scenes are naturally soft, and a little edge emphasis makes the
# difference between seeing a part and seeing a warm blob. 0 disables it.
DETAIL_STRENGTH = 0.3


def reduce_temporal_noise(
    current: np.ndarray, previous: np.ndarray | None, weight: float
) -> np.ndarray:
    """Average this frame with the last one, which halves per-pixel sensor noise."""

    if previous is None or weight >= 1.0:
        return current
    blended = weight * current.astype(np.float32) + (1.0 - weight) * previous.astype(np.float32)
    return blended.astype(np.uint16)


def frame_bounds(frame: np.ndarray) -> tuple[float, float]:
    """The raw counts to map to the ends of the palette.

    Percentiles rather than min and max, so one dead pixel or one glint does not take the whole
    range with it. Taken on raw counts rather than Celsius: the conversion is monotonic, so the
    percentiles land on the same pixels either way, and this skips converting the whole frame to
    float just to find two numbers.
    """

    return (
        float(np.percentile(frame, NORMALIZE_LOW_PERCENTILE)),
        float(np.percentile(frame, NORMALIZE_HIGH_PERCENTILE)),
    )


def smooth_bounds(
    previous: tuple[float, float] | None, current: tuple[float, float], smoothing: float
) -> tuple[float, float]:
    """Ease the displayed range towards the scene instead of snapping to it every frame."""

    if previous is None:
        return current
    return (
        smoothing * current[0] + (1.0 - smoothing) * previous[0],
        smoothing * current[1] + (1.0 - smoothing) * previous[1],
    )


def normalize_to_bytes(frame: np.ndarray, low: float, high: float) -> np.ndarray:
    span = max(high - low, MIN_SPAN_RAW_COUNTS)
    normalized = (frame.astype(np.float32) - low) / span
    return (np.clip(normalized, 0.0, 1.0) * (PALETTE_STEPS - 1)).astype(np.uint8)


def blur_3x3(image: np.ndarray) -> np.ndarray:
    """A separable 1-2-1 blur. Two passes over the frame, no image library needed."""

    padded = np.pad(image.astype(np.float32), 1, mode="edge")
    horizontal = (padded[:, :-2] + 2.0 * padded[:, 1:-1] + padded[:, 2:]) / 4.0
    return (horizontal[:-2, :] + 2.0 * horizontal[1:-1, :] + horizontal[2:, :]) / 4.0


def enhance_detail(image: np.ndarray, strength: float) -> np.ndarray:
    """Unsharp mask: add back a fraction of what a blur removed, which is the edges."""

    if strength <= 0.0:
        return image
    sharpened = image.astype(np.float32) + strength * (image.astype(np.float32) - blur_3x3(image))
    clipped: np.ndarray = np.clip(sharpened, 0.0, 255.0).astype(np.uint8)
    return clipped


@dataclasses.dataclass(frozen=True)
class RenderedFrame:
    """A frame and what it measured, kept together so /stats and the picture cannot disagree."""

    jpeg: bytes
    stats: FrameStats


@dataclasses.dataclass(frozen=True)
class RenderSettings:
    """How the picture is made.

    Grouped rather than passed individually because they are tuned together, and because the control
    page will need to hand a whole new set to the renderer at once.
    """

    bounds_smoothing: float = BOUNDS_SMOOTHING
    noise_reduction_weight: float = NOISE_REDUCTION_WEIGHT
    detail_strength: float = DETAIL_STRENGTH
    upscale: int = DEFAULT_UPSCALE
    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    rotation: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    # Two switches rather than one, because they are two things to a person looking at a tile.
    # The colorbar is a ruler down the edge that says what a colour means and costs five percent
    # of the width; the markers are numbers drawn over the picture itself. Wanting either
    # without the other is reasonable, and one switch could not express it.
    colorbar: bool = True
    reticle: bool = True
    hotspot: bool = True
    coldspot: bool = True
    units: str = DEFAULT_UNITS
    emissivity: float = DEFAULT_EMISSIVITY

    @property
    def readout(self) -> bool:
        """Whether anything is drawn on top of the picture at all.

        What the encode size keys off, since it is text that needs the extra pixels and both
        surfaces draw text. Turning both off returns the plugin to the cost it had before there
        was a readout.
        """

        return self.colorbar or self.reticle or self.hotspot or self.coldspot

    @property
    def mirrors(self) -> tuple[bool, bool]:
        """The two flips as one value, since nothing ever wants only one of them."""

        return (self.flip_horizontal, self.flip_vertical)


class ThermalRenderer:
    """Raw sensor frames in, colourmapped RGB out, carrying the state that spans frames.

    Two things have to persist between frames and so cannot live in a function: the smoothed
    display bounds, and the previous frame the noise reduction averages against.
    """

    def __init__(
        self, palette: np.ndarray, settings: RenderSettings = RenderSettings()
    ) -> None:
        self._palette = palette
        self._settings = settings
        self._bounds: tuple[float, float] | None = None
        self._previous_frame: np.ndarray | None = None

    @property
    def bounds(self) -> tuple[float, float] | None:
        """The smoothed display range in raw counts, or None before the first frame."""

        return self._bounds

    def render(self, thermal_raw: np.ndarray) -> np.ndarray:
        return self.render_image(thermal_raw)[0]

    def render_image(self, thermal_raw: np.ndarray) -> tuple[np.ndarray, FrameStats]:
        denoised = reduce_temporal_noise(
            thermal_raw, self._previous_frame, self._settings.noise_reduction_weight
        )
        self._previous_frame = denoised
        self._bounds = smooth_bounds(
            self._bounds, frame_bounds(denoised), self._settings.bounds_smoothing
        )
        # Measured off the denoised frame rather than the raw one, so the number beside a marker is
        # the temperature of the pixel that was actually drawn there.
        stats = frame_statistics(
            denoised,
            self._bounds,
            self._settings.rotation,
            self._settings.mirrors,
            self._settings.emissivity,
        )
        normalized = normalize_to_bytes(denoised, *self._bounds)
        coloured = self._palette[enhance_detail(normalized, self._settings.detail_strength)]
        # Last, so everything before it works in the sensor's own orientation and the frame kept for
        # noise reduction cannot change shape when the setting does.
        oriented = orient(
            coloured,
            self._settings.rotation,
            self._settings.flip_horizontal,
            self._settings.flip_vertical,
        )
        return oriented, stats

    def render_frame(self, thermal_raw: np.ndarray) -> RenderedFrame:
        image, stats = self.render_image(thermal_raw)
        settings = self._settings
        overlay = (
            Overlay(
                self._palette,
                stats,
                settings.units,
                settings.colorbar,
                settings.reticle,
                settings.hotspot,
                settings.coldspot,
            )
            if settings.readout
            else None
        )
        upscale = encode_upscale(
            (image.shape[1], image.shape[0]), settings.upscale, settings.readout
        )
        return RenderedFrame(
            encode_jpeg(image, upscale, settings.jpeg_quality, overlay), stats
        )

    def render_jpeg(self, thermal_raw: np.ndarray) -> bytes:
        return self.render_frame(thermal_raw).jpeg


def orient(
    image: np.ndarray, rotation: int, flip_horizontal: bool, flip_vertical: bool
) -> np.ndarray:
    """Rotate clockwise by whole quarter turns, then mirror.

    numpy rotates anticlockwise, so the sign is flipped: `rotation` is degrees clockwise, which is
    what a person means when they say the picture is on its side. The result is made contiguous
    because a rotation returns a view with negative strides and Pillow will not read that.
    """

    if rotation:
        image = np.rot90(image, k=-(rotation // 90))
    if flip_horizontal:
        image = np.fliplr(image)
    if flip_vertical:
        image = np.flipud(image)
    return np.ascontiguousarray(image)


def encode_jpeg(
    rgb_frame: np.ndarray, upscale: int, quality: int, overlay: Overlay | None = None
) -> bytes:
    image = Image.fromarray(rgb_frame, mode="RGB")
    height, width = rgb_frame.shape[0], rgb_frame.shape[1]
    if upscale != 1:
        image = image.resize((width * upscale, height * upscale), Image.Resampling.BILINEAR)
    # After the resize, so the text is drawn at the size it will be looked at rather than resampled.
    if overlay is not None:
        draw_overlay(image, overlay)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
