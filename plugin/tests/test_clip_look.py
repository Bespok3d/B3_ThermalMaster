# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""How a clip is drawn apart from the live picture: its own colour scale and its own enlarging.

A clip's colours are fixed for the whole print from a frame with the bed already warm, so the bed
sits near the top of them and the part and the nozzle are hotter still. The stretch drew those in
one colour in every layer of a U1 print, so a clip has the knee unless somebody chooses otherwise,
while the live picture keeps the stretch. And a clip is made once, after the print, so it is
enlarged smoothly unless somebody asks for the cheaper squares.
"""

from __future__ import annotations

import dataclasses
import json
import shutil

import fake_camera
import numpy as np
import pytest

STARTED = 1_790_000_000.0


def recording_of(streamer, root):
    recording = streamer.Recording.create(root, STARTED, None, "cube.gcode")
    for layer, hottest in ((1, 40.0), (2, 60.0), (3, 50.0)):
        recording.append(streamer.LayerRecord(
            layer, streamer.FRAME_RECORD, "high", STARTED + layer,
            fake_camera.thermal_frame(24.0, hottest),
        ))
    recording.finish("complete")
    return recording


def inputs(streamer, palettes, live_scale="stretch", **timelapse):
    return streamer.ClipInputs(
        palettes["ironbow"],
        dataclasses.replace(streamer.RenderSettings(), colour_scale=live_scale),
        dataclasses.replace(streamer.TimelapseSettings(), **timelapse),
        shutil.which("ffmpeg"),
    )


def test_a_clip_has_the_knee_and_is_enlarged_smoothly_unless_told_otherwise(thermal_streamer):
    settings = thermal_streamer.TimelapseSettings()

    assert settings.timelapse_colour_scale == thermal_streamer.SCALE_KNEE
    assert settings.timelapse_upscale_filter == thermal_streamer.SMOOTH_UPSCALE
    assert thermal_streamer.RenderSettings().colour_scale == thermal_streamer.SCALE_STRETCH


def test_the_choices_are_kept_across_a_restart(thermal_streamer, tmp_path):
    saved = tmp_path / "settings.json"
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), saved)
    store.update_timelapse(dataclasses.replace(
        thermal_streamer.TimelapseSettings(),
        timelapse_colour_scale="log-mild", timelapse_upscale_filter="sharp",
    ))

    restored = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), saved
    ).timelapse_snapshot()

    assert (restored.timelapse_colour_scale, restored.timelapse_upscale_filter) == (
        "log-mild", "sharp"
    )


def test_a_file_saved_before_there_was_a_choice_gets_the_defaults(thermal_streamer, tmp_path):
    saved = tmp_path / "settings.json"
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), saved)
    store.update_timelapse(
        dataclasses.replace(thermal_streamer.TimelapseSettings(), timelapse=True)
    )
    written = json.loads(saved.read_text())
    del written["timelapse_colour_scale"], written["timelapse_upscale_filter"]
    saved.write_text(json.dumps(written))

    restored = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), saved
    ).timelapse_snapshot()

    assert restored.timelapse is True
    assert restored.timelapse_colour_scale == thermal_streamer.SCALE_KNEE
    assert restored.timelapse_upscale_filter == thermal_streamer.SMOOTH_UPSCALE


@pytest.mark.parametrize("scale", ["live", "stretch", "knee", "log-mild", "log-strong"])
def test_every_scale_is_taken_from_json_and_from_the_form(thermal_streamer, scale):
    current = thermal_streamer.TimelapseSettings()

    sent = thermal_streamer.timelapse_settings_from_json({"timelapse_colour_scale": scale}, current)
    posted = thermal_streamer.timelapse_settings_from_form(
        {"timelapse_colour_scale": [scale], "timelapse_upscale_filter": ["sharp"]}, current
    )

    assert sent.timelapse_colour_scale == scale
    assert (posted.timelapse_colour_scale, posted.timelapse_upscale_filter) == (scale, "sharp")


def test_an_unknown_scale_or_filter_changes_nothing(thermal_streamer):
    current = dataclasses.replace(
        thermal_streamer.TimelapseSettings(),
        timelapse_colour_scale="log-strong", timelapse_upscale_filter="sharp",
    )

    sent = thermal_streamer.timelapse_settings_from_json(
        {"timelapse_colour_scale": "rainbow", "timelapse_upscale_filter": "lanczos"}, current
    )
    posted = thermal_streamer.timelapse_settings_from_form({}, current)

    for result in (sent, posted):
        assert (result.timelapse_colour_scale, result.timelapse_upscale_filter) == (
            "log-strong", "sharp"
        )


def test_a_clip_is_drawn_with_its_own_scale_and_enlarging(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path)

    settings = thermal_streamer.clip_render_settings(
        recording, inputs(thermal_streamer, palettes, live_scale="stretch")
    )

    assert settings.colour_scale == thermal_streamer.SCALE_KNEE
    assert settings.upscale_filter == thermal_streamer.SMOOTH_UPSCALE


def test_the_same_as_the_live_picture_follows_it(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path)
    following = inputs(
        thermal_streamer, palettes, live_scale="log-strong",
        timelapse_colour_scale=thermal_streamer.TIMELAPSE_SCALE_LIVE,
    )

    settings = thermal_streamer.clip_render_settings(recording, following)

    assert settings.colour_scale == "log-strong"
    assert thermal_streamer.clip_colour_scale(following) == "log-strong"


def test_the_corner_names_the_clips_scale_not_the_live_one(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path)
    labelled = inputs(thermal_streamer, palettes, live_scale="stretch", timelapse_scale_label=True)

    settings = thermal_streamer.clip_render_settings(recording, labelled)

    assert thermal_streamer.clip_stamp(settings, labelled) == "Colour scale: Knee"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")
def test_a_made_clip_records_its_own_scale(thermal_streamer, palettes, tmp_path):
    recording = recording_of(thermal_streamer, tmp_path)

    outcome = thermal_streamer.make_clip(
        recording, inputs(thermal_streamer, palettes, live_scale="stretch")
    )

    assert outcome["scale"] == thermal_streamer.SCALE_KNEE


def picture(streamer, palettes, upscale_filter):
    renderer = streamer.ThermalRenderer(
        palettes["ironbow"],
        streamer.RenderSettings(colorbar=False, reticle=False, hotspot=False, coldspot=False),
    )
    return np.asarray(streamer.render_layer(
        renderer, fake_camera.thermal_frame(24.0, 60.0), (640, 480),
        upscale_filter=upscale_filter,
    )).astype(int)


def test_sharp_keeps_the_sensors_squares_and_smooth_blends_them(thermal_streamer, palettes):
    sharp = picture(thermal_streamer, palettes, thermal_streamer.SHARP_UPSCALE)
    smooth = picture(thermal_streamer, palettes, thermal_streamer.SMOOTH_UPSCALE)

    # A P1 frame enlarged four times: within one sensor pixel's square, sharp is one colour.
    assert all(np.array_equal(sharp[1, 1], sharp[y, x]) for y in range(4) for x in range(4))
    assert sharp.shape == smooth.shape
    steps = lambda image: np.abs(np.diff(image, axis=1)).sum()  # noqa: E731
    assert steps(smooth) < steps(sharp)


def test_a_marker_has_a_dark_edge_so_it_reads_on_any_colour(thermal_streamer, palettes):
    """On a U1 print the hottest marker sat on the orange of the ruler and could not be seen."""

    # A ramp, so the cross lands on a palette colour rather than on the black of a flat field.
    frame = fake_camera.thermal_frame(24.0, 60.0)
    frame[60, 40] = fake_camera.thermal_frame(24.0, 120.0).max()

    def drawn(hotspot):
        renderer = thermal_streamer.ThermalRenderer(
            palettes["ironbow"],
            thermal_streamer.RenderSettings(colorbar=False, reticle=False, hotspot=hotspot,
                                            coldspot=False),
        )
        return np.asarray(thermal_streamer.render_layer(renderer, frame, (640, 480)))

    marked, plain = drawn(True), drawn(False)
    x, y = int(40.5 * 4), int(60.5 * 4)
    shadow = tuple(thermal_streamer.OVERLAY_SHADOW_RGB)

    assert tuple(plain[y + 1, x + 3]) != shadow
    assert tuple(marked[y + 1, x + 3]) == shadow
    assert tuple(marked[y, x + 3]) != shadow


def test_the_timelapse_panel_offers_both_choices_with_the_defaults_chosen(
    thermal_streamer, settings_dict
):
    page = thermal_streamer.render_control_page(settings_dict(), ["ironbow"])

    assert 'name="timelapse_colour_scale"' in page
    assert '<option value="knee" selected>' in page.split('name="timelapse_colour_scale"')[1]
    assert '<option value="live">The same as the live picture</option>' in page
    assert '<option value="smooth" selected>' in page.split('name="timelapse_upscale_filter"')[1]
