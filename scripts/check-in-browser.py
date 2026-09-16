#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Drive this plugin's pages in a real browser and check they do what they say.

Not part of the gate: it needs playwright and a downloaded browser, which is a lot of machinery to
ask of someone working on a printer plugin. Run it by hand after touching either page or its script.

It exists because the pages are the part of this plugin the test suite cannot really exercise. Every
server-side test passed while the calibrate button did nothing at all, twice over: a button named
"action" shadowed `form.action` in the DOM so the background post went to a bogus URL, and the
fallback from that used a plain submit, which does not carry the pressed button. Both were invisible
from Python and obvious to a browser.

The viewer raises the stakes, because there the browser is the product rather than the delivery. Its
whole job is turning a pointer position into the right pixel of a letterboxed image, and getting
that wrong produces a reading that is confidently, plausibly wrong.

    pip install playwright && playwright install chromium
    python3 scripts/check-in-browser.py
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugin"
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
    """The plugin's package, the same way the test suite loads it.

    By import rather than by path. This used to load the entry script by path, which stopped being
    the plugin when the code moved into a package (F-47) and silently kept "working" against stale
    bytecode. Nothing runs this file automatically, so nothing noticed for two releases.
    """

    sys.path.insert(0, str(PLUGIN_DIR / "files" / "lib"))
    import thermal_master

    return thermal_master


def ramp_frame(streamer):
    """A frame that is cold on the left and hot on the right.

    A gradient rather than a flat field, because the mapping being tested is positional: a flat
    frame reads the same temperature wherever the pointer is, so a transposed or mirrored lookup
    would pass. Left to right rather than top to bottom for the same reason it is not square.
    """

    import numpy as np

    width, height = 160, 120
    celsius = np.tile(np.linspace(20.0, 80.0, width, dtype="float32"), (height, 1))
    return ((celsius + 273.15) * 64).astype("uint16")


def serve(streamer):
    store = streamer.SettingsStore("ironbow", streamer.RenderSettings(), None)
    device = streamer.DeviceController(store)
    frames = streamer.LatestFrame()
    # A real encode, so the page's <img> has real dimensions to letterbox against.
    renderer = streamer.ThermalRenderer(streamer.build_palettes()["ironbow"], store.snapshot()[2])
    rendered = renderer.render_frame(ramp_frame(streamer))
    frames.publish(rendered.jpeg, rendered.stats, rendered.thermal)
    server = streamer.ThermalServer(
        ("127.0.0.1", PORT), frames, store, streamer.build_palettes(), device
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


def reading(page) -> float | None:
    text = page.inner_text("#pointer").strip()
    return float(text[:-1]) if text.endswith("C") else None


def fit_stage_to_the_picture(page) -> float:
    """Size the window so the picture exactly fills the canvas, and say how closely it does.

    The picture is letterboxed inside the canvas, so pointing at a fraction of the canvas is not
    pointing at that fraction of the picture. The first version of this recomputed where the
    picture sits, which was wrong twice over: it divided by an image that had not decoded yet, and
    even working it would have been the page's own arithmetic copied, so a mistake in the mapping
    would have been made identically on both sides and passed.

    Removing the letterbox instead means the harness needs no geometry at all. Canvas fractions are
    picture fractions, and the returned ratio is asserted so a layout change cannot quietly bring
    the letterbox back and make every reading below meaningless.
    """

    page.set_viewport_size({"width": 640, "height": 600})
    page.wait_for_timeout(200)
    measured = page.evaluate(
        """() => {
            const rect = document.getElementById("surface").getBoundingClientRect();
            return { width: rect.width, height: rect.height,
                     chrome: window.innerHeight - rect.height };
        }"""
    )
    page.set_viewport_size(
        {"width": 640, "height": int(640 * 3 / 4 + measured["chrome"])}
    )
    page.wait_for_timeout(200)
    rect = page.evaluate(
        """() => {
            const r = document.getElementById("surface").getBoundingClientRect();
            return { width: r.width, height: r.height };
        }"""
    )
    return rect["width"] / rect["height"]


def hover_fraction(page, across: float) -> None:
    """Point at a fraction of the way across the canvas, which fits the picture exactly."""

    rect = page.evaluate(
        """() => {
            const r = document.getElementById("surface").getBoundingClientRect();
            return { left: r.left, top: r.top, width: r.width, height: r.height };
        }"""
    )
    page.mouse.move(rect["left"] + rect["width"] * across, rect["top"] + rect["height"] / 2)


def run_viewer_checks(page, requests: list) -> list:
    """The viewer: does pointing at the picture report the temperature of what is under the point."""

    checks = []
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("an unwatched viewer fetches no frames", len(requests), 0))

    ratio = fit_stage_to_the_picture(page)
    checks.append(("the picture fills the canvas, so fractions mean something",
                   abs(ratio - 4 / 3) < 0.02, True))

    hover_fraction(page, 0.25)
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    cold = reading(page)
    hover_fraction(page, 0.75)
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    warm = reading(page)

    checks.append(("pointing reports a temperature", cold is not None and warm is not None, True))
    # The ramp runs 20 C to 80 C across the frame, so a quarter in is about 35 and three
    # quarters about 65. Loose bounds: this is checking the mapping, not the arithmetic.
    checks.append(("a quarter across reads about 35 C", 30 < (cold or 0) < 40, True))
    checks.append(("three quarters across reads about 65 C", 60 < (warm or 0) < 70, True))
    checks.append(("the reading follows the pointer", (warm or 0) > (cold or 0), True))
    checks.append(("the frame extremes are reported",
                   page.inner_text("#max").endswith("C"), True))

    before = len(requests)
    page.mouse.move(2, 2)
    page.wait_for_timeout(5000)
    quiet = len(requests)
    page.wait_for_timeout(2000)
    checks.append(("it stops fetching once nobody is pointing", len(requests), quiet))
    checks.append(("it was fetching while someone was", quiet > before, True))
    return checks + run_region_checks(page, requests)


def drag_across(page, start: float, end: float) -> None:
    """Drag a box over the middle band of the picture, from one fraction across to another."""

    rect = page.evaluate(
        """() => {
            const r = document.getElementById("surface").getBoundingClientRect();
            return { left: r.left, top: r.top, width: r.width, height: r.height };
        }"""
    )
    page.mouse.move(rect["left"] + rect["width"] * start, rect["top"] + rect["height"] * 0.3)
    page.mouse.down()
    page.mouse.move(rect["left"] + rect["width"] * end, rect["top"] + rect["height"] * 0.7,
                    steps=8)
    page.mouse.up()
    page.wait_for_timeout(SETTLE_MILLISECONDS)


def region_numbers(page) -> dict:
    return {
        name: page.inner_text("#region-" + name).strip()
        for name in ("max", "min", "avg")
    }


def as_celsius(text: str):
    return float(text[:-1]) if text.endswith("C") else None


def run_region_checks(page, requests: list) -> list:
    """Dragging a box: does it measure what is inside it, and does it keep measuring.

    The frame is a 20 C to 80 C ramp left to right, so a box over the left third should read roughly
    20 to 40 and one over the right third roughly 60 to 80. A box that reported the whole frame, or
    the wrong axis, or a stale frame, all fail that.
    """

    checks = []
    drag_across(page, 0.05, 0.33)
    left = region_numbers(page)
    checks.append(("a box reports its own max", 30 < (as_celsius(left["max"]) or 0) < 45, True))
    checks.append(("a box reports its own min", 18 < (as_celsius(left["min"]) or 0) < 30, True))
    checks.append(("a box reports its own average",
                   (as_celsius(left["min"]) or 0) < (as_celsius(left["avg"]) or 0)
                   < (as_celsius(left["max"]) or 99), True))

    drag_across(page, 0.67, 0.95)
    right = region_numbers(page)
    checks.append(("moving the box moves the numbers",
                   (as_celsius(right["avg"]) or 0) > (as_celsius(left["avg"]) or 0), True))

    # The whole point of a region: it keeps answering while nobody is pointing at anything.
    page.mouse.move(2, 2)
    page.wait_for_timeout(5000)
    watching = len(requests)
    page.wait_for_timeout(1500)
    checks.append(("a box keeps the frames coming with the pointer away",
                   len(requests) > watching, True))

    page.keyboard.press("Escape")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("escape clears the box", region_numbers(page)["max"], "-"))

    page.wait_for_timeout(5000)
    cleared = len(requests)
    page.wait_for_timeout(1500)
    checks.append(("and the frames stop again", len(requests), cleared))
    return checks


def run_cold_start_checks(browser, port: int) -> list:
    """Open the tile and draw a box immediately, before any frame has arrived.

    This is the case a warmed up page cannot test, and it is the realistic one: someone opens the
    tile and goes straight for the thing they wanted to measure. The first version dropped that drag
    on the floor, because it turned screen positions into pixels at the moment of the press and
    there was no frame yet to turn them against.
    """

    page = browser.new_page(viewport={"width": 640, "height": 600})
    page.goto(f"http://127.0.0.1:{port}/view", wait_until="domcontentloaded")
    rect = page.evaluate(
        """() => {
            const r = document.getElementById("surface").getBoundingClientRect();
            return { left: r.left, top: r.top, width: r.width, height: r.height };
        }"""
    )
    page.mouse.move(rect["left"] + rect["width"] * 0.1, rect["top"] + rect["height"] * 0.3)
    page.mouse.down()
    page.mouse.move(rect["left"] + rect["width"] * 0.4, rect["top"] + rect["height"] * 0.7, steps=4)
    page.mouse.up()
    page.wait_for_timeout(SETTLE_MILLISECONDS * 2)
    reported = page.inner_text("#region-max").strip()
    page.close()
    return [("a box drawn before the first frame still lands", reported.endswith("C"), True)]


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed. See this file's docstring.")
        raise SystemExit(2) from None

    install_stand_in_driver()
    streamer = load_streamer()
    _server, store, device = serve(streamer)

    problems: list = []

    def report(checks, into):
        for name, actual, expected in checks:
            ok = actual == expected
            print(f"  {name:<44s} {'ok' if ok else f'FAILED, got {actual!r}'}")
            if not ok:
                into.append(name)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: problems.append(f"page error: {error}"))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
        print("")
        print("control page")
        report(run_checks(page, store, device), problems)

        viewer = browser.new_page()
        viewer.on("pageerror", lambda error: problems.append(f"viewer page error: {error}"))
        requests: list = []
        viewer.on("request", lambda r: requests.append(r.url) if "frame.bin" in r.url else None)
        viewer.goto(f"http://127.0.0.1:{PORT}/view", wait_until="domcontentloaded")
        print("")
        print("viewer")
        report(run_viewer_checks(viewer, requests), problems)
        report(run_cold_start_checks(browser, PORT), problems)
        browser.close()
    print("")
    for problem in problems:
        print(f"  {problem}")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
