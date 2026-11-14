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
import functools
import io
import math

import numpy as np
from PIL import Image

from .colour_scale import (
    CURVE_SHAPES,
    DEFAULT_COLOUR_SCALE,
    LOG_CURVE,
    MIN_SPAN_RAW_COUNTS,
    ColourMapping,
    CurveShape,
    ToldScene,
)
from .geometry import orient
from .overlay import (
    HeldReadout,
    Overlay,
    draw_overlay,
    encode_upscale,
    live_view_off_picture,
    stream_off_picture,
)
from .palettes import PALETTE_STEPS
from .temperature import (
    DEFAULT_EMISSIVITY,
    DEFAULT_UNITS,
    FrameStats,
    ThermalFrame,
    frame_statistics,
    raw_for_celsius,
    spot_readings,
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


# The picture is enlarged before the readout is drawn, so the text has pixels to land on, and on the
# printer that one resize is the most expensive operation in the frame: for a P1, which is doubled,
# 3.05 ms bilinear against 0.60 ms nearest, of a 14.85 ms frame. What the extra 2.45 ms buys is
# smoothing that every browser then resamples again, and that the viewer throws away outright by
# asking for the picture pixelated. Whether it is worth it is a question about a particular scene
# on a particular screen, so it is a setting rather than a decision taken here.
# How the display range is decided. Auto follows the scene, which is right when you do not know
# what you are looking at and wrong when you do: a toolhead crossing the view re-maps every colour
# and moves the ruler with it. Fixed is told two temperatures and stays there, so a colour means
# the same thing from frame to frame, the ruler is a constant reference, and the percentile pass
# that auto needs is not run at all.
AUTO_RANGE = "auto"
FIXED_RANGE = "fixed"
VALID_RANGE_MODES = (AUTO_RANGE, FIXED_RANGE)


# Where a fixed range starts before anybody has chosen one: a printer at rest, so switching to
# fixed without typing anything shows something sensible rather than a black rectangle.
DEFAULT_RANGE_LOW_CELSIUS = 20.0
DEFAULT_RANGE_HIGH_CELSIUS = 60.0


# The narrowest fixed range worth allowing. Below this the picture is two colours and the noise
# between them.
MIN_RANGE_SPAN_CELSIUS = 1.0


SMOOTH_UPSCALE = "smooth"
SHARP_UPSCALE = "sharp"
VALID_UPSCALE_FILTERS = (SMOOTH_UPSCALE, SHARP_UPSCALE)


# What the camera sends, and nothing here can change it: upstream's protocol notes know no
# command for the rate, and the U1 counted 25.0 frames a second from both cameras on 2026-11-13
# (F-78).
CAMERA_FRAMES_A_SECOND = 25


# How many of those the live view may be given as pictures, and none at all. In fives, at the
# maintainer's word. Five is the slowest because a picture older than a second is treated as
# stale by everything that serves one (FRESH_ENOUGH_SECONDS in the server), so anything slower
# would need those to follow the rate first.
LIVE_VIEW_OFF = 0
VALID_LIVE_VIEW_RATES = (CAMERA_FRAMES_A_SECOND, 20, 15, 10, 5, LIVE_VIEW_OFF)


# How often the live view's readout is drawn afresh: on every picture, which is what the plugin
# always did, or this many times a second with the last one pasted in between (HeldReadout in
# the overlay). Every picture is nought rather than 25, because it means "do not hold it" at
# whatever rate the live view runs.
READOUT_EVERY_PICTURE = 0
VALID_READOUT_REFRESHES = (READOUT_EVERY_PICTURE, 5, 2, 1)
# Twice a second, the maintainer's choice after 0.30.6 was measured on the U1 on 2026-11-14:
# about 5 points of a core less than every picture for either camera, where once a second
# bought half a point more and left the markers trailing twice as long. "If people want it
# faster, they can tune it up."
DEFAULT_READOUT_REFRESH = 2


NORMALIZE_LOW_PERCENTILE = 2.0


NORMALIZE_HIGH_PERCENTILE = 98.0


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


# The one weight that can be done without leaving the integers, and the only one anything sets.
EVEN_BLEND_WEIGHT = 0.5


def known_live_view_rate(value: object, otherwise: int = CAMERA_FRAMES_A_SECOND) -> int:
    """A live view rate that is offered, or `otherwise` for anything that is not one.

    A whole number and nothing that merely equals one: a JSON `false` equals nought, and a body
    that says `false` has not asked for the live view to be switched off.
    """

    if type(value) is int and value in VALID_LIVE_VIEW_RATES:
        return value
    return otherwise


def known_readout_refresh(value: object, otherwise: int = DEFAULT_READOUT_REFRESH) -> int:
    """A readout refresh that is offered, or `otherwise`. A whole number, as the live view's is."""

    if type(value) is int and value in VALID_READOUT_REFRESHES:
        return value
    return otherwise


def pictures_a_refresh(pictures_a_second: int, refreshes_a_second: int) -> int:
    """How many pictures one drawing of the readout lasts, at this live view rate.

    Rounded down, so the readout refreshes at least as often as was asked: twice a second at 25
    pictures is every twelfth. Never less than one, so a refresh asked for oftener than the
    pictures come is simply every picture.
    """

    return max(pictures_a_second // refreshes_a_second, 1)


def smoothing_at_rate(smoothing: float, pictures_a_second: int) -> float:
    """The easing weight that moves the range as far in a second as `smoothing` does at 25.

    The range eases once for every picture drawn, so a live view at five pictures a second would
    take five times as long by the clock to follow a scene. Compounded over the camera frames
    between two pictures, it arrives where the full rate would have.
    """

    camera_frames_a_picture = CAMERA_FRAMES_A_SECOND / pictures_a_second
    return 1.0 - math.pow(1.0 - smoothing, camera_frames_a_picture)


def reduce_temporal_noise(
    current: np.ndarray, previous: np.ndarray | None, weight: float
) -> np.ndarray:
    """Average this frame with the last one, which halves per-pixel sensor noise.

    The default weight is half, which is an average of two integers, so it is done as one: the
    float path casts a whole frame to float32 and back for it. Measured on the printer at 0.19 ms
    against 0.14 ms, the smallest of the three wins in this round and free, since the two paths
    agree exactly (the sum is well inside float32's exact range, so the float version was already
    computing the same floor).
    """

    if previous is None or weight >= 1.0:
        return current
    if weight == EVEN_BLEND_WEIGHT:
        # Widened first: two uint16 frames added together overflow uint16, and a wrapped sum reads
        # as cold rather than hot, which is the worst direction for this plugin to be wrong in.
        halved: np.ndarray = (
            (current.astype(np.uint32) + previous.astype(np.uint32)) >> 1
        ).astype(np.uint16)
        return halved
    blended = weight * current.astype(np.float32) + (1.0 - weight) * previous.astype(np.float32)
    return blended.astype(np.uint16)


def told_bounds(settings: RenderSettings) -> tuple[float, float] | None:
    """The two raw counts a fixed range means, or None when the range is measured.

    Worked out here, once per settings change, rather than per frame: the conversion from degrees
    to counts is a search through the driver's own emissivity correction, which is cheap once and
    silly at frame rate. It depends on the emissivity, which is why it is recomputed whenever the
    settings are, and never cached across them.
    """

    if not settings.fixed_range:
        return None
    low, high = ordered_range(settings.range_low_celsius, settings.range_high_celsius)
    return (
        raw_for_celsius(low, settings.emissivity),
        raw_for_celsius(high, settings.emissivity),
    )


def ordered_range(low: float, high: float) -> tuple[float, float]:
    """The two ends the right way round, and far enough apart to be a range at all."""

    low, high = min(low, high), max(low, high)
    if high - low < MIN_RANGE_SPAN_CELSIUS:
        high = low + MIN_RANGE_SPAN_CELSIUS
    return (low, high)


def frame_bounds(frame: np.ndarray) -> tuple[float, float]:
    """The raw counts to map to the ends of the palette.

    Percentiles rather than min and max, so one dead pixel or one glint does not take the whole
    range with it. Taken on raw counts rather than Celsius: the conversion is monotonic, so the
    percentiles land on the same pixels either way, and this skips converting the whole frame to
    float just to find two numbers.
    """

    # Both from one call. Two calls partition all 19,200 pixels twice, which on the printer is
    # 2.07 ms against 1.14 ms for the same two numbers to the last bit: the largest single saving
    # in the pipeline and no change at all to the picture. A 2x2 subsample is faster again, at
    # 0.80 ms, and moves the range ends by about a twentieth of a degree; not taken, because that
    # is a change to what is on screen in exchange for a third of what this already saves.
    low, high = np.percentile(frame, (NORMALIZE_LOW_PERCENTILE, NORMALIZE_HIGH_PERCENTILE))
    return (float(low), float(high))


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
    thermal: ThermalFrame


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
    upscale_filter: str = SMOOTH_UPSCALE
    # Whether the unsharp mask runs at all. A switch beside the strength rather than a strength of
    # nought, so that switching it off and on again gives back whatever strength a settings file
    # was edited to. On unless somebody says otherwise, so that upgrading changes nobody's
    # picture. Off, the largest single step of the pipeline is not run: 3.83 ms of a P3 frame on
    # the U1 and 1.70 ms of a P1's, measured on 2026-11-12.
    edge_sharpening: bool = True
    range_mode: str = AUTO_RANGE
    range_low_celsius: float = DEFAULT_RANGE_LOW_CELSIUS
    range_high_celsius: float = DEFAULT_RANGE_HIGH_CELSIUS
    # Places somebody asked to watch, in the orientation the picture is displayed in, capped at
    # MAX_SPOTS. They live here rather than in the browser so that they are burned into the
    # picture: a spot that existed only in one viewer would be missing from the tile, from a
    # recorded clip and from every other browser, which is most of the reasons to place one.
    spots: tuple[tuple[int, int], ...] = ()
    # Which of the scales in `colour_scale` the picture is drawn with. The stretch is what it
    # always was; the curves spread the palette unevenly, over a followed range or a held one.
    colour_scale: str = DEFAULT_COLOUR_SCALE
    # How many pictures a second the live view is given, out of the 25 frames the camera sends,
    # or none. Making pictures is nearly all of what this plugin costs while somebody watches,
    # so fewer of them is the one setting that scales the whole of it. It is here with the
    # rendering because it is about what is done with a frame: the camera is read the same
    # whatever this says, which is what keeps the timelapse working with the live view off. The
    # capture loop does the choosing, and a renderer only ever sees the frames chosen for it.
    live_view_rate: int = CAMERA_FRAMES_A_SECOND
    # How many times a second the live view's readout is drawn afresh, or every picture. Held in
    # between, the ruler's numbers and the markers stand still while the colours carry on. Only
    # the live view's renderer holds one: a clip's frames are layers, and each is drawn.
    readout_refresh: int = DEFAULT_READOUT_REFRESH

    @property
    def live_view(self) -> bool:
        """Whether the live view is drawn at all."""

        return self.live_view_rate != LIVE_VIEW_OFF

    @property
    def readout(self) -> bool:
        """Whether anything is drawn on top of the picture at all.

        What the encode size keys off, since it is text that needs the extra pixels and both
        surfaces draw text. Turning both off returns the plugin to the cost it had before there
        was a readout.
        """

        return bool(
            self.colorbar or self.reticle or self.hotspot or self.coldspot or self.spots
        )

    @property
    def applied_detail_strength(self) -> float:
        """How much edge the unsharp mask adds back: the strength, or none with the switch off."""

        return self.detail_strength if self.edge_sharpening else 0.0

    @property
    def fixed_range(self) -> bool:
        """Whether the display range was told rather than measured."""

        return self.range_mode == FIXED_RANGE

    @property
    def mirrors(self) -> tuple[bool, bool]:
        """The two flips as one value, since nothing ever wants only one of them."""

        return (self.flip_horizontal, self.flip_vertical)


def frame_extremes(frame: np.ndarray) -> tuple[float, float]:
    return (float(frame.min()), float(frame.max()))


class ThermalRenderer:
    """Raw sensor frames in, colourmapped RGB out, carrying the state that spans frames.

    Two things have to persist between frames and so cannot live in a function: the smoothed
    display bounds, and the previous frame the noise reduction averages against. A curve adds a
    third, the smoothed coldest and hottest, eased like the bounds so that a nozzle crossing the
    view does not re-map every colour in one frame.

    A clip can tell it the whole print's coldest and hottest instead, `told_scene`, which is what
    lets a curve mean the same thing from the first layer of a clip to the last.
    """

    def __init__(
        self,
        palette: np.ndarray,
        settings: RenderSettings = RenderSettings(),
        told_scene: ToldScene | None = None,
    ) -> None:
        self._palette = palette
        self._settings = settings
        self._bounds: tuple[float, float] | None = None
        self._previous_frame: np.ndarray | None = None
        self._told_bounds = told_bounds(settings)
        self._told_scene = told_scene
        self._shape: CurveShape | None = CURVE_SHAPES.get(settings.colour_scale)
        self._scene: tuple[float, float] | None = None
        self._mapping: ColourMapping | None = None
        self._held_readout: HeldReadout | None = None

    def hold_readout(self) -> None:
        """Draw the readout as often as the settings say, and paste it on the pictures between.

        Asked for by the live view's source and by nothing else, so a renderer built for a clip
        draws every frame's readout whatever the setting says. With the setting at every
        picture there is nothing to hold, and the picture is drawn as it always was.
        """

        settings = self._settings
        if settings.readout_refresh == READOUT_EVERY_PICTURE or not settings.live_view:
            return
        self._held_readout = HeldReadout(
            pictures_a_refresh(settings.live_view_rate, settings.readout_refresh)
        )

    @property
    def bounds(self) -> tuple[float, float] | None:
        """The smoothed display range in raw counts, or None before the first frame."""

        return self._bounds

    def carry_over(self, previous: ThermalRenderer) -> None:
        """Take the smoothed bounds and the last frame from the renderer being replaced.

        A settings change builds a new renderer rather than mutating one the capture thread may be
        inside. Started empty, that costs about a second of visible re-settling, which is a fair
        price once and an unreasonable one when somebody is placing four spots in a row: every
        placement is a settings change, so the picture would breathe on each of them. Nothing
        carried here depends on any setting: the bounds are raw counts and the previous frame is in
        the sensor's own orientation, so both stay valid across a palette, rotation or spot change.
        """

        self._bounds = previous.bounds
        self._previous_frame = previous._previous_frame
        self._scene = previous._scene

    def keep_undrawn(self, thermal_raw: np.ndarray) -> None:
        """A frame the live view is not drawing, kept as the one before the next it does.

        Noise reduction averages a frame with the one before it. At a slower live view the frame
        before would otherwise be the last one drawn, a fifth of a second old at five pictures a
        second, and a nozzle crossing the view would trail a ghost that far behind it. The camera
        frame just before is read anyway, so it is used instead, and keeping it costs a reference.
        """

        self._previous_frame = thermal_raw

    def render(self, thermal_raw: np.ndarray) -> np.ndarray:
        return self.render_image(thermal_raw)[0]

    def render_image(
        self, thermal_raw: np.ndarray
    ) -> tuple[np.ndarray, FrameStats, np.ndarray]:
        denoised = reduce_temporal_noise(
            thermal_raw, self._previous_frame, self._settings.noise_reduction_weight
        )
        self._previous_frame = denoised
        # A told range needs neither measuring nor easing: it is the same two counts every frame,
        # worked out once when the settings changed. It is also the cheapest frame this plugin can
        # render, since the percentiles are the second most expensive step in the pipeline.
        self._bounds = self._told_bounds or smooth_bounds(
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
        # Attached rather than computed in there: what the frame says is one question, and what
        # somebody asked to watch inside it is another. Skipped entirely when nothing was placed,
        # which is the usual case and the one that must stay free.
        if self._settings.spots:
            stats = dataclasses.replace(
                stats,
                spots=spot_readings(
                    denoised,
                    self._settings.spots,
                    self._settings.rotation,
                    self._settings.mirrors,
                    self._settings.emissivity,
                ),
            )
        normalized = self._palette_indices(denoised, self._bounds)
        # `take` rather than fancy indexing, for byte-identical output at a third of the cost:
        # 1.44 ms against 0.48 ms on the printer. Not on anybody's list of suspects, which is the
        # argument for timing every step rather than the ones that look expensive.
        # Turned last of the steps that look at the scene, so everything before it works in the
        # sensor's own orientation and the frame kept for noise reduction cannot change shape when
        # the setting does. And before the colours rather than after them: a lookup does not care
        # where a pixel is, so the picture is the same either way, and what is copied through the
        # rotated view is one byte a pixel instead of three. For a P3 turned a quarter, 3.67 ms
        # for the two steps against 1.70 ms on the U1, measured on 2026-11-12.
        oriented_indices = orient(
            enhance_detail(normalized, self._settings.applied_detail_strength),
            self._settings.rotation,
            self._settings.flip_horizontal,
            self._settings.flip_vertical,
        )
        return np.take(self._palette, oriented_indices, axis=0), stats, denoised

    def _palette_indices(self, frame: np.ndarray, bounds: tuple[float, float]) -> np.ndarray:
        """The stretch, or the curve and the mapping it used, kept for the ruler."""

        shape = self._shape
        if shape is None:
            self._mapping = None
            return normalize_to_bytes(frame, *bounds)
        scene = self._scene_for(shape, frame, bounds)
        self._mapping = ColourMapping(shape, bounds, scene, self._settings.emissivity)
        return self._mapping.indices(frame)

    def _scene_for(
        self, shape: CurveShape, frame: np.ndarray, bounds: tuple[float, float]
    ) -> tuple[float, float]:
        """What a curve runs over: the scene, the whole print, or the held temperatures.

        Held by a person, a log runs over the held temperatures, so a colour still means the same
        temperature in every frame; a knee keeps them below its bend and reaches past them to the
        hottest thing in view, or in a clip to the print's hottest.
        """

        told = self._told_scene
        if told is None:
            self._scene = smooth_bounds(
                self._scene, frame_extremes(frame), self._settings.bounds_smoothing
            )
            measured = self._scene
        else:
            measured = (told.coldest, told.hottest)
        held = self._settings.fixed_range and (told is None or not told.range_measured)
        return bounds if held and shape.curve == LOG_CURVE else measured

    def overlay_for(self, stats: FrameStats) -> Overlay | None:
        """What to draw over a frame that measured this, or None with the whole readout off.

        One place for the stream and the timelapse, so a clip carries the same readout the tile
        does, switched on and off by the same boxes.
        """

        settings = self._settings
        if not settings.readout:
            return None
        return Overlay(
            self._palette,
            stats,
            settings.units,
            settings.colorbar,
            settings.reticle,
            settings.hotspot,
            settings.coldspot,
            self._mapping,
        )

    def render_frame(self, thermal_raw: np.ndarray) -> RenderedFrame:
        image, stats, denoised = self.render_image(thermal_raw)
        settings = self._settings
        overlay = self.overlay_for(stats)
        upscale = encode_upscale(
            (image.shape[1], image.shape[0]),
            settings.upscale,
            settings.readout,
            whole_steps=settings.upscale_filter == SHARP_UPSCALE,
        )
        # The denoised counts rather than the picture: this is what a viewer reads temperatures
        # out of, and it is stored unconverted because converting is four float passes over the
        # whole frame and nothing is asking for them yet.
        measured = ThermalFrame(denoised, settings.rotation, settings.mirrors, settings.emissivity)
        picture = enlarged_picture(image, upscale, settings.upscale_filter)
        if overlay is not None:
            self._draw_readout(picture, overlay)
        return RenderedFrame(picture_jpeg(picture, settings.jpeg_quality), stats, measured)

    def _draw_readout(self, picture: Image.Image, overlay: Overlay) -> None:
        """The readout on a picture already at its size: held if this renderer holds one."""

        if self._held_readout is None:
            draw_overlay(picture, overlay)
            return
        self._held_readout.draw(picture, overlay)

    def render_jpeg(self, thermal_raw: np.ndarray) -> bytes:
        return self.render_frame(thermal_raw).jpeg


def picture_jpeg(picture: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


@functools.lru_cache(maxsize=1)
def stream_off_jpeg(quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """The placeholder as a JPEG, ready to publish like any other frame.

    Kept rather than re-encoded, because the capture loop republishes it twice a second for as long
    as the camera is switched off, and a feature whose whole point is to stop the printer working
    should not spend a JPEG encode a second saying so.
    """

    return picture_jpeg(stream_off_picture(), quality)


@functools.lru_cache(maxsize=1)
def live_view_off_jpeg(quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """The other placeholder, for the live view switched off with the camera still being read.

    Kept for the same reason: it is republished twice a second to whoever is looking.
    """

    return picture_jpeg(live_view_off_picture(), quality)


def resampling(upscale_filter: str) -> Image.Resampling:
    """Which filter enlarges the picture. Nearest is five times cheaper and shows its pixels."""

    if upscale_filter == SHARP_UPSCALE:
        return Image.Resampling.NEAREST
    return Image.Resampling.BILINEAR


def encode_jpeg(
    rgb_frame: np.ndarray,
    upscale: float,
    quality: int,
    overlay: Overlay | None = None,
    upscale_filter: str = SMOOTH_UPSCALE,
) -> bytes:
    image = enlarged_picture(rgb_frame, upscale, upscale_filter)
    # After the resize, so the text is drawn at the size it will be looked at rather than resampled.
    if overlay is not None:
        draw_overlay(image, overlay)
    return picture_jpeg(image, quality)


def enlarged_picture(rgb_frame: np.ndarray, upscale: float, upscale_filter: str) -> Image.Image:
    """The colours as a picture at the size it is sent, before anything is drawn on it."""

    image = Image.fromarray(rgb_frame, mode="RGB")
    if upscale == 1:
        return image
    height, width = rgb_frame.shape[0], rgb_frame.shape[1]
    # Rounded, because smooth enlarging may be a fraction: a P3 at one and a quarter times.
    return image.resize(
        (round(width * upscale), round(height * upscale)), resampling(upscale_filter)
    )
