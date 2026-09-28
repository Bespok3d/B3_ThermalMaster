#!/usr/bin/env python3
"""Capture a Thermal Master P1 or P3 over libusb and serve it as MJPEG.

This file is the service's entry point and nothing else. It exists under this name because the
manifest names it, and `thermal-master-stream` is a legal program name and an illegal module name:
nothing can import it, and mypy cannot derive a module name for it either, which is why for a long
time none of this code was type checked (F-47). The code now lives in the `thermal_master` package
beside it, and this puts the two directories it needs on the path and calls it.

Both directories go on the END of the path, never the front. The vendored driver taught that one:
at the front, anything left unpacked in the vendor directory wins against the installed package of
the same name, which on the printer's own architecture swaps a dependency silently and anywhere
else fails to load (F-50).
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_FILES = Path(__file__).resolve().parent.parent
# The upstream USB protocol driver, vendored as a source file rather than a package (VENDORING.md).
VENDOR_DIR = PLUGIN_FILES / "vendor"
# This plugin's own modules.
LIB_DIR = PLUGIN_FILES / "lib"
sys.path.append(str(LIB_DIR))
sys.path.append(str(VENDOR_DIR))

from thermal_master.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
