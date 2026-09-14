# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Log lines identify the plugin they came from.

Everything on this printer logs to the same place, and a line labelled with a name that no longer
exists sends whoever is reading it looking for the wrong plugin.

This scans source rather than behaviour, so it is the one test the split into modules had to touch:
the lines it looks for moved out of the entry script. It now reads every Python file the plugin
ships, which is stricter than what it replaced, since a stale label in any module fails it.
"""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGIN_NAME = json.loads((PLUGIN_DIR / "manifest.json").read_text())["name"]
ENTRY_SCRIPT = PLUGIN_DIR / "files" / "bin" / f"{PLUGIN_NAME}-stream.py"
PACKAGE_DIR = PLUGIN_DIR / "files" / "lib" / "thermal_master"
RETIRED_NAME = "thermal-p1"


def shipped_sources() -> dict:
    """Every Python file this plugin ships, except the vendored driver, which is not ours."""

    return {
        path.name: path.read_text()
        for path in [ENTRY_SCRIPT, *sorted(PACKAGE_DIR.glob("*.py"))]
    }


def test_the_entry_script_is_named_after_the_plugin():
    assert ENTRY_SCRIPT.is_file(), "the entry script is not where the plugin name says it is"


def test_the_package_is_where_the_entry_script_looks_for_it():
    assert (PACKAGE_DIR / "__init__.py").is_file()


def test_log_lines_carry_the_current_plugin_name():
    labels = [
        (name, line.strip())
        for name, source in shipped_sources().items()
        for line in source.splitlines()
        if "print(" in line or "serving http" in line or "capture error" in line
    ]

    assert labels, "no log lines found at all, so this test is no longer looking anywhere useful"
    for name, label in labels:
        assert RETIRED_NAME not in label, f"{name}: {label}"


def test_no_shipped_module_carries_the_retired_name():
    for name, source in shipped_sources().items():
        assert RETIRED_NAME not in source, name
