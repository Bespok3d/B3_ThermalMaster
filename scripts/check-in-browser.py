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
import tempfile
import threading
import time
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


def corner_marked(jpeg: bytes, shade: int) -> bytes:
    """The same picture with a small grey block of this shade in its top left corner."""

    import io

    from PIL import Image

    picture = Image.open(io.BytesIO(jpeg)).convert("RGB")
    picture.paste((shade, shade, shade), (0, 0, 24, 24))
    out = io.BytesIO()
    picture.save(out, format="JPEG", quality=90)
    return out.getvalue()


def serve(streamer):
    store = streamer.SettingsStore("ironbow", streamer.RenderSettings(), None)
    device = streamer.DeviceController(store)
    frames = streamer.LatestFrame()
    # A real encode, so the page's <img> has real dimensions to letterbox against.
    renderer = streamer.ThermalRenderer(streamer.build_palettes()["ironbow"], store.snapshot()[2])
    rendered = renderer.render_frame(ramp_frame(streamer))
    frames.publish(rendered.jpeg, rendered.stats, rendered.thermal)

    # Republished on a timer, because a real camera does. A server that publishes once looks the
    # same to most of these checks and is not the same at all to an <img> showing a multipart
    # stream, which needs parts arriving before it will decode one and report a size.
    #
    # And never the same picture twice running, because a real camera never sends one: the
    # viewer's watchdog takes a picture that has not changed for three seconds for a dead stream
    # and reopens it (Phase 7i). Only the JPEG varies, a small block in one corner, so every
    # temperature these checks read stays exactly what it was. Seven versions rather than two:
    # the watchdog samples every two seconds, twenty publications apart, and with an even count
    # it would land on the same version every time.
    variants = [corner_marked(rendered.jpeg, shade) for shade in range(0, 7 * 30, 30)]

    def keep_streaming() -> None:
        count = 0
        while True:
            time.sleep(0.1)
            frames.publish(variants[count % len(variants)], rendered.stats, rendered.thermal)
            count += 1

    threading.Thread(target=keep_streaming, daemon=True).start()
    server = streamer.ThermalServer(
        ("127.0.0.1", PORT), frames, store, streamer.build_palettes(), device
    )
    server.timelapses = timelapse_service(streamer, store)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, store, device


def timelapse_service(streamer, store):
    """A timelapse with one finished print in it, and a Moonraker that is not there."""

    root = Path(tempfile.mkdtemp(prefix="thermal-timelapse-"))
    finished = streamer.Recording.create(root, 1_790_000_000.0, "0002F1", "<cube>.gcode")
    finished.finish("complete")
    finished.clip_path.write_bytes(b"not a real clip, only its bytes")
    return streamer.TimelapseService(
        streamer.TimelapseWiring(
            root=root, client=streamer.MoonrakerClient("http://127.0.0.1:9"),
            settings_store=store, tap=streamer.FrameTap(), streaming=lambda: True,
            palettes=streamer.build_palettes(), ffmpeg=None,
        )
    )


def run_timelapse_refresh_checks(page, service) -> list:
    """A clip finished while the page is open shows up without a reload (found on the U1)."""

    streamer = sys.modules["thermal_master"]
    before = page.locator(".clip").count()
    made = streamer.Recording.create(service.root, 1_790_003_600.0, "0002F2", "later.gcode")
    made.finish("complete")
    made.clip_path.write_bytes(b"another clip's bytes")
    service._say("On. Waiting for a print to start, a clip just made.")  # noqa: SLF001
    page.wait_for_timeout(COST_POLL_WAIT_MILLISECONDS)
    return [("a finished clip appears without a reload", page.locator(".clip").count(), before + 1)]


# The settings page asks for the status line every five seconds; a little over that.
COST_POLL_WAIT_MILLISECONDS = 6500


def run_timelapse_checks(page, store) -> list:
    """The Timelapse section posts in the background like the rest, and never shows the key."""

    navigated = {"yes": False}
    page.on("framenavigated", lambda _frame: navigated.update(yes=True))
    checks = []
    checks.append(("forget is greyed out with no key", page.is_disabled("#forget-key"), True))
    page.check("input[name=timelapse]")
    page.fill("input[name=moonraker_api_key]", "a-made-up-key")
    page.click("fieldset#timelapse button[type=submit]:not([name])")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    saved = store.timelapse_snapshot()
    checks.append(("the switch reaches the plugin", saved.timelapse, True))
    checks.append(("the key reaches the plugin", saved.moonraker_api_key, "a-made-up-key"))
    checks.append(("the switch to wide range is on by default", saved.timelapse_auto_gain, True))
    page.uncheck("input[name=timelapse_auto_gain]")
    page.fill("input[name=timelapse_auto_gain_celsius]", "130")
    page.click("fieldset#timelapse button[type=submit]:not([name])")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    switched = store.timelapse_snapshot()
    checks.append(("and unticking it reaches the plugin", switched.timelapse_auto_gain, False))
    checks.append(("with its temperature", switched.timelapse_auto_gain_celsius, 130.0))
    checks.append(("without reloading the page", navigated["yes"], False))
    emptied = page.input_value("input[name=moonraker_api_key]")
    checks.append(("the key box is emptied", emptied, ""))
    checks.append((
        "and says a key is saved",
        page.get_attribute("input[name=moonraker_api_key]", "placeholder"),
        "Saved",
    ))
    checks.append(("the key is nowhere on the page", "a-made-up-key" in page.content(), False))
    checks.append(("forget is offered once there is a key", page.is_disabled("#forget-key"), False))
    page.click("#forget-key")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("forget forgets it", store.timelapse_snapshot().moonraker_api_key, ""))
    checks.append(("and greys itself out again", page.is_disabled("#forget-key"), True))
    checks.append(("still without reloading the page", navigated["yes"], False))
    checks.append(("a print name is shown as text", page.locator(".clip strong").inner_text(),
                   "<cube>.gcode"))
    page.click(".clip form:has(input[name=delete]) button")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("delete removes the print", page.locator(".clip").count(), 0))
    checks.append(("and comes back to the list", page.url.endswith("#timelapses"), True))
    return checks


def run_info_checks(page) -> list:
    """An (i) shows its text while hovered, keeps it once clicked, and drops it on a second click."""

    about = "#info-colour_scale ~ p.about"
    icon = "label[for=info-colour_scale]"
    checks = [("an explanation starts hidden", page.is_visible(about), False)]
    page.hover(icon)
    checks.append(("hovering its (i) shows it", page.is_visible(about), True))
    page.mouse.move(0, 0)
    checks.append(("and moving away hides it again", page.is_visible(about), False))
    page.click(icon)
    page.mouse.move(0, 0)
    checks.append(("clicking keeps it on the page", page.is_visible(about), True))
    page.click(icon)
    page.mouse.move(0, 0)
    checks.append(("and a second click takes it away", page.is_visible(about), False))
    checks.append(("the icon is a drawn one", page.locator(f"{icon} svg path").count(), 1))
    checks.append((
        "the version is at the foot of the page",
        page.inner_text("p.version").startswith("Thermal Master "),
        True,
    ))
    return checks


# A narrow Android phone's width in CSS pixels, the width the Timelapse panel was found too wide for.
PHONE_WIDTH = 360


def run_phone_checks(browser, port: int) -> list:
    """On a phone: nothing runs off the screen, an (i) stays beside its option, taps open and close.

    A phone keeps the last thing tapped "hovered", so the hover rule has to be for pointers that
    hover only, or a second tap unpins the text while the stuck hover keeps showing it (found on
    an Android phone on 2026-09-30). The same phone showed the Timelapse panel running off the
    right of the screen and the (i)s wrapping under their selects.
    """

    context = browser.new_context(
        viewport={"width": PHONE_WIDTH, "height": 800}, device_scale_factor=2, is_mobile=True,
        has_touch=True,
    )
    page = context.new_page()
    page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
    about = "#info-colour_scale ~ p.about"
    icon = "label[for=info-colour_scale]"
    # Every panel's right edge, not the page's scroll width: a phone zooms out to fit a page that is
    # too wide, which hides the overflow from the page's own measurements.
    widest = page.evaluate(
        "Math.max(...[...document.querySelectorAll('fieldset')]"
        ".map(f => f.getBoundingClientRect().right))"
    )
    checks = [("nothing runs off a phone's screen", widest <= PHONE_WIDTH, True)]
    select = page.locator("select[name=colour_scale]").bounding_box()
    beside = page.locator(icon).bounding_box()
    checks.append((
        "an (i) stays beside its option",
        abs((beside["y"] + beside["height"] / 2) - (select["y"] + select["height"] / 2)) < 8,
        True,
    ))
    page.tap(icon)
    checks.append(("a tap opens an explanation", page.is_visible(about), True))
    page.tap(icon)
    checks.append(("and a second tap closes it", page.is_visible(about), False))
    context.close()
    return checks


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
    checks.append((
        "every colour scale is offered",
        page.locator("select[name=colour_scale] option").count(),
        4,
    ))
    page.select_option("select[name='colour_scale']", "knee")
    page.click("form#controls fieldset:nth-of-type(2) button[type=submit]")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("the colour scale reaches the plugin", store.as_dict()["colour_scale"], "knee"))
    page.select_option("select[name='colour_scale']", "stretch")
    page.click("form#controls fieldset:nth-of-type(2) button[type=submit]")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("and back to the stretch", store.as_dict()["colour_scale"], "stretch"))
    checks += run_info_checks(page)

    # Holding the range is the first button that changes something the form is showing, and the
    # page used to ignore the answer it got back: the plugin went to a fixed range, the page went
    # on saying "follow the scene", and the next Apply posted what the page was saying and undid
    # it. Both halves are checked, because the second one is what made it a defect rather than a
    # missing flourish.
    page.click("button[value=lock-range]")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    held = store.as_dict()
    checks.append(("holding the range switches the plugin", held["range_mode"], "fixed"))
    checks.append(("and the page says so", page.input_value("select[name=range_mode]"), "fixed"))
    checks.append((
        "and the boxes hold the numbers it froze",
        float(page.input_value("input[name=range_low_celsius]")),
        held["range_low_celsius"],
    ))
    page.click("text=Apply")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("and applying again does not undo it", store.as_dict()["range_mode"], "fixed"))
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
    return checks + run_region_checks(page, requests) + run_tool_checks(page)


def canvas_rect(page) -> dict:
    return page.evaluate(
        """() => {
            const r = document.getElementById("surface").getBoundingClientRect();
            return { left: r.left, top: r.top, width: r.width, height: r.height };
        }"""
    )


def picture_rect(page) -> dict:
    """Where the picture element actually is, which the script positions itself."""

    return page.evaluate(
        """() => {
            const r = document.getElementById("feed").getBoundingClientRect();
            return { left: r.left, top: r.top, width: r.width, height: r.height };
        }"""
    )


def run_tool_checks(page) -> list:
    """Zoom, pan, units and the screenshot.

    Zoom is checked against the picture element rather than an internal number, because the failure
    that matters is the picture and the overlay disagreeing about where things are, and only the
    rendered geometry can show that.
    """

    checks = []
    fitted = picture_rect(page)
    page.click("#zoom-in")
    page.wait_for_timeout(200)
    zoomed = picture_rect(page)
    checks.append(("zooming in makes the picture bigger", zoomed["width"] > fitted["width"], True))
    checks.append(("and says so", page.inner_text("#zoom-level") != "100%", True))

    # The overlay is sized to the canvas and drawn from the same rectangle as the picture, so a
    # reading taken at the middle of the picture must still be the middle of the picture zoomed in.
    page.mouse.move(zoomed["left"] + zoomed["width"] / 2, zoomed["top"] + zoomed["height"] / 2)
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    middle_zoomed = reading(page)

    page.click("#fit")
    page.wait_for_timeout(400)
    back = picture_rect(page)
    stage = page.evaluate(
        """() => {
            const r = document.querySelector(".stage").getBoundingClientRect();
            return { width: r.width, height: r.height };
        }"""
    )
    # What Fit means, rather than what the picture happened to measure earlier. Comparing against a
    # width captured before the zoom looked obvious and was wrong: the panel's height changes as its
    # own text changes, which changes the stage, so the two measurements were of different layouts
    # and the check failed by 52 pixels for an honest reason. Fit means the picture is inside the
    # stage and touching it on one axis, and that is true whenever it is true.
    inside = back["width"] <= stage["width"] + 1 and back["height"] <= stage["height"] + 1
    touching = (
        abs(back["width"] - stage["width"]) <= 1 or abs(back["height"] - stage["height"]) <= 1
    )
    checks.append((
        "fit fills the stage without overflowing it",
        "yes" if inside and touching else
        f"picture {back['width']:.0f}x{back['height']:.0f} in stage"
        f" {stage['width']:.0f}x{stage['height']:.0f}",
        "yes",
    ))
    checks.append(("fit says one hundred percent", page.inner_text("#zoom-level"), "100%"))
    page.mouse.move(back["left"] + back["width"] / 2, back["top"] + back["height"] / 2)
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("the same spot reads the same zoomed or not",
                   abs((middle_zoomed or 0) - (reading(page) or 99)) < 3, True))

    page.click("#mode-pan")
    page.click("#zoom-in")
    page.wait_for_timeout(200)
    before_pan = picture_rect(page)
    rect = canvas_rect(page)
    page.mouse.move(rect["left"] + rect["width"] / 2, rect["top"] + rect["height"] / 2)
    page.mouse.down()
    page.mouse.move(rect["left"] + rect["width"] / 2 - 60, rect["top"] + rect["height"] / 2,
                    steps=6)
    page.mouse.up()
    page.wait_for_timeout(200)
    checks.append(("pan mode moves the picture",
                   picture_rect(page)["left"] < before_pan["left"], True))
    checks.append(("pan mode does not draw a box", page.inner_text("#region-max"), "-"))
    page.click("#fit")
    page.click("#mode-measure")

    unit = page.inner_text("#units").strip()
    page.click("#units")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    checks.append(("the units button changes the unit", page.inner_text("#units") != unit, True))
    checks.append(("and the readings follow it",
                   page.inner_text("#max").strip().endswith(page.inner_text("#units").strip()),
                   True))
    page.click("#units")
    page.wait_for_timeout(SETTLE_MILLISECONDS)

    with page.expect_download() as caught:
        page.click("#shot")
    download = caught.value
    checks.append(("saving an image offers a png",
                   download.suggested_filename.endswith(".png"), True))
    checks.append(("named for when it was taken",
                   download.suggested_filename.startswith("thermal-"), True))
    return checks


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


# What Fluidd gives an iframe tile. The portrait one is a narrow dashboard column; the landscape
# one is the shape the maintainer photographed, where the picture had been squeezed into a quarter
# of the tile and the toolbar was hidden outright.
TILE_VIEWPORT = {"width": 260, "height": 340}
WIDE_TILE_VIEWPORT = {"width": 540, "height": 400}


def run_tile_checks(browser, port: int, viewport: dict) -> list:
    """The viewer at the size Fluidd actually gives it.

    Everything else here runs in a window, and in a window the page was fine. In a tile the
    controls, laid out for a window, wrapped to three rows and took all of it: the picture flexed
    down to 41 pixels and the tile showed a toolbar and no camera. The answer then was to hide the
    toolbar below a height breakpoint, which fixed the picture and made the controls unreachable
    in the one place they are most wanted. Both halves are checked here now.
    """

    page = browser.new_page(viewport=viewport)
    page.goto(f"http://127.0.0.1:{port}/view", wait_until="domcontentloaded")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    # Measured before any pointer goes near it, because the picture is positioned by the script
    # and a page that only paints on pointermove looks perfect to every test that moves one first.
    measured = page.evaluate(
        """() => {
            const stage = document.querySelector(".stage").getBoundingClientRect();
            const picture = document.getElementById("feed").getBoundingClientRect();
            const tools = document.querySelector(".tools");
            const buttons = tools.getBoundingClientRect();
            return { stage: stage.height, picture: picture.width, area: picture.width * picture.height,
                     tools: getComputedStyle(tools).display, toolbar: buttons.height,
                     beside: document.body.classList.contains("beside") };
        }"""
    )
    rect = canvas_rect(page)
    page.mouse.move(rect["left"] + rect["width"] / 2, rect["top"] + rect["height"] / 2)
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    reads = reading(page)
    # A control the pointer can actually reach, which is the whole point of showing the toolbar.
    page.click("#zoom-in")
    zoomed = page.inner_text("#zoom-level")
    page.close()
    area = viewport["width"] * viewport["height"]
    return [
        ("a tile shows the picture before anything is touched",
         measured["picture"] > 0, True),
        ("a tile gives the picture most of its height",
         measured["stage"] > viewport["height"] * 0.4, True),
        ("a tile shows the toolbar", measured["tools"], "flex"),
        ("and the toolbar leaves the picture the tile",
         measured["toolbar"] < viewport["height"] * 0.25, True),
        # A budget rather than a bound. Both rows of chrome fit inside it at either tile shape, and
        # a toolbar that wraps to a second row, or a readout that takes three lines, spends enough
        # of the tile to fail it. That is the regression: the picture is what the tile is for.
        ("the picture keeps at least two fifths of the tile", measured["area"] > area * 0.4, True),
        ("a control in a tile can be pressed", zoomed != "100%", True),
        ("and pointing still works in a tile", reads is not None, True),
    ]


# Deliberately the wrong shape for the picture. A stage that happens to be 4:3 cannot tell a
# correctly fitted picture from one stretched to fill the box, which is the failure being watched
# for here.
NO_SCRIPT_VIEWPORT = {"width": 320, "height": 700}
PICTURE_ASPECT = 160 / 120


def click_fraction(page, across: float, down: float) -> None:
    """Click a point on the picture, given as a fraction of it."""

    rect = picture_rect(page)
    page.mouse.click(rect["left"] + rect["width"] * across, rect["top"] + rect["height"] * down)
    page.wait_for_timeout(SETTLE_MILLISECONDS)


def run_spot_checks(browser, port: int, store) -> list:
    """Placing, removing and clearing spots, checked against what the plugin actually holds.

    Spots live in the plugin rather than in the page, so the assertion is on the settings store and
    not on anything the browser drew: the page's job is to post the list it wants, and the picture
    that comes back is what shows them.
    """

    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.goto(f"http://127.0.0.1:{port}/view", wait_until="domcontentloaded")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    page.click("#mode-spot")
    click_fraction(page, 0.3, 0.4)
    placed = len(store.as_dict()["spots"])
    click_fraction(page, 0.7, 0.6)
    both = len(store.as_dict()["spots"])
    label = page.inner_text("#spots-clear")
    # The same point again, which is a click on the spot that is already there.
    click_fraction(page, 0.3, 0.4)
    after_removing = len(store.as_dict()["spots"])
    for step in range(6):
        click_fraction(page, 0.2 + step * 0.1, 0.2)
    capped = len(store.as_dict()["spots"])
    page.click("#spots-clear")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    cleared = len(store.as_dict()["spots"])
    disabled = page.get_attribute("#spots-clear", "disabled")
    page.close()
    return [
        ("clicking the picture places a spot", placed, 1),
        ("and another click places another", both, 2),
        ("the clear button counts them", label, "Clear 2"),
        ("clicking a spot removes that one", after_removing, 1),
        ("no more than four are accepted", capped, 4),
        ("clear removes all of them", cleared, 0),
        ("and then has nothing to do", disabled is not None, True),
    ]


def run_recording_checks(browser, port: int) -> list:
    """Record a short clip and check a real file comes out of it.

    The recorder is browser machinery from end to end: a canvas stream, a muxer, and a download.
    Nothing about it is visible from Python, and the formats a browser will actually mux differ
    between browsers, so the check asserts the clip is one of the two this page asks for and that
    it is named for the container it really is.
    """

    import os

    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.goto(f"http://127.0.0.1:{port}/view", wait_until="domcontentloaded")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    page.click("#record")
    page.wait_for_timeout(1500)
    running = page.inner_text("#record")
    pressed = page.get_attribute("#record", "aria-pressed")
    with page.expect_download() as download:
        page.click("#record")
    clip = download.value
    name = clip.suggested_filename
    size = os.path.getsize(clip.path())
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    stopped = page.inner_text("#record")
    page.close()
    return [
        ("recording says it is recording", running.startswith("Stop"), True),
        ("and marks the button pressed", pressed, "true"),
        ("a clip is offered when it stops", name.startswith("thermal-"), True),
        ("named for the container it is in", name.endswith((".mp4", ".webm")), True),
        ("and there is something in it", size > 1000, True),
        ("the button goes back to offering a recording", stopped, "Rec"),
    ]


def run_switch_checks(browser, port: int, store) -> list:
    """Switching the camera off, from both of the two places that offer it.

    The switch is a saved setting rather than a page state, so the thing to check is that a press
    reaches the plugin and that the button then agrees with what the plugin says. Getting the
    second half wrong is what made "hold what I see now" look broken: the plugin was right and the
    page went on showing the old state, so the next press posted a stale answer.

    Nothing here drives the capture loop, which is where the camera is actually released. That has
    tests of its own; what cannot be tested from Python is whether two buttons and a saved setting
    stay in step.
    """

    def switched() -> bool:
        return store.camera_snapshot()[1].streaming

    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.goto(f"http://127.0.0.1:{port}/view", wait_until="domcontentloaded")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    offered = page.inner_text("#stream")
    page.click("#stream")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    stopped = switched()
    says_start = page.inner_text("#stream")

    # The settings page, opened while the camera is off, has to come up saying Start.
    settings = browser.new_page(viewport={"width": 900, "height": 700})
    navigated = {"yes": False}
    settings.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
    settings.on("framenavigated", lambda _frame: navigated.update(yes=True))
    settings.wait_for_timeout(SETTLE_MILLISECONDS)
    settings_offers = settings.inner_text("#stream-switch")
    told = settings.inner_text("#device-status")
    settings.click("#stream-switch")
    settings.wait_for_timeout(SETTLE_MILLISECONDS)
    started = switched()
    settings_then_offers = settings.inner_text("#stream-switch")

    # The viewer has been showing a Start button for a camera the settings page turned back on.
    # Coming to the front is what makes it ask again. The event is dispatched rather than waited
    # for, because a headless browser never hides a page and so never fires it by itself: what is
    # being checked is the handler and the fetch behind it, which are the parts that can be wrong.
    page.bring_to_front()
    page.evaluate('() => document.dispatchEvent(new Event("visibilitychange"))')
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    caught_up = page.inner_text("#stream")
    page.click("#stream")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    stopped_again = switched()
    page.close()
    settings.close()
    store.update_camera(streamer_camera_on(store))
    return [
        ("the viewer offers to stop a running camera", offered, "Stop"),
        ("pressing it switches the plugin off", stopped, False),
        ("and the button then offers to start it", says_start, "Start"),
        ("the settings page agrees the camera is off", settings_offers, "Start the camera"),
        ("and says so in words", "switched off" in told, True),
        ("starting from the settings page works", started, True),
        ("without reloading the page", navigated["yes"], False),
        ("and relabels the button", settings_then_offers, "Stop the camera"),
        ("coming back to the viewer catches it up", caught_up, "Stop"),
        ("and it can stop the camera again", stopped_again, False),
    ]


def streamer_camera_on(store):
    """The camera settings with streaming back on, so later sections start from a live camera."""

    import dataclasses

    return dataclasses.replace(store.camera_snapshot()[1], streaming=True)


def run_navigation_checks(browser, port: int) -> list:
    """Getting to the settings and back, the way a tile does it.

    A Fluidd tile is an iframe with no browser chrome around it, so a link that navigates the tile
    is one way unless the page it lands on offers a way back. The settings page did not, and the
    only way back to the camera was to reload the whole dashboard.
    """

    page = browser.new_page(viewport=TILE_VIEWPORT)
    page.goto(f"http://127.0.0.1:{port}/view", wait_until="domcontentloaded")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    page.click("text=settings")
    page.wait_for_load_state("domcontentloaded")
    settings = page.url
    page.click("text=Back to the camera")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    returned = page.url
    picture = page.evaluate(
        '() => document.getElementById("feed").getBoundingClientRect().width'
    )
    page.close()
    return [
        ("the viewer reaches the settings", settings.endswith("/"), True),
        ("and the settings reach the viewer", returned.endswith("/view"), True),
        ("and the picture is there when it lands", picture > 0, True),
    ]


def run_no_script_checks(browser, port: int) -> list:
    """The page with JavaScript switched off.

    This is the fallback that a second camera tile used to be. Until 0.16.0 a plain picture was
    registered beside this page on the argument that a script can break and an image cannot, which
    also put two cameras of the same thing on a dashboard where neither can be hidden, because a
    webcam defined in a config file is read-only in Fluidd. Dropping the second tile only costs
    nothing if this page is still a camera without its script, so that is checked rather than
    claimed: the stylesheet lays the picture out, and the script only ever overrides it.
    """

    context = browser.new_context(viewport=NO_SCRIPT_VIEWPORT, java_script_enabled=False)
    page = context.new_page()
    page.goto(f"http://127.0.0.1:{port}/view", wait_until="domcontentloaded")
    page.wait_for_timeout(SETTLE_MILLISECONDS)
    # `evaluate` still runs with page scripts disabled, because it goes in over the protocol rather
    # than through the page. That is what makes this measurable at all, and it is also why the
    # first check below is there: it proves the page's own script really did not run, rather than
    # this whole section quietly testing the scripted layout.
    measured = page.evaluate(
        """() => {
            const feed = document.getElementById("feed");
            const box = feed.getBoundingClientRect();
            const stage = document.querySelector(".stage").getBoundingClientRect();
            // What object-fit: contain actually paints inside the element box. The element itself
            // covers the whole stage, so its rectangle says nothing about where the picture is.
            const scale = Math.min(box.width / feed.naturalWidth,
                                   box.height / feed.naturalHeight);
            const width = feed.naturalWidth * scale;
            const height = feed.naturalHeight * scale;
            return {
                scripted: feed.style.width !== "",
                natural: feed.naturalWidth > 0 && feed.naturalHeight > 0,
                fit: getComputedStyle(feed).objectFit,
                width: width, height: height,
                left: box.left + (box.width - width) / 2,
                top: box.top + (box.height - height) / 2,
                stage: { left: stage.left, top: stage.top,
                         width: stage.width, height: stage.height }
            };
        }"""
    )
    context.close()
    stage = measured["stage"]
    shape = measured["height"] and measured["width"] / measured["height"]
    inside = (
        measured["left"] >= stage["left"] - 1
        and measured["top"] >= stage["top"] - 1
        and measured["left"] + measured["width"] <= stage["left"] + stage["width"] + 1
        and measured["top"] + measured["height"] <= stage["top"] + stage["height"] + 1
    )
    filled = max(measured["width"] / stage["width"], measured["height"] / stage["height"])
    return [
        ("the page's own script really is off", measured["scripted"], False),
        ("without a script there is still a picture", measured["natural"], True),
        ("the stylesheet letterboxes it", measured["fit"], "contain"),
        ("and it keeps the camera's shape", abs(shape - PICTURE_ASPECT) < 0.02, True),
        ("and it stays inside the stage", inside, True),
        ("and it takes all the room it can", filled > 0.98, True),
    ]


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
        timelapse_page = browser.new_page()
        timelapse_page.on("pageerror", lambda error: problems.append(f"page error: {error}"))
        timelapse_page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
        print("")
        print("timelapse")
        report(run_timelapse_checks(timelapse_page, store), problems)
        report(run_timelapse_refresh_checks(timelapse_page, _server.timelapses), problems)

        viewer = browser.new_page()
        viewer.on("pageerror", lambda error: problems.append(f"viewer page error: {error}"))
        requests: list = []
        viewer.on("request", lambda r: requests.append(r.url) if "frame.bin" in r.url else None)
        viewer.goto(f"http://127.0.0.1:{PORT}/view", wait_until="domcontentloaded")
        print("")
        print("viewer")
        report(run_viewer_checks(viewer, requests), problems)
        report(run_cold_start_checks(browser, PORT), problems)
        print("")
        print("viewer in a tall tile")
        report(run_tile_checks(browser, PORT, TILE_VIEWPORT), problems)
        print("")
        print("viewer in a wide tile")
        report(run_tile_checks(browser, PORT, WIDE_TILE_VIEWPORT), problems)
        print("")
        print("spots")
        report(run_spot_checks(browser, PORT, store), problems)
        print("")
        print("recording")
        report(run_recording_checks(browser, PORT), problems)
        print("")
        print("the settings page on a phone")
        report(run_phone_checks(browser, PORT), problems)
        print("")
        print("the off switch")
        report(run_switch_checks(browser, PORT, store), problems)
        print("")
        print("getting there and back")
        report(run_navigation_checks(browser, PORT), problems)
        print("")
        print("viewer with no script")
        report(run_no_script_checks(browser, PORT), problems)
        browser.close()
    print("")
    for problem in problems:
        print(f"  {problem}")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
