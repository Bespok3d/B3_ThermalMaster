# Thermal Master

Watch a print thermally, in Fluidd or Mainsail, with no firmware flashing and no UVC support
required.

## What this is

The Thermal Master P1 and P3 (InfiRay-OEM thermal cameras, USB `3474:45c2` and `3474:45a2`) are not
standard webcams. They do not present a video device, so the kernel never creates a `/dev/video` node
and the hardware-accelerated camera plugin cannot use them. This plugin talks to the camera directly over USB,
turns its 16-bit temperature frame into a colour thermal image, and serves it as ordinary MJPEG.

- Resolution: 160x120 on the P1, 256x192 on the P3, about 25 fps. Frames are served at the sensor's
  own size and scaled by the browser, which is far cheaper on the printer than scaling them here.
- Stream: `/thermal/stream.mjpg`
- Snapshot: `/thermal/snapshot.jpg`
- Temperatures: `/thermal/stats`
- Interactive viewer: `/thermal/view`
- Every pixel's temperature: `/thermal/frame.bin`
- Settings and camera state: `/thermal/settings`, which also accepts a JSON POST to change
  individual settings without disturbing the rest

## Setting it up

Install it, pick a name for the camera, and it appears in Fluidd and Mainsail alongside your other
cameras. There is nothing to add by hand under Settings.

## The control page

Open `/thermal/` on the printer, or follow the plugin's link in the Bespok3d app. It shows the live
view and everything there is to change: palette, rotation in quarter turns, mirroring, the
temperature readout and its units, emissivity, sensor gain, and a button to recalibrate the sensor.
Changes take effect immediately and are remembered across restarts.

Rotation is applied here rather than in Fluidd's own camera settings, because a camera defined by a
config file is read-only there: the panel shows "Managed by your Moonraker configuration" and greys
the controls out.

One thing rotation cannot fix. Both cameras are 4:3, so a quarter turn makes the picture portrait,
and a portrait picture cannot fill a landscape tile: you get black bars at the sides and a smaller
image. If that matters more to you than the orientation, the real fix is to mount the camera the
other way round.

## The temperature readout

Four switches, all on by default, usable in any combination. With all of them off the plugin costs
exactly what it did before there was a readout at all.

It draws four things into the picture itself:

- A colorbar down the right edge, spanning the coldest and hottest temperatures in view and
  labelled with them, in the same colours as the markers that name those pixels.

  Two ticks across it mark where the auto-ranging stops. Between them the colour varies; above and
  below them the bar is one flat colour, because so is the picture. The mapping ignores the top and
  bottom two percent of the scene so that one glint or one dead pixel cannot wash everything out,
  and anything past that is drawn in the end colour. The flat bands are not decoration: they are
  where colour stops carrying information.
- A crosshair in the middle, with the temperature under it.
- A red marker on the hottest pixel in view, with its temperature.
- A blue marker on the coldest pixel, with its temperature.

Where two markers land close together, the second one moves its number rather than writing over the
first. The ruler ticks whichever extremes are switched on, so what the bar says and what the markers
say never disagree.

Burned into the picture rather than drawn over it, so the stream and the still carry their own
readout wherever they are opened, including anything that fetches them outside this plugin. The cost is that switching the readout on doubles
the encoded size, so the text survives being scaled by a browser. Switching it off returns the
plugin to what it cost without a readout.

`/thermal/stats` serves the same numbers as JSON, along with the frame average and the coldest
pixel, and the pixel coordinates of both extremes in the orientation you are looking at.

## The interactive viewer

`/thermal/view`, and it is the camera this plugin registers in Fluidd and Mainsail, under the name
you chose at install time. There is one tile, and this is it. Point at the picture and it tells you
the temperature of the pixel under the pointer, along with the hottest and coldest in view.

In a tile it shows the picture, the readout and the controls, and hovering works there. When the
tile is wider than the picture needs, which it usually is with a rotated camera, the numbers sit
beside the picture rather than underneath it, so the picture gets the height. The controls sit under
the picture in a single compact row, and grow when you open the page full size.

The stream on its own is still there at `/thermal/stream.mjpg`, and the still at
`/thermal/snapshot.jpg`, for anything that wants a plain picture rather than a page. The viewer does
not need its script to show one either: with JavaScript off the stylesheet fits the picture into the
tile by itself, so the worst a broken script costs you is the pointing.

Zoom with the wheel or the buttons and it magnifies towards what you are pointing at; Fit puts it
back. Pan mode drags the picture when you are zoomed in, which is a mode rather than a held key
because Measure mode uses the same gesture for the box and a phone has no key to hold.

The units button changes the unit for the whole plugin rather than just this page, so the readout
burned into the picture agrees with it. Save image writes a PNG at the sensor's own resolution
with the region drawn on it, because a picture of a measurement that does not show what was measured
is not evidence of anything.

Drag a box on the picture and it reports the hottest, coldest and average temperature inside it,
updating as fast as you drag. Click anywhere to clear it, or press Escape. The box is remembered
across reloads, and while one exists the viewer keeps reading frames even with the pointer away,
which is the reason to draw one: box the bed, walk away, come back to a number that means something.

It reads temperatures itself. `/thermal/frame.bin` hands it every pixel as hundredths of a degree
with emissivity already applied, so pointing is a lookup in data the page already holds rather than
a question to the printer, and the physics has one implementation rather than two. Nothing is
fetched until you point at something and it stops a few seconds after you stop, so a tile nobody is
using costs nothing beyond the stream itself.

## Camera controls

**Emissivity** is how much of what a surface radiates is its own heat rather than a reflection of
its surroundings. Matte plastic emits almost all of it, which is why the default is 0.95; bare
aluminium emits almost none and reads far colder than it is until you say so. It changes the numbers
only, never the picture, and it is applied to the temperatures reported rather than to the frame,
which is both correct for the extremes and the only version this processor can afford.

**Gain** picks what the sensor is measuring. High sensitivity covers -20 to 150 C and is the default,
which is the right range for a bed, an enclosure and a warming nozzle. Wide range covers 0 to 550 C
at lower sensitivity, which is what you want pointed at a hotend at printing temperature. The
camera's own automatic mode is not offered because the protocol does not implement it.

**Calibrate now** closes the shutter inside the camera for a moment and re-levels the sensor against
a known uniform surface. The camera does this by itself about every ninety seconds, so the button is
for when the picture has visibly drifted and you would rather not wait. It costs one frame.

The plugin sends the calibration command itself rather than using the vendored driver's helper for
it. That helper also reads back the frame the camera emits afterwards, and it locates that frame
with offsets measured on a P3, which on a P1 point well past the end of the frame buffer. The
command is identical on both; the frame afterwards is one the plugin discards anyway.

All three are sent from the thread that owns the camera, between two frames, never from the web
request that asked for them: a command shares its USB endpoints with the video, so sending one
mid-frame desynchronises the stream. Pressing the button repeatedly gets you one calibration.

Temperatures remain the camera's own readings with an emissivity correction applied. Good for
watching a nozzle warm up or finding a cold corner of a bed; not metrology.

## Notes

- The image is auto-ranged: the coldest and hottest areas in view map to the ends of the palette, so
  contrast follows the scene. The range eases rather than jumping, so the picture stays steady when
  something warm passes through. It is still a relative thermal view, not a calibrated temperature
  readout: the same nozzle can be a different colour depending on what else is in frame.
- Six palettes are available (ironbow, rainbow, white hot, black hot, military, sepia). Ironbow is
  the default, and the control page changes it live.
- The streamer reconnects on its own if the camera is unplugged and replugged.
- The camera tile updates about fifteen times a second, which is Moonraker's default polling rate
  rather than a limit of the camera. Raising `target_fps` on the `[webcam]` entry makes it smoother
  at the cost of more requests.
- Both the P1 (160x120) and the P3 (256x192) are driven. The plugin probes the USB bus and uses
  whichever it finds, so there is nothing to set. Only one thermal camera at a time.
- The stream is not behind authentication, which matches every other camera on the printer. Anyone
  who can reach the printer's web interface can watch the thermal feed.

## Hardware status

Run end to end on a U1 with a P1 attached: the camera tile renders in Fluidd and Mainsail, the
control page changes palette, orientation and the readout live, and the settings survive a restart.
Measured at 41.5% of one of the printer's four Cortex-A53 cores with the readout on, of which about
half is the numpy pipeline rather than the JPEG encode. The P3 is driven by the same code path and
the same protocol but has not been in front of one yet.

Worth checking after an install:

1. Plug the camera in and confirm `lsusb` shows `3474:45c2` (P1) or `3474:45a2` (P3).
2. Install the plugin and confirm the service is running.
3. Open `/thermal/snapshot.jpg` and then `/thermal/stream.mjpg` in a browser.
4. Confirm the camera tile appears in Fluidd or Mainsail under the name you chose, and shows the
   stream rather than a broken image.
5. Unplug and replug the camera, and confirm the stream recovers on its own.
6. Uninstall, and confirm the camera tile disappears and nothing is left behind.

## Credits

USB driver vendored from [jvdillon/p3-ir-camera](https://github.com/jvdillon/p3-ir-camera)
(Apache-2.0), at a pinned commit. Protocol reverse-engineering by @aeternium. See `ATTRIBUTIONS.md`.
