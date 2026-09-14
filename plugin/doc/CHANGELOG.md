# Changelog

## 0.7.0

A temperature readout, drawn into the picture.

- A colorbar down the right edge, labelled at both ends with the range currently mapped to the
  palette.
- A crosshair in the centre with the temperature under it, and a marker on the hottest pixel in
  view with its temperature.
- Celsius or Fahrenheit, from the control page.
- The readout can be switched off, which returns the plugin to exactly what 0.6.0 cost.
- `/thermal/stats` serves the same numbers as JSON, plus the frame average and the coldest pixel,
  for anything that would rather draw its own.

The readout is drawn into the frame rather than onto the control page, because the surface that
matters is the camera tile in Fluidd and Mainsail, and that is a plain image with nowhere to hang
an annotation. Switching it on doubles the encoded size so the text stays legible, which is the
only part of it that costs anything measurable.

Temperatures are the camera's own uncorrected readings. Emissivity is not applied yet, so treat
them as a good guide to a scene and not as measurements.

## 0.6.0

A control page, at `/thermal/` on the printer.

- Palette, rotation and mirroring can be changed while the camera is running. No reinstall, no
  editing files, and the choice survives a restart.
- Orientation is now applied by the plugin rather than by the browser. Rotating in Fluidd needed a
  setting that could only be changed by reinstalling, and the installer did not reliably carry the
  choice through, so a sideways camera could not be corrected at all. Fluidd's own rotation stays at
  zero to avoid rotating twice.
- Rotation and mirroring are no longer install-time questions, since they are live settings now.

Note that changing a setting costs about a second: the auto-ranging restarts and has to find the
scene again.

## 0.5.3

- The tile shape is now an install setting. Rotating by 90 or 270 turns a 4:3 image into a 3:4 one,
  and the tile stayed 4:3, so the picture sat inside it with black bars at the sides and shrank to
  fit. Set the tile shape to match the rotation and it fills again.

## 0.5.2

- Much lighter on the printer. The stream was being upscaled four times and encoded at 640x480,
  twenty-five times a second, so the browser could scale it back down to fit the tile. Measured at
  62.5% of a CPU core, of which the resize and the oversized encode were about four fifths. Frames
  are now encoded at the sensor's own size, with the quality raised to compensate, and the browser
  does the scaling it was going to do anyway. Expect a large drop in CPU with no visible difference.

## 0.5.1

- Rotation and mirroring are now install settings. A camera registered from a config file is
  read-only in Fluidd's settings panel, which shows "Managed by your Moonraker configuration" and
  greys the controls out, so there was no way to correct a camera that is mounted sideways. Both are
  applied by the browser, so they cost the printer nothing.

## 0.5.0

The picture, rather than the plumbing.

- The image no longer flickers. The displayed temperature range was recalculated from scratch every
  frame, so the whole picture re-scaled whenever anything warm entered or left the view. The range
  now eases towards the scene over about a second instead of snapping to it.
- Six palettes: ironbow, rainbow, white hot, black hot, military, sepia. Ironbow stays the default.
  Switching still means changing the service argument; live switching comes with the control page.
- Temporal noise reduction. Each frame is averaged with the one before, which halves the per-pixel
  sensor noise that made still scenes look like they were simmering.
- Detail enhancement. A mild unsharp mask, so a part reads as a part rather than a warm blob.
- Faster. The display range is measured on raw sensor counts instead of converting the whole frame
  to Celsius first, which is the same answer for less work.

## 0.4.1

- Fixed: the camera showed nothing in Safari, while working in Chrome. Fluidd renders this kind of
  camera by fetching the stream inside a Web Worker, and that fetch fails in Safari even though the
  same URL opens fine as a page. The camera is now registered as an adaptive stream, which polls the
  snapshot on a timer rather than holding a connection open. Slightly less smooth, and it works in
  every browser instead of most of them.

## 0.4.0

- The P3 (256x192) works too. The plugin probes the USB bus at startup and drives whichever camera it
  finds, so there is nothing to configure and nothing to pick wrongly. Both models speak the same
  protocol and differ only in sensor size.
- With no camera attached the service now says so plainly instead of assuming a P1 and failing
  somewhere deeper in the driver.

## 0.3.0

Robustness, all of it in how the capture loop handles a camera that misbehaves. No new features.

- Stopping the service now puts the camera down properly. There was no signal handler, so the
  service was killed outright and the USB interface was left claimed with its alternate setting
  still set, which could stop the next start from opening the camera at all. If you ever had to
  unplug the camera to get the stream back after a restart, this was why.
- A single frame whose markers disagree no longer tears down the camera and reconnects. That cost
  at least six seconds of dead video for a fault that clears on the very next read.
- A camera that stops producing frames is now noticed. Previously an empty read was treated exactly
  like a slow frame, so the stream froze on its last good image, forever, while the service went on
  reporting itself healthy.
- Reconnect attempts back off, three seconds doubling to a minute, instead of retrying every three
  seconds. A camera that is simply not plugged in no longer writes a log line every three seconds
  until somebody notices. The backoff resets as soon as a session produces frames again.

## 0.2.3

- Log lines said `thermal-p1`, the plugin's old name. Everything on the printer logs to one place, so
  a line labelled with a name that no longer exists sends whoever is reading it after the wrong
  plugin. Nothing else changed: 0.2.2 and 0.2.3 behave identically.

## 0.2.2

- Fixed: the service never started, so the camera tile could only ever show an error. It was launched
  as plain `python3`, which is the system interpreter, not the one in the virtual environment the
  daemon provisions for this plugin. numpy happened to be importable there and pyusb was not, so it
  died on `No module named 'usb'` every time it was restarted. It now runs
  `$PLUGIN_VENV/bin/python3`, as the platform's own reference Python plugin does.

## 0.2.1

- Fixed: the camera tile showed frames but was labelled an error. Snapshot requests carry a
  cache-busting parameter, and the streamer matched the raw request path, so every one of them got a
  404 while the stream itself kept working. The query string is now discarded before routing, which
  also fixes the `?action=stream` form that mjpg-streamer clients use.

## 0.2.0

Renamed from `thermal-p1`. The old name could only ever describe one of the two cameras this is meant
to serve, and nothing usable had shipped under it, so this is a clean break rather than a migration.

- The camera registers itself in Fluidd and Mainsail. Previously the plugin's own documentation walked
  you through adding it by hand under Settings.
- You choose the name it appears under when you install it.
- The stream is served through a reverse proxy the plugin ships, so `/thermal/stream.mjpg` and
  `/thermal/snapshot.jpg` resolve on the printer's own address.
- The streamer runs as a service the daemon supervises, replacing a hand-written init script.
- numpy, Pillow and pyusb are installed into a virtual environment belonging to this plugin alone,
  instead of being unpacked by hand next to the code. Nothing is installed into the interpreter
  Klipper and Moonraker run on.
- The vendored USB driver is pinned to an exact upstream commit and verified by checksum, instead of
  being fetched from whatever the upstream branch held at build time.
- The udev rule is gone. The service runs as root, so the camera node needs no permission change, and
  the streamer reconnects on its own when the camera is replugged.

Not yet verified on hardware. See the device trial checklist in the plugin's README.

## 0.1.0

First cut, as `thermal-p1`. Never verified against a camera.
