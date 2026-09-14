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

## Setting it up

Install it, pick a name for the camera, and it appears in Fluidd and Mainsail alongside your other
cameras. There is nothing to add by hand under Settings.

## The control page

Open `/thermal/` on the printer, or follow the plugin's link in the Bespok3d app. It shows the live
view and lets you change the palette, rotate the image in quarter turns, mirror it, switch the
temperature readout on or off, and pick Celsius or Fahrenheit. Changes take effect immediately and
are remembered across restarts.

Rotation is applied here rather than in Fluidd's own camera settings, because a camera defined by a
config file is read-only there: the panel shows "Managed by your Moonraker configuration" and greys
the controls out.

One thing rotation cannot fix. Both cameras are 4:3, so a quarter turn makes the picture portrait,
and a portrait picture cannot fill a landscape tile: you get black bars at the sides and a smaller
image. If that matters more to you than the orientation, the real fix is to mount the camera the
other way round.

## The temperature readout

On by default. It draws three things into the picture itself:

- A colorbar down the right edge, labelled with the temperatures at each end of the palette. Those
  are the ends of the range currently being mapped, not the hottest and coldest pixels, so it tells
  you what a colour means.
- A crosshair in the middle, with the temperature under it.
- A marker on the hottest pixel in view, with its temperature.

Into the picture rather than onto this page, because the camera tile in Fluidd and Mainsail is a
plain image and there is nowhere else to put them. The cost is that switching the readout on doubles
the encoded size, so the text survives being scaled by a browser. Switching it off returns the
plugin to what it cost without a readout.

`/thermal/stats` serves the same numbers as JSON, along with the frame average and the coldest
pixel, and the pixel coordinates of both extremes in the orientation you are looking at.

The temperatures are the camera's own uncorrected readings. Emissivity is not applied yet, so a
shiny surface will read low and a matte one close to true. Good for watching a nozzle warm up or
finding a cold corner of a bed; not metrology.

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
The P3 is driven by the same code path and the same protocol but has not been in front of one yet.

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
