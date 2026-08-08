# thermal-p1

The Bespok3d **Thermal Master P1** plugin for the Snapmaker U1: streams the InfiRay-OEM
Thermal Master P1 USB thermal camera as MJPEG, so you can watch a print thermally in
Fluidd/Mainsail with no UVC, no firmware flashing, and no Rockchip MPP dependency.

The P1 is **not** a UVC/V4L2 device. It speaks a vendor protocol over raw USB bulk transfers,
so the kernel `uvcvideo` driver never claims it and the camera-hw-accel V4L2 pipeline cannot see
it. This plugin drives it directly over libusb (pyusb), colormaps the 16-bit thermal frame, and
serves the result over HTTP. At 160x120 / ~25fps the work is trivial for the CPU, so the whole
capture path is pure Python; there is no arm64 build toolchain.

It is a worked example of a libusb-based (non-V4L2) camera plugin. For the concepts see the
Bespok3d docs: `doc/anatomy-of-a-plugin.md`, `doc/package-format.md`.

## Layout

```text
plugin/
  manifest.json          metadata + install directives (single source of truth)
  files/bin/             the capture+stream process (hand-maintained Python)
  files/etc/init.d/      autostart hook
  files/udev/            libusb permission + hotplug start/stop rule for 3474:45c2
  files/vendor/          FETCH OUTPUT, gitignored: vendored p3_camera.py + aarch64 deps
  doc/                   onboard docs rendered in the app
scripts/
  fetch-vendor.sh        pin + download p3_camera.py and unpack aarch64 pyusb/numpy/Pillow
  pack.sh                stage vendor -> compute checksums -> zip the .b3 into dist/
  generate-atom.mjs      emit the index atom (catalog entry) for main-index
.github/workflows/release.yml   CI: fetch vendor -> pack -> release -> commit the atom
```

## Vendored upstream

The USB protocol layer is **not** reimplemented here. `plugin/files/vendor/p3_camera.py` is
fetched verbatim (pinned commit) from
[jvdillon/p3-ir-camera](https://github.com/jvdillon/p3-ir-camera) (Apache-2.0). Protocol
reverse-engineering credit: @aeternium. See `NOTICE`. We only add the colormap + MJPEG serving
layer on top (`files/bin/thermal-p1-stream.py`).

`numpy` and `Pillow` ship as **vendored aarch64 packages** under `files/vendor/`, imported via
`sys.path` at runtime, so nothing is installed into the shared daemon venv and the broken-pip
situation on the U1 never comes up. `pyusb` is pure Python and vendors the same way (it needs
`libusb-1.0.so.0`, present in the U1's USB stack).

## Build locally

Requires Python 3, `pip`, `zip`, `jq`, and `shasum`/`sha256sum`. One command:

```sh
sh scripts/fetch-vendor.sh   # downloads p3_camera.py + aarch64 deps into plugin/files/vendor/
sh scripts/pack.sh           # stages vendor + packs dist/thermal-p1-<version>.b3
node scripts/generate-atom.mjs   # writes dist/thermal-p1.atom.json (local dry-run url)
```

## Releasing

Bump `plugin/manifest.json` `version` and push to `main`. CI fetches the vendored deps, packs
the `.b3`, publishes a `thermal-p1-v<version>` GitHub release with the `.b3` asset, and commits
the atom into `Bespok3d/main-index/atoms/`.

**Required secret:** `MAIN_INDEX_TOKEN` (fine-grained PAT with `contents:write` on
`Bespok3d/main-index`).

Signing is intentionally deferred during private testing; the `.b3` ships unsigned.

## Not verified on hardware yet

The protocol is ported from documented upstream code but has not been run end to end on a U1
with a P1 attached. See `doc/README.md` for the device-trial checklist.
