#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Drive the control page in a real browser and check that its buttons do what they say.

Not part of the gate: it needs playwright and a downloaded browser, which is a lot of machinery to
ask of someone working on a printer plugin. Run it by hand after touching the page or its script.

It exists because the page is the one part of this plugin the test suite cannot really exercise.
Every server-side test passed while the calibrate button did nothing at all, twice over: a button
named "action" shadowed `form.action` in the DOM so the background post went to a bogus URL, and
the fallback from that used a plain submit, which does not carry the pressed button. Both are
invisible from Python and obvious to a browser.

    pip install playwright && playwright install chromium
    python3 scripts/check-control-page.py
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugin"
STREAMER_PATH = PLUGIN_DIR / "files" / "bin" / "thermal-master-stream.py"
PORT = 8099
SETTLE_MILLISECONDS = 800


def install_stand_in_driver() -> None:
    """The same stand-in the test suite uses, so no camera and no USB stack are needed."""

    sys.path.insert(0, str(PLUGIN_DIR / "tests"))
    import fake_camera

    usb_package = types.ModuleType("usb")
    usb_core = types.ModuleType("usb.core")
    usb_core.find = fake_camera.find_usb_device
    usb_package.core = usb_core
    sys.modules["usb"] = usb_package
    sys.modules["usb.core"] = usb_core

    driver = types.ModuleType("p3_camera")
    driver.VID = fake_camera.THERMAL_MASTER_VENDOR_ID
    driver.COMMANDS = fake_camera.COMMANDS
    driver.Model = types.SimpleNamespace(P1="p1", P3="p3")
    driver.P3Camera = fake_camera.StandInCamera
    driver.get_model_config = fake_camera.get_model_config
    driver.raw_to_celsius = fake_camera.raw_to_celsius
    driver.raw_to_celsius_corrected = fake_camera.raw_to_celsius_corrected
    driver.EnvParams = fake_camera.EnvParams
    driver.GainMode = fake_camera.GainMode
    driver.FrameMarkerMismatchError = type("FrameMarkerMismatchError", (Exception,), {})
    sys.modules["p3_camera"] = driver


def load_streamer():
    spec = importlib.util.spec_from_file_location("thermal_stream_in_browser", STREAMER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def serve(streamer):
    store = streamer.SettingsStore("ironbow", streamer.RenderSettings(), None)
    device = streamer.DeviceController(store)
    server = streamer.ThermalServer(
        ("127.0.0.1", PORT), streamer.LatestFrame(), store, streamer.build_palettes(), device
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, store, device


def run_checks(page, store, device) -> list:
    """Each check returns its name, what happened, and whether that is what should happen."""

    navigated = {"yes": False}
    page.on("framenavigated", lambda _frame: navigated.update(yes=True))
    checks = []

    action_is_a_url = page.evaluate(
        "typeof document.getElementById('controls').action === 'string'"
    )
    checks.append(("form.action is the URL and not a control", action_is_a_url, True))

    page.click("button[value='shutter']")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("calibrate reaches the device", device.status()["shutter"]["state"], "pending"))
    checks.append(("calibrate does not reload the page", navigated["yes"], False))
    checks.append(
        ("the status line says so", "Calibration requested" in page.inner_text("#device-status"),
         True)
    )

    device.apply(sys.modules["p3_camera"].P3Camera())
    settled = device.status()["shutter"]["state"]
    page.select_option("select[name='palette']", "sepia")
    page.click("form#controls fieldset:first-of-type button[type=submit]")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("apply changes a setting", store.as_dict()["palette"], "sepia"))
    checks.append(
        ("apply does not re-fire the last button", device.status()["shutter"]["state"], settled)
    )
    return checks


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed. See this file's docstring.")
        raise SystemExit(2) from None

    install_stand_in_driver()
    streamer = load_streamer()
    _server, store, device = serve(streamer)

    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: problems.append(f"page error: {error}"))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
        print("")
        print("thermal-master control page")
        for name, actual, expected in run_checks(page, store, device):
            ok = actual == expected
            print(f"  {name:<44s} {'ok' if ok else f'FAILED, got {actual!r}'}")
            if not ok:
                problems.append(name)
        browser.close()
    print("")
    for problem in problems:
        print(f"  {problem}")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
