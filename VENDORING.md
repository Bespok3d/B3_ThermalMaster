<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
SPDX-License-Identifier: Apache-2.0
-->

# Vendored upstream code

What this repo ships that it did not write, where it came from, and how to verify it.

## p3_camera.py

The USB protocol driver for the Thermal Master P1 and P3. The plugin uses it for everything it
says to the camera except two things, reading a frame and firing the calibration shutter, which
are the plugin's own code beside the driver: see
[What this repo does beside the driver](#what-this-repo-does-beside-the-driver).

| | |
| --- | --- |
| Upstream | [jvdillon/p3-ir-camera](https://github.com/jvdillon/p3-ir-camera) |
| Pinned commit | `e3205dca5727682ff2d903585d1dce5a1d19f1f6` |
| Commit date | 2026-02-06 |
| Licence | Apache-2.0, shipped alongside as `LICENSE.p3-ir-camera` |
| Path | `plugin/files/vendor/p3_camera.py` |
| sha256 | `24e69e23a5a662cd5055e7a2498e185baf81f6aea3545f6a95deb90c8f5a55d8` |
| Licence sha256 | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |

Protocol reverse-engineering credit belongs to @aeternium. See `NOTICE`.

The file is checked in verbatim and unmodified. It is not fetched at build time, and the copy in the
tree is the copy that ships, so a change to it shows up as a diff in review rather than as a silent
difference between two builds of the same version.

### Verifying the pin

```sh
sh scripts/fetch-vendor.sh
```

The check is two-sided. It hashes the committed files, which catches a local edit, and it fetches
what upstream serves at the pinned commit and hashes that too, which catches a tag or branch moved
out from under the pin. It also asserts this document names the same commit and hashes the script
does, since those are two copies of one fact. Any mismatch prints both values and exits non-zero.

By hand, if you would rather not trust the script:

```sh
shasum -a 256 plugin/files/vendor/p3_camera.py plugin/files/vendor/LICENSE.p3-ir-camera
```

### Updating the pin

Upstream is the authority on this protocol, so take its fixes rather than patching around them.

```sh
sh scripts/fetch-vendor.sh --update
```

That overwrites both files from `PINNED_COMMIT` and prints the new hashes. Then edit `PINNED_COMMIT`
and the two hashes in `scripts/fetch-vendor.sh`, update the table above to match, re-run the
verification, and run the gate. Review the diff properly, because this file talks to a USB device on
a printer, and note in `plugin/doc/CHANGELOG.md` that the driver moved.

Never edit the vendored file in place to fix something. That change belongs upstream, or in this
repo's own code beside the driver, where a re-vendor cannot silently drop it.

### What this repo does beside the driver

Two things the driver has a method for are done by the plugin's own code instead, through the
camera object the driver made. Both reach into the driver, so both are what to read again when the
pin moves.

- **The calibration shutter.** `fire_shutter` in `camera.py` sends the driver's own shutter
  command and reads its acknowledgement, and does not call `trigger_shutter`, which fails on a P1
  (upstream issue #17). It uses `COMMANDS["shutter"]`, `_send_command` and `_read_status`.
- **Reading a frame.** `frame_reader.py` reads a whole frame in two bulk reads, where the driver's
  `read_frame` makes fourteen for a P3 and six for a P1, and takes only the temperatures out of
  it. It was measured at a quarter of the processor time for a P3 and a little over a third for
  a P1 (ROADMAP, F-78). It uses `camera.dev`, and from `camera.config` the names `sensor_w`,
  `sensor_h`, `thermal_row_start` and `frame_read_size`. It repeats three things the driver
  writes as literals or keeps to itself: the stream's endpoint, 0x81, the read timeout, and the
  layout of a frame marker. The driver's `read_frame_both` is still called, as the fallback for a
  camera the plugin's reader cannot get in step with.

`plugin/tests/test_frame_reader.py` loads the vendored file itself and holds the reader against
it: the same bytes read both ways, for both cameras, and the geometry and the marker size
compared. A re-vendor that changes any of those fails there. The endpoint and the timeout are not
covered by a test and have to be looked at by hand. Neither piece is in upstream yet; the frame
read is meant to be offered there.

## Runtime dependencies

numpy, Pillow and pyusb are declared in `plugin/requirements.txt` and are not vendored. CI downloads
them as arm64 wheels into `plugin/files/wheels/` at build time, and the daemon installs them on the
printer into a virtual environment belonging to this plugin alone. That directory is build output and
is not committed.

Earlier revisions of this repo unpacked those three packages by hand into `plugin/files/vendor/` and
imported them through a `sys.path` shim. That approach predates knowing the platform does it properly.
