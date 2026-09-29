# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A recording made into a clip: the range it is rendered with, and the clip ffmpeg makes of it.

The ranges are plain arithmetic over the kept frames and are tested without ffmpeg. The clip itself
needs the real ffmpeg, and is skipped where there is none: what matters about it is that every
layer is in it, a missed one included, and that nothing half made is ever left where a finished
clip would be.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess

import fake_camera
import pytest
from PIL import Image

STARTED = 1_790_000_000.0

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed here",
)


def recording_of(streamer, root, layers):
    """A recording of frames ramping up to the temperatures given, None for a layer missed."""

    recording = streamer.Recording.create(root, STARTED, None, "cube.gcode")
    for layer, hottest in layers:
        if hottest is None:
            recording.append(streamer.LayerRecord(layer, streamer.CAMERA_MISSING_RECORD, None,
                                                  STARTED + layer))
            continue
        counts = fake_camera.thermal_frame(24.0, hottest)
        recording.append(streamer.LayerRecord(layer, streamer.FRAME_RECORD, "high",
                                              STARTED + layer, counts))
    recording.finish("complete")
    return recording


def inputs(streamer, palettes, mode, **timelapse):
    return streamer.ClipInputs(
        palettes["ironbow"],
        streamer.RenderSettings(),
        dataclasses.replace(streamer.TimelapseSettings(), timelapse_range_mode=mode, **timelapse),
        shutil.which("ffmpeg"),
    )


def test_the_whole_print_range_spans_every_frame(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path, [(1, 40.0), (2, 60.0), (3, 50.0)])

    settings = thermal_streamer.clip_render_settings(
        recording, inputs(thermal_streamer, palettes, thermal_streamer.TIMELAPSE_RANGE_WHOLE_PRINT)
    )

    assert settings.range_mode == thermal_streamer.FIXED_RANGE
    assert settings.range_high_celsius > 55.0
    assert settings.range_low_celsius < 30.0


def test_the_from_start_range_is_the_second_layers(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path, [(1, 90.0), (2, 45.0), (3, 70.0)])

    settings = thermal_streamer.clip_render_settings(
        recording, inputs(thermal_streamer, palettes, thermal_streamer.TIMELAPSE_RANGE_FROM_START)
    )

    assert 40.0 < settings.range_high_celsius < 46.0


def test_a_print_that_never_reached_layer_two_takes_the_whole_prints_range(
    thermal_streamer, palettes, tmp_path
):
    """A print cancelled at the start: layer 0 on a cold bed and the end, hot. Both must show."""

    recording = recording_of(thermal_streamer, tmp_path, [(0, 30.0), (1, 150.0)])

    settings = thermal_streamer.clip_render_settings(
        recording, inputs(thermal_streamer, palettes, thermal_streamer.TIMELAPSE_RANGE_FROM_START)
    )

    assert settings.range_low_celsius < 30.0
    assert settings.range_high_celsius > 140.0


def test_a_fixed_range_is_the_timelapses_own(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path, [(1, 40.0)])

    settings = thermal_streamer.clip_render_settings(
        recording,
        inputs(thermal_streamer, palettes, thermal_streamer.TIMELAPSE_RANGE_FIXED,
               timelapse_range_low_celsius=25.0, timelapse_range_high_celsius=95.0),
    )

    assert (settings.range_low_celsius, settings.range_high_celsius) == (25.0, 95.0)


def test_noise_reduction_is_off_in_every_mode(thermal_streamer, palettes, tmp_path):
    """Averaging a layer with the layer before is a ghost, not a denoise."""

    recording = recording_of(thermal_streamer, tmp_path, [(1, 40.0)])

    for mode in thermal_streamer.VALID_TIMELAPSE_RANGES:
        settings = thermal_streamer.clip_render_settings(
            recording, inputs(thermal_streamer, palettes, mode)
        )
        assert settings.noise_reduction_weight == 1.0


def test_a_p1_clip_is_four_times_the_sensor(thermal_streamer, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path, [(1, 40.0)])

    assert thermal_streamer.picture_size(recording, 0) == (640, 480)
    assert thermal_streamer.picture_size(recording, 90) == (480, 640)


def test_without_ffmpeg_the_clip_says_why(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path, [(1, 40.0)])
    without = dataclasses.replace(
        inputs(thermal_streamer, palettes, thermal_streamer.TIMELAPSE_RANGE_FROM_START), ffmpeg=None
    )

    outcome = thermal_streamer.make_clip(recording, without)

    assert "ffmpeg is not installed" in outcome["error"]
    assert not recording.has_clip


def test_a_recording_with_no_frame_says_why(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path, [])

    outcome = thermal_streamer.make_clip(
        recording, inputs(thermal_streamer, palettes, thermal_streamer.TIMELAPSE_RANGE_FROM_START)
    )

    assert "layer numbers" in outcome["error"]


def frames_in(clip):
    counted = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames,width,height", "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True, check=True,
    )
    width, height, frames = counted.stdout.strip().split(",")
    return int(width), int(height), int(frames)


@needs_ffmpeg
@pytest.mark.timeout(120)
def test_every_layer_is_in_the_clip_a_missed_one_included(thermal_streamer, palettes, tmp_path):
    recording = recording_of(
        thermal_streamer, tmp_path, [(1, 40.0), (2, None), (3, 50.0), (4, 55.0)]
    )

    outcome = thermal_streamer.make_clip(
        recording, inputs(thermal_streamer, palettes, thermal_streamer.TIMELAPSE_RANGE_FROM_START)
    )

    assert outcome.get("error") is None
    assert outcome["frames"] == 4
    assert frames_in(recording.clip_path) == (640, 480, 4)
    assert Image.open(recording.thumbnail_path).size == (120, 90)
    assert not list(tmp_path.rglob("*.partial"))


@needs_ffmpeg
def test_a_clip_ffmpeg_refuses_leaves_nothing_behind(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path, [(1, 40.0)])
    refusing = dataclasses.replace(
        inputs(thermal_streamer, palettes, thermal_streamer.TIMELAPSE_RANGE_FROM_START),
        ffmpeg=shutil.which("false"),
    )

    outcome = thermal_streamer.make_clip(recording, refusing)

    assert "ffmpeg could not make the clip" in outcome["error"]
    assert not recording.has_clip
    assert not list(tmp_path.rglob("*.partial"))
