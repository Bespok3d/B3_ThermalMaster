# thermal-master

![The viewer measuring a heated bed, with three more scenes below](plugin/doc/images/hero.jpg)

The Bespok3d Thermal Master plugin for the Snapmaker U1: it streams a Thermal Master **P1 or P3**
USB thermal camera, so you can watch a print thermally in Fluidd or Mainsail with no UVC, no
firmware flashing, and no Rockchip MPP dependency. Plug either one in; the plugin tells them apart
by their USB product ID and drives both from the same code path. It also runs on other Klipper
printers, started by hand until Bespok3d supports them (see [hardware status](#hardware-status)).

- **A live thermal camera** in the Fluidd or Mainsail dashboard, with the hottest and coldest
  pixels marked and a temperature ruler.
- **An interactive viewer** at `/thermal/view`: read the temperature under the pointer, measure a
  region, place spot markers, zoom, save a still or record a clip, and hold the colour range so a
  colour means the same temperature in every frame.
- **Six palettes and four colour scales**, from an even stretch to a knee that keeps a hot nozzle
  visible without losing the part.
- **A thermal timelapse of every print**, one frame per layer, made into a clip on the printer and
  copied to the Timelapse page of Fluidd and Mainsail.

The plugin's own documentation, [`plugin/doc/README.md`](plugin/doc/README.md), shows the palettes
and scales and lists every setting.

### How it works

Neither camera is a UVC device. They speak a vendor protocol over raw USB, so the kernel never
creates a video device for them. The plugin drives the camera directly over libusb, turns its 16-bit
temperature frame into a colour image with the readout drawn in, and serves it as ordinary MJPEG.
The whole capture path is pure Python, with no arm64 build toolchain. It is also a worked example
of a camera plugin that does not use V4L2; for the concepts, see the Bespok3d docs
`doc/anatomy-of-a-plugin.md` and `doc/package-format.md`.

### What it costs the printer

A printer has a print to run, so what the plugin takes from the processor is measured rather than
guessed, and most of it is yours to turn down. As installed, one dashboard tile costs about a
third of one core with a P1 and about half of one with a P3; with nothing open, under 3%.

Measured on a Snapmaker U1 on 2026-11-13 and 2026-11-14 with `scripts/measure-cpu-on-printer.sh`,
each state held for 30 seconds, as a share of one of its four Cortex-A53 cores. One dashboard
tile open in Fluidd and nothing else, smooth enlarging, "Sharpen edges" on, the whole readout on,
unless the row says otherwise.

**What is open**

| State | P1 | P3 |
| --- | --- | --- |
| Camera stopped | 0.0% | not measured |
| Nothing watching | 2.1% | 2.5% |
| A tile open, live view Off (the timelapse still records) | 2.6% | 2.8% |
| A tile open, as installed: 25 FPS, readout twice a second | 34.4% | 49.4% |

**Live view frame rate**, with the readout drawn on every frame

| Live view frame rate | P1 | P3 |
| --- | --- | --- |
| 25 FPS (Max) | 40.4% | 56.3% |
| 20 FPS | 33.8% | 45.5% |
| 15 FPS | 25.9% | 34.9% |
| 10 FPS | 17.9% | 23.9% |
| 5 FPS | 10.2% | 13.3% |
| Off | 2.6% | 2.8% |

Each picture a second is about 1.5 points for a P1 and 2.1 for a P3.

**Readout refresh**, at 25 FPS

| Readout refresh | P1 | saved | P3 | saved |
| --- | --- | --- | --- | --- |
| Every frame | 38.9% | | 54.5% | |
| 5 times a second | 36.4% | 2.5 | 51.6% | 2.9 |
| Twice a second, as installed | 34.4% | 4.5 | 49.4% | 5.1 |
| Once a second | 33.7% | 5.2 | 49.1% | 5.4 |

**The other switches**, at 25 FPS with the readout drawn on every frame

| Change | P1 | P3 |
| --- | --- | --- |
| "Sharpen edges" off | about 5 points less | about 7 points less |
| Enlarging set to Sharp | not measured | about 2.6 points more |

The two "every frame at 25 FPS" figures for each camera, a day apart, differ by a point or two:
that is how far two runs of the same thing can be apart, so read a difference smaller than that
as none. A figure measured on one printer says little about another; the settings page's own line
is the one to trust on yours.

A P1 was 62.5% before the rendering pipeline was profiled and 40.6% at rest before the plugin
learned to stop working when nobody is looking, and a P3 was 81.9% on the first day it met a
printer; ROADMAP sections 5 and 7g and finding F-78 tell how it got here. The plugin's own
documentation has the older figures from a Raspberry Pi 4. The settings page shows the plugin's
own share live.

## Sponsorship

Thermal Master supports this project with hardware and an affiliate arrangement. It does not fund
it, and it does not buy any of the technical content: the figures above are measurements from a
printer, the [hardware status](#hardware-status) says plainly what has and has not been checked
on each camera, and anything that stops being true about either one gets written here whatever
the arrangement is.

### Where to buy one

If you are buying one and want the project to benefit, buy it through the shop link below. It costs
you nothing extra, and the discount code applies there.

| | |
| --- | --- |
| Official shop | https://thermalmaster.com/BESPOKD |
| Discount code | `THERMALYML01`, at that shop |

Both cameras run on a Snapmaker U1; the [hardware status](#hardware-status) below says what has
been checked on each.

Disclosure: the shop link is an affiliate link, so the project earns a commission on purchases made
through it, at no extra cost to you.

## Hardware status

**P1 on a Snapmaker U1:** in daily use since 0.2.2, installed through Bespok3d. Every release since
then has been confirmed on the maintainer's printer, and the figures above come from it. The camera
tile works in Fluidd and Mainsail, the viewer and the settings page work in a dashboard tile,
settings survive a restart, and unplugging and replugging the camera recovers on its own.

**P1 on another Klipper printer:** an Ender 2 Pro on mainline Klipper, driven by a Raspberry Pi 4
with Debian 12, since 2026-09-29. Bespok3d does not support that printer yet, so the plugin was
started by hand, from the same wheels the U1 package ships. The camera, the viewer and the
timelapse work, and a replug recovers. ROADMAP section 9 has the details.

**P3 on a Snapmaker U1:** running since 2026-11-12, from 0.30.x test builds to this release. The
picture, the viewer, the settings page and the timelapse work, a calibration pressed with the
stream open does not interrupt it, and every figure in the P3 column above was measured with one.
Not checked yet: the wide range gain, and its readings against a known temperature. It costs about
half as much again as a P1, because its sensor has two and a half times the pixels.

## Layout

```text
plugin/
  manifest.json          metadata + install directives (single source of truth)
  requirements.txt       the three runtime dependencies, pinned
  files/bin/             thermal-master-stream.py: the service entry point, and nothing else
  files/lib/             thermal_master/: the plugin itself, as an importable package
  files/etc/nginx/       the reverse proxy that publishes the plugin under /thermal/
  files/vendor/          p3_camera.py, the vendored USB protocol driver (VENDORING.md)
  files/wheels/          BAKE OUTPUT, gitignored: the arm64 wheels the daemon installs
  files/webcam.conf.tmpl the [webcam] fragment that registers the camera with Moonraker
  doc/                   the documentation shown in the app, plus the changelog
  tests/                 the test suite, run against a stand-in camera rather than hardware
  tests/run.sh           the tests as CI runs them, without the gate's private submodule
scripts/
  check.sh               the gate: pytest, ruff, mypy, and the repo's own rules
  check-in-browser.py    drives both pages in a real browser; not in the gate, needs playwright
  fetch-vendor.sh        verify the vendored driver still matches its pin, in both directions
  measure-cpu-on-printer.sh   what the plugin costs, read from the kernel over ssh
  measure-threads-on-printer.sh   the same split by thread, into user and kernel time
  profile-on-printer.sh       where the time inside a frame goes, measured on the printer
  bench-read-on-printer.sh    ways of reading a frame off the camera; opens it, so stream off first
  refresh-facade.py      keep the package facade in step with what the modules export
  tag_version_guard.sh   refuse a release tag whose version the manifest does not declare
  bench-*.py, compare-rendering.py, measure-thread-cost.py, profile-frame-cost.py
                         one-off measurements behind decisions in the roadmap
.github/workflows/tests.yml     CI on every pull request and branch push: the tests, no secrets
.github/workflows/release.yml   CI on a release tag: bake, test, pack, sign, release, register
```

The entry script only sets a path and calls `main`. `thermal-master-stream` is a valid program name
but not a valid module name, so the code lives in `files/lib/thermal_master/`, where it can be
imported, tested and type checked like any other package (ROADMAP F-47).

## Vendored upstream

The USB protocol driver is not ours. `plugin/files/vendor/p3_camera.py` is checked
in verbatim at a pinned commit from
[jvdillon/p3-ir-camera](https://github.com/jvdillon/p3-ir-camera) (Apache-2.0), with its sha256 and
the update procedure in [VENDORING.md](VENDORING.md). Protocol reverse-engineering credit:
@aeternium. See `NOTICE`. What this repo adds is the rendering, the temperature readout, the two
pages, the timelapse and the service around them.

The driver is not ours to edit, so where it is wrong for a P1 the plugin works around it: its
`trigger_shutter` reads back a frame at offsets measured on a P3, which run past the end of a P1's
smaller frame, so the plugin sends the calibration command itself. That is reported upstream.

It also reads a frame its own way, in `frame_reader.py`: two USB reads through the device the
driver opened, where the driver makes fourteen for a P3 and six for a P1, with the driver's
reader kept as the fallback. Everything else said to the camera goes through the driver.
`VENDORING.md` lists both and what to read again when the pin moves.

`numpy`, `Pillow` and `pyusb` are pinned in `plugin/requirements.txt`. The build downloads them as
arm64 wheels, and the daemon installs them into a virtual environment belonging to this plugin
alone, so nothing lands in the shared daemon venv. `pyusb` needs `libusb-1.0.so.0`, which the U1
has.

## Build locally

Requires Node.js 20+. Install the builder into its own prefix, never through `npx`, which can run a
stale cached copy against an out of date manifest schema:

```sh
npm install --prefix ~/.b3-builder github:Bespok3d/b3-builder
~/.b3-builder/node_modules/.bin/b3-builder build \
  --source ./plugin --out dist --atom-repo Bespok3d/B3_ThermalMaster --bake
```

`--bake` downloads the arm64 wheels into `plugin/files/wheels/`, and is required: the builder
refuses to pack a plugin that declares Python dependencies with no wheels.

To check the vendored driver still matches its pin:

```sh
sh scripts/fetch-vendor.sh
```

## The gate

`scripts/check.sh` has to pass before anything is committed: pytest, ruff, mypy, shellcheck, and
the repo's own rules about release triggers, manifest origin, workflow pinning and dashes. Part of
it lives in the `lib_bespok3d` submodule:

```sh
git submodule sync --recursive && git submodule update --init --recursive
bash scripts/check.sh
```

`lib_bespok3d` is private to the Bespok3d organisation, so a clone from outside it has no gate.
`sh plugin/tests/run.sh` runs the test suite on its own, which is what CI runs on every pull
request.

The pages are the part the test suite cannot really exercise. After touching either one:

```sh
pip install playwright && playwright install chromium
python3 scripts/check-in-browser.py
```

## Releasing

A release is published by a version tag and by nothing else.

1. Bump `version` in `plugin/manifest.json` and set `updated_at` to the release date (the builder
   does not stamp it; `published_at` stays at the first release's). Add the entry to
   `plugin/doc/CHANGELOG.md`.
2. Open a pull request to `main`. It can only merge once the `tests` check passes.
3. Tag `main` with `plugin-thermal-master-v<version>` and push that one tag.

`scripts/tag_version_guard.sh` refuses a tag whose version the manifest does not declare, and
anything that is not a release tag, so a branch can never publish. CI then bakes the wheels, runs
the tests, packs and signs the `.b3`, publishes a GitHub release, and registers it in the
organisation's index.

**Signing.** Packages are signed with the Bespok3d organisation's key, whose fingerprint replaces
`"publisher": "PLACEHOLDER"` in the packed manifest. The key and the index token are organisation
secrets, `REGISTRY_SIGNING_KEY` and `MAIN_INDEX_TOKEN`, read by name and never written into a file
here; neither is needed to build locally. Only maintainers can push a `plugin-*` tag, because a tag
publishes a package carrying the organisation's signature.
