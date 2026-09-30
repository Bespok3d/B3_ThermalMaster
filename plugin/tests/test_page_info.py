# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The (i) beside an option, and the version at the foot of the settings page.

The explanations are shown and hidden by the stylesheet alone, through a checkbox nobody sees, so
what is tested here is the markup that has to be right for that to work: one of each per
explanation, ids that do not collide, and no name that would make the form post them. The hover
and the click themselves are checked in a real browser by scripts/check-in-browser.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "manifest.json"


def page(streamer, palettes):
    store = streamer.SettingsStore("ironbow", streamer.RenderSettings(), None)
    return streamer.render_control_page(store.as_dict(), list(palettes))


def test_every_explanation_has_its_own_toggle_icon_and_text(thermal_streamer, palettes):
    markup = page(thermal_streamer, palettes)

    for key in thermal_streamer.INFO_TEXTS:
        assert markup.count(f'id="info-{key}"') == 1, key
        assert markup.count(f'for="info-{key}"') == 1, key
    assert markup.count('class="about"') == len(thermal_streamer.INFO_TEXTS)
    assert markup.count("<svg") == len(thermal_streamer.INFO_TEXTS)


def test_the_toggles_are_never_posted_with_the_form(thermal_streamer, palettes):
    toggles = re.findall(r'<input type="checkbox" class="info-toggle"[^>]*>', page(
        thermal_streamer, palettes
    ))

    assert toggles
    assert not any("name=" in toggle for toggle in toggles)


def test_the_explanations_are_no_longer_at_the_foot_of_the_page(thermal_streamer, palettes):
    markup = page(thermal_streamer, palettes)
    foot = markup[markup.index('<section id="timelapses">'):]

    assert "Calibration closes the camera" not in foot
    assert "Emissivity is how much" not in foot
    assert "Changes take effect immediately" in foot


def test_the_version_is_the_manifests(thermal_streamer):
    assert thermal_streamer.plugin_version() == json.loads(MANIFEST.read_text())["version"]


def test_a_plugin_without_a_manifest_says_so(thermal_streamer, tmp_path):
    assert thermal_streamer.plugin_version(tmp_path) == thermal_streamer.UNKNOWN_VERSION


def test_the_foot_of_the_page_names_the_version(thermal_streamer, palettes):
    version = json.loads(MANIFEST.read_text())["version"]

    assert f'<p class="version">Thermal Master {version}</p>' in page(thermal_streamer, palettes)
