# Thermal Master P1

Stream a **Thermal Master P1** USB thermal camera on your Snapmaker U1, with no firmware
flashing and no UVC support required.

## What this is

The Thermal Master P1 (an InfiRay-OEM thermal camera, USB `3474:45c2`) is **not** a standard
webcam. It does not present a UVC/V4L2 video device, so the kernel never creates a `/dev/video`
node for it and the hardware-accelerated camera plugin cannot use it. This plugin talks to the
camera directly over USB, turns its 16-bit temperature frame into a colour thermal image, and
serves it as a normal MJPEG stream.

- Resolution: 160x120, about 25 fps
- Stream: `http://<printer>/thermal/stream.mjpg`
- Snapshot: `http://<printer>/thermal/snapshot.jpg`

## Add it to Fluidd / Mainsail

Under **Settings -> Cameras**, add a camera:

- Type: **MJPEG-Stream**
- URL / Stream: `/thermal/stream.mjpg`
- Snapshot: `/thermal/snapshot.jpg`

## Notes

- The image is auto-ranged each frame (the coldest and hottest areas map to the ends of the
  palette), so contrast adapts to the scene. It is a relative thermal view, not a calibrated
  temperature readout.
- Only the P1 (160x120) is enabled. The same upstream driver also supports the P3 (256x192); a
  P3 variant would be a small change.
- The streamer reconnects on its own if the camera is unplugged and replugged.

## Not verified on hardware yet

This plugin's USB protocol is ported from documented, working upstream code but has not been run
end to end on a U1 with a P1 attached. Device-trial checklist:

1. Plug the P1 in; confirm `lsusb` shows `3474:45c2`.
2. Install the plugin; confirm `s65thermal-p1 status` reports running.
3. Open `/thermal/snapshot.jpg` and `/thermal/stream.mjpg` in a browser.
4. Confirm the vendored aarch64 `numpy`/`Pillow` import under the U1's `/usr/bin/python3` (the
   wheels target CPython 3.11; if the device python differs, re-run `fetch-vendor.sh` with the
   matching ABI).
5. Unplug/replug the camera; confirm the stream recovers.

## Credits

USB driver vendored from [jvdillon/p3-ir-camera](https://github.com/jvdillon/p3-ir-camera)
(Apache-2.0). Protocol reverse-engineering by @aeternium. See the repository `NOTICE`.
