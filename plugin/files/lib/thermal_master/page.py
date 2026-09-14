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
    VALID_GAINS,
)
from .pipeline import VALID_ROTATIONS
from .temperature import EMISSIVITY_MATCH, EMISSIVITY_PRESETS, VALID_UNITS

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
  button {{ margin-top: 0.8rem; margin-right: 0.5rem; padding: 0.5rem 1.1rem; border: 0;
            border-radius: 4px; background: #d8752a; color: #14161a; font-weight: 600;
            cursor: pointer; }}
  .status {{ margin: 0.6rem 0 0; font-size: 0.8rem; }}
  p {{ color: #9aa0aa; font-size: 0.85rem; }}
</style>
</head>
<body>
<main>
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
      <button type="submit">Apply</button>
    </fieldset>
    <fieldset>
      <legend>Readout</legend>
      <label><input type="checkbox" name="overlay"{overlay}>
             Show the colorbar, centre reading and hotspot</label>
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
      <p class="status" id="device-status">{device_status}</p>
    </fieldset>
  </form>
  <p>Changes take effect immediately and survive a restart. The picture takes about a second to
     settle afterwards, while the auto-ranging finds the scene again.</p>
  <p>The readout is drawn into the picture, so it shows in the printer's camera tile too. Turning it
     on encodes at a larger size, so the text stays legible. The same numbers, plus the frame
     average and the coldest pixel, are at <a href="stats">stats</a>.</p>
  <p>Emissivity is how much of what a surface radiates is its own heat rather than a reflection of
     the room, so a shiny surface reads cold until you tell the plugin it is shiny. It changes the
     numbers only, never the picture.</p>
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
    // A calibration is applied by the capture thread between two frames, so the answer to the post
    // itself is always "requested". Ask again a few times, briefly, for what actually happened.
    if (state.pending && polls < 8) {
      polls += 1;
      setTimeout(function () { ask({ headers: { "Accept": "application/json" } }); }, 300);
    }
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
    settings: dict, palette_names: list, device_status: dict | None = None
) -> str:
    """The page itself. Plain form, no JavaScript: it has to work in whatever opens it."""

    def option(value: str, label: str, selected: bool) -> str:
        return f'<option value="{value}"{" selected" if selected else ""}>{label}</option>'

    return CONTROL_PAGE_TEMPLATE.format(
        emissivity_options="".join(
            option(f"{value:.2f}", label, abs(value - settings["emissivity"]) < EMISSIVITY_MATCH)
            for value, label in EMISSIVITY_PRESETS
        ),
        gain_options="".join(
            option(name, GAIN_DESCRIPTIONS[name], name == settings["gain"])
            for name in VALID_GAINS
        ),
        device_status=describe_device(device_status),
        control_script=CONTROL_SCRIPT,
        palette_options="".join(
            option(name, name.replace("-", " "), name == settings["palette"])
            for name in palette_names
        ),
        rotation_options="".join(
            option(str(degrees), f"{degrees} degrees", degrees == settings["rotation"])
            for degrees in VALID_ROTATIONS
        ),
        unit_options="".join(
            option(name, name.capitalize(), name == settings["units"]) for name in VALID_UNITS
        ),
        flip_horizontal=" checked" if settings["flip_horizontal"] else "",
        flip_vertical=" checked" if settings["flip_vertical"] else "",
        overlay=" checked" if settings["overlay"] else "",
    )
