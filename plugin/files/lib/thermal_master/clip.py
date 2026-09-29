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

from .log import log_line
from .overlay import draw_overlay, placeholder_picture
from .pipeline import FIXED_RANGE, RenderSettings, ThermalRenderer, frame_bounds, ordered_range
from .recording import CAMERA_OFF_RECORD, Recording
from .temperature import celsius_for_raw, oriented_size
from .timelapse import (
    TIMELAPSE_RANGE_AS_DISPLAYED,
    TIMELAPSE_RANGE_FIXED,
    TIMELAPSE_RANGE_FROM_START,
    TimelapseSettings,
)

# The firmware's own clips run at about 24 frames a second, so a thermal clip beside one plays at
# the same pace: the 150 layer cube is six seconds.
CLIP_FRAMES_PER_SECOND = 24


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


CAMERA_MISSING_TITLE = "Camera disconnected"


CAMERA_MISSING_HINT = "No picture for this layer"


CAMERA_OFF_TITLE = "Camera off"


CAMERA_OFF_HINT = "Switched off for this layer"


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


def fixed_at(live: RenderSettings, bounds: tuple[float, float]) -> RenderSettings:
    low, high = ordered_range(
        celsius_for_raw(bounds[0], live.emissivity), celsius_for_raw(bounds[1], live.emissivity)
    )
    return dataclasses.replace(
        live, range_mode=FIXED_RANGE, range_low_celsius=low, range_high_celsius=high
    )


def clip_render_settings(recording: Recording, inputs: ClipInputs) -> RenderSettings:
    """The live settings with the timelapse's range, and without noise reduction."""

    live = dataclasses.replace(inputs.live, noise_reduction_weight=1.0)
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
    renderer: ThermalRenderer, counts: np.ndarray, size: tuple[int, int]
) -> Image.Image:
    """One frame, as the stream draws it, scaled up by whole pixels before the readout goes on."""

    image, stats, _ = renderer.render_image(counts)
    picture = Image.fromarray(image, mode="RGB").resize(size, Image.Resampling.NEAREST)
    overlay = renderer.overlay_for(stats)
    if overlay is not None:
        draw_overlay(picture, overlay)
    return picture


def missing_layer_picture(kind: int, size: tuple[int, int]) -> Image.Image:
    if kind == CAMERA_OFF_RECORD:
        return placeholder_picture(CAMERA_OFF_TITLE, CAMERA_OFF_HINT, size)
    return placeholder_picture(CAMERA_MISSING_TITLE, CAMERA_MISSING_HINT, size)


def ffmpeg_command(ffmpeg: str, size: tuple[int, int], output: Path) -> list[str]:
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
        "-r", str(CLIP_FRAMES_PER_SECOND), "-i", "-",
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
    recording: Recording, renderer: ThermalRenderer, size: tuple[int, int], sink: BinaryIO
) -> EncodedFrames:
    """Every layer in order, a frame or the picture saying why there is none."""

    encoded = EncodedFrames()
    for record in recording.records():
        if record.counts is None:
            picture = missing_layer_picture(record.kind, size)
        else:
            picture = render_layer(renderer, record.counts, size)
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
    renderer = ThermalRenderer(inputs.palette, settings)
    with tempfile.TemporaryFile() as said:
        process = subprocess.Popen(
            ffmpeg_command(str(inputs.ffmpeg), size, partial), stdin=subprocess.PIPE, stderr=said
        )
        frames_in = cast("BinaryIO", process.stdin)
        try:
            encoded = feed_frames(recording, renderer, size, frames_in)
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
    """Make the clip and say how it went, in the words the settings page shows."""

    if inputs.ffmpeg is None:
        return {"error": "ffmpeg is not installed on this printer, so no clip can be made."}
    started = time.monotonic()
    outcome = encode(recording, clip_render_settings(recording, inputs), inputs)
    seconds = time.monotonic() - started
    said = outcome.get("error") or "clip made"
    log_line(f"timelapse: {recording.recording_id}: {said}, in {seconds:.1f} s")
    return outcome
