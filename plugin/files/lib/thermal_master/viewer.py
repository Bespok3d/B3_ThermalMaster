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
# A drag shorter than this many pixels on the picture is a click, and a click clears the box. Small
# enough that deliberately boxing a nozzle works, large enough that a click does not leave a one
# pixel region behind reporting the same number three times.
VIEWER_MINIMUM_REGION_PIXELS = 3
# Where a region is remembered. A tile in Fluidd reloads whenever the page around it navigates, and
# losing the box you just drew every time is the difference between a tool and a toy.
VIEWER_REGION_KEY = "thermal-master.region"
VIEWER_MAXIMUM_ZOOM = 8.0
VIEWER_ZOOM_STEP = 1.4

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
  .stage {{ overflow: hidden; }}
  /* Positioned and sized from the script on every paint, so the picture, the overlay and the
     pointer mapping all come from one rectangle and cannot disagree about where anything is. */
  .stage img {{ position: absolute; display: block; image-rendering: pixelated; }}
  .stage canvas {{ position: absolute; inset: 0; width: 100%; height: 100%;
                   touch-action: none; cursor: crosshair; }}
  .bar {{ display: flex; gap: 1rem; align-items: baseline; flex-wrap: wrap;
          padding: 0.5rem 0.75rem; background: #14161a; border-top: 1px solid #23262c; }}
  .tools {{ display: flex; gap: 0.35rem; align-items: center; padding: 0.4rem 0.75rem;
            background: #14161a; border-top: 1px solid #23262c; flex-wrap: wrap; }}
  .tools button {{ padding: 0.3rem 0.6rem; border: 1px solid #33373f; border-radius: 4px;
                   background: #1d2026; color: #e8e8ea; font: inherit; cursor: pointer; }}
  .tools button[aria-pressed="true"] {{ background: #d8752a; border-color: #d8752a;
                                        color: #14161a; font-weight: 600; }}
  .tools .zoom {{ min-width: 3.2rem; text-align: center; color: #9aa0aa;
                  font-variant-numeric: tabular-nums; }}
  .bar b {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  .bar span {{ color: #9aa0aa; font-size: 0.8rem; text-transform: uppercase;
               letter-spacing: 0.04em; }}
  .reading {{ font-size: 1.15rem; }}
  .offline {{ color: #d8752a; }}
  .group {{ display: flex; gap: 0.6rem; align-items: baseline; }}
  .group.idle {{ opacity: 0.45; }}
  .hint {{ color: #6c727c; font-size: 0.75rem; }}
  a {{ color: #9aa0aa; margin-left: auto; font-size: 0.8rem; }}
</style>
</head>
<body>
<main>
  <div class="stage">
    <img id="feed" src="stream.mjpg" alt="Live thermal view">
    <canvas id="surface"></canvas>
  </div>
  <div class="tools">
    <button type="button" id="zoom-out" title="Zoom out">-</button>
    <span class="zoom" id="zoom-level">100%</span>
    <button type="button" id="zoom-in" title="Zoom in">+</button>
    <button type="button" id="fit">Fit</button>
    <button type="button" id="mode-measure" aria-pressed="true">Measure</button>
    <button type="button" id="mode-pan" aria-pressed="false">Pan</button>
    <button type="button" id="units">C</button>
    <button type="button" id="shot">Save image</button>
  </div>
  <div class="bar">
    <div class="group">
      <span>Pointer</span><b class="reading" id="pointer">move over the image</b>
    </div>
    <div class="group">
      <span>Frame</span><b id="max">-</b><b id="min">-</b>
    </div>
    <div class="group idle" id="region-group">
      <span>Region</span><b id="region-max">-</b><b id="region-min">-</b><b id="region-avg">-</b>
      <span class="hint" id="region-hint">drag a box</span>
    </div>
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

  var zoomOut = document.getElementById("zoom-out");
  var zoomIn = document.getElementById("zoom-in");
  var zoomLevel = document.getElementById("zoom-level");
  var fitButton = document.getElementById("fit");
  var measureButton = document.getElementById("mode-measure");
  var panButton = document.getElementById("mode-pan");
  var unitsButton = document.getElementById("units");
  var shotButton = document.getElementById("shot");
  var regionGroup = document.getElementById("region-group");
  var regionOut = {
    max: document.getElementById("region-max"),
    min: document.getElementById("region-min"),
    avg: document.getElementById("region-avg"),
    hint: document.getElementById("region-hint")
  };
  var MINIMUM_REGION = MIN_REGION_PX;
  var REGION_KEY = "REGION_KEY_NAME";
  var MAX_ZOOM = MAX_ZOOM_VALUE;
  var ZOOM_STEP = ZOOM_STEP_VALUE;

  var zoom = 1;
  var pan = { x: 0, y: 0 };   // CSS pixels, applied after the zoom, clamped so the picture stays
  var panning = false;
  var panFrom = null;
  var units = "celsius";

  var frame = null;          // {width, height, scale, values}
  var region = null;         // {left, top, right, bottom} in frame pixels, inclusive
  // A drag is held as the two screen positions, not as the pixels they landed on. Pixels cannot be
  // worked out until a frame has arrived, and a box drawn in the first moment after opening the
  // tile is exactly when that has not happened yet: holding screen positions means the drag can be
  // resolved later instead of being silently dropped.
  var dragFrom = null;       // {clientX, clientY} while the button is down
  var pendingDrag = null;    // {from, to} waiting for a frame to make sense of it
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
    var width = (wide ? box.height * natural : box.width) * zoom;
    var height = (wide ? box.height : box.width / natural) * zoom;
    // Clamped so the picture can never be dragged entirely out of the frame: at any zoom there is
    // always something to look at, and letting it go would need a Fit button to be discoverable.
    var slackX = Math.max(0, (width - box.width) / 2);
    var slackY = Math.max(0, (height - box.height) / 2);
    pan.x = Math.max(-slackX, Math.min(slackX, pan.x));
    pan.y = Math.max(-slackY, Math.min(slackY, pan.y));
    return { left: (box.width - width) / 2 + pan.x, top: (box.height - height) / 2 + pan.y,
             width: width, height: height, box: box };
  }

  function applyZoom(next, about) {
    var before = imageBox();
    var anchor = about || { clientX: before.box.left + before.box.width / 2,
                            clientY: before.box.top + before.box.height / 2 };
    // Where the pointer is, as a fraction of the picture, before and after. Holding that fraction
    // still is what makes the wheel zoom towards what you are looking at rather than the middle.
    var atX = (anchor.clientX - before.box.left - before.left) / before.width;
    var atY = (anchor.clientY - before.box.top - before.top) / before.height;
    zoom = Math.max(1, Math.min(MAX_ZOOM, next));
    var after = imageBox();
    if (before.width > 0 && zoom > 1) {
      pan.x += (before.left + atX * before.width) - (after.left + atX * after.width);
      pan.y += (before.top + atY * before.height) - (after.top + atY * after.height);
    }
    if (zoom === 1) { pan = { x: 0, y: 0 }; }
    zoomOut.textContent = "-";
    zoomLevel.textContent = Math.round(zoom * 100) + "%";
    paint();
  }

  function toFramePixel(at, clamp) {
    if (!at || !frame) { return null; }
    var fit = imageBox();
    var x = at.clientX - fit.box.left - fit.left;
    var y = at.clientY - fit.box.top - fit.top;
    var outside = x < 0 || y < 0 || x >= fit.width || y >= fit.height;
    // Outside the picture is no reading at all when hovering, and the nearest edge when dragging:
    // a box pulled slightly past the edge is a box, not a mistake.
    if (outside && !clamp) { return null; }
    return {
      x: Math.max(0, Math.min(frame.width - 1, Math.floor(x / fit.width * frame.width))),
      y: Math.max(0, Math.min(frame.height - 1, Math.floor(y / fit.height * frame.height)))
    };
  }

  function temperatureAt(spot) {
    if (!frame || !spot) { return null; }
    return frame.values[spot.y * frame.width + spot.x] / frame.scale;
  }

  function shown(celsius) {
    if (celsius === null) { return "-"; }
    return units === "fahrenheit"
      ? (celsius * 9 / 5 + 32).toFixed(1) + "F"
      : celsius.toFixed(1) + "C";
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
    var whole = measure({ left: 0, top: 0, right: frame.width - 1, bottom: frame.height - 1 });
    maxOut.textContent = shown(whole.max);
    minOut.textContent = shown(whole.min);
    showRegion();
  }

  // Measured here rather than asked of the printer. The page already holds every pixel, so a box is
  // a loop over a few thousand numbers and the answer changes as fast as the box is dragged. Asking
  // the server would mean a round trip per drag event and a second implementation of the same sum.
  function measure(box) {
    var hottest = null, coldest = null, total = 0, counted = 0;
    for (var y = box.top; y <= box.bottom; y += 1) {
      var row = y * frame.width;
      for (var x = box.left; x <= box.right; x += 1) {
        var value = frame.values[row + x];
        if (hottest === null || value > hottest) { hottest = value; }
        if (coldest === null || value < coldest) { coldest = value; }
        total += value;
        counted += 1;
      }
    }
    return {
      max: hottest === null ? null : hottest / frame.scale,
      min: coldest === null ? null : coldest / frame.scale,
      avg: counted ? total / counted / frame.scale : null,
      count: counted
    };
  }

  function showRegion() {
    if (!region || !frame) {
      regionGroup.classList.add("idle");
      regionOut.max.textContent = "-";
      regionOut.min.textContent = "-";
      regionOut.avg.textContent = "-";
      regionOut.hint.textContent = "drag a box";
      return;
    }
    var inside = measure(region);
    regionGroup.classList.remove("idle");
    regionOut.max.textContent = shown(inside.max);
    regionOut.min.textContent = shown(inside.min);
    regionOut.avg.textContent = shown(inside.avg);
    regionOut.hint.textContent = inside.count + " px, click to clear";
  }

  function rememberRegion() {
    try {
      if (region) {
        window.localStorage.setItem(REGION_KEY, JSON.stringify(region));
      } else {
        window.localStorage.removeItem(REGION_KEY);
      }
    } catch (ignored) {
      // Private browsing and iframe storage rules both refuse this, and neither is a reason to
      // stop working: the box simply does not survive a reload.
    }
  }

  function recallRegion() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(REGION_KEY));
      if (saved && typeof saved.left === "number") { region = saved; }
    } catch (ignored) {
      region = null;
    }
  }

  // Turn two screen positions into the region they describe, once a frame exists to measure
  // against. Returns whether it could, so a drag made too early can be held and retried.
  function resolveDrag(from, to) {
    var start = toFramePixel(from, true);
    var end = toFramePixel(to, true);
    if (!start || !end) { return false; }
    var box = clampRegion(boxBetween(start, end));
    // A click is a drag that went nowhere, and it clears the box rather than leaving a speck
    // behind. Judged on the picture's own pixels, so it means the same at any window size.
    var tiny = (box.right - box.left) < MINIMUM_REGION
      || (box.bottom - box.top) < MINIMUM_REGION;
    region = tiny ? null : box;
    rememberRegion();
    showRegion();
    paint();
    return true;
  }

  function boxBetween(one, other) {
    return {
      left: Math.min(one.x, other.x), top: Math.min(one.y, other.y),
      right: Math.max(one.x, other.x), bottom: Math.max(one.y, other.y)
    };
  }

  function clampRegion(box) {
    if (!frame) { return box; }
    return {
      left: Math.max(0, Math.min(frame.width - 1, box.left)),
      top: Math.max(0, Math.min(frame.height - 1, box.top)),
      right: Math.max(0, Math.min(frame.width - 1, box.right)),
      bottom: Math.max(0, Math.min(frame.height - 1, box.bottom))
    };
  }

  function paint() {
    var fit = imageBox();
    // The picture element is placed from the same rectangle the overlay and the pointer mapping
    // use, rather than being left to object-fit, so zooming cannot drift them apart.
    feed.style.left = fit.left + "px";
    feed.style.top = fit.top + "px";
    feed.style.width = fit.width + "px";
    feed.style.height = fit.height + "px";
    surface.width = fit.box.width;
    surface.height = fit.box.height;
    var pen = surface.getContext("2d");
    pen.clearRect(0, 0, surface.width, surface.height);
    paintRegion(pen, fit);
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

  function paintRegion(pen, fit) {
    if (!region || !frame) { return; }
    var x = fit.left + region.left / frame.width * fit.width;
    var y = fit.top + region.top / frame.height * fit.height;
    var width = (region.right - region.left + 1) / frame.width * fit.width;
    var height = (region.bottom - region.top + 1) / frame.height * fit.height;
    pen.strokeStyle = "rgba(120,220,160,0.95)";
    pen.lineWidth = 2;
    pen.strokeRect(x, y, width, height);
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
        // A box drawn before any frame had arrived resolves now that one has.
        if (pendingDrag && resolveDrag(pendingDrag.from, pendingDrag.to)) { pendingDrag = null; }
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
        if (!wanted()) { stop(); }
      });
  }

  function start() {
    wantUntil = Date.now() + LINGER;
    if (timer === null) {
      timer = setInterval(refresh, POLL);
      refresh();
    }
  }

  // A region is a standing question, so it keeps the frames coming whether or not anyone is
  // pointing. Without this the numbers in a box would freeze the moment the mouse left the tile,
  // which is exactly when someone walks away to let the print run.
  function wanted() {
    return region !== null || Date.now() <= wantUntil;
  }

  function stop() {
    if (timer !== null) { clearInterval(timer); timer = null; }
  }

  surface.addEventListener("pointermove", function (event) {
    lastSeen = { clientX: event.clientX, clientY: event.clientY };
    pointer = toFramePixel(lastSeen);
    pointerOut.textContent = frame ? shown(temperatureAt(pointer)) : "reading...";
    if (panFrom) {
      pan.x = panFrom.x + (event.clientX - panFrom.clientX);
      pan.y = panFrom.y + (event.clientY - panFrom.clientY);
      paint();
      return;
    }
    if (dragFrom) { resolveDrag(dragFrom, lastSeen); }
    paint();
    start();
  });

  surface.addEventListener("pointerdown", function (event) {
    surface.setPointerCapture(event.pointerId);
    if (panning) {
      panFrom = { clientX: event.clientX, clientY: event.clientY, x: pan.x, y: pan.y };
      return;
    }
    dragFrom = { clientX: event.clientX, clientY: event.clientY };
    // Frames have to start arriving now rather than on the first move, or a drag begun the moment
    // the tile opens has nothing to resolve against.
    start();
  });

  surface.addEventListener("pointerup", function (event) {
    if (panFrom) { panFrom = null; return; }
    if (!dragFrom) { return; }
    var to = { clientX: event.clientX, clientY: event.clientY };
    var from = dragFrom;
    dragFrom = null;
    if (!resolveDrag(from, to)) { pendingDrag = { from: from, to: to }; }
    start();
  });

  surface.addEventListener("pointerleave", function () {
    lastSeen = null;
    pointer = null;
    pointerOut.textContent = "move over the image";
    paint();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || !region) { return; }
    region = null;
    rememberRegion();
    showRegion();
    paint();
  });

  // Nothing is fetched until the pointer arrives or a remembered region asks for it, so a tile
  // nobody is using costs the printer the stream and not one byte more.
  surface.addEventListener("wheel", function (event) {
    event.preventDefault();
    applyZoom(zoom * (event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP),
              { clientX: event.clientX, clientY: event.clientY });
  }, { passive: false });

  zoomIn.addEventListener("click", function () { applyZoom(zoom * ZOOM_STEP, null); });
  zoomOut.addEventListener("click", function () { applyZoom(zoom / ZOOM_STEP, null); });
  fitButton.addEventListener("click", function () {
    pan = { x: 0, y: 0 };
    applyZoom(1, null);
  });

  function setMode(toPan) {
    panning = toPan;
    panFrom = null;
    dragFrom = null;
    measureButton.setAttribute("aria-pressed", String(!toPan));
    panButton.setAttribute("aria-pressed", String(toPan));
    surface.style.cursor = toPan ? "grab" : "crosshair";
  }

  // A mode rather than a modifier key, because the same page has to work on a phone, where there
  // is no key to hold and both gestures are a finger dragging across the picture.
  measureButton.addEventListener("click", function () { setMode(false); });
  panButton.addEventListener("click", function () { setMode(true); });

  function showUnits() {
    unitsButton.textContent = units === "fahrenheit" ? "F" : "C";
    summarise();
    pointerOut.textContent = frame && pointer ? shown(temperatureAt(pointer))
                                              : pointerOut.textContent;
  }

  // The unit is the plugin's, not this page's. Changing it here changes what the burned-in readout
  // on the plain tile says too, which is the point: two tiles disagreeing about degrees would be
  // worse than having to open the settings form.
  //
  // Sent as JSON rather than as a form, because a form cannot say "only this": an unticked box is
  // indistinguishable from an absent one, so posting a form with just the unit in it would switch
  // every part of the readout off.
  function pushUnits(next) {
    fetch("settings", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ units: next })
    }).then(function (reply) {
      return reply.ok ? reply.json() : Promise.reject(reply.status);
    }).then(function (state) {
      units = state.units;
      showUnits();
    }).catch(function () {
      // The plugin refused or is not there; leave the button saying what is actually in force.
    });
  }

  unitsButton.addEventListener("click", function () {
    pushUnits(units === "fahrenheit" ? "celsius" : "fahrenheit");
  });

  function stamp() {
    var now = new Date();
    function two(value) { return String(value).padStart(2, "0"); }
    return now.getFullYear() + two(now.getMonth() + 1) + two(now.getDate())
      + "-" + two(now.getHours()) + two(now.getMinutes()) + two(now.getSeconds());
  }

  // Saved at the sensor's own resolution rather than at whatever size the window happens to be,
  // and with the region drawn on, because a picture of a measurement that does not show what was
  // measured is not evidence of anything.
  shotButton.addEventListener("click", function () {
    if (!feed.naturalWidth) { return; }
    var shot = document.createElement("canvas");
    shot.width = feed.naturalWidth;
    shot.height = feed.naturalHeight;
    var pen = shot.getContext("2d");
    pen.drawImage(feed, 0, 0, shot.width, shot.height);
    if (region && frame) {
      var scaleX = shot.width / frame.width;
      var scaleY = shot.height / frame.height;
      pen.strokeStyle = "rgba(120,220,160,0.95)";
      pen.lineWidth = Math.max(1, Math.round(shot.width / 160));
      pen.strokeRect(region.left * scaleX, region.top * scaleY,
                     (region.right - region.left + 1) * scaleX,
                     (region.bottom - region.top + 1) * scaleY);
    }
    shot.toBlob(function (blob) {
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "thermal-" + stamp() + ".png";
      link.click();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 10000);
    }, "image/png");
  });

  // The unit in force belongs to the plugin, so it is asked for rather than assumed.
  fetch("settings", { headers: { "Accept": "application/json" } })
    .then(function (reply) { return reply.ok ? reply.json() : Promise.reject(reply.status); })
    .then(function (state) { units = state.units; showUnits(); })
    .catch(function () { showUnits(); });

  window.addEventListener("resize", paint);
  recallRegion();
  setMode(false);
  if (region) { start(); }
})();
"""


def render_viewer_page() -> str:
    """The viewer, with its timings substituted so they are stated once, in Python."""

    script = (
        VIEWER_SCRIPT.replace("POLL_MS", str(VIEWER_POLL_MILLISECONDS))
        .replace("LINGER_MS", str(VIEWER_LINGER_MILLISECONDS))
        .replace("MIN_REGION_PX", str(VIEWER_MINIMUM_REGION_PIXELS))
        .replace("REGION_KEY_NAME", VIEWER_REGION_KEY)
        .replace("MAX_ZOOM_VALUE", str(VIEWER_MAXIMUM_ZOOM))
        .replace("ZOOM_STEP_VALUE", str(VIEWER_ZOOM_STEP))
    )
    return VIEWER_PAGE_TEMPLATE.format(viewer_script=script)
