# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A recording made into a clip: every layer rendered the way the stream would, handed to ffmpeg.

Done after the print, when the printer can spare the processor, and measured on the U1 at about a
minute for a 1,000 layer print, against the three to five its firmware takes over its own clip
(ROADMAP Phase 9). The encoder is the printer's own ffmpeg with `libx264`, the one H.264 encoder
this ffmpeg can reach there.

The picture is the live picture's: its palette, orientation, readout and spots. Two things differ.
Noise reduction is off, because averaging a layer with the layer before is a ghost, not a denoise.
And the range comes from the timelapse's own setting, because a range that eases from frame to
frame is right for a live view and makes a clip breathe.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, cast

import numpy as np
from PIL import Image

from .colour_scale import SCALE_NAMES, ToldScene
from .log import log_line
from .overlay import (
    PLACEHOLDER_RGB,
    draw_label,
    draw_overlay,
    label_width,
    overlay_style,
    with_banner,
)
from .pipeline import FIXED_RANGE, RenderSettings, ThermalRenderer, frame_bounds, ordered_range
from .recording import CAMERA_OFF_RECORD, LayerRecord, Recording
from .temperature import celsius_for_raw, oriented_size
from .timelapse import (
    CLIP_READOUT_SWITCHES,
    TIMELAPSE_RANGE_AS_DISPLAYED,
    TIMELAPSE_RANGE_FIXED,
    TIMELAPSE_RANGE_FROM_START,
    TimelapseSettings,
)

# The firmware's own clips run at about 24 frames a second, so a thermal clip beside one plays at
# the same pace: the 150 layer cube is six seconds.
CLIP_FRAMES_PER_SECOND = 24


# How long the finished part stays on screen at the end. At 24 frames a second the last layer was
# otherwise gone in a twenty-fourth of a second, before anybody had seen what was printed. ffmpeg
# repeats the frame itself, so the recording and the frame count stay one per layer.
HOLD_LAST_SECONDS = 2


# The short edge the clip is scaled up to, by whole pixels. At the sensor's own size H.264 keeps
# colour at a quarter of that, which smears a palette, and a label drawn into the frame could not
# be read. A P1's 160 by 120 goes to 640 by 480 and a P3's 256 by 192 to 768 by 576.
CLIP_SHORT_EDGE = 480


# Fluidd and Mainsail show a timelapse's `.jpg` of the same name as its thumbnail. The firmware's
# are 120 by 90, and so is a P1 frame's shape.
THUMBNAIL_BOX = (120, 90)


THUMBNAIL_QUALITY = 85


# Two threads at the lowest priority, so a print started straight after is not slowed.
FFMPEG_THREADS = 2


NICENESS = 19


# The frame taken when the second layer starts is the first to show a layer on the bed, which is
# what the "from the start" range is measured on.
FROM_START_LAYER = 2


# What the band across a missed layer says. The layer is named, because the picture under it is the
# last one taken, and a still frame that does not say which layer it is not reads as a stuck part.
CAMERA_MISSING_BANNER = "Camera disconnected, layer {layer}"


CAMERA_OFF_BANNER = "Camera off, layer {layer}"


# Written in the bottom left of every frame when asked for, so that clips of one print made with
# different scales can be told apart once they have left the printer.
CLIP_SCALE_STAMP = "Colour scale: {scale}"


NO_FRAMES_REASON = (
    "No frame was recorded. If the print ran, the slicer is probably not sending layer numbers; "
    "the plugin's README says what to add."
)


@dataclasses.dataclass(frozen=True)
class ClipInputs:
    """Everything a clip is made from besides the recording itself."""

    palette: np.ndarray
    live: RenderSettings
    timelapse: TimelapseSettings
    ffmpeg: str | None


def clip_scale(sensor_size: tuple[int, int]) -> int:
    return -(-CLIP_SHORT_EDGE // min(sensor_size))


def measured_frames(recording: Recording) -> Iterator[tuple[int, np.ndarray]]:
    """The layer and the counts of every record that holds a frame."""

    for record in recording.records():
        if record.counts is not None:
            yield record.layer, record.counts


def whole_print_bounds(recording: Recording) -> tuple[float, float] | None:
    """The coldest and hottest of every frame's own range, in raw counts."""

    ranges = [frame_bounds(counts) for _, counts in measured_frames(recording)]
    if not ranges:
        return None
    return (min(low for low, _ in ranges), max(high for _, high in ranges))


def from_start_bounds(recording: Recording) -> tuple[float, float] | None:
    """The range of the first frame with a layer down, or the whole print's if there is none.

    Not the first frame's: that is layer 0, taken when the start G-code sets the layer count and
    before the bed has heated, so a print cancelled before its second layer would be drawn against
    the range of a cold bed and come out all one colour (tried on the Pi 4, 2026-09-29).
    """

    started = next(
        (counts for layer, counts in measured_frames(recording) if layer >= FROM_START_LAYER), None
    )
    return frame_bounds(started) if started is not None else whole_print_bounds(recording)


def print_scene(recording: Recording) -> tuple[float, float] | None:
    """The coldest and hottest count of any frame of the print, for a curve to run over."""

    extremes = [
        (float(counts.min()), float(counts.max())) for _, counts in measured_frames(recording)
    ]
    if not extremes:
        return None
    return (min(low for low, _ in extremes), max(high for _, high in extremes))


def clip_scene(recording: Recording, inputs: ClipInputs) -> ToldScene | None:
    """The whole print's coldest and hottest, for a curve to hold still over the whole clip.

    None for a clip drawn as displayed, which eases its scene as the live picture does. With the
    timelapse's own temperatures it says the range was held, so a log keeps to them.
    """

    mode = inputs.timelapse.timelapse_range_mode
    extremes = print_scene(recording) if mode != TIMELAPSE_RANGE_AS_DISPLAYED else None
    if extremes is None:
        return None
    return ToldScene(*extremes, range_measured=mode != TIMELAPSE_RANGE_FIXED)


def stamp_box(size: tuple[int, int], text: str) -> tuple[float, float, float, float]:
    """Where the scale's name goes, bottom left, as the rectangle the markers keep clear of."""

    style = overlay_style(size, colorbar=False)
    top = size[1] - style.line_height - style.margin
    return (style.margin, top, style.margin + label_width(text, style.pixel_height), size[1])


def stamp_text(scale: str) -> str:
    return CLIP_SCALE_STAMP.format(scale=SCALE_NAMES.get(scale, scale))


def clip_stamp(settings: RenderSettings, inputs: ClipInputs) -> str | None:
    """What goes in the corner of every frame of this clip, if anything."""

    return stamp_text(settings.colour_scale) if inputs.timelapse.timelapse_scale_label else None


def clip_readout(live: RenderSettings, timelapse: TimelapseSettings) -> RenderSettings:
    """The live settings with the clips' own readout switches in place of the live ones."""

    return dataclasses.replace(
        live,
        spots=live.spots if timelapse.timelapse_spots else (),
        **{field: getattr(timelapse, switch) for switch, field in CLIP_READOUT_SWITCHES.items()},
    )


def fixed_at(live: RenderSettings, bounds: tuple[float, float]) -> RenderSettings:
    low, high = ordered_range(
        celsius_for_raw(bounds[0], live.emissivity), celsius_for_raw(bounds[1], live.emissivity)
    )
    return dataclasses.replace(
        live, range_mode=FIXED_RANGE, range_low_celsius=low, range_high_celsius=high
    )


def clip_render_settings(recording: Recording, inputs: ClipInputs) -> RenderSettings:
    """The live settings with the timelapse's range and readout, and without noise reduction."""

    live = clip_readout(
        dataclasses.replace(inputs.live, noise_reduction_weight=1.0), inputs.timelapse
    )
    mode = inputs.timelapse.timelapse_range_mode
    if mode == TIMELAPSE_RANGE_AS_DISPLAYED:
        return live
    if mode == TIMELAPSE_RANGE_FIXED:
        return dataclasses.replace(
            live,
            range_mode=FIXED_RANGE,
            range_low_celsius=inputs.timelapse.timelapse_range_low_celsius,
            range_high_celsius=inputs.timelapse.timelapse_range_high_celsius,
        )
    measure = from_start_bounds if mode == TIMELAPSE_RANGE_FROM_START else whole_print_bounds
    bounds = measure(recording)
    return fixed_at(live, bounds) if bounds is not None else live


def picture_size(recording: Recording, rotation: int) -> tuple[int, int] | None:
    """The size every picture in the clip is drawn at, from the first frame measured."""

    first = next(measured_frames(recording), None)
    if first is None:
        return None
    width, height = oriented_size(first[1], rotation)
    scale = clip_scale((width, height))
    return (width * scale, height * scale)


def render_layer(
    renderer: ThermalRenderer, counts: np.ndarray, size: tuple[int, int], stamp: str | None = None
) -> Image.Image:
    """One frame, as the stream draws it, scaled up by whole pixels before the readout goes on.

    The scale's name, when there is one to write, goes on last, with the markers told where it
    will be so that none of their numbers ends up underneath it.
    """

    image, stats, _ = renderer.render_image(counts)
    picture = Image.fromarray(image, mode="RGB").resize(size, Image.Resampling.NEAREST)
    overlay = renderer.overlay_for(stats)
    reserved = [stamp_box(size, stamp)] if stamp else []
    if overlay is not None:
        draw_overlay(picture, overlay, reserved)
    if stamp:
        style = overlay_style(size, colorbar=False)
        draw_label(picture, reserved[0][:2], stamp, style)
    return picture


def missing_layer_picture(
    record: LayerRecord, last_frame: Image.Image | None, size: tuple[int, int]
) -> Image.Image:
    """The last picture taken, with a band across its top saying the camera missed this layer.

    The last picture rather than a separate card, so the clip holds still where the camera was lost
    instead of jumping to something else and back, as the viewer does over a frozen stream. With no
    earlier picture, the camera was missing from the start, and the band goes on a dark frame.
    """

    wording = CAMERA_OFF_BANNER if record.kind == CAMERA_OFF_RECORD else CAMERA_MISSING_BANNER
    underneath = last_frame if last_frame is not None else Image.new("RGB", size, PLACEHOLDER_RGB)
    return with_banner(underneath, wording.format(layer=record.layer))


def ffmpeg_command(ffmpeg: str, size: tuple[int, int], output: Path) -> list[str]:
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
        "-r", str(CLIP_FRAMES_PER_SECOND), "-i", "-",
        "-vf", f"tpad=stop_mode=clone:stop_duration={HOLD_LAST_SECONDS}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-threads", str(FFMPEG_THREADS), "-movflags", "+faststart", "-f", "mp4", str(output),
    ]
    nice = shutil.which("nice")
    return [nice, "-n", str(NICENESS), *command] if nice else command


@dataclasses.dataclass
class EncodedFrames:
    """What went into ffmpeg: how many pictures, and the last one that was a real frame."""

    count: int = 0
    last_frame: Image.Image | None = None


def feed_frames(
    recording: Recording,
    renderer: ThermalRenderer,
    size: tuple[int, int],
    sink: BinaryIO,
    stamp: str | None = None,
) -> EncodedFrames:
    """Every layer in order, a frame or the picture saying why there is none."""

    encoded = EncodedFrames()
    for record in recording.records():
        if record.counts is None:
            picture = missing_layer_picture(record, encoded.last_frame, size)
        else:
            picture = render_layer(renderer, record.counts, size, stamp)
            encoded.last_frame = picture
        sink.write(picture.tobytes())
        encoded.count += 1
    return encoded


def encode(recording: Recording, settings: RenderSettings, inputs: ClipInputs) -> dict:
    """Run ffmpeg over the recording, into a partial file moved into place only once it is whole.

    What ffmpeg says goes to a file rather than a pipe: a pipe nobody reads while the frames are
    still going in fills up, and then both ends wait for each other for ever.
    """

    size = picture_size(recording, settings.rotation)
    if size is None:
        return {"error": NO_FRAMES_REASON}
    partial = recording.clip_path.with_name(recording.clip_path.name + ".partial")
    renderer = ThermalRenderer(inputs.palette, settings, clip_scene(recording, inputs))
    with tempfile.TemporaryFile() as said:
        process = subprocess.Popen(
            ffmpeg_command(str(inputs.ffmpeg), size, partial), stdin=subprocess.PIPE, stderr=said
        )
        frames_in = cast("BinaryIO", process.stdin)
        try:
            stamp = clip_stamp(settings, inputs)
            encoded = feed_frames(recording, renderer, size, frames_in, stamp)
            frames_in.close()
        except BrokenPipeError:
            encoded = EncodedFrames()
        finished = process.wait()
        said.seek(0)
        reason = said.read().decode("utf-8", "replace").strip()
    if finished != 0 or encoded.count == 0:
        partial.unlink(missing_ok=True)
        return {"error": f"ffmpeg could not make the clip: {reason or 'no reason given'}"}
    os.replace(partial, recording.clip_path)
    save_thumbnail(encoded.last_frame, recording.thumbnail_path)
    return {"made_at": time.time(), "frames": encoded.count}


def save_thumbnail(frame: Image.Image | None, path: Path) -> None:
    if frame is None:
        return
    thumbnail = frame.copy()
    thumbnail.thumbnail(THUMBNAIL_BOX)
    thumbnail.save(path, format="JPEG", quality=THUMBNAIL_QUALITY)


def make_clip(recording: Recording, inputs: ClipInputs) -> dict:
    """Make the clip and say how it went, in the words the settings page shows.

    A recording with no frame says so before anything is said about ffmpeg: there is nothing to
    encode either way, and what fixes it is the slicer's layer lines, not installing an encoder.
    """

    if next(measured_frames(recording), None) is None:
        return {"error": NO_FRAMES_REASON}
    if inputs.ffmpeg is None:
        return {"error": "ffmpeg is not installed on this printer, so no clip can be made."}
    started = time.monotonic()
    outcome = encode(recording, clip_render_settings(recording, inputs), inputs)
    if not outcome.get("error"):
        outcome["scale"] = inputs.live.colour_scale
        outcome["scale_in_name"] = inputs.timelapse.timelapse_scale_in_name
    seconds = time.monotonic() - started
    said = outcome.get("error") or "clip made"
    log_line(f"timelapse: {recording.recording_id}: {said}, in {seconds:.1f} s")
    return outcome
