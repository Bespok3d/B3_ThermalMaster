# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Log lines identify the plugin they came from.

Everything on this printer logs to the same place, and a line labelled with a name that no longer
exists sends whoever is reading it looking for the wrong plugin.
"""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGIN_NAME = json.loads((PLUGIN_DIR / "manifest.json").read_text())["name"]
STREAMER_SOURCE = (PLUGIN_DIR / "files" / "bin" / f"{PLUGIN_NAME}-stream.py").read_text()


def test_the_streamer_is_named_after_the_plugin():
    assert STREAMER_SOURCE, "the streamer is not where the plugin name says it is"


def test_log_lines_carry_the_current_plugin_name():
    labels = [
        line.strip()
        for line in STREAMER_SOURCE.splitlines()
        if "print(" in line or "serving http" in line or "capture error" in line
    ]
    assert labels
    for label in labels:
        assert "thermal-p1" not in label, label
