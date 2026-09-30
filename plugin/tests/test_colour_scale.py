# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The four colour scales: the stretch, the knee, and the two logs.

What matters is that each curve is the one chosen on the U1, that the ruler and the picture ask
the same question and so cannot disagree, that the stretch's picture is untouched, that the fast
ways of drawing a curve land where the curve itself would, and that a held range keeps its
meaning under every scale. The clip side is here too: the print's own coldest and hottest, and
making a clip again with another scale.
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
CURVES = ["knee", "log-mild", "log-strong"]


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


def renderer_for(streamer, palettes, scale, told=None, **settings):
    return streamer.ThermalRenderer(
        palettes["ironbow"],
        streamer.RenderSettings(colour_scale=scale, **{**PLAIN, **settings}),
        told,
    )


def rendered(streamer, palettes, scale, frames, **settings):
    renderer = renderer_for(streamer, palettes, scale, **settings)
    image = None
    for frame in frames:
        image = renderer.render(frame)
    return renderer, image


def mapping_used(renderer, frame):
    return renderer.overlay_for(renderer.render_image(frame)[1]).mapping


def test_the_four_scales_are_offered_and_the_stretch_is_the_default(thermal_streamer):
    assert thermal_streamer.VALID_COLOUR_SCALES == ("stretch", "knee", "log-mild", "log-strong")
    assert thermal_streamer.RenderSettings().colour_scale == "stretch"
    assert set(thermal_streamer.CURVE_SHAPES) == set(CURVES)


@pytest.mark.parametrize(("saved", "read"), [
    ("today", "stretch"), ("option-a", "stretch"), ("linear", "stretch"),
    ("knee-soft", "stretch"), ("knee", "knee"), ("log-strong", "log-strong"), (3, "stretch"),
])
def test_a_scale_saved_by_a_test_build_reads_as_its_nearest(thermal_streamer, saved, read):
    assert thermal_streamer.known_scale(saved) == read


def test_a_saved_file_from_a_test_build_comes_back_as_the_stretch(thermal_streamer, tmp_path):
    saved = tmp_path / "settings.json"
    saved.write_text('{"colour_scale": "option-a"}')

    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), saved)

    assert store.snapshot()[2].colour_scale == "stretch"


def test_the_stretch_is_the_picture_it_always_was(thermal_streamer, palettes):
    frame = hot_scene()
    renderer, image = rendered(thermal_streamer, palettes, "stretch", [frame])
    bounds = renderer.bounds

    expected = palettes["ironbow"][thermal_streamer.normalize_to_bytes(frame, *bounds)]

    assert np.array_equal(image, expected)
    assert mapping_used(renderer, frame) is None


@pytest.mark.parametrize("scale", CURVES)
def test_every_curve_draws_a_different_picture(thermal_streamer, palettes, scale):
    _, stretch = rendered(thermal_streamer, palettes, "stretch", [hot_scene()])
    _, curved = rendered(thermal_streamer, palettes, scale, [hot_scene()])

    assert not np.array_equal(stretch, curved)


@pytest.mark.parametrize("scale", CURVES)
def test_a_curve_colours_each_pixel_as_its_mapping_says(thermal_streamer, palettes, scale):
    frame = hot_scene()
    renderer, image = rendered(thermal_streamer, palettes, scale, [frame])

    used = mapping_used(renderer, frame)

    assert np.array_equal(image, palettes["ironbow"][used.indices(frame)])


@pytest.mark.parametrize("scale", CURVES)
@pytest.mark.parametrize("scene", [(20.0, 190.0), (20.0, 21.0), (15.0, 400.0)])
def test_the_fast_way_draws_what_the_curve_would(thermal_streamer, scale, scene):
    """A table for the logs and the stretch below the bend for the knee: never a step away."""

    curve = mapping(thermal_streamer, scale, display=(scene[0], (scene[0] + scene[1]) / 2),
                    scene=scene)
    frame = np.linspace(raw_for(scene[0] - 5), raw_for(scene[1] + 5), 19_200)
    frame = frame.astype(np.uint16).reshape(120, 160)
    frame[0, :4] = [0, 1, 65534, 65535]

    direct = (curve.fractions(frame) * 255).astype(np.int16)
    fast = curve.indices(frame).astype(np.int16)

    assert np.abs(fast - direct).max() <= 1


def test_the_knee_gives_the_stretch_its_share_of_the_palette(thermal_streamer):
    knee = mapping(thermal_streamer, "knee")

    def share(celsius):
        return knee.fractions(np.array([raw_for(celsius)], dtype=np.float32))[0]

    assert share(34.0) == pytest.approx(KNEE_SHARE)
    assert share(190.0) == pytest.approx(1.0)
    assert share(10.0) == 0.0


def test_a_strong_log_gives_the_cool_end_more_than_a_gentle_one(thermal_streamer):
    cool = np.array([raw_for(34.0)], dtype=np.float32)

    gentle = mapping(thermal_streamer, "log-mild").fractions(cool)[0]
    strong = mapping(thermal_streamer, "log-strong").fractions(cool)[0]

    assert (34.0 - 20.0) / (190.0 - 20.0) < gentle < strong


@pytest.mark.parametrize("scale", CURVES)
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
    renderer, _ = rendered(thermal_streamer, palettes, "log-mild", [cool, hot_scene()])

    eased = mapping_used(renderer, hot_scene())

    assert raw_for(34.0) < eased.scene[1] < raw_for(190.0)


def held(streamer, low=20.0, high=30.0):
    return {"range_mode": streamer.FIXED_RANGE, "range_low_celsius": low,
            "range_high_celsius": high, "emissivity": 1.0}


@pytest.mark.parametrize("scale", ["log-mild", "log-strong"])
def test_a_log_over_a_held_range_keeps_to_the_held_temperatures(thermal_streamer, palettes, scale):
    renderer = renderer_for(thermal_streamer, palettes, scale, **held(thermal_streamer))

    used = mapping_used(renderer, hot_scene())

    assert used.scene == used.display
    assert used.display == pytest.approx((raw_for(20.0), raw_for(30.0)), abs=0.5)


def test_a_knee_over_a_held_range_reaches_past_it_to_the_hottest(thermal_streamer, palettes):
    renderer = renderer_for(thermal_streamer, palettes, "knee", **held(thermal_streamer))

    used = mapping_used(renderer, hot_scene())

    assert used.display == pytest.approx((raw_for(20.0), raw_for(30.0)), abs=0.5)
    assert used.scene[1] == pytest.approx(raw_for(190.0), abs=1)
    assert used.celsius_at(KNEE_SHARE) == pytest.approx(30.0, abs=0.05)


def test_the_stretch_over_a_held_range_is_unchanged(thermal_streamer, palettes):
    renderer, image = rendered(thermal_streamer, palettes, "stretch", [hot_scene()],
                               **held(thermal_streamer))
    low, high = renderer.bounds

    expected = palettes["ironbow"][thermal_streamer.normalize_to_bytes(hot_scene(), low, high)]

    assert np.array_equal(image, expected)


def test_a_clips_measured_range_curves_over_the_whole_print(thermal_streamer, palettes):
    told = thermal_streamer.ToldScene(raw_for(20.0), raw_for(250.0), range_measured=True)
    renderer = renderer_for(thermal_streamer, palettes, "log-strong", told,
                            **held(thermal_streamer))

    used = mapping_used(renderer, hot_scene())

    assert used.scene == (told.coldest, told.hottest)


def test_a_clips_held_range_keeps_a_log_to_it_and_a_knee_to_the_print(thermal_streamer, palettes):
    told = thermal_streamer.ToldScene(raw_for(20.0), raw_for(250.0), range_measured=False)
    log = renderer_for(thermal_streamer, palettes, "log-strong", told, **held(thermal_streamer))
    knee = renderer_for(thermal_streamer, palettes, "knee", told, **held(thermal_streamer))

    assert mapping_used(log, hot_scene()).scene == mapping_used(log, hot_scene()).display
    assert mapping_used(knee, hot_scene()).scene[1] == told.hottest


def test_the_form_and_json_take_a_scale_and_refuse_an_unknown_one(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings()

    _, posted = thermal_streamer.settings_from_form(
        {"colour_scale": ["knee"]}, palettes, current
    )
    _, sent = thermal_streamer.settings_from_json({"colour_scale": "log-mild"}, palettes, current)
    _, refused = thermal_streamer.settings_from_json(
        {"colour_scale": "today"}, palettes, current
    )

    assert posted.colour_scale == "knee"
    assert sent.colour_scale == "log-mild"
    assert refused.colour_scale == "stretch"


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


@pytest.mark.parametrize(("mode", "measured"), [
    ("from-start", True), ("whole-print", True), ("fixed", False),
])
def test_a_clip_tells_its_curve_the_whole_prints_coldest_and_hottest(
    thermal_streamer, palettes, tmp_path, mode, measured
):
    recording = recording_with(thermal_streamer, tmp_path, [40.0, 180.0, 60.0])

    told = thermal_streamer.clip_scene(recording, clip_inputs(thermal_streamer, palettes, mode))

    assert told.coldest == pytest.approx(raw_for(22.0), abs=1)
    assert told.hottest == pytest.approx(raw_for(180.0), abs=1)
    assert told.range_measured is measured


def test_a_clip_drawn_as_displayed_eases_its_scene_as_live_does(
    thermal_streamer, palettes, tmp_path
):
    recording = recording_with(thermal_streamer, tmp_path, [40.0])

    assert thermal_streamer.clip_scene(
        recording, clip_inputs(thermal_streamer, palettes, "as-displayed")
    ) is None


def test_a_clip_frame_is_stamped_with_its_scale_and_the_markers_keep_off_it(
    thermal_streamer, palettes
):
    frame = fake_camera.thermal_frame(20.0, 34.0)
    frame[-1, 0] = int(raw_for(5.0))
    renderer = renderer_for(thermal_streamer, palettes, "knee")
    text = thermal_streamer.stamp_text("knee")
    box = thermal_streamer.stamp_box((640, 480), text)

    stamped = np.asarray(thermal_streamer.render_layer(renderer, frame, (640, 480), text))
    plain = np.asarray(thermal_streamer.render_layer(renderer, frame, (640, 480)))

    assert text == "Colour scale: Knee"
    left, top, right, bottom = (int(value) for value in box)
    assert not np.array_equal(stamped[top:bottom, left:right], plain[top:bottom, left:right])


def test_the_clip_readout_is_its_own(thermal_streamer, palettes):
    live = thermal_streamer.RenderSettings(spots=((10, 10),))
    plain = dataclasses.replace(
        thermal_streamer.TimelapseSettings(), timelapse_colorbar=False, timelapse_reticle=False,
        timelapse_hotspot=False, timelapse_coldspot=False, timelapse_spots=False,
    )

    drawn = thermal_streamer.clip_readout(live, plain)

    assert not drawn.readout
    assert live.readout
    assert thermal_streamer.clip_readout(live, thermal_streamer.TimelapseSettings()).spots == (
        (10, 10),
    )


def test_copying_the_live_readout_sets_the_clips_to_it(thermal_streamer):
    live = thermal_streamer.RenderSettings(reticle=False, hotspot=True, coldspot=False)

    copied = thermal_streamer.copied_readout(thermal_streamer.TimelapseSettings(), live)

    assert (copied.timelapse_colorbar, copied.timelapse_reticle) == (True, False)
    assert (copied.timelapse_hotspot, copied.timelapse_coldspot) == (True, False)
    assert copied.timelapse_spots is False


def test_the_copy_button_reaches_the_clips(thermal_streamer, palettes):
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), thermal_streamer.LatestFrame(), store, palettes
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5.0)
        connection.request(
            "POST", "/settings", body='{"reticle": false, "command": "copy-live-readout"}',
            headers={"Content-Type": "application/json"},
        )
        connection.getresponse().read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()

    assert store.timelapse_snapshot().timelapse_reticle is False
    assert store.timelapse_snapshot().timelapse_colorbar is True


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
    unnamed = recording.clip_name
    recording.note_clip(
        {"made_at": STARTED, "frames": 1, "scale": "log-mild", "scale_in_name": True}
    )

    assert unmade.endswith("_thermal.mp4")
    assert unnamed == unmade
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
    assert "Colour scale: Knee." in offered
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
