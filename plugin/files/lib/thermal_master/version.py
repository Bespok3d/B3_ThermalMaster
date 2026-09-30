# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Which version of the plugin this is, as its manifest says, for the foot of the settings page.

Read from the manifest rather than written down here, because the manifest is the release
contract and a second copy of the number is one a bump can leave behind. Bespok3d installs the
manifest beside the plugin's files, at the top of the plugin's folder (seen on the U1 on
2026-09-30), which is also where it is in this repository.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

# The plugin's own folder: this file is in files/lib/thermal_master, three below it.
PLUGIN_ROOT = Path(__file__).resolve().parents[3]


UNKNOWN_VERSION = "version unknown"


@functools.lru_cache(maxsize=1)
def plugin_version(root: Path = PLUGIN_ROOT) -> str:
    """The manifest's version, read once, or a plain admission when there is none to read."""

    try:
        version = json.loads((root / "manifest.json").read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return UNKNOWN_VERSION
    return str(version) if version else UNKNOWN_VERSION
