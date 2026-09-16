# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The readout burned into the picture: the colorbar, the centre reading, the hotspot marker.

Into the picture rather than onto a page, because the surface that matters is the camera tile in
Fluidd and Mainsail, and that is a plain image with nowhere to hang an annotation.

The glyph cache is not an optimisation detail. Pillow charges about 0.2 ms for a call to draw text,
and four labels with a shadow each cost more than the JPEG encode of the frame underneath them, so
characters are rendered once and pasted after that.
"""

from __future__ import annotations

import dataclasses
import functools

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .geometry import orient_point  # noqa: F401 - re-exported for the package facade
from .palettes import PALETTE_STEPS
from .temperature import FrameStats, format_temperature

# The readout is burned into the picture rather than drawn over it by a page, because the surface
# that matters is the Fluidd camera tile, and that is a plain <img> with nowhere to hang an
# annotation. The control page and anything else get the same picture for free, and /stats carries
# the same numbers for a client that would rather draw its own.
#
# It is drawn on the encoded image, after any upscale, so the text has real pixels to land on. At
# the sensor's own 120 pixels down the short side, a legible label is nine pixels tall and JPEG
# makes porridge of it, so switching the overlay on raises the encode until the short side reaches
# this. The short side rather than the width, because a rotated camera is 120 across and 160 down,
# and measuring the wrong edge scales that one to three times the pixels for no more legibility.
# It buys back part of the resampling cost F-27 removed, which is why it follows the overlay
# rather than applying to everyone.
OVERLAY_MIN_ENCODE_EDGE = 240


OVERLAY_MARGIN_FRACTION = 0.03


OVERLAY_FONT_HEIGHT_FRACTION = 0.06


OVERLAY_MIN_FONT_PIXELS = 9


COLORBAR_WIDTH_FRACTION = 0.05


COLORBAR_MIN_WIDTH_PIXELS = 8


COLORBAR_HEIGHT_FRACTION = 0.55


RETICLE_ARM_FRACTION = 0.04


HOTSPOT_ARM_FRACTION = 0.03


# The widest label the bar can carry, used to reserve a column for it. Fahrenheit and a minus sign
# are the worst case, and reserving for the worst case is what keeps a marker's number off it.
WIDEST_TEMPERATURE_LABEL = "-888.8F"


OVERLAY_TEXT_RGB = (255, 255, 255)


OVERLAY_SHADOW_RGB = (0, 0, 0)


HOTSPOT_RGB = (255, 90, 90)
COLDSPOT_RGB = (120, 185, 255)


RETICLE_RGB = (235, 235, 235)


@dataclasses.dataclass(frozen=True)
class OverlayStyle:
    """The geometry the overlay draws to, derived once from the picture it is going onto.

    The colorbar's box is here rather than in the function that draws it, because every other label
    needs to know where it is in order to stay off it.
    """

    size: tuple[int, int]
    pixel_height: int
    line_height: int
    margin: int
    bar_box: tuple[int, int, int, int]
    content_right: int


@dataclasses.dataclass(eq=False)
class Overlay:
    """Everything the overlay needs that is not the picture itself."""

    palette: np.ndarray
    stats: FrameStats
    units: str
    colorbar: bool = True
    reticle: bool = True
    hotspot: bool = True
    coldspot: bool = True


@functools.lru_cache(maxsize=8)
def overlay_font(pixel_height: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A font at the size the picture can carry, cached because this runs at frame rate.

    Pillow's built-in face scales only when Pillow was built with FreeType, which the wheels are
    but a source build need not be. Falling back to the fixed bitmap face keeps the overlay ugly
    rather than absent on a printer whose Pillow came from somewhere unexpected.
    """

    try:
        return ImageFont.load_default(size=pixel_height)
    except (AttributeError, OSError, ImportError):
        return ImageFont.load_default()


def overlay_style(size: tuple[int, int], colorbar: bool = True) -> OverlayStyle:
    width, height = size
    pixel_height = max(int(height * OVERLAY_FONT_HEIGHT_FRACTION), OVERLAY_MIN_FONT_PIXELS)
    margin = max(int(width * OVERLAY_MARGIN_FRACTION), 2)
    bar_width = max(int(width * COLORBAR_WIDTH_FRACTION), COLORBAR_MIN_WIDTH_PIXELS)
    bar_height = max(int(height * COLORBAR_HEIGHT_FRACTION), 1)
    bar_left = width - margin - bar_width
    # Reserved against the bar's own labels rather than against the bar. They are right-aligned to
    # the margin and are several times wider than it, which is how a hotspot in the bottom corner
    # still landed on top of the low label after the first attempt at this in 0.7.1.
    # Nothing to stay clear of when the ruler is off, so the markers get the whole width back
    # rather than flipping their labels away from an empty column.
    reserved = (
        max(bar_width, int(label_width(WIDEST_TEMPERATURE_LABEL, pixel_height)) + 1)
        if colorbar
        else 0
    )
    return OverlayStyle(
        size=size,
        pixel_height=pixel_height,
        line_height=pixel_height + 2,
        margin=margin,
        bar_box=(bar_left, (height - bar_height) // 2, bar_width, bar_height),
        content_right=width - margin - reserved,
    )


@functools.lru_cache(maxsize=512)
def glyph_advance(character: str, pixel_height: int) -> float:
    return float(overlay_font(pixel_height).getlength(character))


def label_width(text: str, pixel_height: int) -> float:
    return sum(glyph_advance(character, pixel_height) for character in text)


@functools.lru_cache(maxsize=256)
def glyph_tile(character: str, pixel_height: int, colour: tuple[int, int, int]) -> Image.Image:
    """One character, rendered once, with its shadow already underneath it.

    Pillow charges about 0.2 ms for a call to draw text, and a shadow doubles the calls, so four
    labels a frame cost more than the JPEG encode of the frame they sit on: the readout would have
    been more expensive than the picture. There are only ten digits and a couple of letters, so
    each is drawn once into a transparent tile and pasted from then on, which measured about nine
    times cheaper per label than a single plain draw.

    A shadow rather than a stroke, because Pillow's stroke costs four times a plain draw and this
    only has to separate the text from whatever colour the palette has put behind it.
    """

    font = overlay_font(pixel_height)
    advance = glyph_advance(character, pixel_height)
    tile = Image.new("RGBA", (max(int(advance) + 2, 2), pixel_height * 2 + 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.text((1, 1), character, font=font, fill=(*OVERLAY_SHADOW_RGB, 255))
    draw.text((0, 0), character, font=font, fill=(*colour, 255))
    return tile


def clamp_label(
    position: tuple[float, float],
    text_size: tuple[float, float],
    bounds: tuple[int, int],
) -> tuple[int, int]:
    """Keep a label inside the picture, so a marker near an edge does not lose its number."""

    x = min(max(position[0], 0.0), max(bounds[0] - text_size[0], 0.0))
    y = min(max(position[1], 0.0), max(bounds[1] - text_size[1], 0.0))
    return (int(x), int(y))


def draw_label(
    image: Image.Image,
    position: tuple[float, float],
    text: str,
    style: OverlayStyle,
    colour: tuple[int, int, int] = OVERLAY_TEXT_RGB,
) -> None:
    """Paste a string one cached character at a time, clamped to stay inside the picture."""

    left, top = clamp_label(
        position, (label_width(text, style.pixel_height), style.line_height), image.size
    )
    offset = float(left)
    for character in text:
        tile = glyph_tile(character, style.pixel_height, colour)
        image.paste(tile, (int(offset), top), tile)
        offset += glyph_advance(character, style.pixel_height)


def marker_label_position(
    anchor: tuple[int, int], arm: int, text: str, style: OverlayStyle
) -> tuple[float, int]:
    """Put a marker's number beside it, on whichever side has room, and never over the colorbar.

    Right of the marker by default, because that reads first. On hardware the hotspot landed in the
    bottom right corner and its label was clamped back into the picture straight on top of the
    colorbar's low label, so a marker near the bar now labels itself on its left instead. Clear of
    the arm rather than just past its tip: a number touching the cross reads as part of it, and the
    hotspot usually sits on the brightest part of the picture already.
    """

    anchor_x, top = anchor
    width = label_width(text, style.pixel_height)
    right_of = anchor_x + arm + style.margin
    if right_of + width <= style.content_right:
        return (right_of, top)
    # Left of the marker, and never further right than the reserved column, because the marker
    # itself can be inside that column: flipping alone still left the label overlapping in that
    # case, which is the shape of the collision seen on hardware twice now.
    left_of = min(anchor_x - arm - style.margin - width, style.content_right - width)
    return (max(left_of, 0.0), top)


def bar_position(value: float, low: float, high: float, height: int) -> tuple[int, int]:
    """Which row of the bar a temperature sits on, and which end it is past if it is past one.

    Returns the row and one of -1, 0, 1 for below the bar, on it, and above it.
    """

    span = high - low
    if span <= 0.0:
        return (height // 2, 0)
    fraction = (value - low) / span
    if fraction > 1.0:
        return (0, 1)
    if fraction < 0.0:
        return (height - 1, -1)
    return (int(round((1.0 - fraction) * (height - 1))), 0)


def mark_bar(
    image: Image.Image, row: int, beyond: int, style: OverlayStyle, colour: tuple[int, int, int]
) -> None:
    """Show where the hottest pixel falls on the scale, or that it is off the end of it.

    Without this the bar and the hotspot marker read as contradicting each other, and on hardware
    they did: a bar labelled 29.2 at the top beside a marker reading 35.8. Both are right. The bar
    is labelled with the range the palette covers, which is a percentile of the scene rather than
    its extremes, because otherwise one glint or one dead pixel washes the whole picture out. A
    hotter pixel than that is drawn in the top colour and is genuinely off the top of the scale.

    A tick says where. A triangle at the end of the bar, drawn inside it so it cannot collide with
    the label above, says past here.
    """

    left, top, width, height = style.bar_box
    draw = ImageDraw.Draw(image)
    if beyond == 0:
        y = top + row
        draw.line((left, y, left + width - 1, y), fill=colour)
        draw.line((left - style.margin, y, left - 1, y), fill=colour)
        return
    arrow = max(width // 2, 3)
    apex = top + 1 if beyond > 0 else top + height - 2
    base = apex + arrow if beyond > 0 else apex - arrow
    middle = left + width // 2
    draw.polygon(
        [(middle, apex), (left + 1, base), (left + width - 2, base)], fill=colour
    )


def draw_colorbar(image: Image.Image, overlay: Overlay, style: OverlayStyle) -> None:
    """The palette down the right edge, labelled with the range it currently spans."""

    width = image.size[0]
    left, top, bar_width, bar_height = style.bar_box
    # Reversed, so the hot end of the palette is at the top where a reader expects to find it.
    ramp = overlay.palette[np.arange(PALETTE_STEPS - 1, -1, -1)].reshape(PALETTE_STEPS, 1, 3)
    bar = Image.fromarray(ramp.astype(np.uint8), mode="RGB")
    image.paste(bar.resize((bar_width, bar_height), Image.Resampling.NEAREST), (left, top))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (left, top, left + bar_width - 1, top + bar_height - 1), outline=OVERLAY_SHADOW_RGB
    )
    # A tick per extreme that is being marked on the picture, so the bar and the markers always
    # tell the same story. Switching a marker off takes its tick with it.
    for shown, celsius, colour in (
        (overlay.hotspot, overlay.stats.maximum_celsius, HOTSPOT_RGB),
        (overlay.coldspot, overlay.stats.minimum_celsius, COLDSPOT_RGB),
    ):
        if not shown:
            continue
        row, beyond = bar_position(
            celsius,
            overlay.stats.range_low_celsius,
            overlay.stats.range_high_celsius,
            bar_height,
        )
        mark_bar(image, row, beyond, style, colour)
    high = format_temperature(overlay.stats.range_high_celsius, overlay.units)
    low = format_temperature(overlay.stats.range_low_celsius, overlay.units)
    right = width - style.margin
    draw_label(image, (right - label_width(high, style.pixel_height), top - style.line_height),
               high, style)
    draw_label(image, (right - label_width(low, style.pixel_height), top + bar_height + 1),
               low, style)


@dataclasses.dataclass(frozen=True)
class Marker:
    """One pixel worth pointing at: where it is in the sensor frame, how hot, and in what colour."""

    spot: tuple[int, int]
    celsius: float
    colour: tuple[int, int, int]
    arm_fraction: float = HOTSPOT_ARM_FRACTION


# Internal aliases, underscored to match `_Settings` in settings.py: a label's position is its top
# left corner, and its rectangle is that corner and the opposite one.
_Position = tuple[float, float]
_Rectangle = tuple[float, float, float, float]


def overlapping(one: _Rectangle, other: _Rectangle) -> bool:
    """Whether two label rectangles share any pixel."""

    return bool(
        one[0] < other[2] and other[0] < one[2] and one[1] < other[3] and other[1] < one[3]
    )


def label_candidates(
    anchor: tuple[int, int], arm: int, text: str, style: OverlayStyle
) -> list[_Position]:
    """Places to try putting a marker's number, best first.

    Beside it reads best, which is why that comes first and why it is what a lone marker gets. The
    rest are for when markers are close together: on hardware the centre crosshair and a hotspot a
    few pixels away produced "21.2" and "70.9" written across each other, unreadable and wrong
    looking, and with three markers that is the normal case rather than the unlucky one.
    """

    x, y = anchor
    width = label_width(text, style.pixel_height)
    beside = marker_label_position((x, y - style.line_height), arm, text, style)
    return [
        beside,
        (beside[0], y + arm),
        (x - width / 2, y - arm - style.line_height - 2),
        (x - width / 2, y + arm + 2),
        (beside[0], y - arm - 2 * style.line_height),
        (beside[0], y + arm + style.line_height),
    ]


def place_label(
    anchor: tuple[int, int], arm: int, text: str, style: OverlayStyle, placed: list[_Rectangle]
) -> _Position:
    """The first candidate that is inside the picture and clear of the labels already drawn.

    Falls back to the first candidate when none is clear, because a label in a crowded spot still
    beats no label: the marker itself says where, and the number is worth reading even if it is
    tight.
    """

    width = label_width(text, style.pixel_height)
    for candidate in label_candidates(anchor, arm, text, style):
        left, top = candidate
        rect = (left, top, left + width, top + style.line_height)
        inside = (
            left >= 0
            and rect[2] <= style.content_right
            and top >= 0
            and rect[3] <= style.size[1]
        )
        if inside and not any(overlapping(rect, other) for other in placed):
            return candidate
    return label_candidates(anchor, arm, text, style)[0]


def draw_marker(
    image: Image.Image,
    marker: Marker,
    overlay: Overlay,
    style: OverlayStyle,
    placed: list[_Rectangle],
) -> None:
    """A cross on a pixel with its temperature beside it.

    One function for all three markers rather than one each. They differ in which pixel, which
    temperature and which colour, and nothing else; copies would drift the moment the placement
    rule changed, and that rule has now changed three times.

    `placed` accumulates the label rectangles already drawn on this frame, so each marker can avoid
    the ones before it.
    """

    scale = image.size[0] / overlay.stats.width
    x = int((marker.spot[0] + 0.5) * scale)
    y = int((marker.spot[1] + 0.5) * scale)
    arm = max(int(image.size[0] * marker.arm_fraction), 3)
    draw = ImageDraw.Draw(image)
    draw.line((x - arm, y, x + arm, y), fill=marker.colour)
    draw.line((x, y - arm, x, y + arm), fill=marker.colour)
    label = format_temperature(marker.celsius, overlay.units)
    position = place_label((x, y), arm, label, style, placed)
    placed.append((
        position[0], position[1],
        position[0] + label_width(label, style.pixel_height), position[1] + style.line_height,
    ))
    draw_label(image, position, label, style, marker.colour)


def markers_for(overlay: Overlay) -> list[Marker]:
    """The markers this overlay wants, in the order they get first refusal on a label position.

    The extremes come before the centre because they are the ones being looked for. The centre is a
    fixed point of reference and the one that can most afford to be nudged.
    """

    stats = overlay.stats
    wanted = [
        (overlay.hotspot, Marker(stats.hotspot, stats.maximum_celsius, HOTSPOT_RGB)),
        (overlay.coldspot, Marker(stats.coldspot, stats.minimum_celsius, COLDSPOT_RGB)),
        (
            overlay.reticle,
            Marker(
                (stats.width // 2, stats.height // 2),
                stats.centre_celsius,
                RETICLE_RGB,
                RETICLE_ARM_FRACTION,
            ),
        ),
    ]
    return [marker for shown, marker in wanted if shown]


def draw_overlay(image: Image.Image, overlay: Overlay) -> None:
    style = overlay_style(image.size, overlay.colorbar)
    if overlay.colorbar:
        draw_colorbar(image, overlay, style)
    # Every marker in one pass, so each can see where the ones before it put their labels.
    placed: list[_Rectangle] = []
    for marker in markers_for(overlay):
        draw_marker(image, marker, overlay, style, placed)


def encode_upscale(frame_size: tuple[int, int], upscale: int, readout_enabled: bool) -> int:
    """The upscale actually used, which the overlay can raise but never lower.

    Text is the only part of the picture that does not survive being drawn at the sensor's own size
    and scaled up by a browser, so the overlay pays for its own resolution and nothing else does.
    """

    if not readout_enabled:
        return upscale
    return max(upscale, -(-OVERLAY_MIN_ENCODE_EDGE // min(frame_size)))
