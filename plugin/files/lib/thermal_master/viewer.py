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

from .temperature import MAX_SPOTS

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
# Frames a second written into a recording. The camera itself runs at about fifteen and the tile
# polls at that rate, so asking for more would write duplicates; asking for much less makes a clip
# of a heating nozzle look like a slideshow.
VIEWER_RECORD_FPS = 12
# A recording is held in memory until it is stopped, so it cannot be left running all afternoon by
# a tab nobody is looking at. Ten minutes at this frame rate is a few tens of megabytes.
VIEWER_RECORD_LIMIT_MILLISECONDS = 600000
# The watchdog (Phase 7i, F-74). An <img> showing a stream says nothing when the stream ends, so
# the page looks for itself: on this beat, and only while it is visible, it samples its own picture
# and asks the plugin how many frames it has published.
VIEWER_WATCH_MILLISECONDS = 2000
# How long the picture may stay unchanged before the page calls its stream dead. At 25 frames a
# second this is 75 frames, and the quietest stretch measured in F-74's clip changed in 88% of
# them. Agreed on 2026-09-28; raise it if it proves too eager.
VIEWER_STALE_MILLISECONDS = 3000
# At most one restart of the stream in this long, whatever asked for it.
VIEWER_RESTART_MILLISECONDS = 5000
# How long one question may take before it counts as unanswered. A dropped network does not fail a
# request, it leaves it hanging, and a watchdog waiting on it has stopped watching.
#
# The beat does not slow while the printer is silent. It did in 0.26.0, doubling to ten seconds,
# and on 2026-09-28 that kept the picture frozen 12.5 s after the printer's network came back:
# asking a printer that cannot be reached costs it nothing, so backing off bought nothing.
VIEWER_ASK_TIMEOUT_MILLISECONDS = 4000

VIEWER_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thermal Master</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0d0f12; color: #e8e8ea; overflow: hidden;
         font: 14px/1.45 system-ui, sans-serif; }}
  main {{ display: flex; flex-direction: column; height: 100vh; }}
  .panel {{ display: flex; flex-direction: column; min-height: 0; overflow: auto; }}
  /* The numbers go beside the picture when there is width going spare, which in a Fluidd tile
     there always is: the tile is landscape and a rotated camera is portrait, so stacking the
     readout underneath spends the one dimension the picture actually needed. The script decides,
     because only it knows the picture's shape, and that changes when the camera is rotated. */
  body.beside main {{ flex-direction: row; }}
  /* Narrow on purpose. The readout is four short numbers, and letting it size itself gave it half
     the tile, which is half the width the picture could have had. It takes a fixed narrow column
     now, capped at a third, and the picture keeps the rest. */
  body.beside .panel {{ flex: 0 0 auto; width: 7.5rem; max-width: 34%;
                        border-left: 1px solid #23262c; }}
  body.beside .group {{ flex-wrap: wrap; }}
  body.beside .bar {{ flex-direction: column; align-items: flex-start; gap: 0.15rem;
                      border-top: 0; }}
  /* The picture comes first and keeps most of the room. In a Fluidd tile this page is about 260
     by 340 CSS pixels, and the controls laid out for a window wrapped to three rows and took all
     of it: the stage was flexed down to 41 pixels and the tile showed a toolbar and no camera.
     A flex basis rather than flex: 1, so the picture is sized from the space rather than from
     whatever is left after the chrome. */
  /* The picture and its controls, in that order and always together. Only the readout moves to
     the side when the page is laid out beside; the controls belong under the picture either way. */
  .view {{ display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0; min-height: 0; }}
  .stage {{ position: relative; flex: 1 1 auto; min-height: 50%; background: #000;
            overflow: hidden; }}
  /* Two layouts in one rule. The inset and object-fit are what the picture gets with no script
     at all: letterboxed inside the stage, right shape, whatever the tile is. The script overrides
     left, top, width and height on every paint, so the picture, the overlay and the pointer
     mapping all come from one rectangle and cannot disagree about where anything is.
     This is the whole fallback now that the plain camera tile is gone: with JavaScript off, this
     page is still the camera. */
  .stage img {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain;
                display: block; image-rendering: pixelated; }}
  .stage canvas {{ position: absolute; inset: 0; width: 100%; height: 100%;
                   touch-action: none; cursor: crosshair; }}
  /* Tight enough that the readout is one line of a tile rather than three. Every line it takes
     is a line the picture does not get, and the picture is the reason the tile is there. */
  .bar {{ display: flex; gap: 0.2rem 0.7rem; align-items: baseline; flex-wrap: wrap;
          padding: 0.25rem 0.5rem; background: #14161a; border-top: 1px solid #23262c;
          font-size: 0.78rem; }}
  @media (min-height: 460px) {{
    .bar {{ gap: 1rem; padding: 0.5rem 0.75rem; font-size: 1rem; }}
  }}
  /* Always there, in a tile as well. Hiding them below a height breakpoint put the controls out
     of reach in the one place they are most wanted, the dashboard, and left the tile advertising
     tools it would not show. They are compact instead: small type, tight padding, and free to wrap
     to a second row when the tile is narrow. */
  .tools {{ display: flex; flex: 0 0 auto; gap: 0.25rem; align-items: center;
            justify-content: center; padding: 0.3rem 0.4rem; background: #14161a;
            border-top: 1px solid #23262c; flex-wrap: wrap; font-size: 0.72rem; }}
  @media (min-height: 460px) {{
    .tools {{ gap: 0.3rem; padding: 0.35rem 0.6rem; font-size: 0.8rem; }}
  }}
  .tools button {{ padding: 0.2rem 0.4rem; border: 1px solid #33373f; border-radius: 4px;
                   background: #1d2026; color: #e8e8ea; font: inherit; cursor: pointer; }}
  @media (min-height: 460px) {{
    .tools button {{ padding: 0.25rem 0.5rem; }}
  }}
  .tools button[aria-pressed="true"] {{ background: #d8752a; border-color: #d8752a;
                                        color: #14161a; font-weight: 600; }}
  .tools .zoom {{ min-width: 2.7rem; text-align: center; color: #9aa0aa;
                  font-variant-numeric: tabular-nums; }}
  /* The one thing in the toolbar that is a readout rather than a control. In a narrow tile it is
     what pushes the row to wrap, and a second row of chrome costs the picture more than knowing
     the zoom percentage is worth: the buttons still say which way they go, and Fit still resets. */
  @media (max-width: 340px) {{
    .tools .zoom {{ display: none; }}
  }}
  /* Same bargain as the zoom readout, one size up. Recording is something you set up deliberately,
     which means the page is open properly; a narrow tile spends the width on the controls you
     reach for while glancing at a print. */
  @media (max-width: 380px) {{
    .tools .record {{ display: none; }}
    .tools .spots {{ display: none; }}
    .tools .stream {{ display: none; }}
  }}
  /* Switching the camera off is not a mode, so it does not get the toolbar's orange. It is closer
     to what Fit is: a thing you press, which then reports what it did by changing its own word. */
  .tools .stream[data-streaming="false"] {{ border-color: #4a7f5c; color: #9fd8b4; }}
  .tools button[disabled] {{ opacity: 0.4; cursor: default; }}
  /* Not the toolbar's orange. A recording is running until you stop it, and it should not look
     like one more thing that is merely switched on. */
  .tools .record[aria-pressed="true"] {{ background: #c0392b; border-color: #c0392b;
                                         color: #fff; }}
  .bar b {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  .bar span {{ color: #9aa0aa; font-size: 0.8rem; text-transform: uppercase;
               letter-spacing: 0.04em; }}
  .reading {{ font-size: 1.05em; }}
  .offline {{ color: #d8752a; }}
  /* The watchdog's line over the picture (Phase 7i). The orange the pointer readout uses when the
     plugin does not answer, on a dark band so it reads over a hot scene, and never in the way of
     the pointer. */
  .stale {{ position: absolute; left: 0; right: 0; top: 0; padding: 0.2rem 0.5rem;
            background: rgba(13, 15, 18, 0.8); color: #d8752a; font-size: 0.78rem;
            pointer-events: none; }}
  .stale[hidden] {{ display: none; }}
  .group {{ display: flex; gap: 0.6rem; align-items: baseline; }}
  .group.idle {{ opacity: 0.45; }}
  .hint {{ color: #6c727c; font-size: 0.75em; }}
  a {{ color: #9aa0aa; margin-left: auto; font-size: 0.8rem; }}
</style>
</head>
<body>
<main>
  <div class="view">
    <div class="stage">
      <img id="feed" src="stream.mjpg" alt="Live thermal view"{picture_shape}>
      <canvas id="surface"></canvas>
      <div class="stale" id="stale" role="status" hidden></div>
    </div>
    <div class="panel-tools tools">
      <button type="button" id="zoom-out" title="Zoom out">-</button>
      <span class="zoom" id="zoom-level">100%</span>
      <button type="button" id="zoom-in" title="Zoom in">+</button>
      <button type="button" id="fit">Fit</button>
      <button type="button" id="mode-measure" aria-pressed="true"
              title="Measure: drag a box on the picture">Box</button>
      <button type="button" id="mode-pan" aria-pressed="false">Pan</button>
      <button type="button" id="mode-spot" class="spots" aria-pressed="false"
              title="Place a spot: click the picture, click a spot to remove it">Spot</button>
      <button type="button" id="spots-clear" class="spots" disabled
              title="Remove every spot">Clear</button>
      <button type="button" id="units">C</button>
      <button type="button" id="shot" title="Save image">Save</button>
      <button type="button" id="record" class="record" aria-pressed="false"
              title="Record video">Rec</button>
      <button type="button" id="stream" class="stream" data-streaming="true"
              title="Stop the camera and release it">Stop</button>
    </div>
  </div>
  <div class="panel">
  <div class="bar">
    <div class="group">
      <span>Pointer</span><b class="reading" id="pointer">-</b>
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
  var recordButton = document.getElementById("record");
  var streamButton = document.getElementById("stream");
  var spotButton = document.getElementById("mode-spot");
  var clearSpotsButton = document.getElementById("spots-clear");
  var regionGroup = document.getElementById("region-group");
  var regionOut = {
    max: document.getElementById("region-max"),
    min: document.getElementById("region-min"),
    avg: document.getElementById("region-avg"),
    hint: document.getElementById("region-hint")
  };
  var MINIMUM_REGION = MIN_REGION_PX;
  // The green a region box and a placed spot are both drawn in, here and in the burned-in readout,
  // because both are places a person chose rather than places the scene chose.
  var REGION_INK = "rgba(120,220,160,0.95)";
  var MAX_SPOTS = MAX_SPOTS_VALUE;
  // How near a click has to land to be read as "that one", in frame pixels. Generous, because the
  // picture is 160 pixels across and a finger is not.
  var SPOT_REACH = 6;
  var REGION_KEY = "REGION_KEY_NAME";
  var MAX_ZOOM = MAX_ZOOM_VALUE;
  var ZOOM_STEP = ZOOM_STEP_VALUE;
  var RECORD_FPS = RECORD_FPS_VALUE;
  var RECORD_LIMIT = RECORD_LIMIT_MS;

  var zoom = 1;
  var pan = { x: 0, y: 0 };   // CSS pixels, applied after the zoom, clamped so the picture stays
  var panning = false;
  var spotting = false;
  // The plugin's list, mirrored here so a click knows what it is near. Read from the settings on
  // load rather than kept locally, because these live on the printer: another browser, the
  // dashboard tile and a recorded clip all show the same spots.
  var spots = [];
  var panFrom = null;
  var units = "celsius";
  // Assumed on until the plugin says otherwise, which it does a moment later. Guessing off would
  // show a Start button over a picture that is plainly moving.
  var streaming = true;

  var frame = null;          // {width, height, scale, values}
  var region = null;         // {left, top, right, bottom} in frame pixels, inclusive
  // A drag is held as the two screen positions, not as the pixels they landed on. Pixels cannot be
  // worked out until a frame has arrived, and a box drawn in the first moment after opening the
  // tile is exactly when that has not happened yet: holding screen positions means the drag can be
  // resolved later instead of being silently dropped.
  var dragFrom = null;       // {clientX, clientY} while the button is down
  var pendingDrag = null;    // {from, to} waiting for a frame to make sense of it
  var pendingSpot = null;    // a click waiting for the same thing
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
  // The picture's shape, or null while nothing has told us yet. Deliberately not guessed: the
  // first version fell back to the shape of the box, which fills it exactly and so looks correct
  // while stretching a portrait camera across a landscape tile. Nothing shows until this is known.
  function pictureAspect() {
    if (feed.naturalWidth && feed.naturalHeight) {
      return feed.naturalWidth / feed.naturalHeight;
    }
    if (frame) { return frame.width / frame.height; }
    // What the plugin said when it served the page, which it knows because it is the thing
    // producing the picture. Last rather than first, because the other two are what is actually
    // on screen and this is only what was true when the page was built.
    var told = Number(feed.dataset.width) / Number(feed.dataset.height);
    return told > 0 ? told : null;
  }

  function imageBox() {
    var box = surface.getBoundingClientRect();
    var natural = pictureAspect() || box.width / box.height;
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

  // Which way round the page is laid out. Decided from the picture's shape rather than from a
  // media query, because a rotated camera changes that shape while the window stays the same.
  function arrange() {
    var picture = pictureAspect();
    if (picture === null) { return false; }
    var beside = (window.innerWidth / window.innerHeight) > picture * 1.3;
    if (beside === document.body.classList.contains("beside")) { return false; }
    document.body.classList.toggle("beside", beside);
    return true;
  }

  function paint() {
    if (arrange()) {
      // The layout just changed under us, so every rectangle measured below would be the old one.
      requestAnimationFrame(paint);
      return;
    }
    var fit = imageBox();
    // The picture element is placed from the same rectangle the overlay and the pointer mapping
    // use, rather than being left to object-fit, so zooming cannot drift them apart.
    // Until the shape is known there is no such rectangle, and the earlier answer was to hide the
    // picture until there was one. That was affordable while a plain camera tile existed beside
    // this page. It does not survive being the only tile, so the picture is handed back to the
    // stylesheet instead: object-fit letterboxes it correctly, which is the same thing a browser
    // with no script at all shows. Nothing is drawn over it, because the overlay needs a frame and
    // there is no frame yet either.
    var shaped = pictureAspect() !== null;
    feed.style.left = shaped ? fit.left + "px" : "";
    feed.style.top = shaped ? fit.top + "px" : "";
    feed.style.width = shaped ? fit.width + "px" : "";
    feed.style.height = shaped ? fit.height + "px" : "";
    // The stylesheet pins all four edges. Left and top are overridden above, and these two have to
    // be released or the picture is over-constrained and the width is quietly ignored.
    feed.style.right = shaped ? "auto" : "";
    feed.style.bottom = shaped ? "auto" : "";
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

  // Placing and removing. The spots themselves belong to the plugin, which draws them into the
  // picture, so this page never renders one: it posts the list it wants and the next frame shows
  // it. That is the whole reason they live server side, and the cost is that a placement takes a
  // frame to appear.
  function nearestSpot(at) {
    var nearest = -1;
    var best = SPOT_REACH;
    for (var index = 0; index < spots.length; index += 1) {
      var distance = Math.hypot(spots[index][0] - at.x, spots[index][1] - at.y);
      if (distance <= best) { best = distance; nearest = index; }
    }
    return nearest;
  }

  function showSpots() {
    clearSpotsButton.textContent = spots.length ? "Clear " + spots.length : "Clear";
    clearSpotsButton.disabled = spots.length === 0;
    spotButton.title = spots.length >= MAX_SPOTS
      ? "All " + MAX_SPOTS + " spots placed: click one to remove it"
      : "Place a spot: click the picture, click a spot to remove it";
  }

  function pushSpots(wanted) {
    spots = wanted;
    showSpots();
    fetch("settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spots: spots })
    }).catch(function () {
      pointerOut.classList.add("offline");
      pointerOut.textContent = "no reply";
    });
  }

  function touchSpot(at) {
    if (!at) { return; }
    var existing = nearestSpot(at);
    if (existing >= 0) {
      pushSpots(spots.filter(function (unused, index) { return index !== existing; }));
      return;
    }
    if (spots.length >= MAX_SPOTS) { return; }
    pushSpots(spots.concat([[at.x, at.y]]));
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
        // A box drawn before any frame had arrived resolves now that one has, and so does a spot
        // placed before one had. Both are the same trap: a screen position only becomes a pixel
        // once there is a frame to turn it against, and the first thing anyone does on opening the
        // tile is go straight for the thing they wanted to measure.
        if (pendingDrag && resolveDrag(pendingDrag.from, pendingDrag.to)) { pendingDrag = null; }
        if (pendingSpot) {
          var waiting = pendingSpot;
          pendingSpot = null;
          touchSpot(toFramePixel(waiting));
        }
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
    // Nothing begins on the way down in spot mode: a spot is a click, and starting a drag here
    // would leave a box behind every time one was placed.
    if (spotting) { return; }
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
    if (spotting) {
      var at = { clientX: event.clientX, clientY: event.clientY };
      var spot = toFramePixel(at);
      if (spot) {
        touchSpot(spot);
      } else {
        // No frame yet, so this click cannot be turned into a pixel. Held rather than dropped.
        pendingSpot = at;
        start();
      }
      return;
    }
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
    pointerOut.textContent = "-";
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

  // Three modes, all driven by the same gesture on the picture: drag a box, drag the picture, or
  // put a spot down. A mode rather than a modifier key, because the same page has to work on a
  // phone, where there is no key to hold.
  function setMode(mode) {
    panning = mode === "pan";
    spotting = mode === "spot";
    panFrom = null;
    dragFrom = null;
    measureButton.setAttribute("aria-pressed", String(mode === "measure"));
    panButton.setAttribute("aria-pressed", String(panning));
    spotButton.setAttribute("aria-pressed", String(spotting));
    surface.style.cursor = panning ? "grab" : "crosshair";
  }

  measureButton.addEventListener("click", function () { setMode("measure"); });
  panButton.addEventListener("click", function () { setMode("pan"); });
  spotButton.addEventListener("click", function () { setMode("spot"); });
  clearSpotsButton.addEventListener("click", function () { pushSpots([]); });

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

  // Off is the plugin's state and not this page's: it releases the camera, so a second browser and
  // the dashboard tile are looking at the same switch. Hence asking for it and then believing the
  // answer, rather than flipping a local flag and hoping.
  function showStream() {
    streamButton.textContent = streaming ? "Stop" : "Start";
    streamButton.title = streaming ? "Stop the camera and release it"
                                   : "Start the camera again";
    streamButton.setAttribute("data-streaming", streaming ? "true" : "false");
    showRecordable();
  }

  function pushStream(next) {
    fetch("settings", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ streaming: next })
    }).then(function (reply) {
      return reply.ok ? reply.json() : Promise.reject(reply.status);
    }).then(function (state) {
      streaming = state.streaming !== false;
      showStream();
    }).catch(function () {
      // The plugin refused or is not there; leave the button saying what is actually in force.
    });
  }

  streamButton.addEventListener("click", function () { pushStream(!streaming); });

  function stamp() {
    var now = new Date();
    function two(value) { return String(value).padStart(2, "0"); }
    return now.getFullYear() + two(now.getMonth() + 1) + two(now.getDate())
      + "-" + two(now.getHours()) + two(now.getMinutes()) + two(now.getSeconds());
  }

  // The picture as it should be kept: at the sensor's own resolution rather than at whatever size
  // the window happens to be, and with the region drawn on, because a picture of a measurement
  // that does not show what was measured is not evidence of anything. One painter, used by both
  // the still and the recording, so a saved frame and a saved clip cannot disagree.
  function paintKeepsake(pen, width, height) {
    pen.drawImage(feed, 0, 0, width, height);
    paintStale(pen, width, height);
    if (!region || !frame) { return; }
    var scaleX = width / frame.width;
    var scaleY = height / frame.height;
    var left = region.left * scaleX;
    var top = region.top * scaleY;
    var boxWidth = (region.right - region.left + 1) * scaleX;
    var boxHeight = (region.bottom - region.top + 1) * scaleY;
    pen.strokeStyle = REGION_INK;
    pen.lineWidth = Math.max(1, Math.round(width / 160));
    pen.strokeRect(left, top, boxWidth, boxHeight);
    // And what it said. A box on its own says where the measurement was taken and not what it
    // came to, which in a saved picture or a recorded clip is the half that cannot be recovered
    // later: the readout burned into the stream is about the whole frame, not about this box.
    //
    // The average alone. All three numbers fitted, and read as clutter on a picture that already
    // carries a hot marker, a cold marker and a ruler: the extremes of a box are usually the
    // extremes of the frame, which the picture is already showing. The page still lists all three
    // beside the picture, where there is room for them.
    var inside = measure(region);
    var text = "avg " + shown(inside.avg);
    var size = Math.max(10, Math.round(height / 16));
    pen.font = "600 " + size + "px system-ui, sans-serif";
    pen.textBaseline = "bottom";
    // Above the box, unless the box is against the top edge, in which case underneath it. Kept
    // inside the picture either way, because a number half off the frame is worse than no number.
    var above = top - size * 0.3;
    var baseline = above > size ? above : Math.min(height - size * 0.2, top + boxHeight + size);
    var room = Math.max(2, width - pen.measureText(text).width - 2);
    var textX = Math.min(Math.max(left, 2), room);
    pen.lineWidth = Math.max(2, Math.round(size / 5));
    pen.strokeStyle = "rgba(0,0,0,0.85)";
    pen.strokeText(text, textX, baseline);
    pen.fillStyle = REGION_INK;
    pen.fillText(text, textX, baseline);
  }

  function offer(blob, extension) {
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "thermal-" + stamp() + "." + extension;
    link.click();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 10000);
  }

  shotButton.addEventListener("click", function () {
    if (!feed.naturalWidth) { return; }
    var shot = document.createElement("canvas");
    shot.width = feed.naturalWidth;
    shot.height = feed.naturalHeight;
    paintKeepsake(shot.getContext("2d"), shot.width, shot.height);
    shot.toBlob(function (blob) { offer(blob, "png"); }, "image/png");
  });

  // Recording. The browser will only mux what it will mux, so the formats are tried in the order
  // of what opens without being asked questions: MP4 first, which Safari produces natively and
  // recent Chrome does too, then WebM, which is what Chrome has always produced. The clip is
  // named for what it actually is rather than for what was asked for.
  var RECORDING_FORMATS = [
    { type: "video/mp4;codecs=avc1.42E01E", extension: "mp4" },
    { type: "video/mp4", extension: "mp4" },
    { type: "video/webm;codecs=vp9", extension: "webm" },
    { type: "video/webm;codecs=vp8", extension: "webm" },
    { type: "video/webm", extension: "webm" }
  ];
  var recorder = null;
  var recorded = [];
  var recordingSince = 0;
  var recordingPainter = null;
  var recordingClock = null;

  function canRecord() {
    return typeof MediaRecorder !== "undefined"
      && !!document.createElement("canvas").captureStream
      && RECORDING_FORMATS.some(function (format) {
        return MediaRecorder.isTypeSupported(format.type);
      });
  }

  // Asked for in order of preference, and actually built rather than merely asked about:
  // `isTypeSupported` is a browser's opinion, and at least one of them says yes to a container it
  // then refuses to construct a recorder for. The one that constructs is the one that muxes.
  function makeRecorder(stream) {
    for (var index = 0; index < RECORDING_FORMATS.length; index += 1) {
      var format = RECORDING_FORMATS[index];
      if (!MediaRecorder.isTypeSupported(format.type)) { continue; }
      try {
        return { recorder: new MediaRecorder(stream, { mimeType: format.type }), format: format };
      } catch (refused) {
        continue;
      }
    }
    return null;
  }

  function elapsed() {
    var seconds = Math.floor((Date.now() - recordingSince) / 1000);
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }

  function showRecording(running) {
    recordButton.setAttribute("aria-pressed", running ? "true" : "false");
    recordButton.textContent = running ? "Stop " + elapsed() : "Rec";
    recordButton.title = "Stop recording and save the clip";
    showRecordable();
  }

  // Offered only while the camera's own picture is on screen and moving. A clip's size is fixed
  // when it starts, so one started over the "Stream off" placeholder kept the placeholder's shape
  // for the whole clip and squashed the camera into it when it came on (2026-09-28). Moving is
  // what tells the camera from the placeholder, which is still, so pressing Start does not offer
  // a recording until the camera's first frames are actually showing. A running recording is
  // never disabled: its button is how it stops.
  //
  // And switching the camera off ends one, and saves it: what follows is the placeholder squashed
  // into the camera's shape, which is nothing anybody wanted recorded. Only the switch does this,
  // from this page or from anywhere else, as `health` reports it. A stream that has merely
  // stopped keeps recording, because a clip across an outage is how F-74 was caught.
  function showRecordable() {
    if (recorder) {
      if (!streaming || !watch.streaming) { stopRecording(); }
      recordButton.disabled = false;
      return;
    }
    var live = streaming && watch.streaming && Date.now() - watch.pictureChangedAt < STALE;
    recordButton.disabled = !recordable || !live;
    recordButton.title = !recordable ? "This browser cannot record video"
      : live ? "Record video" : "Nothing to record until the camera's picture is showing";
  }

  function startRecording() {
    // Drawn at the picture's own resolution, which is the resolution the readout was burned in
    // at. Recording the stage instead would record the letterboxing and the zoom.
    var film = document.createElement("canvas");
    film.width = feed.naturalWidth;
    film.height = feed.naturalHeight;
    var pen = film.getContext("2d");
    // One frame before the recorder starts, so a clip stopped almost immediately still holds a
    // picture rather than nothing at all.
    paintKeepsake(pen, film.width, film.height);
    var made = makeRecorder(film.captureStream(RECORD_FPS));
    if (made === null) { return; }
    var format = made.format;
    recorded = [];
    recorder = made.recorder;
    recorder.ondataavailable = function (event) {
      if (event.data && event.data.size) { recorded.push(event.data); }
    };
    recorder.onstop = function () {
      recorder = null;
      showRecording(false);
      if (recorded.length) { offer(new Blob(recorded, { type: format.type }), format.extension); }
      recorded = [];
    };
    recorder.start();
    recordingSince = Date.now();
    recordingPainter = setInterval(function () {
      paintKeepsake(pen, film.width, film.height);
      if (Date.now() - recordingSince >= RECORD_LIMIT) { stopRecording(); }
    }, Math.round(1000 / RECORD_FPS));
    recordingClock = setInterval(function () { showRecording(true); }, 1000);
    showRecording(true);
  }

  function stopRecording() {
    if (recordingPainter !== null) { clearInterval(recordingPainter); recordingPainter = null; }
    if (recordingClock !== null) { clearInterval(recordingClock); recordingClock = null; }
    if (recorder && recorder.state !== "inactive") { recorder.stop(); }
  }

  recordButton.addEventListener("click", function () {
    if (recorder) { stopRecording(); return; }
    if (!feed.naturalWidth) { return; }
    startRecording();
  });

  // A clip nobody stops is a clip nobody gets, so leaving the page ends it rather than dropping it.
  window.addEventListener("pagehide", stopRecording);

  var recordable = canRecord();

  // The watchdog (Phase 7i, F-74). The picture is an <img> of a never ending response, and an
  // <img> says nothing when that response ends: it keeps the last frame, and the page looks live
  // over a picture that stopped. So the page looks for itself, at two things: its own picture,
  // sampled small, and the plugin's count of frames published, from `health`. A picture that has
  // stopped while the count moves is this page's stream, and reopening it is the cure. A count
  // that has stopped is the camera, and reopening the stream would fix nothing.
  var WATCH = WATCH_MS;
  var STALE = STALE_MS;
  var RESTART_GAP = RESTART_MS;
  var ASK_TIMEOUT = ASK_TIMEOUT_MS;
  var staleOut = document.getElementById("stale");
  var staleText = null;
  var sampler = document.createElement("canvas");
  sampler.width = 32;
  sampler.height = 24;
  var samplerPen = sampler.getContext("2d", { willReadFrequently: true });
  var lastSample = null;
  var restarts = 0;
  var watchTimer = null;
  var looking = false;
  // What the page knows, in wall clock milliseconds, because the badge tells the time.
  var watch = {
    pictureChangedAt: Date.now(),
    answered: true,
    silentSince: 0,
    streaming: true,
    // The plugin's count, or null until it has answered since the last gap. Forgotten across a
    // gap rather than kept: the capture idles while nothing can reach it, and a count compared
    // across the gap would blame the camera for that.
    count: null,
    countChangedAt: 0,
    restartedAt: 0,
    // The first restart since the picture last changed, which is what "reconnecting" counts from.
    firstRestartAt: 0
  };

  function clock(time) {
    var at = new Date(time);
    function two(value) { return String(value).padStart(2, "0"); }
    return two(at.getHours()) + ":" + two(at.getMinutes()) + ":" + two(at.getSeconds());
  }

  // The whole decision, in one place and with no page in it: what the page knows and the time
  // in, what to do out. `restart` is whether to reopen the stream now, `badge` the line to show,
  // or null for none. ROADMAP Phase 7i has it as a table.
  function decide(known, now) {
    if (!known.answered) {
      return { restart: false, badge: "printer not answering since " + clock(known.silentSince) };
    }
    // The placeholder is meant to be still.
    if (!known.streaming) { return { restart: false, badge: null }; }
    if (now - known.pictureChangedAt < STALE) { return { restart: false, badge: null }; }
    var since = clock(known.pictureChangedAt);
    if (now - known.countChangedAt >= STALE) {
      return { restart: false, badge: "no frames from the camera since " + since };
    }
    var attempting = known.firstRestartAt > known.pictureChangedAt;
    var badge = attempting && now - known.firstRestartAt >= STALE
      ? "reconnecting, no new picture since " + since : null;
    return { restart: now - known.restartedAt >= RESTART_GAP, badge: badge };
  }

  // Whether the picture on screen has changed since the last look. Drawn small, because all this
  // needs to know is whether anything moved, and a frozen stream is the same bytes exactly. The
  // stream is same origin, so reading it back is allowed.
  function samplePicture(now) {
    if (!feed.naturalWidth) { return; }
    var data;
    try {
      samplerPen.drawImage(feed, 0, 0, sampler.width, sampler.height);
      data = samplerPen.getImageData(0, 0, sampler.width, sampler.height).data;
    } catch (unreadable) {
      return;
    }
    var changed = lastSample === null || data.length !== lastSample.length;
    for (var index = 0; !changed && index < data.length; index += 1) {
      if (data[index] !== lastSample[index]) { changed = true; }
    }
    if (changed) { watch.pictureChangedAt = now; }
    lastSample = data;
  }

  function heard(health, now) {
    if (watch.count === null || health.frame !== watch.count) { watch.countChangedAt = now; }
    watch.count = health.frame;
    watch.answered = true;
    watch.streaming = health.streaming !== false;
  }

  // Dated when the question was asked, not when it gave up: the printer was already silent then,
  // and a timeout is four seconds later than the truth (2026-09-28).
  function unheard(askedAt) {
    if (watch.answered) { watch.silentSince = askedAt; }
    watch.answered = false;
    watch.count = null;
  }

  function showStale(text) {
    staleText = text;
    staleOut.textContent = text || "";
    staleOut.hidden = !text;
  }

  // A new URL is what makes the browser open a new request. The plugin drops the query string
  // before it looks the path up, so this reaches the same stream.
  function restartStream(now) {
    if (!(watch.firstRestartAt > watch.pictureChangedAt)) { watch.firstRestartAt = now; }
    watch.restartedAt = now;
    restarts += 1;
    feed.src = "stream.mjpg?n=" + restarts;
  }

  function act(now) {
    var verdict = decide(watch, now);
    if (verdict.restart) { restartStream(now); }
    showStale(verdict.badge);
    showRecordable();
  }

  function schedule(delay) {
    if (watchTimer !== null || document.hidden) { return; }
    watchTimer = setTimeout(look, delay);
  }

  function look() {
    watchTimer = null;
    if (looking || document.hidden) { return; }
    looking = true;
    var askedAt = Date.now();
    samplePicture(askedAt);
    var control = typeof AbortController === "undefined" ? null : new AbortController();
    var giveUp = control === null ? null
      : setTimeout(function () { control.abort(); }, ASK_TIMEOUT);
    fetch("health", { cache: "no-store", signal: control === null ? undefined : control.signal })
      .then(function (reply) { return reply.ok ? reply.json() : Promise.reject(reply.status); })
      .then(function (health) {
        heard(health, Date.now());
      })
      .catch(function () {
        unheard(askedAt);
      })
      .then(function () {
        if (giveUp !== null) { clearTimeout(giveUp); }
        looking = false;
        act(Date.now());
        schedule(WATCH);
      });
  }

  // A hidden page needs no watchdog and asks nothing, so the capture can idle behind it. Coming
  // back starts afresh: the picture gets its full allowance before it is called stale, because a
  // browser need not have painted a hidden page's picture at all, and the count is taken again.
  document.addEventListener("visibilitychange", function () {
    if (watchTimer !== null) { clearTimeout(watchTimer); watchTimer = null; }
    if (document.hidden) { return; }
    watch.pictureChangedAt = Date.now();
    lastSample = null;
    watch.count = null;
    look();
  });

  // The browser says when its own network comes back, and waiting for the next beat after that
  // only keeps the picture frozen for nothing. So the page asks at once.
  window.addEventListener("online", function () {
    if (watchTimer !== null) { clearTimeout(watchTimer); watchTimer = null; }
    look();
  });

  // A stream that fails outright says so, and is reopened at once, within the same limit. Not
  // while the printer is not answering, when reopening could only fail again.
  feed.addEventListener("error", function () {
    var now = Date.now();
    if (watch.answered && now - watch.restartedAt >= RESTART_GAP) { restartStream(now); }
  });

  // The watchdog's line, burned into a saved picture or a clip while it shows, so a recording can
  // never pass a frozen stretch off as a still scene. That confusion took F-74 an afternoon.
  function paintStale(pen, width, height) {
    if (!staleText) { return; }
    var size = Math.max(9, Math.round(height / 20));
    pen.font = "600 " + size + "px system-ui, sans-serif";
    pen.textBaseline = "top";
    pen.fillStyle = "rgba(13,15,18,0.8)";
    pen.fillRect(0, 0, width, Math.round(size * 1.5));
    pen.fillStyle = "#d8752a";
    pen.fillText(staleText, 4, Math.round(size * 0.25), width - 8);
  }

  // The unit, the spots and the switch all belong to the plugin rather than to this page, so they
  // are asked for rather than assumed.
  function adopt(state) {
    units = state.units;
    spots = Array.isArray(state.spots) ? state.spots : [];
    streaming = state.streaming !== false;
    showUnits();
    showSpots();
    showStream();
  }

  function readSettings() {
    return fetch("settings", { headers: { "Accept": "application/json" } })
      .then(function (reply) { return reply.ok ? reply.json() : Promise.reject(reply.status); })
      .then(adopt);
  }

  readSettings().catch(function () { showUnits(); });

  // Asked for again whenever this page comes back to the front. They are the plugin's settings, so
  // the settings form in another tab, a second browser, or the dashboard can have changed them
  // while this page was not looking, and a toolbar button showing the state from before that is
  // worse than one that is merely a moment late. Free, because it happens when a person returns
  // rather than on a timer: an unwatched viewer still fetches nothing.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { return; }
    readSettings().catch(function () {});
  });

  window.addEventListener("resize", paint);
  feed.addEventListener("load", paint);
  recallRegion();
  setMode("measure");
  showSpots();
  showStream();
  // Painted once at startup, and this is not a nicety. The picture element is positioned by paint
  // rather than by the stylesheet, so until something calls it the image is nought by nought and
  // the page is blank. Before this, a viewer opened with no region showed nothing at all until the
  // pointer happened to cross it, and every screenshot taken of it hid the bug by moving a mouse
  // first.
  paint();

  // The stream is an <img>, and an <img> showing a never-ending multipart response does not
  // reliably fire load, so there is no event to wait for. This watches until the first part has
  // decoded and the picture's shape is finally knowable, then paints properly and stops.
  (function awaitPicture(attempts) {
    if (pictureAspect() !== null) { paint(); return; }
    if (attempts > 600) { return; }
    requestAnimationFrame(function () { awaitPicture(attempts + 1); });
  })(0);

  if (region) { start(); }
  // Last, once everything it looks at exists.
  look();
})();
"""


def render_viewer_page(shape: tuple[int, int] | None = None) -> str:
    """The viewer, with its timings substituted so they are stated once, in Python.

    `shape` is the picture's width and height as it will be displayed, when the plugin knows it.
    It nearly always does, and saying so here saves the page from having to find out: the stream is
    an `<img>` of a never-ending multipart response, which does not reliably report its size until
    a part has decoded and fires no event when one does. Without the hint the page cannot lay
    anything out at all until the first frame arrives, which on a quiet stream is visible as a tile
    that stays blank for a second.
    """

    script = (
        VIEWER_SCRIPT.replace("POLL_MS", str(VIEWER_POLL_MILLISECONDS))
        .replace("LINGER_MS", str(VIEWER_LINGER_MILLISECONDS))
        .replace("MIN_REGION_PX", str(VIEWER_MINIMUM_REGION_PIXELS))
        .replace("REGION_KEY_NAME", VIEWER_REGION_KEY)
        .replace("MAX_ZOOM_VALUE", str(VIEWER_MAXIMUM_ZOOM))
        .replace("ZOOM_STEP_VALUE", str(VIEWER_ZOOM_STEP))
        .replace("RECORD_FPS_VALUE", str(VIEWER_RECORD_FPS))
        .replace("RECORD_LIMIT_MS", str(VIEWER_RECORD_LIMIT_MILLISECONDS))
        .replace("MAX_SPOTS_VALUE", str(MAX_SPOTS))
        .replace("WATCH_MS", str(VIEWER_WATCH_MILLISECONDS))
        .replace("STALE_MS", str(VIEWER_STALE_MILLISECONDS))
        .replace("RESTART_MS", str(VIEWER_RESTART_MILLISECONDS))
        .replace("ASK_TIMEOUT_MS", str(VIEWER_ASK_TIMEOUT_MILLISECONDS))
    )
    picture_shape = (
        f' data-width="{shape[0]}" data-height="{shape[1]}"' if shape else ""
    )
    return VIEWER_PAGE_TEMPLATE.format(viewer_script=script, picture_shape=picture_shape)
