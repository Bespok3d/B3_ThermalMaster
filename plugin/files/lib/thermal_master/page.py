# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The control page served at the plugin's root.

Plain HTML and a form, because it has to work in whatever opens it. The script on top of that is
progressive enhancement and nothing depends on it: with it, a settings change does not tear down
the video stream; without it, the form posts and the browser reloads.

Every URL the page emits is relative. The plugin serves at "/" and nginx publishes it under a prefix
it never sees, so an absolute path is a guess about the mount point, and it has been wrong twice.
"""

from __future__ import annotations

from .camera import (
    GAIN_DESCRIPTIONS,
    SHUTTER_DONE,
    SHUTTER_FAILED,
    SHUTTER_IDLE,
    SHUTTER_PENDING,
    START_STREAM_ACTION,
    STOP_STREAM_ACTION,
    VALID_GAINS,
)
from .cost import describe_cost
from .pipeline import VALID_RANGE_MODES, VALID_ROTATIONS, VALID_UPSCALE_FILTERS
from .temperature import EMISSIVITY_MATCH, EMISSIVITY_PRESETS, VALID_UNITS

# What the two filters are called on the page. The names are about what a person sees rather than
# about the algorithm: nobody choosing how their camera looks wants to be asked about bilinear
# interpolation.
UPSCALE_FILTER_DESCRIPTIONS = {
    "smooth": "Smooth",
    "sharp": "Sharp, cheaper",
}


# The two ways the display range is decided, named for what they do rather than for how.
RANGE_MODE_DESCRIPTIONS = {
    "auto": "Follow the scene",
    "fixed": "Hold these temperatures",
}


# What the switch says, and what pressing it asks for. Labelled by what it will do rather than by
# what is happening, which is the convention every play button follows, and the word on it is the
# word the placeholder picture tells people to look for.
STREAM_SWITCH = {
    True: (STOP_STREAM_ACTION, "Stop the camera"),
    False: (START_STREAM_ACTION, "Start the camera"),
}


# How often the page asks what the plugin is costing. Slow enough to be free and fast enough that
# switching the camera off in one window is visible in the number a moment later.
COST_POLL_MILLISECONDS = 5000


CONTROL_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thermal Master</title>
<!-- Every URL on this page is relative, and it has to stay that way. The plugin serves the page at
     "/" and nginx publishes it at "/thermal/" with the prefix stripped on the way in, so the plugin
     is never told what the browser called it. An absolute path is therefore a guess about the mount
     point: it was wrong for the redirect, which sent people to the Fluidd dashboard, and wrong for
     the form action, which made the page work only behind nginx. Relative references resolve
     against whatever the browser asked for, which is the one thing that is always right. -->
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; padding: 1rem; background: #14161a; color: #e8e8ea;
         font: 15px/1.5 system-ui, sans-serif; }}
  main {{ max-width: 34rem; margin: 0 auto; }}
  img {{ width: 100%; border-radius: 6px; background: #000; display: block; }}
  fieldset {{ border: 1px solid #33373f; border-radius: 6px; margin: 1rem 0 0; padding: 0.75rem; }}
  legend {{ padding: 0 0.4rem; color: #9aa0aa; font-size: 0.85rem; }}
  label {{ display: flex; align-items: center; gap: 0.6rem; margin: 0.4rem 0; }}
  label span {{ min-width: 7rem; }}
  select {{ flex: 1; padding: 0.35rem; background: #1d2026; color: inherit;
            border: 1px solid #33373f; border-radius: 4px; }}
  input[type="number"] {{ width: 6rem; padding: 0.35rem; background: #1d2026; color: inherit;
                          border: 1px solid #33373f; border-radius: 4px; }}
  button {{ margin-top: 0.8rem; margin-right: 0.5rem; padding: 0.5rem 1.1rem; border: 0;
            border-radius: 4px; background: #d8752a; color: #14161a; font-weight: 600;
            cursor: pointer; }}
  .status {{ margin: 0.6rem 0 0; font-size: 0.8rem; }}
  /* The way back. This page is reached from a link in the viewer, and in a Fluidd tile that link
     navigates the tile itself: there is no browser chrome around an iframe, so without this the
     only way back to the camera was to reload the dashboard. */
  .back {{ display: inline-block; margin-bottom: 0.6rem; color: #9aa0aa; font-size: 0.85rem; }}
  p {{ color: #9aa0aa; font-size: 0.85rem; }}
</style>
</head>
<body>
<main>
  <a class="back" href="view">Back to the camera</a>
  <img src="stream.mjpg" alt="Live thermal view">
  <form id="controls" method="post" action="settings">
    <fieldset>
      <legend>Image</legend>
      <label><span>Palette</span><select name="palette">{palette_options}</select></label>
      <label><span>Rotate</span><select name="rotation">{rotation_options}</select></label>
      <label><input type="checkbox" name="flip_horizontal"{flip_horizontal}>
             Mirror left to right</label>
      <label><input type="checkbox" name="flip_vertical"{flip_vertical}>
             Mirror top to bottom</label>
      <label><span>Enlarging</span>
             <select name="upscale_filter">{upscale_filter_options}</select></label>
      <button type="submit">Apply</button>
    </fieldset>
    <fieldset>
      <legend>Range</legend>
      <label><span>Colours</span><select name="range_mode">{range_mode_options}</select></label>
      <label><span>From</span>
             <input type="number" name="range_low_celsius" step="0.1"
                    value="{range_low}"> C</label>
      <label><span>To</span>
             <input type="number" name="range_high_celsius" step="0.1"
                    value="{range_high}"> C</label>
      <button type="submit">Apply</button>
      <button type="submit" name="command" value="lock-range">Hold what I see now</button>
    </fieldset>
    <fieldset>
      <legend>Readout</legend>
      <label><input type="checkbox" name="colorbar"{colorbar}>
             Temperature ruler down the edge</label>
      <label><input type="checkbox" name="reticle"{reticle}>
             Centre crosshair and its reading</label>
      <label><input type="checkbox" name="hotspot"{hotspot}>
             Hottest pixel</label>
      <label><input type="checkbox" name="coldspot"{coldspot}>
             Coldest pixel</label>
      <label><span>Units</span><select name="units">{unit_options}</select></label>
      <label><span>Emissivity</span>
             <select name="emissivity">{emissivity_options}</select></label>
      <button type="submit">Apply</button>
    </fieldset>
    <fieldset>
      <legend>Camera</legend>
      <label><span>Gain</span><select name="gain">{gain_options}</select></label>
      <button type="submit">Apply</button>
      <button type="submit" name="command" value="shutter">Calibrate now</button>
      <button type="submit" name="command" value="{stream_command}"
              id="stream-switch">{stream_label}</button>
      <p class="status" id="device-status">{device_status}</p>
      <p class="status" id="plugin-cost">{plugin_cost}</p>
    </fieldset>
  </form>
  <p>Following the scene maps the coldest and hottest thing in view to the ends of the palette, so
     contrast is always as good as it can be and a colour means nothing in particular: it changes
     whenever the scene does, which is what makes the picture breathe when a toolhead crosses it.
     Holding two temperatures fixes the mapping, so a colour means the same thing in every frame
     and the ruler becomes a constant reference. Anything outside the held range is drawn in the
     end colour, and a triangle on the ruler says the scene has gone past it. "Hold what I see now"
     fills the two boxes from the picture in front of you, which is usually easier than guessing
     numbers. Holding also costs the printer slightly less work, since there is nothing to
     measure.</p>
  <p>Enlarging is how the picture is made bigger before the readout is drawn on it, which only
     happens while some part of the readout is switched on. Smooth blends the sensor's pixels;
     sharp leaves them as squares and is about a sixth less work for the printer per frame. Which
     one looks better depends on the scene and the screen, so it is here rather than decided for
     you.</p>
  <p>Changes take effect immediately and survive a restart. The picture takes about a second to
     settle afterwards, while the auto-ranging finds the scene again.</p>
  <p>The readout is drawn into the picture, so it shows in the printer's camera tile too. Anything
     switched on here encodes at a larger size, so the text stays legible; switching all of it off
     costs nothing at all. The ruler also ticks whichever extremes you are marking, so the two
     always agree. The same numbers, plus the frame
     average and the coldest pixel, are at <a href="stats">stats</a>.</p>
  <p>Emissivity is how much of what a surface radiates is its own heat rather than a reflection of
     the room, so a shiny surface reads cold until you tell the plugin it is shiny. It changes the
     numbers only, never the picture.</p>
  <p>The cost line is this plugin's own share of the printer's processor, read from the kernel
     rather than estimated, and it updates while this page is open. The printer has four cores, so
     100% of one core is a quarter of the machine, and the figure can pass 100% because the plugin
     has more than one thread. Expect it to be highest here, because a settings page is a live
     stream and a live stream is somebody watching.</p>
  <p>Stopping the camera releases it completely: nothing is read, nothing is rendered, and the
     printer pays nothing at all for having the plugin installed. Everything that shows the camera
     shows a "Stream off" picture instead of an error, and the temperatures behind it stop being
     offered, because there are none behind a picture of words. It survives a restart, so a printer
     that reboots overnight comes back the way you left it. The same switch is in the camera
     view's toolbar.</p>
  <p>Calibration closes the camera's internal shutter for a moment and re-levels the sensor against
     it. The camera does this by itself about every ninety seconds; the button is for when the
     picture has drifted and you would rather not wait. It costs one frame.</p>
</main>
{control_script}
</body>
</html>
"""


# Progressive enhancement, and nothing depends on it. Without JavaScript the form posts, the server
# redirects, and the browser reloads the page: correct, but it also tears down the video stream and
# opens a new one on every change, which shows as the picture blinking out for a moment just as the
# auto-ranging is already re-settling. With it, the settings go up in the background and the picture
# is never interrupted. Anything that goes wrong falls back to submitting the form normally.
CONTROL_SCRIPT = """<script>
(function () {
  var form = document.getElementById("controls");
  var line = document.getElementById("device-status");
  var streamSwitch = document.getElementById("stream-switch");
  var costLine = document.getElementById("plugin-cost");
  if (!form || !line || !window.fetch || !window.FormData || !window.URLSearchParams) { return; }
  // getAttribute, not form.action. A named control shadows a form property of the same name, so
  // form.action is only the URL as long as nothing in the form is called "action". The attribute
  // is always the attribute.
  var endpoint = form.getAttribute("action");
  var polls = 0;
  var givingUp = false;

  // The pressed button lives in the form itself rather than being appended to one request body,
  // because a programmatic submit does not include the submitter. Anything below that falls back
  // to a plain submit would otherwise silently drop the calibrate button and reload the page
  // looking like it had worked.
  var pressed = document.createElement("input");
  pressed.type = "hidden";
  form.appendChild(pressed);

  form.addEventListener("submit", function (event) {
    if (givingUp) { return; }
    if (!event.submitter) { return; }
    pressed.name = event.submitter.name || "";
    pressed.value = event.submitter.value || "";
    event.preventDefault();
    polls = 0;
    post(new URLSearchParams(new FormData(form)).toString());
  });

  function post(body) {
    ask({ method: "POST", body: body, headers: {
      "Accept": "application/json",
      "Content-Type": "application/x-www-form-urlencoded"
    }});
  }

  function ask(options) {
    fetch(endpoint, options).then(function (reply) {
      return reply.ok ? reply.json() : Promise.reject(reply.status);
    }).then(show).catch(giveUp);
  }

  function giveUp() {
    givingUp = true;
    form.submit();
  }

  function show(state) {
    line.textContent = state.device;
    reflect(state);
    showStream(state);
    showCost(state);
    // A calibration is applied by the capture thread between two frames, so the answer to the post
    // itself is always "requested". Ask again a few times, briefly, for what actually happened.
    if (state.pending && polls < 8) {
      polls += 1;
      setTimeout(function () { ask({ headers: { "Accept": "application/json" } }); }, 300);
    }
  }

  // The reply carries the whole of the settings, and this page used to throw all of it away
  // except the status line. That was harmless while every change came from the form itself, and
  // stopped being harmless the moment a button changed something the form was showing: "hold what
  // I see now" switched the plugin to a fixed range, the page went on saying "follow the scene",
  // and the next Apply posted what it was saying and undid the hold. A page that posts in the
  // background has to accept the answer it gets back.
  function reflect(state) {
    for (var index = 0; index < form.elements.length; index += 1) {
      var field = form.elements[index];
      // Whatever is being typed into belongs to the person typing, not to the last reply.
      if (!field.name || !(field.name in state) || field === document.activeElement) { continue; }
      if (field.type === "checkbox") {
        field.checked = !!state[field.name];
      } else {
        choose(field, state[field.name]);
      }
    }
  }

  function showCost(state) {
    if (costLine && typeof state.cost === "string") { costLine.textContent = state.cost; }
  }

  // Asked for on its own timer, and only this line is touched with the answer. Running the whole
  // of `show` would reflect every setting back into the form every few seconds, which is a fine
  // way to overwrite a select somebody is halfway through changing. The request itself is free:
  // it reads no frame and wakes nothing, and this page is holding a video stream open anyway, so
  // the plugin is fully awake for as long as anyone is here to read the number.
  function pollCost() {
    fetch(endpoint, { headers: { "Accept": "application/json" } })
      .then(function (reply) { return reply.ok ? reply.json() : Promise.reject(reply.status); })
      .then(showCost)
      .catch(function () {});
  }

  if (costLine) { setInterval(pollCost, COST_POLL_MS); }

  // The one control the loop above cannot handle: it is a button, so its name is "command" and
  // not the name of a setting, and what has to change is the label and the value it posts rather
  // than a field's contents.
  function showStream(state) {
    if (!streamSwitch || typeof state.streaming !== "boolean") { return; }
    streamSwitch.value = state.streaming ? "stop-stream" : "start-stream";
    streamSwitch.textContent = state.streaming ? "Stop the camera" : "Start the camera";
  }

  // A select only takes a string one of its options actually carries, and this page writes some of
  // those to a fixed number of decimals: an emissivity of 1 is the option "1.00". Spellings are
  // tried in turn rather than special cased by field name, and a value that matches nothing leaves
  // the field showing what it showed.
  function choose(field, value) {
    var spellings = [String(value)];
    if (typeof value === "number") { spellings.push(value.toFixed(1), value.toFixed(2)); }
    var was = field.value;
    for (var index = 0; index < spellings.length; index += 1) {
      field.value = spellings[index];
      if (field.value === spellings[index]) { return; }
    }
    field.value = was;
  }
})();
</script>"""


def describe_device(status: dict | None) -> str:
    """One line on what the camera is doing, since the page has no JavaScript to ask again.

    The form posts and redirects, so the reload after a press is the report: by the time the page
    comes back the capture thread has been round the loop and the shutter has either fired or said
    why it could not.
    """

    if status is None:
        return "Camera state is not available."
    if status.get("streaming") is False:
        return "The camera is switched off. Nothing is being read and nothing is being rendered."
    shutter = status.get("shutter", {})
    detail = shutter.get("detail")
    said = {
        SHUTTER_IDLE: "Calibration has not been asked for since this service started.",
        SHUTTER_PENDING: "Calibration requested, waiting for the next frame.",
        SHUTTER_DONE: "Last calibration completed.",
        SHUTTER_FAILED: f"Last calibration failed: {detail}",
    }
    return said.get(shutter.get("state"), "Camera state is not available.")


def render_control_page(
    settings: dict,
    palette_names: list,
    device_status: dict | None = None,
    cost: str | None = None,
) -> str:
    """The page itself. Plain form, no JavaScript: it has to work in whatever opens it."""

    def option(value: str, label: str, selected: bool) -> str:
        return f'<option value="{value}"{" selected" if selected else ""}>{label}</option>'

    stream_command, stream_label = STREAM_SWITCH[bool(settings["streaming"])]
    return CONTROL_PAGE_TEMPLATE.format(
        plugin_cost=cost if cost is not None else describe_cost(None),
        stream_command=stream_command,
        stream_label=stream_label,
        emissivity_options="".join(
            option(f"{value:.2f}", label, abs(value - settings["emissivity"]) < EMISSIVITY_MATCH)
            for value, label in EMISSIVITY_PRESETS
        ),
        gain_options="".join(
            option(name, GAIN_DESCRIPTIONS[name], name == settings["gain"])
            for name in VALID_GAINS
        ),
        device_status=describe_device(device_status),
        control_script=CONTROL_SCRIPT.replace("COST_POLL_MS", str(COST_POLL_MILLISECONDS)),
        palette_options="".join(
            option(name, name.replace("-", " "), name == settings["palette"])
            for name in palette_names
        ),
        rotation_options="".join(
            option(str(degrees), f"{degrees} degrees", degrees == settings["rotation"])
            for degrees in VALID_ROTATIONS
        ),
        range_mode_options="".join(
            option(name, RANGE_MODE_DESCRIPTIONS[name], name == settings["range_mode"])
            for name in VALID_RANGE_MODES
        ),
        range_low=f"{settings['range_low_celsius']:.1f}",
        range_high=f"{settings['range_high_celsius']:.1f}",
        upscale_filter_options="".join(
            option(name, UPSCALE_FILTER_DESCRIPTIONS[name], name == settings["upscale_filter"])
            for name in VALID_UPSCALE_FILTERS
        ),
        unit_options="".join(
            option(name, name.capitalize(), name == settings["units"]) for name in VALID_UNITS
        ),
        flip_horizontal=" checked" if settings["flip_horizontal"] else "",
        flip_vertical=" checked" if settings["flip_vertical"] else "",
        colorbar=" checked" if settings["colorbar"] else "",
        reticle=" checked" if settings["reticle"] else "",
        hotspot=" checked" if settings["hotspot"] else "",
        coldspot=" checked" if settings["coldspot"] else "",
    )
