# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The interactive viewer, served at /view and registered as its own tile in Fluidd.

Separate from the control page, and separate from the plain camera tile, because the three are for
different moments. The plain tile is a picture you glance at and it works in anything. The control
page changes settings and works with JavaScript off. This one is the page you open when you want to
ask the camera a question, and it needs a pointer and a script to be worth anything at all.

It reads temperatures itself rather than asking the server per question. `/frame.bin` hands it every
pixel as hundredths of a degree with the emissivity correction already applied, so hovering is a
lookup in an array the page already has rather than a round trip, and the printer does no work at
all while nobody is pointing at anything.

Every URL here is relative, for the reason written on the control page: nginx publishes this under a
prefix the plugin is never told about.
"""

from __future__ import annotations

VIEWER_POLL_MILLISECONDS = 200
# How long the page keeps asking for frames after the pointer leaves. Without it, moving the mouse
# off and back on again shows a stale reading until the next poll; with it, a glance away costs one
# or two more frames and coming back is instant.
VIEWER_LINGER_MILLISECONDS = 3000

VIEWER_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thermal Master</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0d0f12; color: #e8e8ea;
         font: 14px/1.45 system-ui, sans-serif; }}
  main {{ display: flex; flex-direction: column; height: 100vh; }}
  .stage {{ position: relative; flex: 1; min-height: 0; background: #000; }}
  .stage img {{ position: absolute; inset: 0; width: 100%; height: 100%;
                object-fit: contain; display: block; }}
  .stage canvas {{ position: absolute; inset: 0; width: 100%; height: 100%;
                   touch-action: none; cursor: crosshair; }}
  .bar {{ display: flex; gap: 1rem; align-items: baseline; flex-wrap: wrap;
          padding: 0.5rem 0.75rem; background: #14161a; border-top: 1px solid #23262c; }}
  .bar b {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  .bar span {{ color: #9aa0aa; font-size: 0.8rem; text-transform: uppercase;
               letter-spacing: 0.04em; }}
  .reading {{ font-size: 1.15rem; }}
  .offline {{ color: #d8752a; }}
  a {{ color: #9aa0aa; margin-left: auto; font-size: 0.8rem; }}
</style>
</head>
<body>
<main>
  <div class="stage">
    <img id="feed" src="stream.mjpg" alt="Live thermal view">
    <canvas id="surface"></canvas>
  </div>
  <div class="bar">
    <span>Pointer</span><b class="reading" id="pointer">move over the image</b>
    <span>Max</span><b id="max">-</b>
    <span>Min</span><b id="min">-</b>
    <a href="./">settings</a>
  </div>
</main>
<script>
{viewer_script}
</script>
</body>
</html>
"""

VIEWER_SCRIPT = """
(function () {
  var feed = document.getElementById("feed");
  var surface = document.getElementById("surface");
  var pointerOut = document.getElementById("pointer");
  var maxOut = document.getElementById("max");
  var minOut = document.getElementById("min");
  var POLL = POLL_MS;
  var LINGER = LINGER_MS;

  var frame = null;          // {width, height, scale, values}
  var pointer = null;        // {x, y} in frame pixels
  // Where the pointer physically is, kept separately from which pixel that turned out to be.
  // Without it the first hover after loading never resolves: no frame has arrived yet, so there is
  // no pixel to name, and nothing recomputes it when one does until the mouse happens to move.
  var lastSeen = null;       // {clientX, clientY}
  var wantUntil = 0;         // keep fetching until this timestamp
  var timer = null;
  var inFlight = false;

  // The picture is letterboxed inside the stage by object-fit: contain, so the canvas covers more
  // area than the image does. Everything positional has to go through this or the readings land on
  // the wrong pixel, which is the kind of wrong that still looks plausible.
  function imageBox() {
    var box = surface.getBoundingClientRect();
    var natural = (feed.naturalWidth && feed.naturalHeight)
      ? feed.naturalWidth / feed.naturalHeight
      : (frame ? frame.width / frame.height : box.width / box.height);
    var wide = box.width / box.height > natural;
    var width = wide ? box.height * natural : box.width;
    var height = wide ? box.height : box.width / natural;
    return { left: (box.width - width) / 2, top: (box.height - height) / 2,
             width: width, height: height, box: box };
  }

  function toFramePixel(at) {
    if (!at) { return null; }
    var fit = imageBox();
    var x = at.clientX - fit.box.left - fit.left;
    var y = at.clientY - fit.box.top - fit.top;
    if (!frame || x < 0 || y < 0 || x >= fit.width || y >= fit.height) { return null; }
    return {
      x: Math.min(frame.width - 1, Math.floor(x / fit.width * frame.width)),
      y: Math.min(frame.height - 1, Math.floor(y / fit.height * frame.height))
    };
  }

  function temperatureAt(spot) {
    if (!frame || !spot) { return null; }
    return frame.values[spot.y * frame.width + spot.x] / frame.scale;
  }

  function shown(celsius) {
    return celsius === null ? "-" : celsius.toFixed(1) + "C";
  }

  function decode(buffer) {
    var view = new DataView(buffer);
    var magic = String.fromCharCode(view.getUint8(0), view.getUint8(1),
                                    view.getUint8(2), view.getUint8(3));
    if (magic !== "TMF1") { throw new Error("not a thermal frame"); }
    var width = view.getUint16(4, true);
    var height = view.getUint16(6, true);
    var scale = view.getUint16(8, true);
    return {
      width: width, height: height, scale: scale,
      values: new Int16Array(buffer, 16, width * height)
    };
  }

  function summarise() {
    if (!frame) { return; }
    var values = frame.values;
    var hottest = values[0], coldest = values[0];
    for (var i = 1; i < values.length; i += 1) {
      if (values[i] > hottest) { hottest = values[i]; }
      if (values[i] < coldest) { coldest = values[i]; }
    }
    maxOut.textContent = shown(hottest / frame.scale);
    minOut.textContent = shown(coldest / frame.scale);
  }

  function paint() {
    var fit = imageBox();
    surface.width = fit.box.width;
    surface.height = fit.box.height;
    var pen = surface.getContext("2d");
    pen.clearRect(0, 0, surface.width, surface.height);
    if (!pointer || !frame) { return; }
    var x = fit.left + (pointer.x + 0.5) / frame.width * fit.width;
    var y = fit.top + (pointer.y + 0.5) / frame.height * fit.height;
    pen.strokeStyle = "rgba(255,255,255,0.9)";
    pen.lineWidth = 1;
    pen.beginPath();
    pen.moveTo(x - 9, y); pen.lineTo(x + 9, y);
    pen.moveTo(x, y - 9); pen.lineTo(x, y + 9);
    pen.stroke();
  }

  function refresh() {
    if (inFlight) { return; }
    inFlight = true;
    fetch("frame.bin", { cache: "no-store" })
      .then(function (reply) {
        if (!reply.ok) { throw new Error(reply.status); }
        return reply.arrayBuffer();
      })
      .then(function (buffer) {
        frame = decode(buffer);
        summarise();
        // Recomputed from where the pointer is, not from the pixel worked out when it arrived:
        // that pixel may have been unknowable then, and the picture may have been reoriented since.
        pointer = toFramePixel(lastSeen);
        pointerOut.classList.remove("offline");
        pointerOut.textContent = shown(temperatureAt(pointer));
        paint();
      })
      .catch(function () {
        pointerOut.classList.add("offline");
        pointerOut.textContent = "no frame";
      })
      .then(function () {
        inFlight = false;
        if (Date.now() > wantUntil) { stop(); }
      });
  }

  function start() {
    wantUntil = Date.now() + LINGER;
    if (timer === null) {
      timer = setInterval(refresh, POLL);
      refresh();
    }
  }

  function stop() {
    if (timer !== null) { clearInterval(timer); timer = null; }
  }

  surface.addEventListener("pointermove", function (event) {
    lastSeen = { clientX: event.clientX, clientY: event.clientY };
    pointer = toFramePixel(lastSeen);
    pointerOut.textContent = frame ? shown(temperatureAt(pointer)) : "reading...";
    paint();
    start();
  });

  surface.addEventListener("pointerleave", function () {
    lastSeen = null;
    pointer = null;
    pointerOut.textContent = "move over the image";
    paint();
  });

  // Nothing is fetched until the pointer arrives, so a tile nobody is using costs the printer the
  // stream and not one byte more.
  window.addEventListener("resize", paint);
})();
"""


def render_viewer_page() -> str:
    """The viewer, with its timings substituted so they are stated once, in Python."""

    script = VIEWER_SCRIPT.replace("POLL_MS", str(VIEWER_POLL_MILLISECONDS)).replace(
        "LINGER_MS", str(VIEWER_LINGER_MILLISECONDS)
    )
    return VIEWER_PAGE_TEMPLATE.format(viewer_script=script)
