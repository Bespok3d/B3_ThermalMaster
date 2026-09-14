# Thermal Master

Watch a print thermally, in Fluidd or Mainsail, with no firmware flashing and no UVC support
required.

## What this is

The Thermal Master P1 and P3 (InfiRay-OEM thermal cameras, USB `3474:45c2` and `3474:45a2`) are not
standard webcams. They do not present a video device, so the kernel never creates a `/dev/video` node
and the hardware-accelerated camera plugin cannot use them. This plugin talks to the camera directly over USB,
turns its 16-bit temperature frame into a colour thermal image, and serves it as ordinary MJPEG.

- Resolution: 160x120 on the P1, 256x192 on the P3, about 25 fps
- Stream: `/thermal/stream.mjpg`
- Snapshot: `/thermal/snapshot.jpg`

## Setting it up

Install it, pick a name for the camera, and it appears in Fluidd and Mainsail alongside your other
cameras. There is nothing to add by hand under Settings.

If you want to change the name later, reinstall the plugin and enter a different one.

## Notes

- The image is auto-ranged: the coldest and hottest areas in view map to the ends of the palette, so
  contrast follows the scene. The range eases rather than jumping, so the picture stays steady when
  something warm passes through. It is still a relative thermal view, not a calibrated temperature
  readout: the same nozzle can be a different colour depending on what else is in frame.
- Six palettes are available (ironbow, rainbow, white hot, black hot, military, sepia). Ironbow is
  the default; changing it currently means editing the service arguments.
- The streamer reconnects on its own if the camera is unplugged and replugged.
- The camera tile updates about fifteen times a second, which is Moonraker's default polling rate
  rather than a limit of the camera. Raising `target_fps` on the `[webcam]` entry makes it smoother
  at the cost of more requests.
- Both the P1 (160x120) and the P3 (256x192) are driven. The plugin probes the USB bus and uses
  whichever it finds, so there is nothing to set. Only one thermal camera at a time.
- The stream is not behind authentication, which matches every other camera on the printer. Anyone
  who can reach the printer's web interface can watch the thermal feed.

## Not verified on hardware yet

The USB protocol is ported from documented, working upstream code, but this plugin has not been run
end to end on a U1 with a camera attached. Device trial checklist:

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
