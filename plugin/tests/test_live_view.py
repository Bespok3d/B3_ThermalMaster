# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A live view slower than the camera, and none at all with the timelapse still fed.

Making pictures is nearly all of what the plugin costs while a page is showing one: 55.0% of a
core for a P3 against 2.5% with nothing watching, measured on the U1 on 2026-11-13. So how many
pictures a second is the one setting that scales the whole of it, and "none" is for a dashboard
left open by somebody who only wants the timelapse.

The tests are about the choosing: which frames become pictures, that every frame is still read
and still offered to the timelapse, that the picture drawn at a slower rate is not built on a
stale one, and that off says so in a picture of its own. What each rate costs is the printer's
to say.
"""

from __future__ import annotations

import json
import threading

import numpy as np
import pytest
from fake_camera import StandInCamera, thermal_frame

CAMERA_SECOND = 25


class KeepingTap:
    """Stands in for the timelapse's tap, and keeps count of what it was offered."""

    def __init__(self) -> None:
        self.offered = 0

    def offer(self, counts, gain) -> None:
        self.offered += 1


def source_at(thermal_streamer, pictures_a_second: int):
    settings = thermal_streamer.RenderSettings(live_view_rate=pictures_a_second)
    store = thermal_streamer.SettingsStore("ironbow", settings, None)
    return thermal_streamer.RendererSource(store, thermal_streamer.build_palettes())


def watched_session(thermal_streamer, pictures_a_second: int, camera_frames: int, tap=None):
    """One session at this rate with somebody watching, run until the script is used up."""

    store = thermal_streamer.LatestFrame(tap)
    store.note_interest()
    camera = StandInCamera(scripted_frames=[thermal_frame() for _ in range(camera_frames)])
    with pytest.raises(Exception, match="scripted frames"):
        thermal_streamer.stream_frames(
            camera, store, source_at(thermal_streamer, pictures_a_second), threading.Event()
        )
    return store


def pictures_drawn(thermal_streamer, pictures_a_second: int, camera_frames: int) -> list[int]:
    """Which camera frames, counted from nought, a pace at this rate draws."""

    pace = thermal_streamer.LiveViewPace()
    return [frame for frame in range(camera_frames) if pace.due(pictures_a_second)]


def test_the_live_view_shows_every_frame_until_somebody_says_otherwise(thermal_streamer):
    settings = thermal_streamer.RenderSettings()

    assert settings.live_view_rate == thermal_streamer.CAMERA_FRAMES_A_SECOND == CAMERA_SECOND
    assert settings.live_view is True


def test_the_rates_offered_are_in_fives_down_to_five_and_off(thermal_streamer):
    assert thermal_streamer.VALID_LIVE_VIEW_RATES == (25, 20, 15, 10, 5, 0)
    assert thermal_streamer.LIVE_VIEW_OFF == 0


@pytest.mark.parametrize("pictures_a_second", [25, 20, 15, 10, 5])
def test_a_second_of_camera_frames_gives_the_pictures_asked_for(
    thermal_streamer, pictures_a_second
):
    drawn = pictures_drawn(thermal_streamer, pictures_a_second, 4 * CAMERA_SECOND)

    assert len(drawn) == 4 * pictures_a_second


@pytest.mark.parametrize("pictures_a_second", [25, 20, 15, 10, 5])
def test_the_first_frame_is_always_a_picture(thermal_streamer, pictures_a_second):
    """A session, or a wake from a silence, must not open on a gap."""

    assert pictures_drawn(thermal_streamer, pictures_a_second, 1) == [0]


def test_five_a_second_is_every_fifth_frame(thermal_streamer):
    assert pictures_drawn(thermal_streamer, 5, 16) == [0, 5, 10, 15]


def test_ten_a_second_is_uneven_by_one_camera_frame_and_no_more(thermal_streamer):
    """Ten does not divide twenty five, so the gaps are 80 and 120 ms in turn."""

    drawn = pictures_drawn(thermal_streamer, 10, 2 * CAMERA_SECOND)
    gaps = {later - earlier for earlier, later in zip(drawn, drawn[1:], strict=False)}

    assert gaps == {2, 3}


def test_a_slower_live_view_draws_fewer_pictures(thermal_streamer):
    store = watched_session(thermal_streamer, 5, camera_frames=11)

    assert store.published_count == 3


def test_every_frame_is_still_read_at_a_slower_rate(thermal_streamer):
    """The frames not drawn count as read, which is what the reconnect backoff looks at."""

    store = watched_session(thermal_streamer, 5, camera_frames=11)

    assert store.seen_count == 11


def test_the_timelapse_is_offered_every_frame_whatever_the_live_view_is(thermal_streamer):
    slower, off = KeepingTap(), KeepingTap()

    watched_session(thermal_streamer, 5, camera_frames=11, tap=slower)
    watched_session(thermal_streamer, thermal_streamer.LIVE_VIEW_OFF, camera_frames=11, tap=off)

    assert (slower.offered, off.offered) == (11, 11)


def test_off_draws_nothing_and_says_so(thermal_streamer):
    store = watched_session(thermal_streamer, thermal_streamer.LIVE_VIEW_OFF, camera_frames=6)

    assert store.snapshot() == thermal_streamer.live_view_off_jpeg()
    assert store.latest_stats() is None
    assert store.latest_thermal() is None


def test_off_says_so_twice_a_second_and_not_once_a_frame(thermal_streamer, monkeypatch):
    """Six frames inside one half second, so the picture of words is handed over once."""

    import time

    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)

    store = watched_session(thermal_streamer, thermal_streamer.LIVE_VIEW_OFF, camera_frames=6)

    assert thermal_streamer.OFF_REFRESH_SECONDS == 0.5
    assert store.published_count == 1
    assert store.seen_count == 6


def test_off_with_nobody_looking_publishes_nothing(thermal_streamer):
    store = thermal_streamer.LatestFrame()
    camera = StandInCamera(scripted_frames=[thermal_frame(), thermal_frame()])

    with pytest.raises(Exception, match="scripted frames"):
        thermal_streamer.stream_frames(
            camera, store, source_at(thermal_streamer, 0), threading.Event()
        )

    assert store.published_count == 0
    assert store.seen_count == 2


def test_switching_the_live_view_back_on_starts_the_picture_clean(thermal_streamer):
    """What the renderer holds is from before the live view was switched off."""

    settings_store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(live_view_rate=0), None
    )
    source = thermal_streamer.RendererSource(settings_store, thermal_streamer.build_palettes())
    restarts: list[bool] = []
    source.restart = lambda: restarts.append(True)  # type: ignore[method-assign]
    store = thermal_streamer.LatestFrame()
    store.note_interest()
    live_view = thermal_streamer.LiveView(store, source)

    live_view.take(thermal_frame())
    settings_store.update("ironbow", thermal_streamer.RenderSettings(live_view_rate=25))
    live_view.take(thermal_frame())

    assert restarts == [True]
    assert store.latest_stats() is not None


def test_a_picture_is_averaged_with_the_camera_frame_just_before_it(thermal_streamer):
    """And not with the last picture, which at five a second is a fifth of a second old."""

    cold, warm, hot = (
        thermal_frame(20.0, 30.0), thermal_frame(40.0, 50.0), thermal_frame(60.0, 70.0)
    )
    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"], thermal_streamer.RenderSettings()
    )
    renderer.render_image(cold)

    renderer.keep_undrawn(warm)
    _, _, averaged = renderer.render_image(hot)

    expected = ((hot.astype(np.uint32) + warm.astype(np.uint32)) >> 1).astype(np.uint16)
    assert np.array_equal(averaged, expected)


def test_the_loop_hands_the_frames_it_passes_over_to_the_renderer(thermal_streamer):
    source = source_at(thermal_streamer, 5)
    store = thermal_streamer.LatestFrame()
    store.note_interest()
    live_view = thermal_streamer.LiveView(store, source)
    drawn, passed_over = thermal_frame(20.0, 30.0), thermal_frame(60.0, 70.0)
    kept: list[np.ndarray] = []

    live_view.take(drawn)
    source.current().keep_undrawn = kept.append  # type: ignore[method-assign]
    live_view.take(passed_over)

    assert len(kept) == 1
    assert kept[0] is passed_over


def test_a_frame_passed_over_before_any_picture_is_let_go(thermal_streamer):
    source_at(thermal_streamer, 5).pass_over(thermal_frame())


def test_at_the_cameras_own_rate_the_renderer_gets_the_settings_untouched(thermal_streamer):
    """So a picture at 25 a second is what it was before the live view had a rate."""

    settings = thermal_streamer.RenderSettings()

    assert thermal_streamer.paced(settings) is settings


def test_the_range_eases_as_far_in_a_second_at_any_rate(thermal_streamer):
    """Five steps of the easing at 25 a second is one step at five."""

    settings = thermal_streamer.RenderSettings(live_view_rate=5)
    at_full_rate = (0.0, 0.0)
    for _ in range(5):
        at_full_rate = thermal_streamer.smooth_bounds(
            at_full_rate, (100.0, 200.0), settings.bounds_smoothing
        )

    paced = thermal_streamer.paced(settings)
    at_five = thermal_streamer.smooth_bounds((0.0, 0.0), (100.0, 200.0), paced.bounds_smoothing)

    assert at_five == pytest.approx(at_full_rate)
    assert paced.live_view_rate == 5


def test_a_posted_form_sets_the_rate(thermal_streamer, palettes):
    form = {"live_view_rate": ["10"]}

    _, updated = thermal_streamer.settings_from_form(
        form, palettes, thermal_streamer.RenderSettings()
    )

    assert updated.live_view_rate == 10


@pytest.mark.parametrize("posted", ["12", "fast", "-5", ""])
def test_a_rate_that_is_not_offered_leaves_the_setting_alone(thermal_streamer, palettes, posted):
    current = thermal_streamer.RenderSettings(live_view_rate=15)

    _, updated = thermal_streamer.settings_from_form(
        {"live_view_rate": [posted]}, palettes, current
    )

    assert updated.live_view_rate == 15


def test_a_json_body_sets_the_rate_and_can_switch_the_live_view_off(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(rotation=90)

    _, slower = thermal_streamer.settings_from_json({"live_view_rate": 20}, palettes, current)
    _, off = thermal_streamer.settings_from_json({"live_view_rate": 0}, palettes, current)

    assert (slower.live_view_rate, slower.rotation) == (20, 90)
    assert off.live_view is False


@pytest.mark.parametrize("posted", [False, True, 7, "20", None, 20.0])
def test_a_json_value_that_is_not_an_offered_whole_number_changes_nothing(
    thermal_streamer, palettes, posted
):
    """`false` equals nought in Python, and a body saying false has not asked for off."""

    current = thermal_streamer.RenderSettings(live_view_rate=15)

    _, updated = thermal_streamer.settings_from_json(
        {"live_view_rate": posted}, palettes, current
    )

    assert updated.live_view_rate == 15


def test_a_json_body_that_says_nothing_about_it_changes_nothing(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(live_view_rate=5)

    _, updated = thermal_streamer.settings_from_json({"units": "celsius"}, palettes, current)

    assert updated.live_view_rate == 5


def test_changing_the_rate_keeps_the_spots(thermal_streamer, palettes):
    """It moves nothing on the picture, so nothing a spot points at has changed."""

    current = thermal_streamer.RenderSettings(spots=((10, 20),))

    _, updated = thermal_streamer.settings_from_json({"live_view_rate": 5}, palettes, current)

    assert updated.spots == ((10, 20),)


def test_the_rate_survives_a_restart(thermal_streamer, tmp_path):
    """A reboot must not quietly bring back a cost somebody had turned down."""

    state_file = tmp_path / "settings.json"
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    store.update("ironbow", thermal_streamer.RenderSettings(live_view_rate=0))
    restarted = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert json.loads(state_file.read_text())["live_view_rate"] == 0
    assert restarted.snapshot()[2].live_view is False


@pytest.mark.parametrize("edited", [12, "5", None, False])
def test_a_settings_file_edited_to_a_rate_not_offered_falls_back_to_every_frame(
    thermal_streamer, tmp_path, edited
):
    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "ironbow", "live_view_rate": edited}))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert store.snapshot()[2].live_view_rate == CAMERA_SECOND


def test_the_control_page_offers_every_rate_and_marks_the_one_in_force(thermal_streamer):
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(live_view_rate=10), None
    )

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert 'name="live_view_rate"' in page
    for pictures_a_second in thermal_streamer.VALID_LIVE_VIEW_RATES:
        assert f'<option value="{pictures_a_second}"' in page
    assert '<option value="10" selected>10 a second</option>' in page
    assert '<option value="0">Off</option>' in page


def test_the_page_says_what_off_keeps_and_what_stopping_the_camera_does_instead(
    thermal_streamer,
):
    about, text = thermal_streamer.INFO_TEXTS["live_view"]

    assert about == "the live view"
    assert "timelapse" in text
    assert "stop it instead" in text


def test_the_picture_for_off_says_what_has_happened_and_the_way_back(thermal_streamer):
    picture = thermal_streamer.live_view_off_picture()

    assert thermal_streamer.LIVE_VIEW_OFF_TITLE == "Live view off"
    assert "timelapse" in thermal_streamer.LIVE_VIEW_OFF_HINTS[0]
    assert "settings" in thermal_streamer.LIVE_VIEW_OFF_HINTS[1]
    assert picture.size == thermal_streamer.STREAM_OFF_SIZE
    assert thermal_streamer.live_view_off_jpeg()[:2] == b"\xff\xd8"


def test_every_line_of_it_fits_inside_the_picture(thermal_streamer):
    """A line wider than the tile is cut off at both ends, which reads as a fault."""

    width, _ = thermal_streamer.STREAM_OFF_SIZE
    style = thermal_streamer.overlay_style(thermal_streamer.STREAM_OFF_SIZE, colorbar=False)

    for hint in thermal_streamer.LIVE_VIEW_OFF_HINTS:
        assert thermal_streamer.label_width(hint, style.pixel_height) < width


def test_it_is_not_the_picture_for_a_camera_switched_off(thermal_streamer):
    """Two different states with two different ways back, so two different pictures."""

    assert thermal_streamer.live_view_off_jpeg() != thermal_streamer.stream_off_jpeg()


def test_the_picture_for_off_is_encoded_once(thermal_streamer):
    assert thermal_streamer.live_view_off_jpeg() is thermal_streamer.live_view_off_jpeg()


def test_the_stream_off_picture_is_drawn_as_it_always_was(thermal_streamer):
    """Both placeholders come from one function now; the old one must not have moved a pixel."""

    from PIL import Image, ImageDraw

    size = thermal_streamer.STREAM_OFF_SIZE
    style = thermal_streamer.overlay_style(size, colorbar=False)
    title_height = max(style.pixel_height * 2, thermal_streamer.OVERLAY_MIN_FONT_PIXELS * 2)
    expected = Image.new("RGB", size, thermal_streamer.PLACEHOLDER_RGB)
    draw = ImageDraw.Draw(expected)
    title = thermal_streamer.STREAM_OFF_TITLE
    draw.text(
        ((size[0] - thermal_streamer.label_width(title, title_height)) / 2,
         size[1] / 2 - title_height),
        title,
        font=thermal_streamer.overlay_font(title_height),
        fill=thermal_streamer.OVERLAY_TEXT_RGB,
    )
    hint = thermal_streamer.STREAM_OFF_HINT
    draw.text(
        ((size[0] - thermal_streamer.label_width(hint, style.pixel_height)) / 2,
         size[1] / 2 + style.line_height / 2),
        hint,
        font=thermal_streamer.overlay_font(style.pixel_height),
        fill=(154, 160, 170),
    )

    assert thermal_streamer.stream_off_picture().tobytes() == expected.tobytes()


def health_with(thermal_streamer, settings) -> dict:
    import http.client

    store = thermal_streamer.SettingsStore("ironbow", settings, None)
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), thermal_streamer.LatestFrame(), store,
        thermal_streamer.build_palettes(), thermal_streamer.DeviceController(store),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", "/health")
        payload: dict = json.loads(connection.getresponse().read())
        connection.close()
        return payload
    finally:
        server.shutdown()
        server.server_close()


def test_health_says_when_the_live_view_is_off_and_the_camera_is_not(thermal_streamer):
    """The watchdog must leave a picture that is still on purpose alone, for the right reason."""

    payload = health_with(thermal_streamer, thermal_streamer.RenderSettings(live_view_rate=0))

    assert payload["live_view"] is False
    assert payload["streaming"] is True


def test_health_says_the_live_view_is_on_at_any_rate(thermal_streamer):
    payload = health_with(thermal_streamer, thermal_streamer.RenderSettings(live_view_rate=5))

    assert payload["live_view"] is True


def test_the_viewer_treats_the_live_view_off_as_a_picture_meant_to_be_still(thermal_streamer):
    """Neither a stream to reopen nor one to record: the same as the camera switched off."""

    page = thermal_streamer.render_viewer_page(None)

    assert "health.streaming !== false && health.live_view !== false" in page


def test_a_renderer_built_without_the_source_is_not_paced(thermal_streamer, palettes):
    """Only the live view's source paces. A clip builds its own renderer from these settings, and
    its frames are layers, eased as they always were.
    """

    frames = [thermal_frame(20.0, 30.0), thermal_frame(40.0, 60.0)]

    def bounds_after(settings):
        renderer = thermal_streamer.ThermalRenderer(palettes["ironbow"], settings)
        for frame in frames:
            renderer.render_image(frame)
        return renderer.bounds

    slower = thermal_streamer.RenderSettings(live_view_rate=5)

    assert bounds_after(slower) == bounds_after(thermal_streamer.RenderSettings())
