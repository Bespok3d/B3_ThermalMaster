# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The timelapse's settings: what is saved, what a page is shown, and what a posted change may be.

The Moonraker key is the reason this file exists apart from the others. It is saved with the rest,
and it is the one setting that must never be sent back out: not in `/settings`, not in the page.
"""

from __future__ import annotations

import dataclasses
import json


def store_with(streamer, path=None, **timelapse):
    store = streamer.SettingsStore("ironbow", streamer.RenderSettings(), path)
    if timelapse:
        store.update_timelapse(dataclasses.replace(streamer.TimelapseSettings(), **timelapse))
    return store


def test_the_timelapse_is_off_until_switched_on(thermal_streamer):
    assert store_with(thermal_streamer).timelapse_snapshot().timelapse is False


def test_the_key_is_never_in_what_a_page_is_shown(thermal_streamer):
    shown = store_with(thermal_streamer, moonraker_api_key="a-made-up-key").as_dict()

    assert "moonraker_api_key" not in shown
    assert "a-made-up-key" not in json.dumps(shown)
    assert shown["moonraker_api_key_set"] is True


def test_the_key_is_saved_and_restored(thermal_streamer, tmp_path):
    saved = tmp_path / "settings.json"
    store_with(thermal_streamer, saved, moonraker_api_key="a-made-up-key", timelapse=True)

    restored = store_with(thermal_streamer, saved).timelapse_snapshot()

    assert restored.moonraker_api_key == "a-made-up-key"
    assert restored.timelapse is True
    assert "moonraker_api_key_set" not in json.loads(saved.read_text())


def test_a_hand_edited_file_is_validated_on_the_way_in(thermal_streamer, tmp_path):
    saved = tmp_path / "settings.json"
    saved.write_text(json.dumps({"timelapse_keep": 5000, "timelapse_range_mode": "sideways"}))

    restored = store_with(thermal_streamer, saved).timelapse_snapshot()

    assert restored.timelapse_keep == thermal_streamer.MAX_TIMELAPSE_KEEP
    assert restored.timelapse_range_mode == thermal_streamer.DEFAULT_TIMELAPSE_RANGE


def test_json_changes_only_what_it_names(thermal_streamer):
    current = dataclasses.replace(thermal_streamer.TimelapseSettings(), timelapse=True,
                                  moonraker_api_key="a-made-up-key")

    updated = thermal_streamer.timelapse_settings_from_json({"timelapse_keep": 3}, current)

    assert updated == dataclasses.replace(current, timelapse_keep=3)


def test_json_is_validated(thermal_streamer):
    current = thermal_streamer.TimelapseSettings()

    updated = thermal_streamer.timelapse_settings_from_json(
        {"timelapse_keep": 0, "timelapse_range_mode": "sideways", "moonraker_api_key": 42},
        current,
    )

    assert updated.timelapse_keep == thermal_streamer.MIN_TIMELAPSE_KEEP
    assert updated.timelapse_range_mode == current.timelapse_range_mode
    assert updated.moonraker_api_key == ""


def test_an_empty_json_key_forgets_it(thermal_streamer):
    current = dataclasses.replace(thermal_streamer.TimelapseSettings(),
                                  moonraker_api_key="a-made-up-key")

    assert thermal_streamer.timelapse_settings_from_json(
        {"moonraker_api_key": ""}, current
    ).moonraker_api_key == ""


def form(**fields):
    return {name: [value] for name, value in fields.items()}


def test_a_form_without_the_box_is_off(thermal_streamer):
    current = dataclasses.replace(thermal_streamer.TimelapseSettings(), timelapse=True)

    assert not thermal_streamer.timelapse_settings_from_form(form(), current).timelapse


def test_a_form_reads_every_timelapse_field(thermal_streamer):
    updated = thermal_streamer.timelapse_settings_from_form(
        form(timelapse="on", timelapse_keep="4", timelapse_range_mode="fixed",
             timelapse_range_low_celsius="30", timelapse_range_high_celsius="90"),
        thermal_streamer.TimelapseSettings(),
    )

    assert (updated.timelapse, updated.timelapse_keep, updated.timelapse_range_mode) == (
        True, 4, "fixed"
    )
    assert (updated.timelapse_range_low_celsius, updated.timelapse_range_high_celsius) == (
        30.0, 90.0
    )


def test_an_empty_key_box_leaves_the_saved_key_alone(thermal_streamer):
    """A password box is never filled in from the page, so empty is not a request to clear."""

    current = dataclasses.replace(thermal_streamer.TimelapseSettings(),
                                  moonraker_api_key="a-made-up-key")

    kept = thermal_streamer.timelapse_settings_from_form(form(moonraker_api_key=""), current)
    typed = thermal_streamer.timelapse_settings_from_form(form(moonraker_api_key=" new-key "),
                                                          current)
    forgotten = thermal_streamer.timelapse_settings_from_form(
        form(command=thermal_streamer.FORGET_KEY_ACTION), current
    )

    assert kept.moonraker_api_key == "a-made-up-key"
    assert typed.moonraker_api_key == "new-key"
    assert forgotten.moonraker_api_key == ""


def test_the_page_has_the_timelapse_section(thermal_streamer, settings_dict):
    page = thermal_streamer.render_control_page(settings_dict(), ["ironbow"])

    assert 'name="timelapse"' in page
    assert 'name="timelapse_keep"' in page
    assert 'name="moonraker_api_key"' in page
    assert "No timelapses yet." in page


def test_the_page_warns_about_high_sensitivity_only_with_the_timelapse_on(
    thermal_streamer, settings_dict
):
    shown = {**settings_dict(), "timelapse_auto_gain": False}
    off = thermal_streamer.render_control_page(shown, ["ironbow"])
    on = thermal_streamer.render_control_page({**shown, "timelapse": True}, ["ironbow"])

    assert "reads too low. Wide range is under Camera" not in off
    assert "reads too low. Wide range is under Camera" in on


def test_with_the_switch_on_the_page_says_what_will_happen_instead(thermal_streamer, settings_dict):
    shown = {**settings_dict(), "timelapse": True, "timelapse_auto_gain_celsius": 140.0}

    page = thermal_streamer.render_control_page(shown, ["ironbow"])

    assert "reads too low. Wide range is under Camera" not in page
    assert "when something passes 140 C" in page


def test_a_print_name_is_escaped_in_the_list(thermal_streamer, settings_dict):
    listed = {
        "status": "On.",
        "timelapses": [{
            "id": "20260921-141320", "name": "x_thermal.mp4", "filename": "<b>cube</b>.gcode",
            "started_at": 1790000000.0, "state": "complete", "frames": 4, "has_clip": True,
            "clip_bytes": 2 * 1024 * 1024, "has_frames": True, "error": None,
        }],
    }

    page = thermal_streamer.render_control_page(settings_dict(), ["ironbow"], None, None, listed)

    assert "<b>cube</b>" not in page
    assert "&lt;b&gt;cube&lt;/b&gt;" in page
    assert 'src="timelapse.mp4?id=20260921-141320"' in page
    assert "2.0 MB" in page


def test_a_clip_on_the_timelapse_page_says_so(thermal_streamer, settings_dict):
    listed = {"status": "On.", "timelapses": [{
        "id": "20260921-141320", "name": "x_thermal.mp4", "filename": "cube.gcode",
        "started_at": 1790000000.0, "state": "complete", "frames": 4, "has_clip": True,
        "clip_bytes": 1024, "has_frames": True, "error": None,
        "published_as": "cube_20260921141320_thermal.mp4", "publish_error": None,
    }]}

    page = thermal_streamer.render_control_page(settings_dict(), ["ironbow"], None, None, listed)

    assert "Also on the Timelapse page." in page


def test_the_forget_button_also_works_as_json(thermal_streamer):
    current = dataclasses.replace(thermal_streamer.TimelapseSettings(),
                                  moonraker_api_key="a-made-up-key")

    updated = thermal_streamer.timelapse_settings_from_json(
        {"command": thermal_streamer.FORGET_KEY_ACTION}, current
    )

    assert updated.moonraker_api_key == ""


def test_the_forget_button_is_greyed_out_until_there_is_a_key(thermal_streamer, settings_dict):
    shown = settings_dict()
    without = thermal_streamer.render_control_page(shown, ["ironbow"])
    with_key = thermal_streamer.render_control_page(
        {**shown, "moonraker_api_key_set": True}, ["ironbow"]
    )

    assert 'id="forget-key" disabled>' in without
    assert 'id="forget-key">' in with_key
    assert 'name="forget_moonraker_api_key"' not in without


def test_a_print_being_recorded_offers_no_delete(thermal_streamer, settings_dict):
    recording = {
        "id": "20260921-141320", "name": "x_thermal.mp4", "filename": "cube.gcode",
        "started_at": 1790000000.0, "state": "printing", "frames": 84, "has_clip": False,
        "clip_bytes": 0, "has_frames": True, "error": None,
        "published_as": None, "publish_error": None,
    }
    finished = {**recording, "state": "complete", "has_clip": True}

    printing_page = thermal_streamer.render_control_page(
        settings_dict(), ["ironbow"], None, None, {"status": "", "timelapses": [recording]}
    )
    finished_page = thermal_streamer.render_control_page(
        settings_dict(), ["ironbow"], None, None, {"status": "", "timelapses": [finished]}
    )

    assert "Recording now, 84 frames." in printing_page
    assert 'name="delete"' not in printing_page
    assert 'name="delete"' in finished_page
