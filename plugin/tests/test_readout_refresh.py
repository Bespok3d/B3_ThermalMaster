# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The readout drawn a few times a second and pasted on the pictures in between.

Drawing the ruler and the markers is 3.01 ms of every frame on the U1 and pasting them from
patches is 0.59 ms (F-78, benched on 2026-11-14). The maker's own phone app refreshes its
pointers and ruler about once a second, which is where the maintainer took the idea from.

The tests are about what is held and what is not: the pasted readout is the drawn one, a held
readout stands still for exactly as many pictures as it should, a change of settings shows on
the next picture, a clip never holds one, and "every frame" is the picture it always was. What
it saves is the printer's to say.
"""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
from fake_camera import thermal_frame
from PIL import Image

PICTURE_SIZE = (240, 320)
# Where a soft edge was composited once into a patch and then onto the picture, instead of
# straight onto the picture: off by rounding, which the bench on the U1 put at 1 of 255.
ROUNDING = 1


def measured(thermal_streamer, palettes, settings):
    """What a frame measured, and the overlay a renderer with these settings would draw for it."""

    renderer = thermal_streamer.ThermalRenderer(palettes["ironbow"], settings)
    _, stats, _ = renderer.render_image(thermal_frame(20.0, 210.0))
    return renderer.overlay_for(stats)


def scene_picture() -> Image.Image:
    """A picture with something in every pixel, so a patch's soft edges have colour under them."""

    across = np.linspace(0, 255, PICTURE_SIZE[0], dtype=np.uint8)
    down = np.linspace(255, 0, PICTURE_SIZE[1], dtype=np.uint8)
    red = np.tile(across, (PICTURE_SIZE[1], 1))
    green = np.tile(down.reshape(-1, 1), (1, PICTURE_SIZE[0]))
    return Image.fromarray(np.dstack([red, green, 255 - red]), mode="RGB")


def furthest_apart(one: Image.Image, other: Image.Image) -> int:
    return int(np.abs(np.asarray(one, dtype=np.int16) - np.asarray(other, dtype=np.int16)).max())


def held_source(thermal_streamer, **changed):
    settings = thermal_streamer.RenderSettings(**changed)
    store = thermal_streamer.SettingsStore("ironbow", settings, None)
    return store, thermal_streamer.RendererSource(store, thermal_streamer.build_palettes())


def count_drawings(thermal_streamer, monkeypatch) -> list[tuple[int, int]]:
    """Every time the readout is drawn into patches from here on, by the size it was drawn at."""

    import thermal_master.overlay as overlay_module

    drawn: list[tuple[int, int]] = []
    draw = overlay_module.readout_patches

    def counted(size, overlay):
        drawn.append(size)
        return draw(size, overlay)

    monkeypatch.setattr(overlay_module, "readout_patches", counted)
    return drawn


def test_the_readout_is_refreshed_twice_a_second_until_somebody_says_otherwise(thermal_streamer):
    """The maintainer's choice after 0.30.6 was measured on the U1: nearly all of the saving."""

    assert thermal_streamer.RenderSettings().readout_refresh == 2
    assert thermal_streamer.VALID_READOUT_REFRESHES == (0, 5, 2, 1)


def test_a_live_view_nobody_has_set_up_holds_its_readout(thermal_streamer, monkeypatch):
    drawn = count_drawings(thermal_streamer, monkeypatch)
    _, source = held_source(thermal_streamer)

    for _ in range(25):
        source.current().render_frame(thermal_frame())

    # Every twelfth picture: the first, the thirteenth and the twenty-fifth.
    assert len(drawn) == 3


@pytest.mark.parametrize(
    "pictures_a_second, refreshes_a_second, pictures",
    [(25, 5, 5), (25, 2, 12), (25, 1, 25), (20, 5, 4), (15, 2, 7), (10, 1, 10), (5, 2, 2)],
)
def test_a_refresh_lasts_the_pictures_between_two_of_them(
    thermal_streamer, pictures_a_second, refreshes_a_second, pictures
):
    """Rounded down, so the readout is never refreshed less often than was asked."""

    lasting = thermal_streamer.pictures_a_refresh(pictures_a_second, refreshes_a_second)

    assert lasting == pictures


def test_a_refresh_asked_for_oftener_than_the_pictures_is_every_picture(thermal_streamer):
    assert thermal_streamer.pictures_a_refresh(5, 5) == 1
    assert thermal_streamer.pictures_a_refresh(2, 5) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {},
        {"spots": ((3, 4), (150, 110), (80, 60))},
        {"colour_scale": "knee"},
        {"colour_scale": "log-strong", "units": "fahrenheit"},
        {"colorbar": False},
        {"reticle": False, "hotspot": False, "coldspot": False},
    ],
)
def test_the_pasted_readout_is_the_drawn_one(thermal_streamer, palettes, changed):
    """To within rounding on the soft edges of the text, on every pixel of the picture."""

    overlay = measured(thermal_streamer, palettes, thermal_streamer.RenderSettings(**changed))
    drawn, pasted = scene_picture(), scene_picture()

    thermal_streamer.draw_overlay(drawn, overlay)
    patches = thermal_streamer.readout_patches(PICTURE_SIZE, overlay)
    thermal_streamer.paste_readout(pasted, patches)

    assert furthest_apart(drawn, pasted) <= ROUNDING
    assert furthest_apart(pasted, scene_picture()) > ROUNDING


def test_the_patches_are_a_small_part_of_the_picture(thermal_streamer, palettes):
    """Which is the point of them: a paste costs by the pixels it covers."""

    overlay = measured(thermal_streamer, palettes, thermal_streamer.RenderSettings())

    patches = thermal_streamer.readout_patches(PICTURE_SIZE, overlay)
    covered = sum(patch.picture.size[0] * patch.picture.size[1] for patch in patches)

    assert len(patches) == 4
    assert covered < PICTURE_SIZE[0] * PICTURE_SIZE[1] / 3


def test_a_readout_with_nothing_switched_on_is_no_patches(thermal_streamer, palettes):
    settings = thermal_streamer.RenderSettings()
    overlay = measured(thermal_streamer, palettes, settings)
    overlay.colorbar = overlay.reticle = overlay.hotspot = overlay.coldspot = False

    assert thermal_streamer.readout_patches(PICTURE_SIZE, overlay) == []


def test_a_held_readout_is_drawn_once_and_pasted_until_its_pictures_are_used_up(
    thermal_streamer, palettes, monkeypatch
):
    drawn = count_drawings(thermal_streamer, monkeypatch)
    overlay = measured(thermal_streamer, palettes, thermal_streamer.RenderSettings())
    held = thermal_streamer.HeldReadout(5)

    for _ in range(11):
        held.draw(scene_picture(), overlay)

    # The first, the sixth and the eleventh.
    assert len(drawn) == 3


def test_what_is_pasted_in_between_is_the_readout_as_it_was(thermal_streamer, palettes):
    """The numbers hold: a hotter scene a picture later still reads what the refresh read."""

    settings = thermal_streamer.RenderSettings()
    cooler = measured(thermal_streamer, palettes, settings)
    hotter = thermal_streamer.ThermalRenderer(palettes["ironbow"], settings)
    hotter_overlay = hotter.overlay_for(hotter.render_image(thermal_frame(90.0, 300.0))[1])
    held = thermal_streamer.HeldReadout(5)
    first, second, fresh = scene_picture(), scene_picture(), scene_picture()

    held.draw(first, cooler)
    held.draw(second, hotter_overlay)
    thermal_streamer.draw_overlay(fresh, hotter_overlay)

    assert first.tobytes() == second.tobytes()
    assert furthest_apart(second, fresh) > ROUNDING


def test_a_picture_of_another_size_is_a_refresh(thermal_streamer, palettes, monkeypatch):
    """The other camera plugged in changes no setting, and a patch belongs to one size."""

    drawn = count_drawings(thermal_streamer, monkeypatch)
    overlay = measured(thermal_streamer, palettes, thermal_streamer.RenderSettings())
    held = thermal_streamer.HeldReadout(25)

    held.draw(scene_picture(), overlay)
    held.draw(Image.new("RGB", (320, 240)), overlay)

    assert drawn == [PICTURE_SIZE, (320, 240)]


def test_the_live_view_holds_its_readout_for_the_pictures_the_setting_says(
    thermal_streamer, monkeypatch
):
    drawn = count_drawings(thermal_streamer, monkeypatch)
    _, source = held_source(thermal_streamer, readout_refresh=1, live_view_rate=10)

    for _ in range(20):
        source.current().render_frame(thermal_frame())

    assert len(drawn) == 2


def test_every_frame_is_the_picture_it_always_was(thermal_streamer, palettes, monkeypatch):
    """Byte for byte, and without a patch being made."""

    drawn = count_drawings(thermal_streamer, monkeypatch)
    _, source = held_source(thermal_streamer, readout_refresh=0)
    plain = thermal_streamer.ThermalRenderer(
        palettes["ironbow"], thermal_streamer.RenderSettings()
    )

    for _ in range(3):
        assert (
            source.current().render_frame(thermal_frame()).jpeg
            == plain.render_frame(thermal_frame()).jpeg
        )
    assert drawn == []


def test_a_held_picture_is_the_drawn_one_but_for_the_jpeg_of_a_rounding(
    thermal_streamer, palettes
):
    _, source = held_source(thermal_streamer, readout_refresh=1)
    plain = thermal_streamer.ThermalRenderer(
        palettes["ironbow"], thermal_streamer.RenderSettings()
    )

    held = Image.open(io.BytesIO(source.current().render_frame(thermal_frame()).jpeg))
    drawn = Image.open(io.BytesIO(plain.render_frame(thermal_frame()).jpeg))

    assert held.size == drawn.size
    # Decoded JPEGs, so a rounding in one pixel is spread over its block.
    assert furthest_apart(held.convert("RGB"), drawn.convert("RGB")) <= 16


def test_a_change_of_settings_shows_on_the_next_picture(thermal_streamer, monkeypatch):
    """A spot just placed must not wait a second to appear."""

    drawn = count_drawings(thermal_streamer, monkeypatch)
    store, source = held_source(thermal_streamer, readout_refresh=1)
    source.current().render_frame(thermal_frame())
    source.current().render_frame(thermal_frame())

    _, palette_name, settings = store.snapshot()
    store.update(palette_name, thermal_streamer.RenderSettings(
        readout_refresh=settings.readout_refresh, spots=((40, 30),)
    ))
    source.current().render_frame(thermal_frame())

    assert len(drawn) == 2


def test_coming_back_from_a_silence_is_a_fresh_readout(thermal_streamer, monkeypatch):
    drawn = count_drawings(thermal_streamer, monkeypatch)
    _, source = held_source(thermal_streamer, readout_refresh=1)
    source.current().render_frame(thermal_frame())

    source.restart()
    source.current().render_frame(thermal_frame())

    assert len(drawn) == 2


def test_a_clip_never_holds_its_readout(thermal_streamer, palettes, monkeypatch):
    """A renderer built without the live view's source draws every frame, whatever the setting."""

    drawn = count_drawings(thermal_streamer, monkeypatch)
    settings = thermal_streamer.RenderSettings(readout_refresh=1)
    for_a_clip = thermal_streamer.ThermalRenderer(palettes["ironbow"], settings)
    every_frame = thermal_streamer.ThermalRenderer(
        palettes["ironbow"], thermal_streamer.RenderSettings()
    )

    assert (
        for_a_clip.render_frame(thermal_frame()).jpeg
        == every_frame.render_frame(thermal_frame()).jpeg
    )
    assert drawn == []


def test_a_posted_form_sets_the_refresh(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings()

    _, updated = thermal_streamer.settings_from_form(
        {"readout_refresh": ["2"]}, palettes, current
    )

    assert updated.readout_refresh == 2


@pytest.mark.parametrize("posted", ["", "3", "often", "-1", "2.0"])
def test_a_refresh_that_is_not_offered_leaves_the_setting_alone(
    thermal_streamer, palettes, posted
):
    current = thermal_streamer.RenderSettings(readout_refresh=5)

    _, updated = thermal_streamer.settings_from_form(
        {"readout_refresh": [posted]}, palettes, current
    )

    assert updated.readout_refresh == 5


def test_a_json_body_sets_the_refresh_and_nothing_else(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(rotation=90, spots=((10, 20),))

    _, updated = thermal_streamer.settings_from_json({"readout_refresh": 1}, palettes, current)

    assert (updated.readout_refresh, updated.rotation) == (1, 90)
    assert updated.spots == ((10, 20),)


@pytest.mark.parametrize("posted", [False, True, 3, "5", None, 5.0])
def test_a_json_value_that_is_not_an_offered_whole_number_changes_nothing(
    thermal_streamer, palettes, posted
):
    current = thermal_streamer.RenderSettings(readout_refresh=2)

    _, updated = thermal_streamer.settings_from_json(
        {"readout_refresh": posted}, palettes, current
    )

    assert updated.readout_refresh == 2


def test_the_refresh_survives_a_restart(thermal_streamer, tmp_path):
    state_file = tmp_path / "settings.json"
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    store.update("ironbow", thermal_streamer.RenderSettings(readout_refresh=1))
    restarted = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert json.loads(state_file.read_text())["readout_refresh"] == 1
    assert restarted.snapshot()[2].readout_refresh == 1


@pytest.mark.parametrize("edited", [4, "5", None, True])
def test_a_settings_file_edited_to_a_refresh_not_offered_falls_back_to_twice_a_second(
    thermal_streamer, tmp_path, edited
):
    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "ironbow", "readout_refresh": edited}))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert store.snapshot()[2].readout_refresh == 2


def test_a_settings_file_from_before_the_setting_gets_twice_a_second(
    thermal_streamer, tmp_path
):
    """Upgrading is meant to bring the saving with it, to a file that never said otherwise."""

    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "ironbow", "rotation": 90}))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert store.snapshot()[2].readout_refresh == 2


def test_a_settings_file_that_says_every_frame_keeps_it(thermal_streamer, tmp_path):
    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "ironbow", "readout_refresh": 0}))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert store.snapshot()[2].readout_refresh == 0


def test_the_control_page_offers_every_refresh_and_marks_the_one_in_force(thermal_streamer):
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(readout_refresh=2), None
    )

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert "<span>Readout refresh</span>" in page
    assert '<option value="0">Every frame</option>' in page
    assert '<option value="5">5 times a second</option>' in page
    assert '<option value="2" selected>Twice a second</option>' in page
    assert '<option value="1">Once a second</option>' in page


def test_the_page_says_what_holds_still_and_what_does_not(thermal_streamer):
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert "The colours themselves always follow every frame." in page
    assert "timelapse clips are not affected" in page
