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
def palettes(thermal_streamer):
    return thermal_streamer.build_palettes()


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
    assert page.count("checked") == 1  # the overlay, which is on by default


def test_the_control_page_ticks_the_boxes_that_are_set(thermal_streamer):
    state = {
        "palette": "sepia",
        "rotation": 90,
        "flip_horizontal": True,
        "flip_vertical": False,
        "overlay": False,
        "units": "celsius",
        "emissivity": 0.95,
        "gain": "high",
    }

    page = thermal_streamer.render_control_page(state, ["ironbow", "sepia"])

    assert '<input type="checkbox" name="flip_horizontal" checked>' in page
    assert '<input type="checkbox" name="flip_vertical">' in page


def test_the_control_page_offers_the_camera_controls(thermal_streamer, store):
    page = thermal_streamer.render_control_page(store.as_dict(), ["ironbow"], None)

    assert 'name="gain"' in page
    assert 'name="emissivity"' in page
    assert 'name="action" value="shutter"' in page


def test_the_control_page_preselects_the_stored_emissivity(thermal_streamer):
    state = dict(
        palette="ironbow", rotation=0, flip_horizontal=False, flip_vertical=False,
        overlay=True, units="celsius", emissivity=0.30, gain="high",
    )

    page = thermal_streamer.render_control_page(state, ["ironbow"], None)

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
