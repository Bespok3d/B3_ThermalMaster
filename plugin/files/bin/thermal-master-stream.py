#!/usr/bin/env python3
"""Capture the Thermal Master P1 over libusb and serve it as MJPEG.

The USB protocol lives in the vendored p3_camera driver; this module only colormaps the
16-bit thermal frame and serves /stream.mjpg + /snapshot.jpg over HTTP.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import functools
import io
import json
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# The vendored USB protocol driver sits beside this file rather than in the plugin's environment,
# because it is an upstream source file and not a package (see VENDORING.md). numpy, Pillow and
# pyusb come from the environment, so this goes on the END of sys.path: at the front, anything left
# unpacked in the vendor directory wins against the installed package of the same name, which on
# the printer's own architecture swaps a dependency silently and anywhere else fails to load.
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
sys.path.append(str(VENDOR_DIR))

import numpy as np  # noqa: E402
import usb.core  # noqa: E402
from p3_camera import (  # noqa: E402
    COMMANDS,
    VID,
    EnvParams,
    FrameMarkerMismatchError,
    GainMode,
    Model,
    P3Camera,
    get_model_config,
    raw_to_celsius_corrected,
)
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8082
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
PALETTE_STEPS = 256

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
RETICLE_RGB = (235, 235, 235)

CELSIUS = "celsius"
FAHRENHEIT = "fahrenheit"
VALID_UNITS = (CELSIUS, FAHRENHEIT)
DEFAULT_UNITS = CELSIUS

# Gain is a device command; emissivity is arithmetic done here. They sit together on the control
# page because to a person they are both "how the camera reads", but only one of them travels over
# USB, and that difference decides which thread each one runs on.
#
# The driver's enum has an AUTO alongside these two, and its own comment says the protocol does not
# implement it: set_gain_mode records the mode and sends nothing. So it is not offered.
GAIN_HIGH = "high"
GAIN_LOW = "low"
VALID_GAINS = (GAIN_HIGH, GAIN_LOW)
DEFAULT_GAIN = GAIN_HIGH
GAIN_MODES = {GAIN_HIGH: GainMode.HIGH, GAIN_LOW: GainMode.LOW}
GAIN_DESCRIPTIONS = {
    GAIN_HIGH: "High sensitivity, -20 to 150 C",
    GAIN_LOW: "Wide range, 0 to 550 C",
}

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
    (0.95, "0.95 matte plastic, PLA, painted"),
    (0.90, "0.90 rough surfaces, ceramic"),
    (0.85, "0.85 glossy plastic, PETG"),
    (0.60, "0.60 oxidised steel"),
    (0.30, "0.30 anodised aluminium"),
    (0.10, "0.10 bare shiny metal"),
)

SHUTTER_IDLE = "idle"
SHUTTER_PENDING = "pending"
SHUTTER_DONE = "done"
SHUTTER_FAILED = "failed"
SHUTTER_ACTION = "shutter"

# How many reads in a row may come back empty before the camera counts as stalled rather than slow.
# At the idle sleep below this is a fifth of a second of nothing, where a healthy camera delivers
# twenty-five frames a second.
MAX_CONSECUTIVE_FRAME_FAILURES = 20
INITIAL_RECONNECT_DELAY_SECONDS = 3.0
MAX_RECONNECT_DELAY_SECONDS = 60.0
RECONNECT_BACKOFF_FACTOR = 2.0
SHUTDOWN_GRACE_SECONDS = 5.0

# Both cameras speak the same protocol and differ only in sensor size, so the plugin drives
# whichever is plugged in rather than being told. The product IDs come from the driver's own model
# configs, so there is one place that knows them and it is not this file.
SUPPORTED_MODELS = (Model.P1, Model.P3)
FRAME_IDLE_SLEEP_SECONDS = 0.01
STREAM_WAIT_SECONDS = 1.0
MJPEG_BOUNDARY = "frame"

# Request path to handler name. Clients we do not control decorate these URLs: Fluidd and Mainsail
# add a cache-busting parameter to a snapshot, mjpg-streamer clients add an action, so the query
# string is discarded before the lookup. Exact match on what is left, never a prefix.
ROUTES = {
    "/snapshot.jpg": "serve_snapshot",
    "/stream.mjpg": "serve_stream",
    "/settings": "serve_settings",
    "/stats": "serve_stats",
    "/": "serve_control_page",
}
POST_ROUTES = {"/settings": "apply_settings"}

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
    return np.clip(sharpened, 0.0, 255.0).astype(np.uint8)


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


@dataclasses.dataclass(frozen=True)
class OverlayStyle:
    """The geometry the overlay draws to, derived once from the picture it is going onto.

    The colorbar's box is here rather than in the function that draws it, because every other label
    needs to know where it is in order to stay off it.
    """

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


@functools.lru_cache(maxsize=8)
def overlay_font(pixel_height: int) -> object:
    """A font at the size the picture can carry, cached because this runs at frame rate.

    Pillow's built-in face scales only when Pillow was built with FreeType, which the wheels are
    but a source build need not be. Falling back to the fixed bitmap face keeps the overlay ugly
    rather than absent on a printer whose Pillow came from somewhere unexpected.
    """

    try:
        return ImageFont.load_default(size=pixel_height)
    except (AttributeError, OSError, ImportError):
        return ImageFont.load_default()


def overlay_style(size: tuple[int, int]) -> OverlayStyle:
    width, height = size
    pixel_height = max(int(height * OVERLAY_FONT_HEIGHT_FRACTION), OVERLAY_MIN_FONT_PIXELS)
    margin = max(int(width * OVERLAY_MARGIN_FRACTION), 2)
    bar_width = max(int(width * COLORBAR_WIDTH_FRACTION), COLORBAR_MIN_WIDTH_PIXELS)
    bar_height = max(int(height * COLORBAR_HEIGHT_FRACTION), 1)
    bar_left = width - margin - bar_width
    # Reserved against the bar's own labels rather than against the bar. They are right-aligned to
    # the margin and are several times wider than it, which is how a hotspot in the bottom corner
    # still landed on top of the low label after the first attempt at this in 0.7.1.
    reserved = max(bar_width, int(label_width(WIDEST_TEMPERATURE_LABEL, pixel_height)) + 1)
    return OverlayStyle(
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
def glyph_tile(character: str, pixel_height: int, colour: tuple[int, int, int]):
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


def clamp_label(position: tuple[float, float], text_size: tuple[float, float], bounds) -> tuple:
    """Keep a label inside the picture, so a marker near an edge does not lose its number."""

    x = min(max(position[0], 0.0), max(bounds[0] - text_size[0], 0.0))
    y = min(max(position[1], 0.0), max(bounds[1] - text_size[1], 0.0))
    return (int(x), int(y))


def draw_label(image, position, text: str, style: OverlayStyle, colour=OVERLAY_TEXT_RGB) -> None:
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


def mark_bar(image, row: int, beyond: int, style: OverlayStyle) -> None:
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
        draw.line((left, y, left + width - 1, y), fill=HOTSPOT_RGB)
        draw.line((left - style.margin, y, left - 1, y), fill=HOTSPOT_RGB)
        return
    arrow = max(width // 2, 3)
    apex = top + 1 if beyond > 0 else top + height - 2
    base = apex + arrow if beyond > 0 else apex - arrow
    middle = left + width // 2
    draw.polygon(
        [(middle, apex), (left + 1, base), (left + width - 2, base)], fill=HOTSPOT_RGB
    )


def draw_colorbar(image, overlay: Overlay, style: OverlayStyle) -> None:
    """The palette down the right edge, labelled with the range it currently spans."""

    width = image.size[0]
    left, top, bar_width, bar_height = style.bar_box
    # Reversed, so the hot end of the palette is at the top where a reader expects to find it.
    ramp = overlay.palette[np.arange(PALETTE_STEPS - 1, -1, -1)].reshape(PALETTE_STEPS, 1, 3)
    bar = Image.fromarray(ramp.astype(np.uint8), mode="RGB")
    image.paste(bar.resize((bar_width, bar_height), Image.NEAREST), (left, top))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (left, top, left + bar_width - 1, top + bar_height - 1), outline=OVERLAY_SHADOW_RGB
    )
    row, beyond = bar_position(
        overlay.stats.maximum_celsius,
        overlay.stats.range_low_celsius,
        overlay.stats.range_high_celsius,
        bar_height,
    )
    mark_bar(image, row, beyond, style)
    high = format_temperature(overlay.stats.range_high_celsius, overlay.units)
    low = format_temperature(overlay.stats.range_low_celsius, overlay.units)
    right = width - style.margin
    draw_label(image, (right - label_width(high, style.pixel_height), top - style.line_height),
               high, style)
    draw_label(image, (right - label_width(low, style.pixel_height), top + bar_height + 1),
               low, style)


def draw_reticle(image, overlay: Overlay, style: OverlayStyle) -> None:
    """A crosshair in the middle, with what the middle reads. The one fixed point of reference."""

    width, height = image.size
    centre_x, centre_y = width // 2, height // 2
    arm = max(int(width * RETICLE_ARM_FRACTION), 3)
    draw = ImageDraw.Draw(image)
    draw.line((centre_x - arm, centre_y, centre_x + arm, centre_y), fill=RETICLE_RGB)
    draw.line((centre_x, centre_y - arm, centre_x, centre_y + arm), fill=RETICLE_RGB)
    label = format_temperature(overlay.stats.centre_celsius, overlay.units)
    draw_label(image, marker_label_position((centre_x, centre_y + 2), arm, label, style),
               label, style)


def draw_hotspot(image, overlay: Overlay, style: OverlayStyle) -> None:
    """Mark the hottest pixel. On a printer that is the whole reason to point a thermal camera."""

    scale = image.size[0] / overlay.stats.width
    x = int((overlay.stats.hotspot[0] + 0.5) * scale)
    y = int((overlay.stats.hotspot[1] + 0.5) * scale)
    arm = max(int(image.size[0] * HOTSPOT_ARM_FRACTION), 3)
    draw = ImageDraw.Draw(image)
    draw.line((x - arm, y, x + arm, y), fill=HOTSPOT_RGB)
    draw.line((x, y - arm, x, y + arm), fill=HOTSPOT_RGB)
    label = format_temperature(overlay.stats.maximum_celsius, overlay.units)
    draw_label(image, marker_label_position((x, y - style.line_height), arm, label, style),
               label, style, HOTSPOT_RGB)


def draw_overlay(image, overlay: Overlay) -> None:
    style = overlay_style(image.size)
    draw_colorbar(image, overlay, style)
    draw_reticle(image, overlay, style)
    draw_hotspot(image, overlay, style)


def encode_upscale(frame_size: tuple[int, int], upscale: int, overlay_enabled: bool) -> int:
    """The upscale actually used, which the overlay can raise but never lower.

    Text is the only part of the picture that does not survive being drawn at the sensor's own size
    and scaled up by a browser, so the overlay pays for its own resolution and nothing else does.
    """

    if not overlay_enabled:
        return upscale
    return max(upscale, -(-OVERLAY_MIN_ENCODE_EDGE // min(frame_size)))


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
    overlay: bool = True
    units: str = DEFAULT_UNITS
    emissivity: float = DEFAULT_EMISSIVITY

    @property
    def mirrors(self) -> tuple[bool, bool]:
        """The two flips as one value, since nothing ever wants only one of them."""

        return (self.flip_horizontal, self.flip_vertical)


@dataclasses.dataclass(frozen=True)
class CameraSettings:
    """What gets sent to the hardware, as opposed to what is done with what comes back.

    Separate from RenderSettings, and with a revision of its own, because these travel over USB and
    so can only be applied by the thread that owns the camera. Emissivity is deliberately not here:
    it never reaches the device, it is arithmetic applied to the numbers on the way out, so it
    belongs with the rendering.
    """

    gain: str = DEFAULT_GAIN


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
        overlay = Overlay(self._palette, stats, settings.units) if settings.overlay else None
        upscale = encode_upscale(
            (image.shape[1], image.shape[0]), settings.upscale, settings.overlay
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
        image = image.resize((width * upscale, height * upscale), Image.BILINEAR)
    # After the resize, so the text is drawn at the size it will be looked at rather than resampled.
    if overlay is not None:
        draw_overlay(image, overlay)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


class LatestFrame:
    def __init__(self) -> None:
        self._jpeg: bytes | None = None
        self._stats: FrameStats | None = None
        self._published_count = 0
        self._updated = threading.Condition()

    @property
    def published_count(self) -> int:
        """How many frames have ever been published: a working session from a failing one."""

        with self._updated:
            return self._published_count

    def publish(self, jpeg: bytes, stats: FrameStats | None = None) -> None:
        with self._updated:
            self._jpeg = jpeg
            self._stats = stats
            self._published_count += 1
            self._updated.notify_all()

    def latest_stats(self) -> FrameStats | None:
        """What the last published frame measured, or None before there was one."""

        with self._updated:
            return self._stats

    def snapshot(self) -> bytes | None:
        with self._updated:
            return self._jpeg

    def wait_next(self, timeout: float) -> bytes | None:
        with self._updated:
            self._updated.wait(timeout)
            return self._jpeg


def fire_shutter(camera: P3Camera) -> None:
    """Fire the calibration shutter, without asking the driver to read the frame that follows.

    The driver has its own `trigger_shutter`, and it cannot be used on a P1. It sends this command
    and then reads back the mistimed frame the camera emits afterwards, reassembling it from two
    segments whose offsets are absolute line counts measured on a P3. `shutter_seg_2` is 800 lines:
    on a 256 wide sensor that is 204,812 bytes and lands inside the 206,872 byte frame buffer, and
    on a 160 wide P1 it is 128,012 bytes into a buffer of 83,224. The read overruns the buffer
    first, which on hardware surfaced as "memoryview assignment: lvalue and rvalue have different
    structures" and no calibration. Reported upstream. Not patched here: the vendored driver is
    pinned and is not ours to edit (VENDORING.md).

    The command itself is model independent, a fixed control transfer and its acknowledgement, and
    those two lines are all a calibration actually needs. The frame afterwards is the part we did
    not want anyway, and the driver's ordinary reader already copes with it: `read_frame` spots an
    end marker arriving before the end of a frame, drops what it has and starts again, so the next
    read or two resynchronises by itself.

    Reaching past the underscore is deliberate and is the smaller of two couplings. The alternative
    is issuing the control transfers here, which would copy the endpoint, the request numbers and
    the timeout out of the driver and leave them to rot when the pin moves.
    """

    camera._send_command(COMMANDS["shutter"])
    camera._read_status()


class DeviceController:
    """Everything that has to be said to the camera, said on the thread that owns it.

    `trigger_shutter` and `set_gain_mode` are not side channels: they write a control transfer and
    then read the same bulk endpoint the frame loop reads. Called from an HTTP handler, one of two
    things happens, and which one is a matter of timing: the command's acknowledgement is consumed
    as pixels, or a frame is consumed as the acknowledgement. Either desynchronises the stream for
    as long as the session lasts. So a request from the network only sets a flag here, and the
    capture thread acts on it between two frames, where nothing else is in flight.

    The gain is a setting and the shutter is an action, and they need different handling.

    A setting is declarative: the store holds what the gain should be, and this compares its
    revision against the last one applied. That makes it idempotent, so it can be checked every
    frame for the cost of an integer comparison, and it makes a reconnect self-healing, because a
    camera that has just been replugged comes up in its own default and `forget_session` is enough
    to have the user's choice re-sent.

    An action has no such resting state, so it is a flag that is cleared as it is taken. Repeated
    presses coalesce into one pending shutter rather than queueing: a person clicking three times
    wants a calibration, not three of them, and each one costs a dropped frame.
    """

    def __init__(self, settings_store: SettingsStore) -> None:
        self._settings_store = settings_store
        self._lock = threading.Lock()
        self._shutter_requested = False
        self._shutter_state = SHUTTER_IDLE
        self._shutter_detail: str | None = None
        self._applied_gain_revision: int | None = None
        self._gain_in_effect: str | None = None

    def request_shutter(self) -> None:
        """Ask for a calibration. Safe from any thread; nothing here touches the camera."""

        with self._lock:
            self._shutter_requested = True
            self._shutter_state = SHUTTER_PENDING

    def forget_session(self) -> None:
        """A camera that has just been opened is in its own default gain, not the chosen one."""

        with self._lock:
            self._applied_gain_revision = None
            self._gain_in_effect = None

    def apply(self, camera: P3Camera) -> None:
        """Called by the capture thread between frames. The only place commands are sent."""

        self._apply_gain(camera)
        self._apply_shutter(camera)

    def _apply_gain(self, camera: P3Camera) -> None:
        revision, settings = self._settings_store.camera_snapshot()
        with self._lock:
            if revision == self._applied_gain_revision:
                return
        camera.set_gain_mode(GAIN_MODES[settings.gain])
        with self._lock:
            self._applied_gain_revision = revision
            self._gain_in_effect = settings.gain

    def _apply_shutter(self, camera: P3Camera) -> None:
        with self._lock:
            if not self._shutter_requested:
                return
            self._shutter_requested = False
        try:
            fire_shutter(camera)
        except Exception as error:  # noqa: BLE001 - recorded for the page, then re-raised
            with self._lock:
                self._shutter_state = SHUTTER_FAILED
                self._shutter_detail = str(error)
            # A control transfer that fails is a camera that has gone away, not a slow frame, so
            # this belongs on the reconnect path rather than being swallowed into a stuck stream.
            raise
        with self._lock:
            self._shutter_state = SHUTTER_DONE
            self._shutter_detail = None

    def status(self) -> dict:
        """What the device is actually doing, as opposed to what it has been asked to do."""

        with self._lock:
            return {
                "gain": self._gain_in_effect,
                "shutter": {"state": self._shutter_state, "detail": self._shutter_detail},
            }


class CameraStalledError(Exception):
    """The camera is still connected but has stopped producing usable frames."""


class CameraNotFoundError(Exception):
    """No supported thermal camera is on the USB bus."""


def detect_camera_model() -> Model | None:
    """Which supported camera is plugged in, or None if none is.

    Probing beats configuring: the P1 and the P3 differ only in sensor size, the driver already
    knows both, and a user who has to pick from a list is a user who can pick wrong and get a plugin
    that fails in a way the printer cannot explain.
    """

    for model in SUPPORTED_MODELS:
        product_id = get_model_config(model).pid
        if usb.core.find(idVendor=VID, idProduct=product_id) is not None:
            return model
    return None


def next_thermal_frame(camera: P3Camera) -> np.ndarray | None:
    """One thermal frame, or None when the camera returned nothing usable.

    A marker mismatch is a glitched frame, not a dead camera: the driver raises on it, and letting
    that reach the reconnect path costs seconds of dead video for a fault the next read clears.
    """

    try:
        _, thermal_raw = camera.read_frame_both()
    except FrameMarkerMismatchError:
        return None
    return thermal_raw


def stream_frames(
    camera: P3Camera,
    frame_store: LatestFrame,
    renderer_source: RendererSource,
    shutdown: threading.Event,
    device: DeviceController | None = None,
) -> None:
    """Publish frames until the camera stalls or a shutdown is asked for.

    Empty reads are tolerated in ones and twos, because that is what a glitch looks like, and
    raised as a stall once there have been enough in a row. The distinction matters: the driver
    returns nothing at all when streaming has silently stopped, and the previous version of this
    loop treated that as a slow frame, so the stream froze on its last good image with the
    process still reporting itself healthy.
    """

    consecutive_failures = 0
    while not shutdown.is_set():
        # Before the read rather than after it, so the first thing a fresh session does is put the
        # camera into the gain the user chose, ahead of any frame being published from the default.
        if device is not None:
            device.apply(camera)
        thermal_raw = next_thermal_frame(camera)
        if thermal_raw is not None:
            consecutive_failures = 0
            rendered = renderer_source.current().render_frame(thermal_raw)
            frame_store.publish(rendered.jpeg, rendered.stats)
            continue
        consecutive_failures += 1
        if consecutive_failures >= MAX_CONSECUTIVE_FRAME_FAILURES:
            raise CameraStalledError(f"no usable frame in {consecutive_failures} reads")
        time.sleep(FRAME_IDLE_SLEEP_SECONDS)


def release_camera(camera: P3Camera) -> None:
    """Hand the USB interface back.

    stop_streaming resets the interface's alternate setting and disconnect releases the claim.
    Skipping either leaves the device claimed, and the next start cannot open it: from the outside
    that looks like a camera that needs unplugging. Both are best-effort, because this runs while
    unwinding from a failure and a camera that has already been pulled raises from both.
    """

    with contextlib.suppress(Exception):
        camera.stop_streaming()
    with contextlib.suppress(Exception):
        camera.disconnect()


def run_capture_session(
    frame_store: LatestFrame,
    renderer_source: RendererSource,
    shutdown: threading.Event,
    device: DeviceController | None = None,
) -> None:
    model = detect_camera_model()
    if model is None:
        raise CameraNotFoundError("no Thermal Master camera on the USB bus")
    camera = P3Camera(config=get_model_config(model))
    camera.connect()
    camera.init()
    camera.start_streaming()
    if device is not None:
        device.forget_session()
    try:
        stream_frames(camera, frame_store, renderer_source, shutdown, device)
    finally:
        release_camera(camera)


def next_reconnect_delay(current_delay: float) -> float:
    """Back off after a failed session, up to a ceiling.

    A camera that is simply not plugged in fails instantly and forever, and a fixed retry turns that
    into a log line every three seconds until someone notices.
    """

    return min(current_delay * RECONNECT_BACKOFF_FACTOR, MAX_RECONNECT_DELAY_SECONDS)


def capture_loop(
    frame_store: LatestFrame,
    renderer_source: RendererSource,
    shutdown: threading.Event,
    device: DeviceController | None = None,
) -> None:
    """Keep a capture session running: an unplug or a read error reconnects, it never exits."""

    reconnect_delay = INITIAL_RECONNECT_DELAY_SECONDS
    while not shutdown.is_set():
        frames_before_session = frame_store.published_count
        try:
            run_capture_session(frame_store, renderer_source, shutdown, device)
            return
        except Exception as error:  # noqa: BLE001
            print(f"thermal-master: capture error: {error}", file=sys.stderr, flush=True)
        if frame_store.published_count > frames_before_session:
            reconnect_delay = INITIAL_RECONNECT_DELAY_SECONDS
        shutdown.wait(reconnect_delay)
        reconnect_delay = next_reconnect_delay(reconnect_delay)


def resolve_route(request_path: str) -> str | None:
    """The handler a raw request path asks for, or None if nothing serves it."""

    return ROUTES.get(urlparse(request_path).path)


def restored(current, saved: dict):
    """Rebuild a settings dataclass from a saved file, ignoring keys it does not have.

    One function for both sets, because the file is flat: a key belongs to whichever dataclass
    declares it, and a key from an older or newer version belongs to neither and is dropped rather
    than raising on the way to a camera that then never starts.
    """

    known = {field.name for field in dataclasses.fields(current)}
    return dataclasses.replace(
        current, **{key: value for key, value in saved.items() if key in known}
    )


class SettingsStore:
    """The live settings, and the file they survive a restart in.

    Held behind a lock because HTTP handler threads write and the capture thread reads. Handed out
    as a whole snapshot rather than field by field, so a frame is never rendered from half of one
    change and half of another. `revision` is what lets the capture loop notice a change without
    polling every field.
    """

    def __init__(
        self,
        palette_name: str,
        settings: RenderSettings,
        state_file: Path | None,
        camera: CameraSettings = CameraSettings(),
    ) -> None:
        self._palette_name = palette_name
        self._settings = settings
        self._camera = camera
        self._state_file = state_file
        self._revision = 0
        self._camera_revision = 0
        self._lock = threading.Lock()
        self._load()

    def snapshot(self) -> tuple[int, str, RenderSettings]:
        with self._lock:
            return (self._revision, self._palette_name, self._settings)

    def camera_snapshot(self) -> tuple[int, CameraSettings]:
        """Counted separately, so changing a palette does not re-send a gain command over USB."""

        with self._lock:
            return (self._camera_revision, self._camera)

    def update(self, palette_name: str, settings: RenderSettings) -> None:
        with self._lock:
            self._palette_name = palette_name
            self._settings = settings
            self._revision += 1
        self._save()

    def update_camera(self, camera: CameraSettings) -> None:
        with self._lock:
            self._camera = camera
            self._camera_revision += 1
        self._save()

    def as_dict(self) -> dict:
        _, palette_name, settings = self.snapshot()
        _, camera = self.camera_snapshot()
        return {
            "palette": palette_name,
            **dataclasses.asdict(settings),
            **dataclasses.asdict(camera),
        }

    def _load(self) -> None:
        """Restore what was saved. A missing or unreadable file just means the defaults stand."""

        if self._state_file is None or not self._state_file.is_file():
            return
        try:
            saved = json.loads(self._state_file.read_text())
        except (OSError, ValueError) as error:
            print(f"thermal-master: ignoring unreadable settings: {error}", file=sys.stderr)
            return
        self._palette_name = saved.pop("palette", self._palette_name)
        self._settings = restored(self._settings, saved)
        self._camera = restored(self._camera, saved)

    def _save(self) -> None:
        if self._state_file is None:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(self.as_dict(), indent=2))
        except OSError as error:
            print(f"thermal-master: could not save settings: {error}", file=sys.stderr, flush=True)


def settings_from_form(
    form: dict, palettes: dict, current: RenderSettings
) -> tuple[str | None, RenderSettings]:
    """Read a posted form into a palette name and settings, ignoring anything unrecognised.

    Everything is validated against what actually exists: an unknown palette or a rotation that is
    not a quarter turn leaves that field as it was, rather than reaching the renderer and failing
    one frame later where the cause is invisible.
    """

    palette_name = form.get("palette", [None])[0]
    posted_rotation = form.get("rotation", [""])[0]
    rotation = int(posted_rotation) if posted_rotation.isdigit() else -1
    posted_units = form.get("units", [""])[0]
    return (
        palette_name if palette_name in palettes else None,
        dataclasses.replace(
            current,
            rotation=rotation if rotation in VALID_ROTATIONS else current.rotation,
            flip_horizontal="flip_horizontal" in form,
            flip_vertical="flip_vertical" in form,
            overlay="overlay" in form,
            units=posted_units if posted_units in VALID_UNITS else current.units,
            emissivity=posted_emissivity(form, current.emissivity),
        ),
    )


def posted_emissivity(form: dict, current: float) -> float:
    """Read an emissivity, clamped rather than refused.

    The page offers a list, but the settings file is a supported thing to hand-edit, so anything
    parseable is accepted and pulled into range. Zero would divide by zero in the correction, which
    is why the floor is not zero.
    """

    try:
        posted = float(form.get("emissivity", [""])[0])
    except ValueError:
        return current
    return min(max(posted, MIN_EMISSIVITY), MAX_EMISSIVITY)


def camera_settings_from_form(form: dict, current: CameraSettings) -> CameraSettings:
    """The half of the form that becomes a USB command rather than a rendering choice."""

    gain = form.get("gain", [""])[0]
    return dataclasses.replace(current, gain=gain if gain in VALID_GAINS else current.gain)


CONTROL_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thermal Master</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; padding: 1rem; background: #14161a; color: #e8e8ea;
         font: 15px/1.5 system-ui, sans-serif; }}
  main {{ max-width: 34rem; margin: 0 auto; }}
  img {{ width: 100%; border-radius: 6px; background: #000; display: block; }}
  fieldset {{ border: 1px solid #33373f; border-radius: 6px; margin: 1rem 0 0; padding: 0.75rem; }}
  legend {{ padding: 0 0.4rem; color: #9aa0aa; font-size: 0.85rem; }}
  label {{ display: flex; align-items: center; gap: 0.6rem; margin: 0.4rem 0; }}
  label span {{ min-width: 7rem; }}
  select {{ flex: 1; padding: 0.35rem; background: #1d2026; color: inherit;
            border: 1px solid #33373f; border-radius: 4px; }}
  button {{ margin-top: 0.8rem; margin-right: 0.5rem; padding: 0.5rem 1.1rem; border: 0;
            border-radius: 4px; background: #d8752a; color: #14161a; font-weight: 600;
            cursor: pointer; }}
  .status {{ margin: 0.6rem 0 0; font-size: 0.8rem; }}
  p {{ color: #9aa0aa; font-size: 0.85rem; }}
</style>
</head>
<body>
<main>
  <img src="/thermal/stream.mjpg" alt="Live thermal view">
  <form method="post" action="/thermal/settings">
    <fieldset>
      <legend>Image</legend>
      <label><span>Palette</span><select name="palette">{palette_options}</select></label>
      <label><span>Rotate</span><select name="rotation">{rotation_options}</select></label>
      <label><input type="checkbox" name="flip_horizontal"{flip_horizontal}>
             Mirror left to right</label>
      <label><input type="checkbox" name="flip_vertical"{flip_vertical}>
             Mirror top to bottom</label>
      <button type="submit">Apply</button>
    </fieldset>
    <fieldset>
      <legend>Readout</legend>
      <label><input type="checkbox" name="overlay"{overlay}>
             Show the colorbar, centre reading and hotspot</label>
      <label><span>Units</span><select name="units">{unit_options}</select></label>
      <label><span>Emissivity</span>
             <select name="emissivity">{emissivity_options}</select></label>
      <button type="submit">Apply</button>
    </fieldset>
    <fieldset>
      <legend>Camera</legend>
      <label><span>Gain</span><select name="gain">{gain_options}</select></label>
      <button type="submit">Apply</button>
      <button type="submit" name="action" value="shutter">Calibrate now</button>
      <p class="status">{device_status}</p>
    </fieldset>
  </form>
  <p>Changes take effect immediately and survive a restart. The picture takes about a second to
     settle afterwards, while the auto-ranging finds the scene again.</p>
  <p>The readout is drawn into the picture, so it shows in the printer's camera tile too. Turning it
     on encodes at a larger size, so the text stays legible. The same numbers, plus the frame
     average and the coldest pixel, are at <a href="/thermal/stats">/thermal/stats</a>.</p>
  <p>Emissivity is how much of what a surface radiates is its own heat rather than a reflection of
     the room, so a shiny surface reads cold until you tell the plugin it is shiny. It changes the
     numbers only, never the picture.</p>
  <p>Calibration closes the camera's internal shutter for a moment and re-levels the sensor against
     it. The camera does this by itself about every ninety seconds; the button is for when the
     picture has drifted and you would rather not wait. It costs one frame.</p>
</main>
</body>
</html>
"""


def describe_device(status: dict | None) -> str:
    """One line on what the camera is doing, since the page has no JavaScript to ask again.

    The form posts and redirects, so the reload after a press is the report: by the time the page
    comes back the capture thread has been round the loop and the shutter has either fired or said
    why it could not.
    """

    if status is None:
        return "Camera state is not available."
    shutter = status.get("shutter", {})
    detail = shutter.get("detail")
    said = {
        SHUTTER_IDLE: "Calibration has not been asked for since this service started.",
        SHUTTER_PENDING: "Calibration requested, waiting for the next frame.",
        SHUTTER_DONE: "Last calibration completed.",
        SHUTTER_FAILED: f"Last calibration failed: {detail}",
    }
    return said.get(shutter.get("state"), "Camera state is not available.")


def render_control_page(
    settings: dict, palette_names: list, device_status: dict | None = None
) -> str:
    """The page itself. Plain form, no JavaScript: it has to work in whatever opens it."""

    def option(value: str, label: str, selected: bool) -> str:
        return f'<option value="{value}"{" selected" if selected else ""}>{label}</option>'

    return CONTROL_PAGE_TEMPLATE.format(
        emissivity_options="".join(
            option(f"{value:.2f}", label, abs(value - settings["emissivity"]) < EMISSIVITY_MATCH)
            for value, label in EMISSIVITY_PRESETS
        ),
        gain_options="".join(
            option(name, GAIN_DESCRIPTIONS[name], name == settings["gain"])
            for name in VALID_GAINS
        ),
        device_status=describe_device(device_status),
        palette_options="".join(
            option(name, name.replace("-", " "), name == settings["palette"])
            for name in palette_names
        ),
        rotation_options="".join(
            option(str(degrees), f"{degrees} degrees", degrees == settings["rotation"])
            for degrees in VALID_ROTATIONS
        ),
        unit_options="".join(
            option(name, name.capitalize(), name == settings["units"]) for name in VALID_UNITS
        ),
        flip_horizontal=" checked" if settings["flip_horizontal"] else "",
        flip_vertical=" checked" if settings["flip_vertical"] else "",
        overlay=" checked" if settings["overlay"] else "",
    )


class RendererSource:
    """Hands out a renderer that matches the current settings, rebuilding it when they change.

    Rebuilding discards the smoothed bounds and the previous frame, so a settings change costs about
    a second of re-settling. That is the right trade: the alternative is mutating a renderer while
    the capture thread is inside it.
    """

    def __init__(self, settings_store: SettingsStore, palettes: dict) -> None:
        self._settings_store = settings_store
        self._palettes = palettes
        self._revision: int | None = None
        self._renderer: ThermalRenderer | None = None

    def current(self) -> ThermalRenderer:
        revision, palette_name, settings = self._settings_store.snapshot()
        if revision != self._revision or self._renderer is None:
            self._revision = revision
            self._renderer = ThermalRenderer(self._palettes[palette_name], settings)
        return self._renderer


class ThermalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        frame_store: LatestFrame,
        settings_store: SettingsStore | None = None,
        palettes: dict | None = None,
        device: DeviceController | None = None,
    ) -> None:
        super().__init__(address, ThermalRequestHandler)
        self.frame_store = frame_store
        self.settings_store = settings_store
        self.palettes = palettes if palettes is not None else build_palettes()
        self.device = device


class ThermalRequestHandler(BaseHTTPRequestHandler):
    @property
    def frames(self) -> LatestFrame:
        return self.server.frame_store  # type: ignore[attr-defined]

    @property
    def settings_store(self) -> SettingsStore | None:
        return self.server.settings_store  # type: ignore[attr-defined]

    @property
    def palettes(self) -> dict:
        return self.server.palettes  # type: ignore[attr-defined]

    @property
    def device(self):
        return self.server.device  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        route = resolve_route(self.path)
        if route is None:
            self.send_error(404)
            return
        getattr(self, route)()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler names it
        route = POST_ROUTES.get(urlparse(self.path).path)
        if route is None:
            self.send_error(404)
            return
        getattr(self, route)()

    def serve_stats(self) -> None:
        """The numbers behind the picture, for anything that would rather draw its own overlay."""

        stats = self.frames.latest_stats()
        if stats is None:
            self.send_error(503, "no frame yet")
            return
        units = DEFAULT_UNITS
        if self.settings_store is not None:
            _, _, settings = self.settings_store.snapshot()
            units = settings.units
        payload = stats.as_dict(units)
        if self.device is not None:
            payload["device"] = self.device.status()
        self.send_json(payload)

    def serve_settings(self) -> None:
        if self.settings_store is None:
            self.send_error(503, "settings are not available")
            return
        self.send_json(self.settings_store.as_dict())

    def apply_settings(self) -> None:
        """Accept a posted form, then send the browser back to the page it came from."""

        if self.settings_store is None:
            self.send_error(503, "settings are not available")
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        _, current_palette, current = self.settings_store.snapshot()
        palette_name, settings = settings_from_form(form, self.palettes, current)
        self.settings_store.update(palette_name or current_palette, settings)
        _, current_camera = self.settings_store.camera_snapshot()
        camera = camera_settings_from_form(form, current_camera)
        if camera != current_camera:
            self.settings_store.update_camera(camera)
        # A button, not a setting: the capture thread picks this up between two frames.
        if self.device is not None and SHUTTER_ACTION in form.get("action", []):
            self.device.request_shutter()
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def serve_control_page(self) -> None:
        if self.settings_store is None:
            self.serve_stream()
            return
        status = self.device.status() if self.device is not None else None
        page = render_control_page(
            self.settings_store.as_dict(), sorted(self.palettes), status
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page)

    def send_json(self, payload: dict) -> None:
        body = json.dumps(payload, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_snapshot(self) -> None:
        jpeg = self.frames.snapshot()
        if jpeg is None:
            self.send_error(503, "no frame yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.end_headers()
        self.wfile.write(jpeg)

    def serve_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        self.end_headers()
        self.stream_parts()

    def stream_parts(self) -> None:
        # The client closing the stream surfaces as a broken pipe; that ends this request quietly.
        try:
            self.write_parts_until_disconnect()
        except (BrokenPipeError, ConnectionResetError):
            return

    def write_parts_until_disconnect(self) -> None:
        while True:
            jpeg = self.frames.wait_next(STREAM_WAIT_SECONDS)
            if jpeg is not None:
                self.write_one_part(jpeg)

    def write_one_part(self, jpeg: bytes) -> None:
        self.wfile.write(f"--{MJPEG_BOUNDARY}\r\n".encode())
        self.wfile.write(b"Content-Type: image/jpeg\r\n")
        self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
        self.wfile.write(jpeg)
        self.wfile.write(b"\r\n")

    def log_message(self, format: str, *args: object) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream the Thermal Master P1 as MJPEG.")
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--palette", default=DEFAULT_PALETTE, choices=sorted(build_palettes()))
    parser.add_argument("--upscale", type=int, default=DEFAULT_UPSCALE)
    parser.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY)
    parser.add_argument("--rotate", type=int, default=0, choices=VALID_ROTATIONS)
    parser.add_argument("--flip-horizontal", action="store_true")
    parser.add_argument("--flip-vertical", action="store_true")
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument("--units", default=DEFAULT_UNITS, choices=VALID_UNITS)
    parser.add_argument("--gain", default=DEFAULT_GAIN, choices=VALID_GAINS)
    parser.add_argument("--emissivity", type=float, default=DEFAULT_EMISSIVITY)
    parser.add_argument("--settings-file", default=None)
    return parser.parse_args()


def install_shutdown_handlers(shutdown: threading.Event, server: ThermalServer) -> None:
    """Unwind on the signal the daemon stops a service with.

    SIGTERM's default disposition kills the process outright, so the capture session never unwinds
    and the USB interface is left claimed with its alternate setting still set. The next start then
    cannot claim the camera. server.shutdown blocks until serve_forever returns and so cannot be
    called from the thread running it, which is why it goes on a thread of its own.
    """

    def request_shutdown(_received_signal: int, _frame: object) -> None:
        shutdown.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, request_shutdown)


def main() -> None:
    options = parse_args()
    palettes = build_palettes()
    # The command line supplies the starting point; anything saved from the control page overrides
    # it, so a restart keeps whatever the last person chose.
    settings_store = SettingsStore(
        options.palette,
        RenderSettings(
            upscale=options.upscale,
            jpeg_quality=options.jpeg_quality,
            rotation=options.rotate,
            flip_horizontal=options.flip_horizontal,
            flip_vertical=options.flip_vertical,
            overlay=not options.no_overlay,
            units=options.units,
            emissivity=options.emissivity,
        ),
        Path(options.settings_file) if options.settings_file else None,
        CameraSettings(gain=options.gain),
    )
    renderer_source = RendererSource(settings_store, palettes)
    frame_store = LatestFrame()
    shutdown = threading.Event()
    device = DeviceController(settings_store)
    worker = threading.Thread(
        target=capture_loop, args=(frame_store, renderer_source, shutdown, device), daemon=True
    )
    worker.start()
    server = ThermalServer(
        (options.bind, options.port), frame_store, settings_store, palettes, device
    )
    install_shutdown_handlers(shutdown, server)
    listening_on = f"thermal-master: serving http://{options.bind}:{options.port}/stream.mjpg"
    print(listening_on, file=sys.stderr, flush=True)
    server.serve_forever()
    # Give the capture thread its chance to put the camera down before the process goes away. It is
    # a daemon thread, so without this the interpreter exits from under it mid-read.
    worker.join(SHUTDOWN_GRACE_SECONDS)
    print("thermal-master: stopped", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
