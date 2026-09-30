# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Live settings: what a posted form is allowed to do, and what survives a restart.

This is the one surface a stranger on the network can write to, so most of these are about what it
refuses rather than what it accepts.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def store(thermal_streamer, tmp_path):
    settings = thermal_streamer.RenderSettings()
    return thermal_streamer.SettingsStore("ironbow", settings, tmp_path / "settings.json")


def test_a_new_store_reports_its_defaults(store):
    state = store.as_dict()

    assert state["palette"] == "ironbow"
    assert state["rotation"] == 0
    assert state["flip_horizontal"] is False


def test_an_update_bumps_the_revision(thermal_streamer, store):
    before, _, settings = store.snapshot()

    store.update("sepia", thermal_streamer.RenderSettings(rotation=90))
    after, palette_name, _ = store.snapshot()

    assert after > before
    assert palette_name == "sepia"


def test_settings_survive_being_reloaded(thermal_streamer, tmp_path):
    state_file = tmp_path / "settings.json"
    first = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), state_file)

    first.update("military", thermal_streamer.RenderSettings(rotation=270, flip_vertical=True))
    second = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert second.as_dict()["palette"] == "military"
    assert second.as_dict()["rotation"] == 270
    assert second.as_dict()["flip_vertical"] is True


def test_an_unreadable_settings_file_falls_back_to_defaults(thermal_streamer, tmp_path):
    """A truncated write or a hand-edit must not stop the camera from starting."""

    state_file = tmp_path / "settings.json"
    state_file.write_text("{not json at all")

    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), state_file)

    assert store.as_dict()["palette"] == "ironbow"


def test_unknown_keys_in_the_settings_file_are_ignored(thermal_streamer, tmp_path):
    """An older or newer version's file must not crash this one."""

    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "sepia", "invented_setting": 42}))

    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), state_file)

    assert store.as_dict()["palette"] == "sepia"


def test_a_store_without_a_file_still_works(thermal_streamer):
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)

    store.update("rainbow", thermal_streamer.RenderSettings())

    assert store.as_dict()["palette"] == "rainbow"


def test_a_posted_form_sets_what_it_names(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings()

    palette_name, settings = thermal_streamer.settings_from_form(
        {"palette": ["sepia"], "rotation": ["90"], "flip_horizontal": ["on"]}, palettes, current
    )

    assert palette_name == "sepia"
    assert settings.rotation == 90
    assert settings.flip_horizontal is True
    assert settings.flip_vertical is False


def test_an_unknown_palette_is_refused(thermal_streamer, palettes):
    palette_name, _ = thermal_streamer.settings_from_form(
        {"palette": ["../../etc/passwd"]}, palettes, thermal_streamer.RenderSettings()
    )

    assert palette_name is None


def test_a_rotation_that_is_not_a_quarter_turn_is_refused(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(rotation=180)

    _, settings = thermal_streamer.settings_from_form({"rotation": ["45"]}, palettes, current)

    assert settings.rotation == 180


def test_a_nonsense_rotation_does_not_raise(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(rotation=90)

    _, settings = thermal_streamer.settings_from_form({"rotation": ["banana"]}, palettes, current)

    assert settings.rotation == 90


def test_settings_a_form_does_not_mention_are_left_alone(thermal_streamer, palettes):
    """The form carries the image settings; it must not reset the encode settings with them."""

    current = thermal_streamer.RenderSettings(upscale=2, jpeg_quality=70)

    _, settings = thermal_streamer.settings_from_form({"rotation": ["90"]}, palettes, current)

    assert settings.upscale == 2
    assert settings.jpeg_quality == 70


def test_the_renderer_is_reused_until_the_settings_change(thermal_streamer, store, palettes):
    source = thermal_streamer.RendererSource(store, palettes)

    first = source.current()

    assert source.current() is first


def test_the_renderer_is_rebuilt_after_a_change(thermal_streamer, store, palettes):
    source = thermal_streamer.RendererSource(store, palettes)
    first = source.current()

    store.update("sepia", thermal_streamer.RenderSettings(rotation=90))

    assert source.current() is not first


def test_the_control_page_shows_the_current_state(thermal_streamer, store):
    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow", "sepia"])

    assert '<option value="ironbow" selected>' in page
    assert '<option value="0" selected>' in page
    assert '<option value="celsius" selected>' in page
    # The attribute in the markup, not the word anywhere on the page: the page carries a script
    # now, and that script has every right to contain the word "checked".
    # The ruler and the three markers, all on by default, and the clips' own five, also on.
    assert page.count(" checked>") == 9


def test_the_control_page_ticks_the_boxes_that_are_set(thermal_streamer, settings_dict):
    state = settings_dict(
        palette="sepia", rotation=90, flip_horizontal=True, flip_vertical=False,
        colorbar=False, reticle=False, hotspot=False, coldspot=False,
    )

    page = thermal_streamer.render_control_page(state, ["ironbow", "sepia"])

    assert '<input type="checkbox" name="flip_horizontal" checked>' in page
    assert '<input type="checkbox" name="flip_vertical">' in page


def test_the_control_page_offers_the_camera_controls(thermal_streamer, store):
    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert 'name="gain"' in page
    assert 'name="emissivity"' in page
    assert 'name="command" value="shutter"' in page


def test_the_control_page_preselects_the_stored_emissivity(thermal_streamer, settings_dict):
    page = thermal_streamer.render_control_page(
        settings_dict(emissivity=0.30), ["ironbow"], None
    )

    assert '<option value="0.30"' in page
    assert '<option value="0.30" selected' in page


def test_the_control_page_says_what_the_shutter_last_did(thermal_streamer, store):
    status = {"gain": "high", "shutter": {"state": "failed", "detail": "device disconnected"}}

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], status)

    assert "device disconnected" in page


def test_the_control_page_copes_with_no_device_at_all(thermal_streamer, store):
    """The page is served whether or not the capture side was wired up."""

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert "not available" in page


# Properties of HTMLFormElement. A named control is exposed as a property of its own form, so a
# control sharing one of these names shadows it: a button named "action" makes `form.action` return
# the button rather than the URL. That shipped, and it produced a page that reloaded and a
# calibration that was never requested, with nothing anywhere to say why.
FORM_ELEMENT_PROPERTIES = frozenset({
    "acceptCharset", "action", "autocomplete", "elements", "encoding", "enctype", "length",
    "method", "name", "noValidate", "requestSubmit", "reset", "submit", "target",
})


def form_control_names(page: str) -> set:
    import re

    return set(re.findall(r'name="([^"]+)"', page))


def test_no_form_control_shadows_a_form_property(thermal_streamer, store):
    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert not form_control_names(page) & FORM_ELEMENT_PROPERTIES


def without_comments(page: str) -> str:
    """The page with its script comments dropped, so a rule can be checked against real code.

    The comments explain the rule below, and naming the thing they warn about is how they explain
    it, so a check against the raw page fails on its own documentation.
    """

    return "\n".join(
        line for line in page.splitlines() if not line.strip().startswith("//")
    )


def test_the_page_script_does_not_read_the_shadowable_property(thermal_streamer, store):
    """Belt and braces for the rule above: the attribute is always the attribute."""

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert "form.action" not in without_comments(page)
    assert 'form.getAttribute("action")' in page


def test_the_calibrate_button_posts_the_field_the_handler_reads(thermal_streamer, store):
    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert f'name="{thermal_streamer.SHUTTER_FIELD}"' in page
    assert f'value="{thermal_streamer.SHUTTER_ACTION}"' in page


def test_the_control_page_offers_the_way_back_to_the_camera(thermal_streamer, store):
    """A tile has no browser chrome around it.

    The viewer links here, and in Fluidd that link navigates the tile itself, so a page with no way
    back strands the user on the settings until they reload the whole dashboard.
    """

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert 'href="view"' in page


def test_the_page_carries_no_absolute_paths_of_its_own(thermal_streamer, store):
    """The plugin cannot know its mount point, so every URL it emits has to be relative.

    nginx publishes the page under a prefix and strips it on the way in, so an absolute path is a
    guess. Guessing sent every settings change to the Fluidd dashboard, and made the form work only
    behind nginx and never on a direct connection to the port.
    """
    import re

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)
    referenced = re.findall(r'(?:href|src|action)="([^"]*)"', page)

    assert [url for url in referenced if url.startswith("/")] == []


def test_a_settings_file_from_before_the_split_keeps_its_choice(thermal_streamer, tmp_path):
    """Someone who turned the readout off must not have it return because a field was renamed."""

    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "ironbow", "overlay": False}))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert store.as_dict()["colorbar"] is False
    assert store.as_dict()["reticle"] is False
    assert store.as_dict()["hotspot"] is False
    assert store.as_dict()["coldspot"] is False


def test_an_old_file_that_had_it_on_stays_on(thermal_streamer, tmp_path):
    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "ironbow", "overlay": True}))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert store.as_dict()["colorbar"] is True
    assert store.as_dict()["hotspot"] is True
    assert store.as_dict()["coldspot"] is True


def test_a_leftover_overlay_key_does_not_override_the_real_ones(thermal_streamer, tmp_path):
    """A file this version wrote carries both, and may still carry the old key beside them."""

    state_file = tmp_path / "settings.json"
    state_file.write_text(
        json.dumps({"palette": "ironbow", "overlay": False, "colorbar": True, "hotspot": False})
    )

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    assert store.as_dict()["colorbar"] is True
    assert store.as_dict()["hotspot"] is False


def test_the_page_offers_both_switches(thermal_streamer, store):
    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert 'name="colorbar"' in page
    assert 'name="reticle"' in page
    assert 'name="hotspot"' in page
    assert 'name="coldspot"' in page


def test_a_settings_file_from_before_both_splits_is_carried_through_each(thermal_streamer,
                                                                        tmp_path):
    """0.8.x knew only `overlay`. It became `markers`, which became three switches."""

    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "ironbow", "overlay": False}))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    state = store.as_dict()
    assert [state["colorbar"], state["reticle"], state["hotspot"], state["coldspot"]] == [
        False, False, False, False
    ]


def test_a_settings_file_from_between_the_splits_keeps_its_markers_choice(thermal_streamer,
                                                                         tmp_path):
    """0.9.x had `colorbar` and `markers`. Only the second split applies to it."""

    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({"palette": "ironbow", "colorbar": True, "markers": False}))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    state = store.as_dict()
    assert state["colorbar"] is True
    assert [state["reticle"], state["hotspot"], state["coldspot"]] == [False, False, False]


def test_a_current_file_is_not_touched_by_either_split(thermal_streamer, tmp_path):
    state_file = tmp_path / "settings.json"
    state_file.write_text(json.dumps({
        "palette": "ironbow", "overlay": False, "markers": False,
        "colorbar": True, "reticle": False, "hotspot": True, "coldspot": False,
    }))

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    state = store.as_dict()
    assert [state["colorbar"], state["reticle"], state["hotspot"], state["coldspot"]] == [
        True, False, True, False
    ]


def test_a_saved_file_carries_no_setting_that_no_longer_exists(thermal_streamer, tmp_path):
    """What this version writes should not need migrating by the next one."""

    state_file = tmp_path / "settings.json"
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), state_file
    )

    store.update("sepia", thermal_streamer.RenderSettings())
    written = json.loads(state_file.read_text())

    assert "overlay" not in written
    assert "markers" not in written


def test_a_json_update_changes_only_what_it_names(thermal_streamer, palettes):
    """The whole reason JSON exists here: a form cannot say "just this one".

    An unticked checkbox and an absent one look identical in a form, so a form carrying only a unit
    would read as every readout switch turned off. JSON distinguishes them.
    """

    current = thermal_streamer.RenderSettings(
        colorbar=True, reticle=True, hotspot=True, coldspot=True, rotation=90
    )

    _, settings = thermal_streamer.settings_from_json(
        {"units": "fahrenheit"}, palettes, current
    )

    assert settings.units == "fahrenheit"
    assert [settings.colorbar, settings.reticle, settings.hotspot, settings.coldspot] == [
        True, True, True, True
    ]
    assert settings.rotation == 90


def test_a_json_update_can_still_switch_something_off(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(coldspot=True)

    _, settings = thermal_streamer.settings_from_json({"coldspot": False}, palettes, current)

    assert settings.coldspot is False


def test_json_validates_what_a_form_validates(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings(units="celsius", rotation=90, emissivity=0.85)

    _, settings = thermal_streamer.settings_from_json(
        {"units": "kelvin", "rotation": 45, "emissivity": 99}, palettes, current
    )

    assert settings.units == "celsius"
    assert settings.rotation == 90
    assert settings.emissivity == thermal_streamer.MAX_EMISSIVITY


def test_an_unknown_palette_is_refused_in_json_too(thermal_streamer, palettes):
    palette_name, _ = thermal_streamer.settings_from_json(
        {"palette": "../../etc/passwd"}, palettes, thermal_streamer.RenderSettings()
    )

    assert palette_name is None


def test_a_json_update_can_change_the_gain(thermal_streamer):
    current = thermal_streamer.CameraSettings(gain="high")

    assert thermal_streamer.camera_settings_from_json({"gain": "low"}, current).gain == "low"
    assert thermal_streamer.camera_settings_from_json({}, current).gain == "high"


def test_the_control_page_offers_both_ways_of_enlarging(thermal_streamer, store):
    """The one setting that trades a visible difference for printer time, so it is the user's.

    Built from the store rather than from a literal, which is what the plugin does, so a setting
    added later is present here whether or not anybody remembers this test.
    """

    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert 'name="upscale_filter"' in page
    for name in thermal_streamer.VALID_UPSCALE_FILTERS:
        assert f'value="{name}"' in page


def test_enlarging_smoothly_is_what_a_camera_does_unless_told_otherwise(thermal_streamer):
    """Upgrading must not change the picture under somebody who never asked."""

    assert thermal_streamer.RenderSettings().upscale_filter == thermal_streamer.SMOOTH_UPSCALE


def test_a_posted_filter_has_to_be_one_that_exists(thermal_streamer, palettes):
    current = thermal_streamer.RenderSettings()
    form = {"palette": ["ironbow"], "rotation": ["0"], "units": ["celsius"],
            "emissivity": ["0.95"], "upscale_filter": ["bicubic-please"]}

    _, updated = thermal_streamer.settings_from_form(form, palettes, current)

    assert updated.upscale_filter == thermal_streamer.SMOOTH_UPSCALE


def test_the_sharp_filter_is_the_cheap_one(thermal_streamer):
    """Named for what it does to the picture, and it has to reach the resize as nearest."""

    from PIL import Image

    assert thermal_streamer.resampling(thermal_streamer.SHARP_UPSCALE) is Image.Resampling.NEAREST
    assert thermal_streamer.resampling(thermal_streamer.SMOOTH_UPSCALE) is Image.Resampling.BILINEAR


def test_the_two_filters_produce_different_pictures(thermal_streamer):
    """A setting nobody can see the effect of is not a setting."""

    import numpy as np

    counts = np.full((120, 160), 19000, dtype=np.uint16)
    counts[40:80, 40:80] = 21000
    palettes = thermal_streamer.build_palettes()
    smooth = thermal_streamer.ThermalRenderer(
        palettes["ironbow"], thermal_streamer.RenderSettings()
    ).render_frame(counts).jpeg
    sharp = thermal_streamer.ThermalRenderer(
        palettes["ironbow"],
        thermal_streamer.RenderSettings(upscale_filter=thermal_streamer.SHARP_UPSCALE),
    ).render_frame(counts).jpeg

    assert smooth != sharp
