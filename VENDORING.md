<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
SPDX-License-Identifier: Apache-2.0
-->

# Vendored upstream code

What this repo ships that it did not write, where it came from, and how to verify it.

## p3_camera.py

The USB protocol layer for the Thermal Master P1 and P3. It is not reimplemented here.

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

## Runtime dependencies

numpy, Pillow and pyusb are declared in `plugin/requirements.txt` and are not vendored. CI downloads
them as arm64 wheels into `plugin/files/wheels/` at build time, and the daemon installs them on the
printer into a virtual environment belonging to this plugin alone. That directory is build output and
is not committed.

Earlier revisions of this repo unpacked those three packages by hand into `plugin/files/vendor/` and
imported them through a `sys.path` shim. That approach predates knowing the platform does it properly.
