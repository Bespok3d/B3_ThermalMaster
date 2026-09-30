# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The colour scales being compared on the test/color-bar branch.

What matters is that each curve is the one the study drew, that the ruler and the picture ask the
same question and so cannot disagree, that today's picture is untouched, and that a held range is
left alone. The clip side is here too: the print's own coldest and hottest, the stamp, and making a
clip again with another scale.
"""

from __future__ import annotations

import dataclasses
import http.client
import threading

import fake_camera
import numpy as np
import pytest
from PIL import Image

STARTED = 1_790_000_000.0
KNEE_SHARE = 0.85
PLAIN = {"noise_reduction_weight": 1.0, "detail_strength": 0.0}


def raw_for(celsius):
    return (celsius + fake_camera.KELVIN_AT_ZERO_CELSIUS) * fake_camera.RAW_UNITS_PER_KELVIN


def hot_scene():
    """A cool bed with a small hot nozzle in it, the shape of the frames the study was made on."""

    frame = fake_camera.thermal_frame(20.0, 34.0)
    frame[40:44, 70:74] = int(raw_for(190.0))
    return frame


def mapping(streamer, scale, display=(20.0, 34.0), scene=(20.0, 190.0)):
    return streamer.ColourMapping(
        streamer.CURVE_SHAPES[scale],
        (raw_for(display[0]), raw_for(display[1])),
        (raw_for(scene[0]), raw_for(scene[1])),
    )


def rendered(streamer, palettes, scale, frames, **settings):
    renderer = streamer.ThermalRenderer(
        palettes["ironbow"],
        streamer.RenderSettings(colour_scale=scale, **{**PLAIN, **settings}),
    )
    image = None
    for frame in frames:
        image = renderer.render(frame)
    return renderer, image


def test_the_seven_scales_are_offered_and_today_is_the_default(thermal_streamer):
    assert len(thermal_streamer.VALID_COLOUR_SCALES) == 7
    assert thermal_streamer.RenderSettings().colour_scale == thermal_streamer.SCALE_TODAY
    assert set(thermal_streamer.CURVE_SHAPES) == set(thermal_streamer.VALID_COLOUR_SCALES) - {
        thermal_streamer.SCALE_TODAY, thermal_streamer.SCALE_OPTION_A
    }


def test_today_and_option_a_draw_the_same_picture_and_the_curves_do_not(
    thermal_streamer, palettes
):
    frames = [hot_scene()]
    _, today = rendered(thermal_streamer, palettes, "today", frames)
    _, option_a = rendered(thermal_streamer, palettes, "option-a", frames)
    assert np.array_equal(today, option_a)
    for scale in thermal_streamer.CURVE_SHAPES:
        _, curved = rendered(thermal_streamer, palettes, scale, frames)
        assert not np.array_equal(today, curved), scale


def test_option_a_draws_its_ruler_over_the_colours(thermal_streamer, palettes):
    renderer, _ = rendered(thermal_streamer, palettes, "option-a", [hot_scene()])
    stats = renderer.render_image(hot_scene())[1]

    overlay = renderer.overlay_for(stats)

    assert overlay.fixed_range is True
    assert overlay.mapping is None


def test_a_curve_colours_each_pixel_as_its_mapping_says(thermal_streamer, palettes):
    frame = hot_scene()
    renderer, image = rendered(thermal_streamer, palettes, "log-strong", [frame])

    used = renderer.overlay_for(renderer.render_image(frame)[1]).mapping

    assert np.array_equal(image, palettes["ironbow"][used.indices(frame)])


@pytest.mark.parametrize("scale", ["linear", "log-mild", "log-strong", "knee", "knee-soft"])
def test_the_table_draws_what_the_curve_would(thermal_streamer, scale):
    """Looked up rather than worked out, and never more than one step of the palette off."""

    curve = mapping(thermal_streamer, scale)
    frame = hot_scene()
    frame[0, :8] = [0, 1, int(raw_for(-40.0)), int(raw_for(19.9)), int(raw_for(34.0)),
                    int(raw_for(189.9)), int(raw_for(400.0)), 65535]

    direct = (curve.fractions(frame) * 255).astype(np.int16)
    looked_up = curve.indices(frame).astype(np.int16)

    assert looked_up.dtype == np.int16
    assert np.abs(looked_up - direct).max() <= 1


def test_a_narrow_scene_gets_a_table_finer_than_a_count(thermal_streamer):
    curve = mapping(thermal_streamer, "log-strong", display=(20.0, 20.5), scene=(20.0, 21.0))
    frame = np.linspace(raw_for(19.0), raw_for(22.0), 19_200).astype(np.uint16).reshape(120, 160)

    direct = (curve.fractions(frame) * 255).astype(np.int16)

    assert np.abs(curve.indices(frame).astype(np.int16) - direct).max() <= 1


def test_the_knee_gives_the_stretch_its_share_of_the_palette(thermal_streamer):
    knee = mapping(thermal_streamer, "knee")
    bend = np.array([raw_for(34.0)], dtype=np.float32)

    assert knee.fractions(bend)[0] == pytest.approx(KNEE_SHARE)
    assert knee.fractions(np.array([raw_for(190.0)], dtype=np.float32))[0] == pytest.approx(1.0)
    assert knee.fractions(np.array([raw_for(10.0)], dtype=np.float32))[0] == 0.0


def test_a_log_curve_gives_the_cool_end_more_than_a_straight_line_would(thermal_streamer):
    cool = np.array([raw_for(34.0)], dtype=np.float32)
    shares = {
        scale: float(mapping(thermal_streamer, scale).fractions(cool)[0])
        for scale in ("linear", "log-mild", "log-strong")
    }

    assert shares["linear"] < shares["log-mild"] < shares["log-strong"]


@pytest.mark.parametrize("scale", ["linear", "log-mild", "log-strong", "knee", "knee-soft"])
@pytest.mark.parametrize("fraction", [0.0, 0.1, 0.5, 0.8, 0.85, 0.9, 1.0])
def test_the_ruler_and_the_picture_agree_at_every_row(thermal_streamer, scale, fraction):
    """The count the ruler labels a row with is the count the picture draws in that row's colour."""

    curve = mapping(thermal_streamer, scale)
    counts = np.array([curve.raw_at(fraction)], dtype=np.float32)

    assert curve.fractions(counts)[0] == pytest.approx(fraction, abs=1e-4)


def test_the_ruler_ticks_the_bend_or_the_middle(thermal_streamer):
    assert mapping(thermal_streamer, "knee").ticks == (KNEE_SHARE,)
    assert mapping(thermal_streamer, "log-mild").ticks == (0.5,)
    bend = thermal_streamer.celsius_for_raw(raw_for(34.0))
    assert mapping(thermal_streamer, "knee").celsius_at(KNEE_SHARE) == pytest.approx(bend, abs=0.01)


def test_a_curved_ruler_is_the_whole_palette_and_carries_its_tick(thermal_streamer, palettes):
    frame = hot_scene()
    renderer, _ = rendered(thermal_streamer, palettes, "knee", [frame])
    overlay = renderer.overlay_for(renderer.render_image(frame)[1])
    picture = Image.new("RGB", (640, 480))
    style = thermal_streamer.overlay_style(picture.size)

    placed = thermal_streamer.draw_curved_colorbar(picture, overlay, style, overlay.mapping)

    left, top, width, height = style.bar_box
    column = np.asarray(picture)[top + 1:top + height - 1, left + width // 2]
    assert len({tuple(pixel) for pixel in column}) > height // 2
    assert len(placed) == 1
    assert placed[0][2] < left


def test_the_scene_eases_rather_than_jumping(thermal_streamer, palettes):
    cool = fake_camera.thermal_frame(20.0, 34.0)
    renderer, _ = rendered(thermal_streamer, palettes, "linear", [cool, hot_scene()])

    eased = renderer.overlay_for(renderer.render_image(hot_scene())[1]).mapping

    assert raw_for(34.0) < eased.scene[1] < raw_for(190.0)


def test_a_held_range_ignores_the_curve(thermal_streamer, palettes):
    held = {"range_mode": thermal_streamer.FIXED_RANGE}
    _, today = rendered(thermal_streamer, palettes, "today", [hot_scene()], **held)
    renderer, curved = rendered(thermal_streamer, palettes, "log-strong", [hot_scene()], **held)

    assert np.array_equal(today, curved)
    assert renderer.overlay_for(renderer.render_image(hot_scene())[1]).mapping is None


def test_a_told_scene_holds_a_curve_under_a_held_range(thermal_streamer, palettes):
    settings = thermal_streamer.RenderSettings(
        colour_scale="linear", range_mode=thermal_streamer.FIXED_RANGE, **PLAIN
    )
    told = (raw_for(20.0), raw_for(250.0))
    renderer = thermal_streamer.ThermalRenderer(palettes["ironbow"], settings, told)

    used = renderer.overlay_for(renderer.render_image(hot_scene())[1]).mapping

    assert used.scene == told


def test_the_form_and_json_take_a_scale_and_refuse_an_unknown_one(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings()

    _, posted = thermal_streamer.settings_from_form(
        {"colour_scale": ["knee"]}, palettes, current
    )
    _, sent = thermal_streamer.settings_from_json({"colour_scale": "log-mild"}, palettes, current)
    _, refused = thermal_streamer.settings_from_json(
        {"colour_scale": "sideways"}, palettes, current
    )

    assert posted.colour_scale == "knee"
    assert sent.colour_scale == "log-mild"
    assert refused.colour_scale == "today"


def test_the_page_offers_every_scale(thermal_streamer, palettes):
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)

    page = thermal_streamer.render_control_page(store.as_dict(), list(palettes))

    assert 'name="colour_scale"' in page
    for scale in thermal_streamer.VALID_COLOUR_SCALES:
        assert f'value="{scale}"' in page


def recording_with(streamer, root, hottest):
    recording = streamer.Recording.create(root, STARTED, None, "cube.gcode")
    for layer, top in enumerate(hottest, start=1):
        recording.append(streamer.LayerRecord(
            layer, streamer.FRAME_RECORD, "high", STARTED + layer,
            fake_camera.thermal_frame(22.0, top),
        ))
    recording.finish("complete")
    return recording


def clip_inputs(streamer, palettes, mode):
    return streamer.ClipInputs(
        palettes["ironbow"],
        streamer.RenderSettings(colour_scale="log-strong"),
        dataclasses.replace(streamer.TimelapseSettings(), timelapse_range_mode=mode),
        None,
    )


def test_a_clip_with_the_prints_own_range_curves_over_the_whole_print(
    thermal_streamer, palettes, tmp_path
):
    recording = recording_with(thermal_streamer, tmp_path, [40.0, 180.0, 60.0])

    for mode in (thermal_streamer.TIMELAPSE_RANGE_FROM_START,
                 thermal_streamer.TIMELAPSE_RANGE_WHOLE_PRINT):
        low, high = thermal_streamer.clip_scene(
            recording, clip_inputs(thermal_streamer, palettes, mode)
        )
        assert low == pytest.approx(raw_for(22.0), abs=1)
        assert high == pytest.approx(raw_for(180.0), abs=1)
    for mode in (thermal_streamer.TIMELAPSE_RANGE_FIXED,
                 thermal_streamer.TIMELAPSE_RANGE_AS_DISPLAYED):
        assert thermal_streamer.clip_scene(
            recording, clip_inputs(thermal_streamer, palettes, mode)
        ) is None


def test_a_clip_frame_is_stamped_with_its_scale(thermal_streamer):
    picture = Image.new("RGB", (640, 480))

    thermal_streamer.stamp_scale(picture, "knee-soft")

    corner = np.asarray(picture)[400:, :200]
    assert corner.any()
    assert not np.asarray(picture)[:80].any()


def test_a_finished_print_with_temperatures_can_be_made_again(
    thermal_streamer, palettes, tmp_path
):
    service = service_at(thermal_streamer, tmp_path, palettes)
    with_frames = recording_with(thermal_streamer, tmp_path, [40.0])
    without = thermal_streamer.Recording.create(tmp_path, STARTED + 60, None, "empty.gcode")
    without.finish("complete")

    assert service.remake(with_frames.recording_id)
    assert not service.remake(without.recording_id)
    assert not service.remake("not-a-recording")


def test_a_clip_is_named_for_its_scale(thermal_streamer, tmp_path):
    recording = recording_with(thermal_streamer, tmp_path, [40.0])
    unmade = recording.clip_name

    recording.note_clip({"made_at": STARTED, "frames": 1, "scale": "log-mild"})

    assert unmade.endswith("_thermal.mp4")
    assert recording.clip_name == unmade.replace("_thermal.mp4", "_thermal_log-mild.mp4")
    assert recording.summary()["scale"] == "log-mild"


def test_a_clip_made_again_goes_back_under_the_name_it_was_given(thermal_streamer, tmp_path):
    recording = recording_with(thermal_streamer, tmp_path, [40.0])
    assert thermal_streamer.published_base(recording) is None

    recording.note_clip({"published": {"files": ["Cube_2026_thermal_knee.mp4"]}})
    assert thermal_streamer.published_base(recording) == "Cube_2026"

    recording.note_clip({"published": {"files": ["x_thermal.mp4"], "base": "Firmware_Name"}})
    assert thermal_streamer.published_base(recording) == "Firmware_Name"

    recording.note_clip({"published": {"error": "Not copied"}})
    assert thermal_streamer.published_base(recording) is None


def service_at(streamer, root, palettes):
    return streamer.TimelapseService(
        streamer.TimelapseWiring(
            root=root, client=streamer.MoonrakerClient("http://127.0.0.1:9"),
            settings_store=streamer.SettingsStore("ironbow", streamer.RenderSettings(), None),
            tap=streamer.FrameTap(), streaming=lambda: True, palettes=palettes, ffmpeg=None,
        )
    )


def test_a_print_waiting_for_its_clip_is_busy_until_it_is_made(
    thermal_streamer, palettes, tmp_path
):
    service = service_at(thermal_streamer, tmp_path, palettes)
    recording = recording_with(thermal_streamer, tmp_path, [40.0])

    assert service.remake(recording.recording_id)
    (waiting,) = service.summaries()
    assert waiting["busy"] is True
    assert not service.remake(recording.recording_id)
    assert not service.delete(recording.recording_id)

    service.make_clip_now(recording.recording_id)

    (made,) = service.summaries()
    assert made["busy"] is False


def clip_summary(**changes):
    summary = {
        "id": "20260921-141320-0002F1", "name": "cube", "filename": "cube.gcode",
        "started_at": STARTED, "state": "complete", "frames": 3, "has_clip": True,
        "clip_bytes": 1000, "has_frames": True, "error": None, "published_as": None,
        "publish_error": None, "scale": "knee",
    }
    return {**summary, **changes}


def test_the_list_offers_to_make_a_clip_again_only_when_it_can(thermal_streamer):
    offered = thermal_streamer.clip_entry(clip_summary())
    recording_now = thermal_streamer.clip_entry(clip_summary(state="printing"))
    no_frames = thermal_streamer.clip_entry(clip_summary(has_frames=False))

    assert 'name="remake"' in offered
    assert "Scale: knee." in offered
    assert 'name="remake"' not in recording_now
    assert 'name="remake"' not in no_frames


def test_the_list_offers_nothing_for_a_print_being_made_into_a_clip(thermal_streamer):
    busy = thermal_streamer.clip_entry(clip_summary(busy=True))

    assert 'name="remake"' not in busy
    assert 'name="delete"' not in busy
    assert "Being made into a clip." in busy


def test_the_page_button_asks_for_a_clip_again(thermal_streamer, palettes, tmp_path):
    asked = []

    class Timelapses:
        def remake(self, recording_id):
            asked.append(recording_id)
            return True

        def status_line(self):
            return "On."

        def summaries(self):
            return []

    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), thermal_streamer.LatestFrame(), store, palettes
    )
    server.timelapses = Timelapses()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5.0)
        connection.request("POST", "/timelapses", body="remake=20260921-141320-0002F1",
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
        status = connection.getresponse().status
        connection.close()
    finally:
        server.shutdown()
        server.server_close()

    assert status == 303
    assert asked == ["20260921-141320-0002F1"]
