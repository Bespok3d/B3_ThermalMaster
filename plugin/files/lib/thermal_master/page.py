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

import html
import time

from .camera import (
    GAIN_DESCRIPTIONS,
    GAIN_HIGH,
    SHUTTER_DONE,
    SHUTTER_FAILED,
    SHUTTER_IDLE,
    SHUTTER_PENDING,
    START_STREAM_ACTION,
    STOP_STREAM_ACTION,
    VALID_GAINS,
)
from .colour_scale import SCALE_NAMES, VALID_COLOUR_SCALES
from .cost import describe_cost
from .pipeline import VALID_RANGE_MODES, VALID_ROTATIONS, VALID_UPSCALE_FILTERS
from .recording import BYTES_PER_MEGABYTE, gain_switch_sentence
from .temperature import EMISSIVITY_MATCH, EMISSIVITY_PRESETS, VALID_UNITS
from .timelapse import (
    COPY_READOUT_ACTION,
    FORGET_KEY_ACTION,
    MAX_AUTO_GAIN_CELSIUS,
    MAX_TIMELAPSE_KEEP,
    MIN_AUTO_GAIN_CELSIUS,
    MIN_TIMELAPSE_KEEP,
    TIMELAPSE_SWITCHES,
    VALID_TIMELAPSE_RANGES,
)
from .version import plugin_version

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


# The four scales, named for what they do to the picture.
COLOUR_SCALE_DESCRIPTIONS = {
    "stretch": "Stretch",
    "knee": "Knee, hot end squeezed in",
    "log-mild": "Log, gentle",
    "log-strong": "Log, strong",
}


# How the timelapse's colour range is offered, in the order it is offered.
TIMELAPSE_RANGE_DESCRIPTIONS = {
    "from-start": "Fixed once the print has started",
    "whole-print": "The whole print, coldest to hottest",
    "fixed": "Hold these temperatures",
    "as-displayed": "The same as the live picture",
}


# What a finished recording's state is called in the list of timelapses.
RECORDING_STATE_DESCRIPTIONS = {
    "printing": "Recording now",
    "complete": "Finished",
    "cancelled": "Cancelled",
    "error": "Stopped by an error",
    "interrupted": "Interrupted, by a restart or a power cut",
    "stopped": "Recording switched off during the print",
}


HIGH_SENSITIVITY_WARNING = (
    '<p class="status">The camera is in high sensitivity, which reads nothing above about 205 C, '
    "so a hotter nozzle in view stops there. Wide range is under Camera, or tick the switch "
    "above.</p>"
)


# Said instead while the automatic switch is on: nothing to fix, only what will happen.
AUTO_GAIN_NOTE = (
    '<p class="status">The camera is in high sensitivity. While a print is recorded, it switches '
    "to wide range for the rest of the print when something passes {celsius:.0f} C.</p>"
)


# Added to the camera's line while the timelapse holds it in wide range for a print.
OVERRIDDEN_GAIN_SENTENCE = (
    " Wide range for this print, switched by the timelapse; the chosen gain comes back when the "
    "print ends."
)


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


# Material Icons' "info", outlined, from @material-design-icons/svg 0.14.15, under the Apache
# License 2.0 (see NOTICE). Inline, since the page loads nothing from anywhere else.
INFO_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 7h2v2h-2zm0 4h2v6h-2zm1-9C6.48 2 2 '
    '6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 '
    '8 8-3.59 8-8 8z"/></svg>'
)


# What each (i) explains, by the placeholder it fills: what it is about, for a screen reader, and
# the explanation. They were paragraphs at the foot of the page until 0.28.6, a long way from the
# options they explained and long enough that nobody read to the end of them.
INFO_TEXTS = {
    "enlarging": (
        "enlarging",
        "Enlarging is how the picture is made bigger before the readout is drawn on it, which only "
        "happens while some part of the readout is switched on. Smooth blends the sensor's pixels; "
        "sharp leaves them as squares and is about a sixth less work for the printer per frame. "
        "Which one looks better depends on the scene and the screen.",
    ),
    "range": (
        "the colours",
        "Following the scene spreads the palette over the middle of what is in view, so contrast "
        "is always as good as it can be, and a colour means nothing in particular: it changes "
        "whenever the scene does. Holding two temperatures fixes it, so a colour means the same "
        "temperature in every frame. \"Hold what I see now\" fills the two boxes from the picture "
        "in front of you. Either way the ruler spans the colours, and a triangle at one end says "
        "the scene goes past it. Holding costs the printer slightly less work, since there is "
        "nothing to measure.",
    ),
    "colour_scale": (
        "the colour scale",
        "How temperatures are spread over the palette. Stretch spreads it evenly over the range "
        "and draws anything hotter in the top colour: the most contrast for the bed and the part, "
        "none for the nozzle. Knee is the stretch below a bend, with everything hotter squeezed "
        "into the top of the palette, so the nozzle still shows. The two logs spread the whole "
        "scene and give most of the colours to its cool end, strong more so than gentle. Over a "
        "held range, the logs keep to the held temperatures and the knee squeezes in anything "
        "hotter. It changes the picture, never the numbers, and the curves cost the printer "
        "slightly more than the stretch.",
    ),
    "readout": (
        "the readout",
        "The readout is drawn into the picture, so it shows in the printer's camera tile too. "
        "Anything switched on here encodes at a larger size, so the text stays legible; switching "
        "all of it off costs nothing at all. The same numbers, and the frame average, are at "
        '<a href="stats">stats</a>.',
    ),
    "emissivity": (
        "emissivity",
        "Emissivity is how much of what a surface radiates is its own heat rather than a "
        "reflection of the room, so a shiny surface reads cold until you tell the plugin it is "
        "shiny. It changes the numbers only, never the picture.",
    ),
    "camera": (
        "calibrating and stopping the camera",
        "Calibration closes the camera's internal shutter for a moment and re-levels the sensor "
        "against it. The camera does this by itself about every ninety seconds; the button is for "
        "when the picture has drifted and you would rather not wait. It costs one frame. Stopping "
        "the camera releases it completely: nothing is read, nothing is rendered, and the printer "
        "pays nothing at all for having the plugin installed. Everything that shows the camera "
        "shows a \"Stream off\" picture instead, it survives a restart, and the same switch is in "
        "the camera view's toolbar.",
    ),
    "cost": (
        "the cost line",
        "This plugin's own share of the printer's processor, read from the kernel rather than "
        "estimated, and updated while this page is open. The printer has four cores, so 100% of "
        "one core is a quarter of the machine, and the figure can pass 100% because the plugin "
        "has more than one thread. Expect it to be highest here, because a settings page is a "
        "live stream and a live stream is somebody watching.",
    ),
    "timelapse": (
        "the timelapse",
        "The timelapse takes one frame each time the layer number changes, and one more when the "
        "print ends, and makes them into a clip once it has. It needs the slicer to tell Klipper "
        "the layer number with <code>SET_PRINT_STATS_INFO</code>; the plugin's README says what to "
        "add. It keeps the temperatures rather than pictures, so the clip is drawn when it is "
        "made. Its colours are fixed once the print has started, which keeps what the bed and "
        "nozzle did before the first layer out of it, the whole print's coldest to hottest, "
        "temperatures of your own, or the live picture's. The oldest prints go once there are "
        "more than the number to keep, and sooner if the printer's disk runs short of space. "
        "Where Moonraker has a Timelapse page, as the U1 does and mainline Klipper does with "
        "moonraker-timelapse, each clip is copied there too, named after the printer's own clip "
        "of the print when it made one.",
    ),
    "auto_gain": (
        "the switch to wide range",
        "High sensitivity reads nothing above about 205 C, so a hotter nozzle in view stops "
        "there in every layer. With this ticked, while a print is being recorded, the first frame "
        "with something past the temperature below switches the camera to wide range for the "
        "rest of the print, and the gain chosen under Camera comes back when the print ends. No "
        "frame is taken for five seconds after the switch, while the camera recalibrates. It "
        "waits until it is needed: below about 200 C the two gains agree on a nozzle, and for "
        "everything cooler high sensitivity is the more accurate. Wide range reads the room 5 to "
        "10 C low and its picture is noisier. The temperature is the one the readout shows; 195 C "
        "switches before high sensitivity runs out, and one at or above what it can read never "
        "switches.",
    ),
    "clip_readout": (
        "what is drawn into the clips",
        "The clips' readout is their own, so a clip can come out plain while the camera tile "
        "keeps its numbers, or the other way round. \"Copy the readout from the live view\" ticks "
        "the boxes ticked in the Readout section as last applied, and placed spots if any are "
        "placed. The palette and the colour scale are always the live picture's. The colour "
        "scale in the corner, or in the name, is for telling apart clips of one print made with "
        "different scales.",
    ),
    "moonraker_key": (
        "the Moonraker key",
        "Only needed when Moonraker asks for a login. It is kept on the printer and never shown "
        "again, and \"Forget the saved key\" removes it.",
    ),
    "timelapses": (
        "the list of timelapses",
        "Each print's clip, newest first. \"Make the clip again with the current colours\" draws "
        "it again from the kept temperatures, with the palette, colour scale and clip readout "
        "chosen now, and puts it back on the Timelapse page under the same name. Only the two "
        "newest prints keep their temperatures, so older ones can be played, downloaded or "
        "deleted, but not made again.",
    ),
}


def info(key: str) -> str:
    """An (i) and what it explains, as three siblings for the stylesheet to show and hide."""

    about, text = INFO_TEXTS[key]
    return (
        f'<input type="checkbox" class="info-toggle" id="info-{key}" aria-label="About {about}">'
        f'<label class="info" for="info-{key}">{INFO_ICON}</label>'
        f'<p class="about">{text}</p>'
    )


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
  /* A fieldset is as wide as its widest content unless told otherwise, and a select is as wide as
     its longest option: on a phone the Timelapse panel ran off the right of the screen. */
  fieldset {{ border: 1px solid #33373f; border-radius: 6px; margin: 1rem 0 0; padding: 0.75rem;
              min-width: 0; }}
  legend {{ padding: 0 0.4rem; color: #9aa0aa; font-size: 0.85rem; }}
  label {{ display: flex; align-items: center; gap: 0.6rem; margin: 0.4rem 0; }}
  label span {{ min-width: 7rem; }}
  select {{ flex: 1; min-width: 0; padding: 0.35rem; background: #1d2026; color: inherit;
            border: 1px solid #33373f; border-radius: 4px; }}
  input[type="number"] {{ width: 6rem; padding: 0.35rem; background: #1d2026; color: inherit;
                          border: 1px solid #33373f; border-radius: 4px; }}
  button:disabled {{ opacity: 0.4; cursor: default; }}
  button {{ margin-top: 0.8rem; margin-right: 0.5rem; padding: 0.5rem 1.1rem; border: 0;
            border-radius: 4px; background: #d8752a; color: #14161a; font-weight: 600;
            cursor: pointer; }}
  .status {{ margin: 0.6rem 0 0; font-size: 0.8rem; }}
  /* The way back. This page is reached from a link in the viewer, and in a Fluidd tile that link
     navigates the tile itself: there is no browser chrome around an iframe, so without this the
     only way back to the camera was to reload the dashboard. */
  .back {{ display: inline-block; margin-bottom: 0.6rem; color: #9aa0aa; font-size: 0.85rem; }}
  p {{ color: #9aa0aa; font-size: 0.85rem; }}
  input[type="password"] {{ flex: 1; padding: 0.35rem; background: #1d2026; color: inherit;
                            border: 1px solid #33373f; border-radius: 4px; }}
  h2 {{ margin: 1.4rem 0 0.4rem; font-size: 1rem; font-weight: 600; }}
  .clip {{ border: 1px solid #33373f; border-radius: 6px; margin: 0.6rem 0; padding: 0.6rem; }}
  .clip video {{ width: 100%; border-radius: 4px; background: #000; display: block; }}
  .clip p {{ margin: 0.4rem 0; }}
  .clip form {{ display: inline; }}
  .clip a {{ color: #d8752a; margin-right: 0.8rem; }}
  /* An option and its (i), side by side, with the explanation underneath once asked for. The
     explanation is a sibling of a checkbox nobody sees, so hovering the icon shows it for as long
     as the pointer stays, and clicking it keeps it until the next click. No script: it has to
     work in whatever opens the page, a Fluidd tile included. The checkbox has no name, so the form
     never posts it and the script that reflects settings back never touches it. */
  .field {{ position: relative; display: flex; flex-wrap: wrap; align-items: center;
            column-gap: 0.4rem; }}
  .field > :first-child {{ flex: 1; min-width: 0; }}
  .info-toggle {{ position: absolute; opacity: 0; width: 1px; height: 1px; margin: 0; }}
  label.info {{ display: inline-flex; margin: 0; cursor: pointer; color: #9aa0aa; }}
  label.info svg {{ width: 18px; height: 18px; fill: currentColor; }}
  .info-toggle:checked + label.info {{ color: #d8752a; }}
  .info-toggle:focus-visible + label.info {{ outline: 2px solid #d8752a; border-radius: 50%; }}
  .about {{ display: none; flex-basis: 100%; margin: 0.2rem 0 0.6rem; }}
  .info-toggle:checked + label.info + .about {{ display: block; }}
  /* Only where there is a pointer that hovers. A phone keeps the last thing tapped "hovered" until
     the next tap somewhere else, so with this rule everywhere a second tap unpinned the text and
     the hover still showed it. */
  @media (hover: hover) {{
    label.info:hover {{ color: #d8752a; }}
    label.info:hover + .about {{ display: block; }}
  }}
  /* Beside an Apply that has something to apply, put there by the script. */
  .pending {{ margin-right: 0.5rem; color: #d8752a; font-size: 0.8rem; }}
  .version {{ margin-top: 1.4rem; font-size: 0.75rem; }}
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
      <div class="field">
        <label><span>Enlarging</span>
               <select name="upscale_filter">{upscale_filter_options}</select></label>
        {info_enlarging}
      </div>
      <button type="submit">Apply</button>
    </fieldset>
    <fieldset>
      <legend>Range</legend>
      <div class="field">
        <label><span>Colours</span><select name="range_mode">{range_mode_options}</select></label>
        {info_range}
      </div>
      <div class="field">
        <label><span>Colour scale</span>
               <select name="colour_scale">{colour_scale_options}</select></label>
        {info_colour_scale}
      </div>
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
      <div class="field">
        <label><input type="checkbox" name="colorbar"{colorbar}>
               Temperature ruler down the edge</label>
        {info_readout}
      </div>
      <label><input type="checkbox" name="reticle"{reticle}>
             Centre crosshair and its reading</label>
      <label><input type="checkbox" name="hotspot"{hotspot}>
             Hottest pixel</label>
      <label><input type="checkbox" name="coldspot"{coldspot}>
             Coldest pixel</label>
      <label><span>Units</span><select name="units">{unit_options}</select></label>
      <div class="field">
        <label><span>Emissivity</span>
               <select name="emissivity">{emissivity_options}</select></label>
        {info_emissivity}
      </div>
      <button type="submit">Apply</button>
    </fieldset>
    <fieldset>
      <legend>Camera</legend>
      <label><span>Gain</span><select name="gain">{gain_options}</select></label>
      <div class="field">
        <div class="buttons">
          <button type="submit">Apply</button>
          <button type="submit" name="command" value="shutter">Calibrate now</button>
          <button type="submit" name="command" value="{stream_command}"
                  id="stream-switch">{stream_label}</button>
        </div>
        {info_camera}
      </div>
      <p class="status" id="device-status">{device_status}</p>
      <div class="field">
        <p class="status" id="plugin-cost">{plugin_cost}</p>
        {info_cost}
      </div>
    </fieldset>
    <fieldset id="timelapse">
      <legend>Timelapse</legend>
      <div class="field">
        <label><input type="checkbox" name="timelapse"{timelapse}>
               Record one frame per layer of every print</label>
        {info_timelapse}
      </div>
      <label><span>Keep</span>
             <input type="number" name="timelapse_keep" min="{keep_minimum}" max="{keep_maximum}"
                    step="1" value="{timelapse_keep}"> prints</label>
      <div class="field">
        <label><input type="checkbox" name="timelapse_auto_gain"{timelapse_auto_gain}>
               Switch to wide range when something passes</label>
        {info_auto_gain}
      </div>
      <label><span>Passes</span>
             <input type="number" name="timelapse_auto_gain_celsius" min="{auto_gain_minimum}"
                    max="{auto_gain_maximum}" step="1" value="{auto_gain_celsius}"> C</label>
      <label><span>Colours</span>
             <select name="timelapse_range_mode">{timelapse_range_options}</select></label>
      <label><span>From</span>
             <input type="number" name="timelapse_range_low_celsius" step="0.1"
                    value="{timelapse_low}"> C</label>
      <label><span>To</span>
             <input type="number" name="timelapse_range_high_celsius" step="0.1"
                    value="{timelapse_high}"> C</label>
      <div class="field">
        <p class="status">Drawn into the clips:</p>
        {info_clip_readout}
      </div>
      <label><input type="checkbox" name="timelapse_colorbar"{timelapse_colorbar}>
             Temperature ruler down the edge</label>
      <label><input type="checkbox" name="timelapse_reticle"{timelapse_reticle}>
             Centre crosshair and its reading</label>
      <label><input type="checkbox" name="timelapse_hotspot"{timelapse_hotspot}>
             Hottest pixel</label>
      <label><input type="checkbox" name="timelapse_coldspot"{timelapse_coldspot}>
             Coldest pixel</label>
      <label><input type="checkbox" name="timelapse_spots"{timelapse_spots}>
             Placed spots</label>
      <label><input type="checkbox" name="timelapse_scale_label"{timelapse_scale_label}>
             Write the colour scale in the corner of each clip</label>
      <label><input type="checkbox" name="timelapse_scale_in_name"{timelapse_scale_in_name}>
             Put the colour scale in the clip's name</label>
      <button type="submit" name="command" value="{copy_readout_command}">Copy the readout from
              the live view</button>
      <div class="field">
        <label><span>Moonraker key</span>
               <input type="password" name="moonraker_api_key" autocomplete="off"
                      placeholder="{key_placeholder}"></label>
        {info_moonraker_key}
      </div>
      <button type="submit">Apply</button>
      <button type="submit" name="command" value="{forget_key_command}"
              id="forget-key"{forget_disabled}>Forget the saved key</button>
      <p class="status" id="timelapse-status">{timelapse_status}</p>
      <div id="gain-note">{gain_note}</div>
    </fieldset>
  </form>
  <section id="timelapses">
    <div class="field">
      <h2>Timelapses</h2>
      {info_timelapses}
    </div>
    <div id="timelapse-list">{timelapse_list}</div>
  </section>
  <p>Each Apply applies its own section, and changes survive a restart. The picture takes about
     a second to settle afterwards, while the auto-ranging finds the scene again.</p>
  <p class="version">Thermal Master {plugin_version}</p>
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
  var timelapseLine = document.getElementById("timelapse-status");
  var timelapseList = document.getElementById("timelapse-list");
  var gainNote = document.getElementById("gain-note");
  var listedFor = timelapseLine ? timelapseLine.textContent : "";
  if (!form || !line || !window.fetch || !window.JSON || !form.closest) { return; }
  // getAttribute, not form.action. A named control shadows a form property of the same name, so
  // form.action is only the URL as long as nothing in the form is called "action". The attribute
  // is always the attribute.
  var endpoint = form.getAttribute("action");
  var polls = 0;
  var givingUp = false;

  // What each field showed when the plugin last said what it was set to, by name. A panel whose
  // fields all still show that has nothing to apply, so its Apply is greyed out until one changes.
  var applied = {};
  // The panel whose Apply is waiting for its answer. Its fields take the answer whatever they
  // show; every other panel keeps what is being edited in it.
  var applying = null;
  // Whether the answer should empty the key box: only once the key in it has gone up, or after
  // the saved one was forgotten. Applying another panel leaves a half typed key where it is.
  var clearsKey = false;

  // The pressed button lives in the form itself rather than being appended to one request body,
  // because a programmatic submit does not include the submitter. Anything below that falls back
  // to a plain submit would otherwise silently drop the calibrate button and reload the page
  // looking like it had worked.
  var pressed = document.createElement("input");
  pressed.type = "hidden";
  form.appendChild(pressed);

  function named(scope) {
    return Array.prototype.filter.call(scope.querySelectorAll("input, select"), function (field) {
      return field.name && field.type !== "hidden";
    });
  }

  function current(field) { return field.type === "checkbox" ? field.checked : field.value; }

  // The page writes temperatures to one decimal and the plugin answers with a number, so "20.0"
  // and "20" are the same setting and must not count as a change.
  function same(field, one, other) {
    if (one === other) { return true; }
    return field.type === "number" && one !== "" && other !== "" && Number(one) === Number(other);
  }

  // The key is never sent back, so its box is a change whenever anything is typed into it.
  function changed(field) {
    if (field.type === "password") { return field.value !== ""; }
    return !same(field, current(field), applied[field.name]);
  }

  function applyButton(panel) { return panel.querySelector("button[type=submit]:not([name])"); }

  var panels = Array.prototype.filter.call(form.querySelectorAll("fieldset"), applyButton);
  var notes = panels.map(function (panel) {
    var button = applyButton(panel);
    var note = document.createElement("span");
    note.className = "pending";
    note.textContent = "Not applied yet";
    note.hidden = true;
    button.parentNode.insertBefore(note, button.nextSibling);
    return note;
  });

  function mark() {
    panels.forEach(function (panel, index) {
      var waiting = named(panel).some(changed);
      applyButton(panel).disabled = !waiting;
      notes[index].hidden = !waiting;
    });
  }

  named(form).forEach(function (field) { applied[field.name] = current(field); });
  mark();
  form.addEventListener("input", mark);
  form.addEventListener("change", mark);

  // Each Apply applies its own panel and nothing else, and every other button sends only what it
  // is for. They used to post the whole form, so pressing Apply under Image also applied whatever
  // was half changed under Timelapse, and Calibrate now applied both.
  form.addEventListener("submit", function (event) {
    if (givingUp || !event.submitter) { return; }
    var button = event.submitter;
    var panel = button.closest("fieldset");
    if (!button.name && !panel) { return; }
    event.preventDefault();
    if (button.name) { command(button); } else { applyPanel(panel); }
  });

  // Enter in a field applies the panel the field is in. Left to the browser it would press the
  // first button in the form, which is the Image panel's Apply wherever the field was.
  form.addEventListener("keydown", function (event) {
    var field = event.target;
    if (givingUp || event.key !== "Enter" || field.tagName !== "INPUT") { return; }
    event.preventDefault();
    var panel = field.name ? field.closest("fieldset") : null;
    var button = panel ? applyButton(panel) : null;
    if (button && !button.disabled) { applyPanel(panel); }
  });

  // A number goes up as a number, because that is what the plugin checks a rotation against. An
  // empty box goes up empty, which the plugin reads as "leave it as it was".
  function valueOf(field) {
    if (field.type === "checkbox") { return field.checked; }
    var numeric = field.type === "number" || field.tagName === "SELECT";
    if (numeric && field.value !== "" && isFinite(Number(field.value))) {
      return Number(field.value);
    }
    return field.value;
  }

  function applyPanel(panel) {
    var body = {};
    named(panel).forEach(function (field) {
      // An empty key box means "keep the saved key"; sent empty it would forget it.
      if (field.type === "password" && field.value === "") { return; }
      body[field.name] = valueOf(field);
    });
    pressed.name = "";
    pressed.value = "";
    clearsKey = "moonraker_api_key" in body;
    send(body, panel);
  }

  function command(button) {
    var body = {};
    body[button.name] = button.value;
    pressed.name = button.name;
    pressed.value = button.value;
    clearsKey = button.id === "forget-key";
    send(body, null);
  }

  function send(body, panel) {
    applying = panel;
    polls = 0;
    ask({ method: "POST", body: JSON.stringify(body), headers: {
      "Accept": "application/json",
      "Content-Type": "application/json"
    }});
  }

  function ask(options) {
    fetch(endpoint, options).then(function (reply) {
      return reply.ok ? reply.json() : Promise.reject(reply.status);
    }).then(show).catch(giveUp);
  }

  // Posts the whole form, the way the page works without this script. Only for when the plugin
  // did not answer as expected, and a reload that applies everything beats a page that pretends.
  function giveUp() {
    givingUp = true;
    form.submit();
  }

  function show(state) {
    line.textContent = state.device;
    reflect(state);
    showStream(state);
    showCost(state);
    forgetSecrets(state);
    if (gainNote && typeof state.gain_note === "string" && gainNote.innerHTML !== state.gain_note) {
      gainNote.innerHTML = state.gain_note;
    }
    mark();
    // A calibration is applied by the capture thread between two frames, so the answer to the post
    // itself is always "requested". Ask again a few times, briefly, for what actually happened.
    if (state.pending && polls < 8) {
      polls += 1;
      setTimeout(function () { ask({ headers: { "Accept": "application/json" } }); }, 300);
    }
  }

  // The reply carries the whole of the settings, and the page has to accept the answer it gets
  // back: "hold what I see now" switches the plugin to a fixed range, and a page still saying
  // "follow the scene" would undo the hold at the next Apply. What the answer must not do is wipe
  // out a change being made in another panel. So a field takes the answer when it is in the panel
  // just applied, or when the plugin's value is not the one it had before, which is a button
  // having changed it. Otherwise it keeps what it shows, and only what it is compared with moves.
  function reflect(state) {
    named(form).forEach(function (field) {
      if (field.type === "password" || !(field.name in state)) { return; }
      var mine = current(field);
      put(field, state[field.name]);
      var saved = current(field);
      var answered = applying !== null && applying.contains(field);
      if (!answered && same(field, saved, applied[field.name])) { put(field, mine); }
      applied[field.name] = saved;
    });
    applying = null;
  }

  function put(field, value) {
    if (field.type === "checkbox") {
      field.checked = !!value;
    } else if (!(field.type === "number" && same(field, field.value, String(value)))) {
      choose(field, value);
    }
  }

  function showCost(state) {
    if (costLine && typeof state.cost === "string") { costLine.textContent = state.cost; }
    if (timelapseLine && typeof state.timelapse_status === "string") {
      timelapseLine.textContent = state.timelapse_status;
      if (state.timelapse_status !== listedFor) {
        listedFor = state.timelapse_status;
        refreshTimelapses();
      }
    }
  }

  // The list was drawn once, when the page loaded, and a clip finished while the page was open
  // stayed "Recording now" until a reload. It is asked for again whenever the timelapse's line
  // changes, as the same HTML the page was drawn with, and left alone while a clip is playing, so
  // somebody watching one is not interrupted by the next.
  function refreshTimelapses() {
    if (!timelapseList) { return; }
    fetch("timelapses.html", { headers: { "Accept": "text/html" } })
      .then(function (reply) { return reply.ok ? reply.text() : Promise.reject(reply.status); })
      .then(function (markup) {
        var playing = Array.prototype.some.call(
          timelapseList.querySelectorAll("video"), function (clip) { return !clip.paused; });
        if (!playing && timelapseList.innerHTML !== markup) { timelapseList.innerHTML = markup; }
      })
      .catch(function () {});
  }

  // The key is never sent back, so the box it was typed into is emptied once it has gone up, and
  // the button that forgets it is only offered while there is one to forget.
  function forgetSecrets(state) {
    var key = form.elements.namedItem("moonraker_api_key");
    var forget = document.getElementById("forget-key");
    var saved = !!state.moonraker_api_key_set;
    if (key) {
      if (clearsKey) { key.value = ""; }
      key.placeholder = saved ? "Saved" : "Not set";
    }
    clearsKey = false;
    if (forget) { forget.disabled = !saved; }
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
    line = said.get(shutter.get("state"), "Camera state is not available.")
    return line + OVERRIDDEN_GAIN_SENTENCE if status.get("gain_overridden") else line


def option(value: str, label: str, selected: bool) -> str:
    return f'<option value="{value}"{" selected" if selected else ""}>{label}</option>'


def gain_note(settings: dict) -> str:
    """The line under the Timelapse section about the camera's gain, or nothing when there is none.

    Sent with the settings as well as drawn into the page. It was only drawn, so ticking or
    unticking the switch and applying left it saying what the page had said when it loaded.
    """

    if not (settings["timelapse"] and settings["gain"] == GAIN_HIGH):
        return ""
    if settings["timelapse_auto_gain"]:
        return AUTO_GAIN_NOTE.format(celsius=settings["timelapse_auto_gain_celsius"])
    return HIGH_SENSITIVITY_WARNING


def timelapse_fields(settings: dict) -> dict:
    """The Timelapse section's placeholders, from the settings a page may be shown."""

    return {
        "timelapse": " checked" if settings["timelapse"] else "",
        "keep_minimum": MIN_TIMELAPSE_KEEP,
        "keep_maximum": MAX_TIMELAPSE_KEEP,
        "timelapse_keep": settings["timelapse_keep"],
        "timelapse_range_options": "".join(
            option(
                name, TIMELAPSE_RANGE_DESCRIPTIONS[name], name == settings["timelapse_range_mode"]
            )
            for name in VALID_TIMELAPSE_RANGES
        ),
        "timelapse_low": f"{settings['timelapse_range_low_celsius']:.1f}",
        "timelapse_high": f"{settings['timelapse_range_high_celsius']:.1f}",
        "key_placeholder": "Saved" if settings["moonraker_api_key_set"] else "Not set",
        "forget_key_command": FORGET_KEY_ACTION,
        "forget_disabled": "" if settings["moonraker_api_key_set"] else " disabled",
        "gain_note": gain_note(settings),
        "auto_gain_minimum": f"{MIN_AUTO_GAIN_CELSIUS:.0f}",
        "auto_gain_maximum": f"{MAX_AUTO_GAIN_CELSIUS:.0f}",
        "auto_gain_celsius": f"{settings['timelapse_auto_gain_celsius']:.0f}",
        "copy_readout_command": COPY_READOUT_ACTION,
        **{switch: " checked" if settings[switch] else "" for switch in TIMELAPSE_SWITCHES},
    }


def described_size(size: int) -> str:
    return f"{size / BYTES_PER_MEGABYTE:.1f} MB"


def clip_entry(summary: dict) -> str:
    """One print in the list: its clip if there is one, what it was, and what can be done to it."""

    quoted = html.escape(str(summary["id"]), quote=True)
    started = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(summary["started_at"]))
    state = RECORDING_STATE_DESCRIPTIONS.get(summary["state"], summary["state"])
    video = (
        f'<video controls preload="none" poster="timelapse.jpg?id={quoted}" '
        f'src="timelapse.mp4?id={quoted}"></video>'
        if summary["has_clip"]
        else ""
    )
    download = (
        f'<a href="timelapse.mp4?id={quoted}&amp;download=1">Download, '
        f"{described_size(summary['clip_bytes'])}</a>"
        if summary["has_clip"]
        else ""
    )
    # Not offered while the print is still being recorded: the service would refuse it, and a
    # button that does nothing is worse than none.
    # Nor while it is waiting to be made into a clip or being made into one, for the same reason.
    held = summary["state"] == "printing" or bool(summary.get("busy"))
    delete = "" if held else clip_button("delete", quoted, "Delete")
    remake = (
        clip_button("remake", quoted, "Make the clip again with the current colours")
        if summary["has_frames"] and not held
        else ""
    )
    error = f" {html.escape(summary['error'])}" if summary.get("error") else ""
    if summary.get("busy") and summary["state"] != "printing":
        error += " Being made into a clip."
    if summary.get("scale"):
        named = SCALE_NAMES.get(str(summary["scale"]), str(summary["scale"]))
        error += f" Colour scale: {html.escape(named)}."
    error += gain_switch_sentence(summary.get("gain_switch"))
    if summary.get("published_as"):
        error += " Also on the Timelapse page."
    elif summary.get("publish_error"):
        error += f" {html.escape(summary['publish_error'])}"
    return (
        f'<article class="clip">{video}'
        f"<p><strong>{html.escape(str(summary['filename'] or 'A print'))}</strong>, {started}. "
        f"{state}, {summary['frames']} frames.{error}</p>"
        f"<div>{download}{remake}{delete}</div></article>"
    )


def clip_button(field: str, quoted: str, label: str) -> str:
    return (
        f'<form method="post" action="timelapses">'
        f'<input type="hidden" name="{field}" value="{quoted}">'
        f'<button type="submit">{label}</button></form>'
    )


def timelapse_list(timelapses: dict | None) -> str:
    summaries = (timelapses or {}).get("timelapses") or []
    if not summaries:
        return "<p>No timelapses yet.</p>"
    return "".join(clip_entry(summary) for summary in summaries)


def render_control_page(
    settings: dict,
    palette_names: list,
    device_status: dict | None = None,
    cost: str | None = None,
    timelapses: dict | None = None,
) -> str:
    """The page itself. Plain form, no JavaScript: it has to work in whatever opens it."""

    stream_command, stream_label = STREAM_SWITCH[bool(settings["streaming"])]
    return CONTROL_PAGE_TEMPLATE.format(
        **{f"info_{key}": info(key) for key in INFO_TEXTS},
        plugin_version=html.escape(plugin_version()),
        **timelapse_fields(settings),
        timelapse_status=html.escape((timelapses or {}).get("status") or ""),
        timelapse_list=timelapse_list(timelapses),
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
        colour_scale_options="".join(
            option(name, COLOUR_SCALE_DESCRIPTIONS[name], name == settings["colour_scale"])
            for name in VALID_COLOUR_SCALES
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
