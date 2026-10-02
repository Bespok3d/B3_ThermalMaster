# thermal-master roadmap

Plan of action for taking this repo from the current unverified P1 streamer to a plugin that serves
both the Thermal Master P1 and P3 into Fluidd/Mainsail, with the measurement and control features the
desktop viewer has.

Status: planning only. Nothing below is implemented.

Revision 6. Revised against the b3-builder documentation set (`plugin-zero-to-hero`,
`kinds-of-plugins`, `anatomy-of-the-manifest`, `anatomy-of-a-plugin`, `anatomy-of-a-b3-file`,
`channels`, `local-testing`) and the `Bespok3d/u1-remote-screen` reference plugin. Those sources
answered the previous revision's blocking question and superseded several of its conclusions; the
changes are marked inline. Revision 3 adds the `Bespok3d/u1-camera-configs` plugins, which are the
closest precedent for registering a camera, and corrects the `publisher` question. Revision 4 adds
`Bespok3d/u1-hw-camera`, which retracts revision 2's central claim about the manifest, and records the
maintainer's answers to six of the open questions. Revision 5 records the b3-builder source spike,
which closes the Python dependency question and the dialect question. Revision 6 records the printer
diagnosis, which closes every remaining blocking question and deletes the udev file from the plan.

## 1. Goal

As a Snapmaker U1 owner with a Thermal Master P1 or P3, I want the camera to appear in Fluidd/Mainsail
as a normal camera stream, and I want the viewer's measurement and control features available from the
printer's web UI, so that I can watch and measure a print thermally without a desktop app and without
flashing firmware.

## 2. Decisions taken

1. **One plugin, model auto-detected.** A single package probes VID `0x3474` for PID `0x45C2` (P1) or
   `0x45A2` (P3) and selects the model config at connect time. This renames the plugin away from
   `thermal-p1`, which is a breaking change for anyone on 0.1.0; the manifest's `migration` block
   (`from_version` plus `summary`) is the documented way to carry that.
2. **Two surfaces, phased.** `/thermal/stream.mjpg` stays the plain camera feed the Fluidd and Mainsail
   webcam widgets consume. A richer page at `/thermal/` carries the interactive features. Interactive
   work runs client-side so per-viewer cost stays off the printer CPU.
3. **All four finding groups are blocking.** Supply chain and packaging, the missing gate and CI,
   runtime correctness, and manifest/doc drift are fixed before new features land.
4. **The platform does the wiring, not us.** Everything this plugin currently hand-rolls (an init
   script, a packing script, an atom generator, a vendor fetcher) is a facility the platform already
   provides. Those are deleted rather than repaired.
5. **One self-contained plugin, not a service plus a registration plugin.** `u1-camera-configs` splits
   the job: the Camera HW Accel plugin provides `camera-service` and serves the pixels, while
   `webcam-builtin` and `webcam-usb` are thin packages that do nothing but place a `[webcam]` fragment
   and declare `require: [{ "service": "camera-service", "cardinality": "one" }]`. That split earns its
   keep when one server feeds several cameras. Here one plugin owns one USB device end to end, so
   `u1-remote-screen` is the right precedent: it runs its own service, ships its own `web-location`, and
   registers its own `[webcam]` entry in a single package.
6. **`author` is Mauricio (Mauker); `publisher` stays `PLACEHOLDER` in source.** See F-5.

## 3. What the platform actually provides

The previous revision planned around mechanisms this repo invented. The real ones:

**Services.** `install.service[]` declares a long-running process: `name`, `command`, `args`, `env`,
`ports`, `autostart`, `venv`. The daemon supervises it. Path variables `$BESPOK3D` and
`$BESPOK3D_PLUGINS` are expanded in the args. This is what `s65thermal-p1` is a hand-rolled version of.
Permission: `managed-service`.

**Web endpoints.** A plugin ships its own nginx location file and places it with
`install.place[{ "class": "web-location" }]`. Permission: `web-location`. This answers revision 1's
blocking question: nothing is synthesized, the file is ours to write.

**Automatic camera registration.** This is the important one, and revision 1 missed it entirely.
`u1-remote-screen` places a rendered `moonraker-config` fragment that writes a Moonraker `[webcam]`
entry, so its stream appears as a tile in Fluidd and Mainsail with no manual setup at all:

```
[webcam $REMOTE_SCREEN_NAME]
service: iframe
stream_url: /screen/
snapshot_url: /screen/snapshot.jpg
aspect_ratio: 480:320
```

`u1-camera-configs` shows the same mechanism for real cameras rather than iframes, and it is a
four-line file:

```
[webcam $USB_CAMERA_NAME]
service: webrtc-camerastreamer
stream_url: /webcam2/webrtc
snapshot_url: /webcam2/snapshot.jpg
aspect_ratio: 16:9
```

Points worth copying exactly. The URLs are absolute paths on the printer host, matching whatever the
`web-location` conf serves. The name is a templated `config[]` value, so the user chooses what the tile
is called, and the same variable is declared twice, once in `requires.variables` and once in `config[]`;
both camera plugins and the screen plugin do this, so it is the convention rather than an oversight.
`service:` names the Moonraker webcam type, `webrtc-camerastreamer` there, `iframe` for the screen
mirror, and `mjpegstreamer` for what this plugin serves. Both our models are 4:3 (160x120 and 256x192),
so one `aspect_ratio` covers the P1 and the P3.

The instruction in `plugin/doc/README.md` telling users to add the camera by hand under Settings then
Cameras should not exist.

**Python dependencies.** Read from the builder source (`src/core/bake/python-deps.ts`) and
`doc/kinds/python.md`. A `requirements.txt` at the plugin root is the declaration; presence alone, no
manifest entry. CI downloads wheels into `files/wheels/` with exactly the flags this plugin's
`fetch-vendor.sh` hand-rolls:

```
--platform manylinux2014_aarch64 --python-version 3.11 --implementation cp
--abi cp311 --abi abi3 --abi none --only-binary=:all:
```

`--only-binary=:all:` is deliberate: a dependency with no arm64 wheel fails the build loudly rather than
shipping a broken package. On the printer the daemon creates a venv belonging to the plugin alone and
runs `pip install --no-index --find-links files/wheels -r requirements.txt` with the network off, and
the service opts in with `"venv": true` (the manifest doc's `"venv": "/path/to/venv"` is stale;
`doc/kinds/python.md` documents the boolean plus `$PLUGIN_VENV`). A refuse-to-pack gate
(`src/core/bake/assert-baked.ts`) fails the build if `requirements.txt` exists and `files/wheels/` is
empty.

The sibling `klipper_requirements.txt` unpacks into `files/site-packages/` and gets symlinked into the
system interpreter, for code Klipper or Moonraker imports. It is the wrong one here, and the daemon
refuses a plugin carrying both. The doc's stated reason for the whole mechanism is this printer: pip is
broken on the first target board, and nothing may be installed into the interpreter Klipper runs on.
That is the exact problem `files/vendor/` and the `sys.path` shim were invented to solve.

**Which dialect the builder enforces: neither.** `src/core/types.ts` models the manifest as opaque JSON
on purpose, so that a second copy of the schema cannot drift from the daemon's. Grepping the builder for
`symlinks`, `place`, `init.autostart` or `web-location` returns nothing. The builder will pack either
dialect; only the daemon decides which one installs.

**Packaging.** The builder is installed into its own prefix and invoked against the plugin directory:

```sh
npm install --prefix ~/.b3-builder github:Bespok3d/b3-builder
~/.b3-builder/node_modules/.bin/b3-builder build --source ./plugin --atom-repo Bespok3d/<repo> --bake
```

`u1-hw-camera` documents both traps this avoids: `npx b3-builder` resolves to whatever copy npm cached
earlier and silently builds against an out of date manifest schema, and a plain `npm install` in a repo
with no `package.json` installs into the nearest one above it. That produces the `.b3`, the atom, and
the signatures. The builder walks `files/`, computes `files[]`, and
stamps `published_at`, `updated_at` and `publisher`. A hand-written `files[]` is rejected.

**Package integrity.** The daemon recomputes every hash at install, and a package holding an archive
member that `files[]` does not list is refused outright. Only `manifest.json`, `manifest.json.sig` and
`doc/` are exempt. Modes are restricted to 644 and 755.

**The gate.** `scripts/check.sh` is a thin wrapper over `lib_bespok3d/tooling/gate-lib.sh`. The
reference version calls `b3d_python_tools`, then `run_check` around `pytest_in_dir`, `ruff_in_dir` and
`mypy_in_dir`, then `release_trigger_check`, `manifest_origin_check`, `workflow_pinning_check`,
`em_dash_check` and `shellcheck_repo`, then `gate_summary`. The submodule is declared as
`url = ../lib_bespok3d.git`.

**Releases.** Tag-triggered on `plugin-*-v*` only, never a branch push, with a
`scripts/tag_version_guard.sh` refusing a tag that does not match a manifest version, the
`Bespok3d/b3-builder` action pinned to a commit SHA with a dated comment, and a second pinned action
registering atoms into `Bespok3d/main-index`. Secrets: `REGISTRY_SIGNING_KEY` and `MAIN_INDEX_TOKEN`.

**Confirmed on the printer, 2026-09-13.** The diagnosis in section 6.3 was run on the device with a P1
attached, and it settles more than it was asked to.

Everything on this printer runs as root: the daemon, all of `camera-hw-accel`'s processes,
`remote-screen`, `status-feed`. The camera enumerates as
`Bus 004 Device 076: ID 3474:45c2 Thermal Master Technology Co., Ltd. P1`, its node
`/dev/bus/usb/004/076` is `crw-rw-r-- root root`, and root therefore has read/write on it already.
`libusb-1.0.so.0` is present in both `/lib` and `/usr/lib` at 1.0.3.0, so pyusb has something to bind
to; `README.md` asserted this and it is now verified. The interpreter is CPython 3.11.8, `aarch64`,
SOABI `cpython-311-aarch64-linux-gnu`, on glibc (Buildroot 2.38) with `ld-linux-aarch64.so.1` and no
musl loader, which is exactly the `manylinux2014_aarch64` / cp311 target the builder hardcodes.

Two things the process list shows that no document states. `status-feed` runs from
`/userdata/bespok3d/venv-plugins/status-feed/bin/python3`, so `"venv": true` produces
`$BESPOK3D/venv-plugins/<plugin>/`, and the mechanism is in production today. And `camera-hw-accel`
wraps every one of its processes in `fake-service --retry 3`, a C program shipped purely to restart a
child, while `remote-screen` and `status-feed` run bare: under `install.service[]` the daemon supervises,
so the babysitter is not needed. That is a second, concrete argument for the documented dialect, beyond
being the one the docs describe.

The path shapes also differ between dialects, which matters for writing service args. The legacy
`symlinks` form lands at `/userdata/bespok3d/usr/local/plugins/camera-hw-accel/bin/...`, flattening
`files/`; the documented form keeps it, at
`/userdata/bespok3d/usr/local/plugins/remote-screen/files/bin/...`, which is what
`$BESPOK3D_PLUGINS/<name>/files/bin/<script>` expands to.

## 4. Review findings

Every finding has a number, given in the order it was found and never changed or reused, because
code comments, tests, commit messages and other documents name findings by number. The first
review's findings are the low numbers, and the revisions and the work since went on from there.
This section groups the findings by subject, so the numbers jump within a subsection, and from F-57
on most findings sit in the phase, or the part of section 9, where they were found. F-4 was
superseded by F-33 in revision 2. F-1, F-19 and F-21 were not carried into this revision, and
nothing here records what they were. The findings are kept as written, so the reasoning stays
readable, and most carry their own resolution note; the index below is where to look a number up.

### 4.0 Index, and what is still open

Checked on 2026-10-02 against each finding's own note, the phases in section 7, and the code. Two
are not closed: F-55, parked until the tile's frame rate decay gets in the way, and F-77, on
Bespok3d's side and reported to them. Everything else is closed. "Where" is the subsection of this
section, the phase in section 7, or section 9.

| Finding | What | Where | Status |
| --- | --- | --- | --- |
| F-2 | The vendored driver is not pinned | 4.2 | Closed by Phase 0 |
| F-3 | Runtime wheels are unpinned | 4.2 | Closed by Phase 0: pinned in `requirements.txt` |
| F-5 | `publisher` is a fingerprint field, and `author` is missing | 4.2 | Closed in 0.27.0, the first signed release |
| F-6 | Packages ship unsigned | 4.2 | Closed in 0.27.0 |
| F-7 | The udev rule is world-writable | 4.4, with F-8 | Closed by Phase 1: the udev file is gone |
| F-8 | The udev rule sleeps inside `RUN+=` | 4.4, with F-7 | Closed by Phase 1: the udev file is gone |
| F-9 | `scripts/check.sh` is absent | 4.5 | Closed by Phase 0 |
| F-10 | No Python quality layer | 4.5 | Closed by Phase 0 |
| F-11 | No `release.yml` | 4.5 | Closed by Phase 1 |
| F-12 | Detector scoping | 4.5 | Closed by Phase 0 |
| F-13 | No SIGTERM handling | 4.3 | Closed in 0.3.0 (Phase 3) |
| F-14 | `stop_streaming()` is never called | 4.3 | Closed in 0.3.0 (Phase 3) |
| F-15 | One bad frame tears down the whole camera | 4.3 | Closed in 0.3.0 (Phase 3) |
| F-16 | A `None` frame is a silent permanent freeze | 4.3 | Closed in 0.3.0 (Phase 3) |
| F-17 | Unbounded retry noise | 4.3 | Closed in 0.3.0 (Phase 3) |
| F-18 | Control commands cannot be issued from HTTP threads | 4.3 | Closed in 0.8.0 (Phase 6) |
| F-20 | No `plugin/doc/CHANGELOG.md` | 4.6 | Closed by Phase 1 |
| F-22 | `README.md` describes a vendor fetch and a build that do not exist | 4.6 | Closed by Phase 1, the last part with F-11 |
| F-23 | The plugin README says only the P1 is enabled | 4.6 | Closed in 0.4.0 (Phase 4) |
| F-24 | Bind, port and camera details are hardcoded twice | 4.6 | Closed in 0.4.0 (Phase 4): the init script is gone, and the name and aspect are `config[]` |
| F-25 | The whole frame is converted to Celsius to find two bounds | 4.4 | Closed in 0.5.0 (Phase 5) |
| F-26 | No smoothing on the auto-gain bounds | 4.4 | Closed in 0.5.0 (Phase 5) |
| F-27 | A fixed 4x upscale before the JPEG encode | 4.4 | Closed in 0.5.2 |
| F-28 | `wait_next` re-sends the current frame on timeout | 4.4 | Closed: each frame carries its publication number, which the stream hands back (see F-73) |
| F-29 | `serve_snapshot` sets no `Cache-Control` | 4.4 | Closed in 0.3.0, at the proxy |
| F-30 | A `type: ignore` that the mypy gate would not take | 4.4 | Closed in 0.8.5, with F-47 |
| F-31 | The vendor directory holds drafts and 70 MB of unpacked wheels | 4.4 | Closed by Phase 0 |
| F-32 | The manifest is in an older dialect than the documentation | 4.1 | Closed by Phase 1 |
| F-33 | `files` is hand-written and the packed archive is unusable | 4.1 | Closed by Phase 1 |
| F-34 | The init script duplicates a platform facility | 4.1 | Closed by Phase 1 |
| F-35 | Nothing registers the camera with Moonraker | 4.1 | Closed by Phase 1 |
| F-36 | No nginx location file | 4.1 | Closed by Phase 1 |
| F-37 | The udev rule thought inexpressible | 4.1 | Closed by Phase 1, with F-7 and F-8 |
| F-38 | `channel` is `lts` on code that has never run | 4.1 | Closed by Phase 1 |
| F-39 | Manifest fields are missing | 4.1 | Closed by Phase 1 |
| F-40 | Three hand-rolled scripts duplicate `b3-builder` | 4.2 | Closed by Phase 0 and Phase 1 |
| F-41 | Releases are documented as push-to-main | 4.2 | Closed by Phase 1 |
| F-42 | Repo scaffolding is missing | 4.2 | Closed by Phase 0 |
| F-43 | The vendoring strategy is obsolete | 4.2 | Closed by Phase 1 |
| F-44 | Attributions are incomplete | 4.2 | Closed by Phase 1 |
| F-45 | The licence does not match the org's other plugin repos | 4.2 | Closed by Phase 1: Apache-2.0 kept, with a per-plugin `doc/LICENSE` |
| F-46 | Dependency and capability metadata is empty | 4.2 | Closed by Phase 1 |
| F-47 | mypy cannot check a single-file streamer | 4.4 | Closed in 0.8.5 |
| F-48 | The streamer carried pre-existing lint | 4.4 | Closed by Phase 0 |
| F-49 | The shared tool venv has no runtime libraries | 4.5 | Closed by Phase 0 |
| F-50 | The vendor directory shadowed installed packages | 4.4 | Closed by Phase 0 |
| F-51 | The gate venv collided across machines | 4.4 | Closed by Phase 0 |
| F-52 | Query strings 404ed | 4.4 | Closed in 0.2.1 |
| F-53 | The service ran under the wrong interpreter | 4.4 | Closed in 0.2.2 |
| F-54 | The camera worked in Chrome and not in Safari | 4.4 | Closed in 0.4.1 |
| F-55 | The adaptive tile decays from 15 fps to 5 fps | 4.4 | **Parked**, until the decay gets in the way |
| F-56 | The numpy pipeline, not the encode, is the cost | 4.4 | Closed in 0.21.0 |
| F-57 | The driver's `trigger_shutter` cannot work on a P1 | Phase 6 | Closed in 0.8.1, and reported upstream (10.1) |
| F-58 | The colorbar and the hotspot marker seem to disagree | Phase 6 | Closed in 0.8.1 |
| F-59 | Every settings change bounced out to the dashboard | Section 9 | Closed in 0.8.2 |
| F-60 | The calibrate button silently stopped working | Section 9 | Closed in 0.8.3 |
| F-61 | A camera plugged in after boot | Phase 7e | Closed: never broken |
| F-62 | The live tile showed its controls and no camera | Phase 7, 0.14.0 round | Closed in 0.14.0 |
| F-63 | The ruler and the markers disagreed, again | Phase 7, 0.14.0 round | Closed in 0.14.0 |
| F-64 | The viewer was blank until the pointer crossed it | Phase 7, 0.15.0 round | Closed in 0.15.0 |
| F-65 | A landscape tile spent its width on nothing | Phase 7, 0.15.0 round | Closed in 0.15.0 |
| F-66 | Two cameras of the same thing | Phase 7, 0.15.0 round | Closed in 0.16.0 |
| F-67 | The controls were hidden where they were wanted | Phase 7, 0.15.0 round | Closed in 0.17.0 |
| F-68 | The settings page was a one way trip | Phase 7, 0.15.0 round | Closed in 0.17.0 |
| F-69 | Placing a spot before the first frame did nothing | Phase 7e | Closed before 0.19.0 shipped |
| F-70 | The finder found itself | 4.4 | Closed in `scripts/find-plugin.sh`, which does not ship |
| F-71 | The page threw away the answer it asked for | Phase 7f | Closed in 0.22.1 |
| F-72 | The picture froze every thirty-one seconds | Section 9 | Closed: the printer's wifi, off since 2026-09-27; log timestamps in 0.25.2 |
| F-73 | Two open streams sent each other the same frame | Section 9 | Closed in 0.25.1 |
| F-74 | A network break froze the picture for good | Section 9 | Closed in 0.26.0 |
| F-75 | A fresh store counted as watched after boot | Section 9 | Closed before 0.27.0 |
| F-76 | The first release's page showed no documentation | Section 9 | Closed in 0.27.1 |
| F-77 | The Bespok3d app's Doc tab shows no images | Section 9 | **Open**, on Bespok3d's side; reported (10.6) |

### 4.1 The manifest describes a plugin model that does not exist

**F-32. The manifest is written in an older dialect that the documentation no longer describes.**
Revision 2 claimed these directives were invented and that the manifest could not install. That was
wrong, and `u1-hw-camera` is the proof: `camera-hw-accel` 0.1.11, channel `stable`, updated 2026-08-25,
uses exactly this shape, `install.dirs`, `install.templates`, `install.symlinks`, `install.patches`,
`install.start`, a top-level `stop`, and permissions `init.autostart` and `nginx.location`, and it
symlinks a udev rule into `/etc/udev/rules.d` just as this plugin does. This repo was clearly modelled
on it, down to `pack.sh` describing itself as a slim port of that repo's.

What is true is narrower and still worth acting on. Two dialects are live at once:

| | legacy | documented |
| --- | --- | --- |
| files | `install.symlinks`, `install.templates` | `install.place[]` with a `class` |
| service | `install.start` / top-level `stop` plus an init script | `install.service[]` |
| restart | inside the init script | `install.restart[]` |
| permissions | `init.autostart`, `nginx.location` | `web-location`, `managed-service`, `moonraker-config`, `restart` |

Both the `main` and `dev` copies of `anatomy-of-the-manifest` describe only the documented dialect.
`u1-remote-screen` (0.1.22) and both `u1-camera-configs` plugins (0.1.2) use it. `camera-hw-accel` is
the sole holdout, and it is the one plugin this repo copied. Writing a new plugin in the dialect the
docs have dropped is choosing the losing side of a migration, so target the documented one, but confirm
first that the daemon version being pinned actually implements it (see open question 1).

**F-33. `files` is hand-written and the packed archive is already unusable.** The manifest carries
`"files": []` and `scripts/pack.sh` injects an array into it, but the builder computes that array and a
hand-written one is rejected. Worse, `pack.sh` excludes `.DS_Store` from the array it builds while
zipping `files/` wholesale, so `dist/thermal-p1-0.1.0.b3` contains `files/.DS_Store` that no entry
covers. Per the package format, one unlisted member rejects the whole package: that artifact would be
refused at install today. Supersedes revision 1's F-4, whose fix was to repair `pack.sh`.

**F-34. The init script duplicates a platform facility.** `plugin/files/etc/init.d/s65thermal-p1` and
the `install.start`/`stop` command strings reimplement `install.service[]`, badly (see F-13 and F-14).
Delete both.

**F-35. Nothing registers the camera with Moonraker.** No `[webcam]` fragment ships, so
`plugin/doc/README.md` instead walks the user through adding the stream by hand. This is the plugin's
headline job and the platform automates it.

**F-36. No nginx location file exists.** Whatever the daemon does with `endpoints`, the proxy itself
has to be shipped. It also has to be written correctly for MJPEG: the reference conf sets
`proxy_buffering off` and `proxy_read_timeout 300s` on the stream location, and redirects `/screen` to
`/screen/`. A location without `proxy_buffering off` will stall an MJPEG stream.

**F-37. Resolved with F-7 and F-8; kept for the record.** Revision 2 called
this rule inexpressible. `camera-hw-accel` ships
`files/udev/99-camera-usb.rules` symlinked to `/etc/udev/rules.d/99-camera-usb.rules`, with no
udev-specific permission declared, so the mechanism is fine in the legacy dialect. Its rule is also
where ours was copied from, two-second `sleep` inside `RUN+=` and all.

The difference that matters: their rule matches `SUBSYSTEM=="video4linux", DRIVERS=="uvcvideo"`, a
device the kernel already owns and permissions, and it sets no `MODE`. Ours matches a raw USB device
with no kernel driver, and sets `MODE="0666"` to let libusb claim it. Whether that is needed at all
depends on what user the plugin's process runs as, which is open question 1. If it runs as root, drop
the `MODE` and the world-writable device node with it.

If the documented dialect is the target (F-32), a second question follows: `install.place[]` has no
udev class, so either the legacy `symlinks` form stays available alongside it, or udev rules move
somewhere else.

**F-38. `channel` is `lts` on code that has never run.** The channel doc reserves `lts` for thoroughly
tested work on a quarterly cadence, and puts device-specific work tested only on the author's own
machine in `experiment`, first releases in `testing`. This should be `experiment` until the hardware
trial passes.

**F-39. Manifest fields are missing.** No `author`, `license`, `changelog`, `min_daemon_version`,
`attributions`, and no `config[]`, so nothing is user-configurable at install time. Conversely
`published_at` and `updated_at` are hand-set although the builder stamps them.

### 4.2 Build tooling that should not exist

**F-40. Three hand-rolled scripts duplicate `b3-builder`.** `scripts/pack.sh` (packing and checksums),
`scripts/generate-atom.mjs` (the atom), and `scripts/fetch-vendor.sh` (dependency acquisition) are all
platform responsibilities. Deleting them removes the F-33 class of bug by construction.
`fetch-vendor.sh` is now a vendor verification tool rather than a build step (F-2), so it no longer
duplicates the builder. The other two remain until the manifest rewrite, because they are inert rather
than destructive; `README.md` already points at `b3-builder` instead, so nothing references them. Supersedes revision 1's Phase 2, which planned to fix them in place.

**F-2. The vendored driver is not pinned. Fixed.** `scripts/fetch-vendor.sh` set
`JVDILLON_REF="refs/heads/main"` while `README.md` claimed a pinned commit, so any upstream push
changed what ships onto printers. `p3_camera.py` and its Apache-2.0 licence are now checked in at
`jvdillon/p3-ir-camera` commit `e3205dca5727682ff2d903585d1dce5a1d19f1f6`, with both sha256 values, the
verification commands and the update procedure recorded in `VENDORING.md`. The bytes already in the
tree were verified against that commit before being unignored, so nothing was replaced: the working
copy was already exactly it, and the commit is still the tip of main.

`scripts/fetch-vendor.sh` is rewritten rather than deleted. Both of its old jobs are gone, since the
driver is committed and the wheels come from the bake, and its first act was a recursive delete of the
vendor directory, which after this change would destroy a tracked file. What replaces it is a
maintenance tool: `sh scripts/fetch-vendor.sh` verifies, `--update` moves the pin. The verification is
two-sided, committed bytes against the recorded hashes and upstream at the pinned commit against the
same hashes, so it catches both a local edit and a moved ref, and it fails if `VENDORING.md` has
drifted from the script's constants. Both failure paths were exercised against a tampered copy before
this was called done.

**F-3. Runtime wheels are unpinned. Resolved by F-43.** `pip download ... numpy Pillow` and
`pip download ... pyusb` carry no version constraint. Replaced by a `requirements.txt` with `==` pins,
which CI resolves once per release.

**F-41. Releases are documented as push-to-main.** `README.md` describes CI triggered by a push to
`main`. The gate's `release_trigger_check` exists specifically to fail that, and the reference workflow
comments explain why: a branch trigger once handed enrolled printers packages built from work in
progress. Tag-only, plus a version guard.

**F-5. `publisher` is a fingerprint field, and `author` is missing.** `publisher` is correct as-is:
the schema defines it as the GPG key fingerprint, the docs require the literal `PLACEHOLDER` in source,
and the builder stamps the real value during signing. `zero-to-hero` uses the field as the signing
check, so a hand-written name there would both fail that check and put a name where a fingerprint is
expected. Revision 1 listed the placeholder as a defect; it is not. The missing piece is `author`, the
schema's unverified display name, which every reference plugin sets (`"author": "bespoked"`) alongside
`"publisher": "PLACEHOLDER"`. Set `"author": "Mauker"`; `publisher` becomes Mauricio's real fingerprint
the first time the builder runs with a signing key. **Closed in 0.27.0:** the package is signed with
the Bespok3d organisation's key rather than a personal one, so the stamped `publisher` is the
organisation's fingerprint (Phase 8).

**F-6. Packages ship unsigned.** Signing needs a GPG keypair with the private key in the
`REGISTRY_SIGNING_KEY` secret. Until then packages install but show as unknown publisher.
**Closed in 0.27.0**, the first release signed, with the organisation's key (Phase 8).

**F-42. Repo scaffolding is missing.** No `lib_bespok3d` submodule, no `.gitmodules`, no
`CONTRIBUTING.md`, no `tests/`, no `.github/`, no SPDX headers. Revision 3 also wanted the plugin
directory renamed from `plugin/` to the plugin's name; withdrawn, `u1-hw-camera` uses `plugin/` for a
single-plugin repo and only the multi-plugin `u1-camera-configs` names directories after plugins.
`u1-hw-camera` additionally carries `REUSE.toml` and a `LICENSES/` directory, which is worth copying
alongside the licence decision.

**F-43. The vendoring strategy is obsolete. Resolved.** `plugin/files/vendor/` holds unpacked aarch64
numpy and Pillow imported through a `sys.path.insert` shim, to avoid the U1's broken pip. The platform
solves exactly this, better: a three-line `requirements.txt` with `==` pins, wheels baked by CI, a
per-plugin venv installed offline by the daemon, and `"venv": true` on the service. Deleted by this:
`scripts/fetch-vendor.sh`'s wheel handling, `files/vendor/`'s numpy and Pillow trees, the `sys.path`
shim at the top of the streamer, roughly 70 MB of packed payload, and step 4 of the device-trial
checklist. What stays vendored is `p3_camera.py` alone, which is a source file rather than a package.

**F-45. The licence does not match the org's other plugin repos.** This repo's root `LICENSE` is
Apache-2.0, while `u1-remote-screen` and `u1-camera-configs` are both GPL-3.0, the latter noting that
Bespok3d's own code elsewhere is AGPL-3.0-or-later. Both reference repos also carry a per-plugin
`doc/LICENSE` that the manifest's `license` field points at by URL, which this plugin has neither of.
Apache-2.0 is defensible here, since the vendored `p3-ir-camera` driver is Apache-2.0 and nothing
GPL-licensed ships, but it is a deliberate divergence and should be a decision rather than an accident.

**F-46. Dependency and capability metadata is empty.** `requires.capabilities` is `[]`, where
`u1-remote-screen` declares `klipper-generic` and the camera plugins declare `camera-mipi` and
`camera-usb`. `provides` names `thermal-camera-service` with no `exclusive` flag, although only one
process can hold the USB device, and the schema supports `"exclusive": true`. `min_daemon_version` is
absent where all three reference plugins pin `0.10.1`.

**F-44. Attributions are incomplete.** The repo has a root `NOTICE`, but the manifest has no
`attributions` field and there is no `doc/ATTRIBUTIONS.md`, both of which the docs expect. The
reference plugin carries a full attribution table naming each upstream, its licence, whether it is
needed at runtime, and whether its code ships.

### 4.3 Runtime correctness

All in `plugin/files/bin/thermal-p1-stream.py`. These survive revision 1 unchanged.

**F-13. No SIGTERM handling,** so `run_capture_session`'s `finally: camera.disconnect()` never runs on
stop and the USB interface is left claimed with interface 1 on alternate setting 1, which can make the
next start fail to claim the device. Under `install.service` the daemon owns process lifecycle, so
clean SIGTERM handling matters more, not less.

**F-14. `stop_streaming()` is never called.** It is what resets the alternate setting that F-13 leaves
dangling.

**F-15. One bad frame tears down the whole camera.** `FrameMarkerMismatchError` from `read_frame`
propagates through `stream_frames` to `capture_loop`, which sleeps 3 seconds and then re-runs `connect`,
`init` and `start_streaming`, a sequence that itself sleeps another 3 seconds. One glitched frame costs
at least 6 seconds of dead video. Catch per frame, reconnect only after N consecutive failures.

**F-16. A `None` frame is a silent permanent freeze.** `read_frame_both` returns `(None, None)` whenever
`streaming` is false or the handle is gone, and `extract_both` returns `None` on a short frame.
`stream_frames` answers with `time.sleep(0.01); continue`, forever, never reconnecting and never
logging. The stream freezes on its last good frame while the process still looks healthy.

**F-17. Unbounded retry noise.** `capture_loop` catches bare `Exception`, prints, sleeps a fixed 3
seconds. With no camera attached this logs forever. Back off exponentially and treat "camera not found"
as an expected idle state.

**F-18. Control commands cannot be issued from HTTP threads.** `trigger_shutter` and `set_gain_mode`
write commands and read status on the same USB endpoints the frame loop reads. Controls must be queued
and executed on the capture thread between frames. The draft in `plugin/files/vendor/p3_stream.py`
already uses that pattern; it becomes a documented rule.

### 4.4 Streamer design and performance

**F-25. The pipeline converts the whole frame to Celsius just to find bounds.** `raw_to_celsius` is
monotonic, so the percentiles can be taken on the raw `uint16` and only the two bounds converted.

**F-26. No temporal smoothing on the auto-gain bounds,** so the palette range jumps frame to frame and
the image flickers. The `p3_stream.py` draft already added an EMA.

**F-27. A fixed 4x bilinear upscale before JPEG encode.** Encoding 640x480 instead of 160x120 is roughly
16 times the work for scaling the browser does for free, and 1024x768 on the P3.

**F-28. `wait_next` returns the current frame on timeout,** so a stalled camera re-sends an identical
JPEG every second. Fine as a keepalive, but a sequence number would let a client tell the two apart.

**F-29. `serve_snapshot` sets no `Cache-Control`.** The reference conf sets `no-store` at the nginx
layer for exactly this class of problem.

**F-30. `# type: ignore[attr-defined]` on `self.server.frame_store`** will not survive the mypy gate
cleanly.

**F-31. The vendor directory is now the driver and nothing else. Resolved.** It held two later
streamer drafts (`p3_stream.py`, `temp.py`) and 70 MB of hand-unpacked numpy, Pillow and pyusb. All of
it was gitignored, which is not the same as harmless: the builder walks `files/` and ships what it
finds, so every byte of it would have gone into the package and its `files[]` list. The packages are
deleted, since `requirements.txt` and the bake replace them. The drafts are moved to
`reference/streamer-drafts/`, outside the payload and gitignored, with a README naming what is worth
harvesting from them in Phase 5: the EMA auto-gain (F-26), the palettes and colorbar, the pending-request
queue for device controls (F-18), and the per-frame handling of a marker mismatch (F-15).
`plugin/files/vendor/` is down to `p3_camera.py` and its licence, 48 KB, both tracked and pinned.

**F-7 and F-8. Resolved: delete the udev file.** F-7 was the world-writable `MODE="0666"`, F-8 the
two-second `sleep` inside `RUN+=`. The printer diagnosis removes the need for either. The service runs
as root and the device node is already `rw` for root, so no mode change is required; the daemon
supervises the service under `install.service[]`, so no hotplug start rule is required; and the streamer
reconnects on its own, so no hotplug stop rule is required. `plugin/files/udev/99-thermal-p1.rules` goes
away entirely rather than being fixed, which also disposes of the "no udev class exists" problem, since
nothing needs placing.

**F-47. Fixed in 0.8.5.** The streamer is now `plugin/files/lib/thermal_master/`, nine modules
behind an entry script that does nothing but set two paths and call `main`. mypy runs on the whole
package and is in the gate as a seventh check.

Three things are worth recording about how it was done.

The tests were not rewritten, and that was the point: `__init__.py` re-exports every public name, so
the suite takes the package as one namespace and asks for names rather than files. 187 tests passed
unchanged, which is the only real evidence a refactor changed nothing. Two had to move anyway, and
both for honest reasons: one asserted about the contents of a source file, which is layout rather
than behaviour, and one used a driver exception the flat module had leaked by accident.

Turning mypy on found 28 things, which is what a file that has never been type checked contains.
Most were missing annotations on the drawing functions. One was a real latent defect:
`settings_payload` dereferenced a settings store that its own type said could be None. Both callers
happened to check first, so it could not fire, but nothing made that true. It now takes the store as
an argument, which makes the check provable rather than a coincidence.

The dangerous one was not in the code. `.gitignore` carried `lib/` from the standard Python
template, and it matched `plugin/files/lib`, so the entire new package was invisible to git. The
build kept working, because b3-builder reads the working tree, and the first symptom would have been
a commit whose plugin contained no code. `!plugin/files/lib/` now follows it, with a comment. Worth
considering for the gate: a check that everything b3-builder packs is tracked by git would have
caught this in a second, and I have not added it because the gate does not run git.

The original finding, for the record:

**F-47 as first written. The streamer cannot be type-checked while it is one hyphenated script.** mypy derives a
module name from the filename, and `thermal-p1-stream` is not a legal one, so the file holding all the
logic cannot be checked. `u1-remote-screen` has the same shape and works around it by checking only its
underscore-named modules, leaving its own entry script unchecked. The fix is to split the logic into an
importable module beside a thin entry script, which Phase 3 wants anyway for testability, and to turn
mypy on in the gate at that point. Until then the gate runs pytest and ruff only, with the reason
written at the missing line.

**F-48. The streamer carried pre-existing lint. Fixed.** Against the shared ruleset the streamer had
unsorted imports and two lines over the 100 column limit. The plan was to leave these to Phase 3, which
was wrong: the gate has to be green for Phase 0 to be done, so they were fixed here. All four changes
are formatting only, no behaviour touched: the import block reordered by `ruff --fix`, and two long
expressions split by naming their intermediate value (`ramped_channel`, `listening_on`).

**F-50. The vendor directory shadowed installed packages. Fixed.** The streamer put `files/vendor/` at
the FRONT of `sys.path`, so anything unpacked there won against the installed package of the same
name. With the hand-unpacked aarch64 numpy and Pillow still sitting in a working tree, that meant the
plugin imported Linux binaries in preference to the environment's own copies. On the printer's
architecture it does that silently, which is the dangerous case: the code appears to work while
running a dependency nobody declared. On any other machine the binaries refuse to load, which is how
it surfaced, as `ImportError: cannot import name '_imaging' from 'PIL'` on macOS.

The tests passed on Linux and failed on macOS for exactly this reason, so the gate was
platform-dependent, which is worse than a red gate. `sys.path.append` replaces the insert: the
environment wins, and the vendor directory is reachable for the one thing it holds, the upstream
driver. `test_the_vendor_directory_does_not_shadow_installed_packages` pins it, and was confirmed to
fail on the old code (`assert ['PIL'] == []`) before the fix landed.

Still outstanding: those numpy and Pillow trees are gitignored, but `b3-builder` walks `files/` and
ships what it finds, so they must be deleted before the first real build or they end up in the
package and in its `files[]` list.

**F-51. The gate venv collided across machines.** It was built at `.venv` inside the repo, which is
shared between a macOS host and a Linux VM working the same checkout, so each run deleted and rebuilt
the other's, and a stale symlink to a missing interpreter reported as a confusing "not 3.11" error.
The path now carries `uname -s` and `uname -m`, so each platform keeps its own.

**F-52. Query strings 404ed. Fixed in 0.2.1, found on hardware.** The request handler matched
`self.path`, the raw request target, against its route table. Every client that decorates a URL
therefore got a 404: Fluidd and Mainsail add a cache-busting parameter to snapshot requests so the
browser cannot serve a stale one, and mjpg-streamer clients add `?action=stream`. The failure mode is
deceptive, because the stream is loaded as a plain `<img>` with no query and keeps working: the camera
tile renders live frames and is labelled an error at the same time, which is what the first hardware
install showed. `resolve_route` now discards the query before an exact-match lookup, with tests
covering the cache-busted snapshot, the action-style stream, the root path, an unknown path, and a
path that merely starts with a known one, so discarding the query cannot quietly become prefix
matching.

Worth recording honestly: this was fixed on a misreading. "The frame shows up" was taken to mean
video frames were rendering, when it meant the tile appeared. The routing bug is real and would have
bitten as soon as the service ran, but it was not the cause of the reported symptom (F-53 was), and
it was diagnosed from an assumption rather than from evidence. The three curl lines that settled it
should have come first.

**F-53. The service ran under the wrong interpreter, so it never started at all.** The manifest
launched it as `python3`, which is the printer's system interpreter, not the one in the virtual
environment the daemon provisions from `requirements.txt`. The system interpreter happens to have
numpy and does not have pyusb, so it got past the first import and died on `No module named 'usb'`,
once per restart, into `var/log/thermal-master.log`. Nothing was listening on 8082, which is why
every endpoint answered 502 while nginx and the Moonraker registration were both fine.

The fix is the form the platform's own reference Python plugin uses:
`"command": "$PLUGIN_VENV/bin/python3"`, with no `venv` field. Note that
`doc/kinds/python.md` documents `"venv": true` as what opts a service into its environment, and
`anatomy-of-the-manifest` documents `"venv"` as a path; `reference-python-plugins/status-feed`, which
is the stated reference for this mechanism and is running on the printer today, uses neither and
names the interpreter directly. Trust the running plugin over both documents.

`plugin/tests/test_manifest.py` now pins this and four other manifest promises that only fail after
an install: that every placed file exists, that service arguments naming plugin files point at real
ones, that the registered camera URLs match locations the proxy actually serves, and that the
declared port is the one the service is told to bind. The interpreter test was confirmed to fail
against the broken command before being called done.

**F-54. The camera worked in Chrome and not in Safari. Fixed in 0.4.1.** Fluidd does not render a
`mjpegstreamer` camera with an `<img>`: it fetches the stream inside a Web Worker and parses the
multipart itself. In Safari that fetch fails with `TypeError: Load failed`, a generic network failure,
while the identical URL renders when opened as a page and works in Chrome. Registering the camera as
`mjpegstreamer-adaptive` fixes it, because adaptive polls the snapshot on a timer and never holds a
streaming connection open. Confirmed by the maintainer at about fifteen frames a second, which is
Moonraker's default `target_fps` rather than anything about the camera.

The cause was not chased further than that. Three things about our response differ from a typical
mjpg-streamer, HTTP/1.0 rather than 1.1, no cache headers on the stream, and no leading CRLF before
the first boundary, and any of them might be the trigger. Changing all three to see if the symptom
moves would not have said which mattered, and adaptive is the better default regardless: the plugin
should not depend on a browser holding a streaming fetch open for hours during a print.

**F-55. The adaptive tile decays from 15 fps to 5 fps if left open.** Reported from hardware, cause
not yet established. The leading suspicion is our own transport: the request handler never sets
`protocol_version`, so it answers HTTP/1.0 and every snapshot is a fresh TCP connection and a fresh
thread in `ThreadingHTTPServer`. At fifteen a second that is nine hundred connections a minute, and
over hours the sockets left in TIME_WAIT on a small board are a plausible reason for each request to
get slower, which is exactly what an adaptive poller responds to by slowing down.

Measured on hardware, and the suspicion is probably wrong. A snapshot from the printer's own shell
returns in 17 ms, and the streamer holds 6 threads, so the server is healthy. `netstat` does show 989
sockets against port 8082, but that is the expected steady state rather than a leak: fifteen
connections a second against a sixty second TIME_WAIT is nine hundred, which is what we see. It is 3%
of the ephemeral port range, not exhaustion.

So unless that measurement was taken while the tile was fast, the decay is client-side, in Safari's
adaptive poller rather than in anything we serve. Still to confirm: whether curl stays at 17 ms while
the tile sits at 5 fps.

Healthy baseline for comparison, taken 2026-09-14 with 0.4.1, both Safari and Chrome open at about
fifteen frames a second each: a snapshot in 15 ms, 768 sockets against port 8082, 6 threads. The
decay was not reproducible at that moment, so the investigation waits for it to happen again.

**F-56. The numpy pipeline, not the JPEG encode, is what this plugin spends its time on.** Closed
2026-09-17 at 0.21.0, having invalidated the assumption three rounds of tuning were built on.

Profiled on the printer with `scripts/profile-frame-cost.py`, P1 frame rotated 270, four cumulative
stages:

| stage | cost | delta |
| --- | --- | --- |
| pipeline and statistics | 8.20 ms | |
| plus the 1x encode | 9.79 ms | +1.59 ms |
| plus the 2x encode instead | 13.31 ms | +3.51 ms |
| plus the readout drawn on it | 16.62 ms | +3.31 ms |

The pipeline is half the frame and the 1x encode is a tenth of it. Every earlier estimate had that
the other way round, because on a development machine the same pipeline is 0.36 ms and the printer's
is 8.20: **twenty-three times slower, while the encode is only about twenty times slower**, so it is
not a flat scaling factor and the ratios cannot be carried across. That is the mechanism behind all
three bad forecasts, and it also explains why removing the 4x upscale in 0.5.2 returned a third
rather than the three quarters predicted: the upscale was never four fifths of the work on hardware,
it was four fifths of the work on a laptop.

**Step 1, done, and the profiler was broken.** It loaded the streamer by path, which stopped being
the plugin when the code moved into a package (F-47), and it built settings with an `overlay` field
that the 0.10.0 split replaced. So the table above is from 0.8.x and nothing has been measured since.
It now imports the package the way the harness does, and prints a second table timing every step
inside the pipeline on its own: noise reduction, the two percentiles, statistics, normalise, the
unsharp mask and its blur, the palette lookup, orient, both encodes and the readout drawing.

**And the invocation failed, on a path that turned out to be right.** Both commands answered
"No such file or directory" for the interpreter, so the working theory was that
`$BESPOK3D/venv-plugins/<plugin>/bin/python3` was another plugin's layout rather than this one's.
The discovery run then printed exactly that path. The interpreter was never the problem; the
handover was, a two line recipe with shell variables that only works if both lines are run in one
shell. Recorded because the wrong conclusion was one inference away from being written down as a
platform fact, and the thing that corrected it was asking the printer rather than reasoning about
it. `scripts/profile-on-printer.sh` now reads the interpreter and the entry script out of the
running service's own `/proc` command line and pipes the profiler in over stdin, which copies
nothing to the printer and cannot disagree with what is actually running.

**F-70. The finder found itself.** The third run reported "no thermal-master service is running on
this printer" while the plugin was serving on 8082 with the camera on the bus. The loop asked
whether a process's command line contained "thermal-master-stream", and the command line it was
running inside contained that string too, as the search pattern: given a process id that sorted
ahead of the service's, it matched its own shell, took "bash" as the interpreter, found no script
path, and declared the service missing. It had worked twice before purely because of how two
process ids happened to sort.

The fix is to ask for what a service actually looks like rather than for a substring: one argument
that IS the entry script, ending in its file name, and a first argument that is a python
interpreter. Both conditions are needed; either alone still matches the wrong thing.

Two diagnoses in a row went the same way here. The first guess was that the interpreter path was
another plugin's layout; the printer said the path was right. The second guess, when the discovery
failed, was that the daemon had moved to a private process namespace; the printer said the process
was listed in `/proc` exactly where it should be, with the entry script in its command line. Both
times the evidence was one read-only command away, and both times the plausible story was wrong.

**Step 2 is built as one measurement rather than three round trips.** The printer is not a machine
anyone develops on, so `scripts/bench-pipeline-candidates.py` carries every candidate for those
steps and prints, for each, the current cost, the candidate's cost and how far the two answers
differ. A candidate earns its way in by being faster there and by agreeing with what it replaces.
Candidates: both percentiles from one call; both from a 2x2 subsample; the half-and-half noise
reduction in integers; the unsharp mask in integers with the blur carried at 16 times its value;
and the palette lookup through `np.take`.

On a development machine, where the ratios famously do not transfer, one percentile call is 40%
off that step with identical numbers, the subsample is 60% off with the range ends moving 0.047 C,
the integer noise reduction is exact, the integer unsharp mask differs by at most one of 255 on
0.0% of pixels, and `np.take` is three times faster than fancy indexing with identical output. The
last one is not on F-56's list of suspects at all, which is the argument for measuring the whole
pipeline rather than the three steps somebody guessed at.

**Measured, 2026-09-17, at 0.19.0.** The same four stages, and every step inside the pipeline on
its own. The stage table barely moved across a dozen releases of new features, which is worth knowing on
its own: 17.20 ms a frame with the readout on, against 16.62 ms at 0.8.x.

| step | cost | share of a core at 25 fps |
| --- | --- | --- |
| noise reduction | 0.18 ms | 0.4% |
| bounds, two percentiles | 1.85 ms | 4.6% |
| statistics and emissivity | 0.54 ms | 1.4% |
| normalise to bytes | 0.33 ms | 0.8% |
| unsharp mask, of which 1.20 ms is the blur | 1.62 ms | 4.0% |
| palette lookup | 1.44 ms | 3.6% |
| orient | 0.97 ms | 2.4% |
| encode at 1x | 1.03 ms | 2.6% |
| encode at 2x | 5.29 ms | 13.2% |
| draw the readout | 3.46 ms | 8.6% |

**Taken, and in 0.20.0.** All three are the same picture, byte for byte, which is why they are taken
without argument:

- **Both percentiles from one call**, 2.07 ms to 1.14 ms. The largest single saving in the pipeline,
  and the two numbers are identical to the last bit.
- **The palette lookup through `np.take`**, 1.44 ms to 0.48 ms, output identical. This was on
  nobody's list. It is the argument for timing every step rather than the three that looked
  expensive: the suspicions named the unsharp mask, which turned out to be worth a fifth of this.
- **The half-and-half noise reduction in integers**, 0.19 ms to 0.14 ms, exactly equal because the
  sum is well inside float32's exact range and the float version was already computing the same
  floor.

About 1.9 ms of the 8.10, a quarter of the pipeline and a ninth of the whole frame.

**Rejected, with reasons, both still in the bench.**

- **Percentiles from a 2x2 subsample**, 1.14 ms to 0.80 ms on top of the change above. It moves the
  range ends by about a twentieth of a degree, which is a change to what is on screen in exchange
  for a third of what the single call already saves. Available if the frame budget ever gets tight.
- **The unsharp mask in integers**, 1.58 ms to 1.36 ms, with one pixel in the frame differing by one
  of 255. Named as a prime suspect by the original finding and worth 0.22 ms in practice, which does
  not pay for a picture that is not quite the same picture.

**Confirmed on the printer at 0.20.0, with nothing watching.** The pipeline is 5.91 ms against
8.10, a saving of 2.19 ms, which is 27% of that stage and 14% of the whole frame: 14.76 ms with the
readout on, against 17.20. The per-step numbers agree with where it was supposed to come from: the
bounds are 0.89 ms against 1.85, the noise reduction 0.13 against 0.18, and `np.take` 0.45 against
the 1.42 that fancy indexing still costs in the same run.

Two things the confirmation run showed that the change itself did not:

- **The profiler was measuring the step it had replaced.** Its palette line called `palette[detailed]`
  by hand, so after 0.20.0 it reported 1.42 ms for a step the plugin no longer performs, and the
  saving would have looked like nothing at all. It now times both and says which is which. A
  profiler that does not go through the code under test is a second implementation, with the same
  drift problem as any other.
- **A watching browser costs almost nothing on this path.** The same run with the viewer open and
  the pointer moving: 6.47 ms against 5.91 on the stage line, and every per-step line within
  0.03 ms. Most of that 0.56 ms is ambient scheduling rather than a systematic cost, since the
  steps that make up the stage did not move. Worth recording against F-55: a viewer polling frames
  is not what turns 15 fps into 5, so the decay is in the transport, where the HTTP/1.0 connection
  churn already pointed.

**Where the next round is, and it is not the pipeline.** The encode at 2x is 5.29 ms and the readout
drawing is 3.46 ms: 8.75 ms between them, more than the whole pipeline, and both exist only because
the burned-in text needs pixels to land on. The suspicion is the same as it was, now with a number
behind it: the drawing is 3.46 ms on the printer against 0.10 ms on a development machine, so what
is expensive there is not the glyph rendering the cache already avoids but the pasting. Before
any candidate, the two blocks are being split: the profiler now times the 2x resize and the JPEG
save separately, and the readout's colorbar, markers and a single label separately, because the
first guess about where the drawing goes was that pasting glyph tiles is expensive, and the
colorbar builds a fresh gradient every frame. On a development machine the 2x resize is two thirds
of the encode and the same resize with NEAREST is seven times cheaper, which is the first candidate
measured in the same run: the picture is resampled again by every browser that shows it, and the
viewer asks for it pixelated anyway.

**The readout half, measured, 2026-09-17 at 0.20.0.** Of a 15.03 ms frame:

| part | cost |
| --- | --- |
| the 2x resize, bilinear | 3.06 ms |
| the same resize, nearest | 0.60 ms |
| saving the 2x JPEG | 1.81 ms |
| the colorbar, all of it | 1.59 ms |
| the three markers | 1.22 ms |
| one label, of those | 0.24 ms |

Two things fall out of it. **The single most expensive operation in the whole frame is the upscale**,
at 3.06 ms, and it exists only so the text has pixels to land on: the picture itself gains nothing
from being smoothly doubled, because every browser scales it again afterwards and the viewer asks
for it pixelated. Nearest is 0.60 ms, which is 2.46 ms of a 15.03 ms frame, and it is the one
candidate so far that changes what is on screen in a way a person could point at: edges become
steps. Pixels differ by about half a level of 255 on average on a synthetic scene, which is a
number rather than a judgement, so it goes to the maintainer with two pictures rather than being
taken here.

**And the labels are the biggest thing inside the drawing**, which nothing suspected: five labels a
frame at 0.24 ms each is about 1.2 ms of the 2.99 ms. The candidate is caching a whole label tile
rather than pasting it one character at a time, keyed by the string, which hits whenever the tenth
of a degree has not changed. `scripts/bench-readout-candidates.py` measures that on a hit and on a
permanent miss, and re-measures Pillow's own `draw.text` against the glyph cache, because the cache
was justified on a development machine and no ratio from that machine has survived contact with
this one yet. It also breaks the colorbar into its gradient, its paste and its outline, since
1.59 ms for a strip 8 pixels wide is more than it looks like it should cost.

**Answers from the maintainer, 2026-09-17.**

- **The upscale filter becomes a setting rather than a decision.** Smooth or sharp, chosen live on
  the control page beside the palette and the rotation, because it is a question about what looks
  best on a given scene and the person looking at it is the one who can answer it. It only bites
  when the readout is on, since that is the only time the picture is upscaled at all.
- **F-56 stops when the easy wins run out**, rather than at a target number. What is left after
  that gets written down here rather than chased.
- **F-55 is parked** until the fps decay actually gets in the way.
- **The P3 has never met this plugin**, and both the manifest and the README now say so in as many
  words rather than implying it works. The driver's own model configuration is in place; nothing
  has been run against the hardware.

**The readout candidates, measured 2026-09-17.** One taken, three rejected, one still unexplained.

- **Taken: the upscale filter becomes a setting.** 3.09 ms bilinear against 0.60 ms nearest, which
  is 2.49 ms of a 14.85 ms frame, the largest saving available anywhere in this plugin. It is also
  the only candidate that changes what is on screen, so it ships as "Enlarging: smooth or sharp" on
  the control page, defaulting to smooth so that upgrading changes nobody's picture. The saving is
  one click away and the page says what it is worth.
- **Rejected: the same upscale through `np.repeat`**, 2.51 ms against Pillow's 0.61 ms for the
  identical picture. Pillow's resize is not the slow thing; the filter is.
- **Rejected: caching a whole label tile.** 0.24 ms to 0.10 ms when the string repeats, and 0.25 ms
  to 0.39 ms when it does not. Five labels a frame, but only three distinct strings, since the bar's
  ends carry the same numbers as the hotspot and coldspot markers: two guaranteed hits and three
  builds on any frame where the numbers moved, which is a wash. And on a 160 by 120 sensor with
  noise, the hottest pixel's tenth of a degree moves most frames, so the cold path is the common
  one. A candidate whose value depends on how still the scene is, in both directions, is not worth
  the cache.
- **Rejected, and the old comment vindicated: Pillow's own `draw.text`.** 2.12 ms against 0.24 ms
  for the glyph cache, which is the nine times the cache was justified by on a development machine
  two years of ratios ago. It is the one measurement from that machine that has transferred.
- **Unexplained: the colorbar.** 1.61 ms as it ships, against about 0.4 ms for its measured pieces
  and another 0.5 ms for its two labels. The bench now also times building an `ImageDraw` and one
  of the bar's triangles, because every function in the readout builds its own drawing context and
  a frame builds six or seven of them. That is a suspicion with a cheap measurement attached, not a
  change; it goes in the next run.

**Closed, 2026-09-17.** The colorbar's missing half millisecond turned out to be nothing in
particular, which is an answer. Building a drawing context is 0.02 ms and a triangle 0.04, so six
or seven contexts a frame were never the explanation: the bar is 0.18 ms of gradient, 0.19 to turn
that into an image and paste it, 0.50 for its two labels, 0.12 for triangles and an outline, and
about half a millisecond spread so thin across small operations that no single one of them is worth
a change. The suspicion was wrong and cost one bench row to disprove, which is the right price.

Where it ends. A frame was 17.20 ms with the readout on; it is 14.94 ms now, and 12.5 ms with
Enlarging set to sharp. At the tile's real fifteen frames a second that is 22% of one core, or 19%
sharp, against 26% when this started. The pipeline went from 8.10 ms to 5.89.

What was taken: both percentiles in one call, the palette lookup through `np.take`, the half and
half noise reduction in integers, and the upscale filter as a setting. Three of the four are
byte-identical to what they replaced; the fourth is the user's choice and says so on the page.

What was rejected, with its number, so nobody has to wonder:

| candidate | worth | why not |
| --- | --- | --- |
| percentiles from a 2x2 subsample | 0.11 to 0.31 ms, and it moved between runs | changes the range ends by 0.047 C for a saving that will not stay still |
| unsharp mask in integers | 0.22 ms | one pixel in 255 differs, for 1.5% of a frame |
| `np.repeat` for the upscale | negative, 2.52 ms against 0.60 | Pillow's resize is not the slow part, the filter is |
| a cached whole-label tile | 0.15 ms on a hit, minus 0.14 on a miss | five labels but three distinct strings a frame, and on a noisy sensor the tenth of a degree moves most frames |
| Pillow's own `draw.text` | negative, 2.11 ms against 0.24 | the glyph cache is nine times cheaper, on this machine as on the one it was written for |
| one drawing context per frame | 0.12 ms at the very most | measured before writing it, which is the only reason it was not written |

What is left on the table, deliberately, for whoever comes back to this:

- **The readout is the expensive half and always was**: 8.2 ms of a 14.94 ms frame between the
  upscale and the drawing, against 5.89 for the whole pipeline. Everything cheap in it has been
  taken. What remains needs a different design rather than a faster line, most obviously not
  upscaling at all, which means finding another way to make nine pixel text survive a JPEG.
- **Measuring the range every fourth frame** rather than every frame, worth about 0.69 ms, with the
  smoothing weight re-expressed per update so the response time does not quietly quadruple. Not
  taken because it belongs with the ruler question rather than with this finding.
- **Locking the range** removes the percentile work altogether, 0.92 ms, and is the same lever from
  the other end. That is a feature decision, in section 6.

Sequencing note, kept because it stopped being true: "fix the pipeline first, it is the largest
single stage" was right when the stages were four lines. With every step timed, the pipeline is
8.10 ms across seven steps and the readout is 8.75 ms across two, so the next round goes to the
readout.

Reducing the connection churn is worth doing regardless, and there is a constraint worth recording
before anyone plans it: `keepalive` is only valid inside an nginx `upstream` block, which belongs to
the `http` context, and a `web-location` file is included inside a `server` block. So the nginx half
cannot be shipped from this plugin at all as the install classes stand. `protocol_version = "HTTP/1.1"`
on our side alone does not help, because nginx closes the upstream connection per request without it.
Lowering `target_fps` is the only lever we actually hold.

### 4.5 Gate and CI

**F-9. `scripts/check.sh` is absent,** with the `lib_bespok3d` submodule, `.gitmodules` and
`CONTRIBUTING.md`. The reference `check.sh` is short and mostly portable: change `PLUGIN_DIR` and the
per-tool target lists. This is a cheap fix now that the shape is known.

**F-10. No Python quality layer:** no ruff config, no mypy config, no `tests/`. `CLAUDE.md` step 6 wants
a regression test in the same change as a fix and there is nowhere for one to land.

**F-11. No `.github/workflows/release.yml`,** although `README.md` documents one.

**F-12. Detector scoping. Resolved, and the guess was wrong.** The em-dash guard walks the
filesystem, not git, so being gitignored protects nothing: pointed at the repo root it flagged ten
files, two in the vendored numpy tree and eight in the upstream viewer clone. The guard is built for
this, though: "each repo runs this over its own trees and passes its own scope on the command line",
with build output, caches and `lib_bespok3d` skipped by directory name. So `scripts/check.sh` names
this repo's authored trees explicitly rather than the root, and `shellcheck_repo` is scoped the same
way, since the viewer clone ships its own shell scripts. A path that does not exist is skipped, so
naming `files/udev` costs nothing once it is deleted.

**F-49. The shared tool venv has no runtime libraries, by design.** `python-tools.txt` carries pytest,
ruff and mypy only: "a package whose own tests need a runtime library declares that library in its own
repo", and one venv is shared across every plugin repo that has none. This plugin has three and its
tests import two, so pytest failed on `No module named 'numpy'`. `scripts/check.sh` reassigns
`B3D_TOOLS_VENV` to a repo-local `.venv` after sourcing `gate-lib.sh` and before building it, then adds
the pins from `plugin/requirements.txt` on top. Nothing in the submodule is modified and the shared venv
stays what the other repos share.

### 4.6 Documentation drift

**F-20.** No `plugin/doc/CHANGELOG.md`, and no `changelog` field pointing at one.

**F-22. Partly fixed.** `README.md` claimed a pinned vendor fetch that did not exist and described the
build as three hand-run scripts. Both are corrected: the build section is now the `b3-builder`
invocation with `--bake` plus the submodule setup, and the vendoring section points at `VENDORING.md`
and describes the requirements and wheels path. Still stale: the CI description (F-11), which stays
wrong until the workflow exists.

**F-23.** `plugin/doc/README.md` says only the P1 is enabled and a P3 variant "would be a small change",
and walks the user through manual camera setup (F-35).

**F-24.** Configuration is hardcoded in two places: bind and port as module constants in the streamer
and again as `BIND` and `PORT` in the init script. Under the real model these are `config[]` entries
rendered into service args, the way the reference passes `--bind`, `--port` and `--html-dir`.

## 5. Feature gap analysis

The desktop viewer's feature list, sorted by where each feature runs once the camera is on a printer.

### Client-side, in the embedded page

Mouse-over temperature readout, ROI drag with max/min/average and tracking markers, hotspot markers,
Celsius/Fahrenheit toggle, zoom, center reticle, PNG screenshot via canvas export. None cost the printer
anything per viewer, but all need temperature values in the browser, not just pixels.

Rotate and flip stay out of the MJPEG feed: Fluidd and Mainsail already apply per-webcam rotation
and flip, and duplicating it server-side would fight their setting. The embedded page needs its own.

That reasoning was right and incomplete, and hardware found the gap. A webcam defined by a config
file is read-only in Fluidd's settings panel, which reports it as managed by Moonraker and greys the
controls out, so the user cannot reach the client-side setting at all. Since this plugin owns that
config, it has to expose them. `rotation`, `flip_horizontal` and `flip_vertical` became install
settings in 0.5.1, rendered into the `[webcam]` fragment; the browser still does the work, so there
is still no per-frame cost.

That exposed a second gap immediately. Both cameras are 4:3, so a 90 or 270 rotation produces a
portrait image, and the tile was still declared 4:3: the picture sat inside a landscape box with bars
at the sides, scaled down to fit between them. `aspect_ratio` became an install setting too in 0.5.3.
Worth noting how this presented: it landed in the same session as the native-size encode, and the
obvious suspect for a smaller picture was the smaller encode. It was not. Two changes at once, and
the visible symptom pointed at the wrong one. A test now fails if any `.tmpl` uses a variable that is not both declared
and configurable, because an unrendered `$NAME` reaches Moonraker as a literal and breaks the camera
in a way that points at Moonraker rather than at us.

### Server-side

Palette selection applies where the JPEG is produced. Shutter/NUC, gain mode and emissivity are device
commands subject to F-18. TNR is an exponential moving average and DDE is an unsharp mask against a 3x3
Gaussian, both cheap in numpy.

CLAHE is the exception: the viewer gets it from OpenCV, and pulling arm64 OpenCV into the payload for one
function is disproportionate even with bake handling the build. Either implement tiled histogram
equalization in numpy or ship DDE plus TNR without it.

MP4 recording via FFmpeg is dropped from printer scope. Recording belongs in the client or in Moonraker's
timelapse.

### The transport question

Measurement needs the 16-bit grid in the browser. Every raw frame is 38.4 KB for the P1, about 960 KB/s
at 25 fps, and 98 KB for the P3, about 2.4 MB/s. Fine over ethernet, unpleasant over the printer's wifi.

Planned: MJPEG stays the pixel path; a separate WebSocket channel carries the temperature grid at 5 to 10
fps, opened only while the embedded page is visible. Display smoothness and measurement rate decouple,
and an idle printer pays nothing.

### Registration in Fluidd and Mainsail

Two Moonraker `[webcam]` entries, both rendered from `config[]` values so the user names them at install:
an `mjpegstreamer` entry for the plain thermal feed, and an `iframe` entry for the interactive page with
`aspect_ratio` matching the detected model (4:3 for the P1 at 160x120, 4:3 for the P3 at 256x192, so one
value covers both). Whether the iframe tile ships in the same package or waits for Phase 7 is a
sequencing choice, not an architectural one.

## 6. Questions

### 6.1 Settled

- **Pinned upstream driver.** Done, see F-2 and `VENDORING.md`.
- **No auth on the MJPEG location.** It matches every other camera on the printer, and an `auth_request`
  would break the `[webcam]` tile, which is a plain `<img>`. Stated in the plugin doc rather than left
  to inference.
- **`min_daemon_version`: `0.14.0`.** Note the tradeoff: the reference plugins pin `0.10.1`, so `0.14.0`
  excludes anyone on an older daemon. Defensible for a plugin nobody is running yet, and it is the
  version the migration documentation names as current.
- **No `migration` block for the rename.** Nothing has shipped that works, so a clean break is honest.
- **Licence: Apache-2.0.** A deliberate divergence from the org's GPL-3.0 plugin repos, justified by the
  vendored driver being Apache-2.0 and no GPL code shipping. Add a per-plugin `doc/LICENSE` and consider
  `u1-hw-camera`'s `REUSE.toml` plus `LICENSES/` layout.
- **Language: Python.** The vendored driver and the desktop viewer are both Python and both work; there
  is no reason to introduce another toolchain.
- **Dependencies come from `requirements.txt`, not a vendor directory.** Settled by the builder spike;
  see F-43 and section 3.
- **Target the documented manifest dialect.** The builder is dialect-agnostic, so the question was only
  whether the daemon implements the documented one. It does, and has for a while: `u1-remote-screen`
  0.1.22 is published and stable on `install.service[]` plus `web-location` with
  `min_daemon_version: 0.10.1`, four minor versions below the `0.14.0` being pinned here, and both it
  and `status-feed` are running that way on the printer right now.
- **The service runs as root, so no udev rule ships.** Confirmed on the device; see section 3 and F-7.
- **The interpreter target is confirmed,** CPython 3.11.8 on aarch64 glibc, matching what the builder
  bakes for. No ABI risk to manage.

### 6.2 Open

Nothing blocking remains. Two decisions are outstanding, neither of which stops work starting.

1. **Which capability should `requires.capabilities` declare?** More data now: `camera-hw-accel`
   declares `rockchip-mpp`, a SoC feature it genuinely needs, and `webcam-usb` declares `camera-usb`
   while also requiring the `camera-service` that plugin provides. That pairing suggests `camera-usb`
   means "this printer's USB camera pipeline", and the diagnosis reinforces it: the thermal camera
   enumerates on the USB bus with no `/dev/video` node of its own, entirely outside the V4L2 pipeline
   that capability describes. `klipper-generic`, as `u1-remote-screen` declares, is the recommendation.
2. **Is CLAHE worth a numpy reimplementation? Answered no, on real frames. See 6.4.** Originally
   a Phase 5 decision, best made by looking at a real print
   scene. See section 6.4.

### 6.3 Printer diagnosis, read only

Every command below only reads. Nothing changes the printer, so no per-action authorization is needed.
Run them over SSH with the thermal camera plugged in, and paste the output.

```sh
# identity and the raw USB device
id
lsusb
ls -l /dev/bus/usb/*/*
ls -l /dev/video* 2>/dev/null

# what user existing plugin services actually run as
ps aux | grep -E 'camera|bespok3d' | grep -v grep

# does libusb exist for pyusb to bind to
ls -l /usr/lib/libusb-1.0.so* /lib/libusb-1.0.so* 2>/dev/null

# python ABI and libc, which decide whether the aarch64 wheels can load at all
uname -m
python3 -VV
python3 -c "import sysconfig; print(sysconfig.get_platform(), sysconfig.get_config_var('SOABI'))"
ls -l /lib/ld-musl-aarch64.so.1 /lib/ld-linux-aarch64.so.1 2>/dev/null
ldd --version 2>&1 | head -1
```

What each answers. `id` plus `ps aux` settle question 1: a service running as root needs no `MODE` line.
`ls -l /dev/bus/usb/*/*` shows the current owner and mode of the node libusb has to open; a plain `/dev`
listing is not enough, because a non-UVC camera creates no `/dev/video` node at all, only a
`/dev/bus/usb/<bus>/<device>` entry. The libusb check tests an assumption `README.md` states as fact and
nobody has verified. The last four lines settle question 6, and the libc line is the one that matters
most: the wheels being fetched are `manylinux2014_aarch64`, which requires glibc. If that printer is
musl, they cannot load at all, whoever fetches them, and the vendoring approach needs rethinking rather
than repairing.

### 6.4 What CLAHE is, and why it was a question. Answered no, 2026-09-17.

**Settled on six frames captured from the maintainer's own printer**, not on argument. The frames
are gitignored under `reference/frames/`; `scripts/compare-rendering.py` re-renders them, and
re-runs on any new capture in one command, which is the only reason this answer can be revisited
cheaply.

What the frames said:

- **On ordinary scenes**, a bed and a part spanning 12 to 15 C, CLAHE opens up the machinery in the
  background and adds grain to the bed. High-frequency texture, which on a flat surface is noise,
  goes from 3.3 to 4.0 today up to 4.5 to 6.3 at clip 2 and 5.4 to 8.1 at clip 4.
- **On a cold room**, where there is nothing to find, it amplifies sensor noise into what looks
  like structure. That is the honest counter-example and it is plain to see.
- **On a bed at 100 C with a part on it**, which is the case it was supposed to win, the grain cost
  vanishes, since there is no flat field to stretch: texture actually drops, 4.3 to 4.0. It shows
  more of the gantry and some mottling on the bed.
- **And then the comparison that ended it.** Plain linear ranging, narrowed to the bed's own
  temperatures, 90 to 99 C, shows the bed's real temperature distribution: hotter through the
  middle and left, cooler at the edges, the part sitting on it as a cold spot, and in the frame
  taken while it was still heating, the heater trace itself. Neither the global picture nor CLAHE
  is close. The arithmetic behind it: the bed surface spans 90.3 to 99.1 C against a frame
  auto-ranged 32.6 to 98.0, so its nine degrees get 39 of 256 levels, and they land at the top of
  ironbow where everything is nearly white.

So the answer to "I cannot see the detail on the bed" is a narrower range rather than a different
algorithm. Narrowing keeps one linear mapping, which means the colours still mean a temperature and
the ruler still tells the truth, and it costs less rather than more, because a fixed range has no
percentiles to compute. CLAHE would have cost an estimated 3 to 5 ms a frame, on top of breaking
the ruler: the maintainer's own suggestion was to hide the ruler whenever it was switched on, which
is exactly the right coupling and is also the clearest statement of what it costs.

One idea of mine died in the same experiment, which is worth recording because it sounded better
than it was: ranging from the region box. A box drawn around the bed still contains the part, the
frame and the gap, so its percentiles come out almost identical to the whole frame's and the
picture barely changes. The range has to be set on what you want to see, which argues for typed
numbers or a lock rather than for inferring one from a box.

What would reopen it: a camera pointed at a hotend, where a 250 C object and a 30 C background
share a frame, which is the case CLAHE was invented for and the one this printer cannot produce
from above, because the toolhead hides the nozzle.

### 6.4a What CLAHE is

The viewer's "image enhancement" is three separate things stacked, and only one of them is awkward on
the printer.

TNR, temporal noise reduction, averages each frame with the previous one, so per-pixel sensor noise
settles down. Three lines of numpy. DDE, detail density enhancement, is an unsharp mask: blur the frame
slightly, subtract the blur from the original to isolate edges, add a fraction of that back. Also
trivial in numpy.

CLAHE, contrast limited adaptive histogram equalization, is the interesting one. Plain auto-gain, which
this plugin already does, maps the coldest and hottest pixels in the whole frame to the ends of the
palette. That works badly when a scene holds one very hot thing and a lot of nearly-uniform warm
surface: the hotend takes the entire top of the range and the bed, the part and the draft shield all
collapse into a few indistinguishable shades. CLAHE instead splits the frame into tiles, equalizes
contrast within each tile, and interpolates between them, so local detail stays visible regardless of
what else is in frame. The "contrast limited" part caps how much any one tile can amplify, which is
what stops it turning sensor noise in a flat region into visible texture.

For a print bed that matters: the nozzle will always be the hottest thing in frame by a wide margin, and
everything you actually want to see is in a narrow band well below it.

The question is only where the implementation comes from. The viewer calls OpenCV's, and pulling arm64
OpenCV into the payload for one function is disproportionate. Written directly in numpy against a
160x120 or 256x192 frame it is roughly forty lines and a few milliseconds. My recommendation: implement
it in numpy, behind a toggle, in Phase 5, and decide it is worth keeping by looking at a real print
scene rather than in advance.

## 7. Phases

### Phase 0: spikes and scaffolding

1. Stand up the repo skeleton against the reference: `lib_bespok3d` submodule and `.gitmodules`,
   `scripts/check.sh` adapted from the reference, ruff and mypy config, a `tests/` layer with one real
   test, `CONTRIBUTING.md`, SPDX headers, and detector scoping (F-9, F-10, F-12, F-42).
2. Decide the fate of the vendored drafts (F-31).

Exit: `bash scripts/check.sh` runs green.

Status: complete. `bash scripts/check.sh` reports `All checks passed (6/6)`: pytest, ruff, release
trigger, manifest source and homepage, workflow pinning, and the em-dash ban. Written in this phase:
`scripts/check.sh`, `CONTRIBUTING.md`, `plugin/tests/conftest.py`, `plugin/tests/test_thermal_colormap.py`
and `plugin/requirements.txt`. Resolved along the way: F-12, F-48, F-49.

Two things to know. `shellcheck` reports as skipped rather than passed until `brew install shellcheck`,
so the shell in this repo is currently unchecked. And `plugin/requirements.txt` now exists, which means
a build without `--bake` will be refused by the builder's own gate, correctly, since the wheels
directory it declares is empty until CI or a local `--bake` fills it.

### Phase 1: a manifest that can actually install

Rewrite `plugin/manifest.json` to the real schema (F-32, F-39): `install.place[]` with a `web-location`
conf and a `moonraker-config` webcam fragment, `install.service[]` replacing the init script,
`install.restart`, `"venv": true` on the service, real permissions, `config[]` for bind, port and camera
display name, `channel` down
to `experiment` (F-38), plus `author` set to Mauker, `license`, `changelog`, `min_daemon_version`,
`attributions`, and the capability and exclusivity metadata from F-46. Add a per-plugin `doc/LICENSE`
and settle F-45.
Write the nginx location with `proxy_buffering off` (F-36). Delete `s65thermal-p1` (F-34), the udev file
(F-7, F-8, F-37), and the vendored numpy and Pillow trees, replacing them with a pinned
`requirements.txt` (F-43). Delete `pack.sh`, `generate-atom.mjs` and `fetch-vendor.sh`
(F-40), and build with `npx b3-builder build`. Add `.github/workflows/release.yml` and
`scripts/tag_version_guard.sh` on the reference pattern (F-11, F-41). Add `doc/CHANGELOG.md` and
`doc/ATTRIBUTIONS.md` (F-20, F-44).

Exit: a `.b3` built by `b3-builder` installs, the service starts, `/thermal/stream.mjpg` resolves
through nginx, and the camera appears in Fluidd without the user touching Settings.

Status: written and building. `thermal-master-0.2.0.b3` packs clean, and the archive was cross-checked
against its own `files[]`: nine payload members, every one listed, nothing shipped that is not, modes
only 644 and 755. The bake pulled the three aarch64 wheels, so the package is 20 MB where the
hand-unpacked trees were 70 MB. `publisher` reads `PLACEHOLDER`, which is correct for an unsigned
build. The plugin is renamed to `thermal-master`, capability `klipper-generic`, channel `experiment`.
The tag guard was exercised both ways, accepting `plugin-thermal-master-v0.2.0` and refusing a tag
claiming a version the manifest does not declare.

The service now starts, and the camera answers: with 0.2.2 installed the log ends at
`serving http://127.0.0.1:8082/stream.mjpg` with no traceback after it, and the camera's shutter is
audible, which means `connect`, `init` and `start_streaming` all got through and the NUC calibration
ran. That settles three things at once that were previously assumptions: root can claim the device
with no udev rule (F-7, F-8), `libusb-1.0.so.0` is where the diagnosis said it was, and the vendored
protocol handshake works against real hardware.

And then the rest of it: `/thermal/snapshot.jpg` returns 200 with a 12722 byte body starting `ffd8`.
That is a real JPEG off real hardware, so the whole path holds end to end, from USB bulk transfer
through the protocol, frame decode, colormap, JPEG encode, the HTTP server and the proxy. Phase 2 is
done.

Verified since, because a magic number is not an image: the snapshot decodes fully under Pillow as
640x480 RGB with a luminance range of 0 to 249, which is a real scene rather than a flat field, and
both `/thermal/snapshot.jpg` and `/thermal/stream.mjpg` render in a browser opened straight at the
printer's LAN address. Over the LAN the stream delivers about 1 MB in 3 seconds, roughly 26 frames a
second. Moonraker's `webcams/list` shows exactly one `Thermal` entry, `mjpegstreamer`, correct URLs,
`source: config`, no leftovers from the earlier installs, and the location file is symlinked beside
the screen plugin's.

The tile reporting an error while every URL it named worked turned out to be browser cache: the same
Fluidd page in a different browser rendered the camera immediately. Worth recording as a process
lesson rather than a finding. Four rounds of server-side diagnosis went by before the cheapest check
was made, and the evidence that should have prompted it arrived early, when the snapshot returned a
valid JPEG through the proxy. Cheap client-side explanations belong before expensive server-side
ones.

Installing it works, and the trial was worth its cost immediately. 0.2.0 installed cleanly: nginx
served the location, Moonraker registered the camera, the tile appeared in Fluidd under the chosen
name. The service, however, had never once started. Found by the trial: F-53 (wrong interpreter, the
actual cause) and F-52 (query strings 404ing, real but not the reported symptom). Both fixed in
0.2.2, which is built and in `dist/`. What is still unproven is everything past process start: the
USB protocol, the frame loop, and whether an image ever appears.

### Phase 2: verify on hardware

Nothing in this repo has run against a physical camera. Run the device-trial checklist on a P1 before
anything else is rewritten. This needs the maintainer at the printer: per `CLAUDE.md`, no device-changing
step happens without explicit authorization.

Exit: a known-good baseline, or a list of what actually breaks, which likely reorders everything after
this point.

### Phase 3: runtime robustness

F-13 through F-17, each with a regression test against a fake camera object so the frame-loop failure
modes are testable without hardware.

Exit: the streamer survives unplug, replug, a glitched frame, and a stop immediately followed by a
start.

Status: written, shipped in 0.3.0, and green, but only the tests have exercised it. F-13 and F-14 are
a SIGTERM handler that unwinds the session and a `release_camera` that calls `stop_streaming` before
`disconnect`, both best-effort so a camera already pulled cannot block the unwind. F-15 turns a marker
mismatch into one skipped frame rather than a teardown. F-16 counts consecutive empty reads and raises
`CameraStalledError` at twenty, which at the idle sleep is a fifth of a second of nothing where a
healthy camera delivers twenty-five frames a second; a single good frame forgives everything before
it, so an occasional empty read is not a stall. F-17 backs the reconnect delay off from three seconds
to a ceiling of sixty, resetting as soon as a session publishes anything.

F-29 is resolved at the proxy instead: the nginx location sets `Cache-Control: no-store` on the
snapshot. F-18 stays open, since there are no device controls yet to serialize; it belongs with
Phase 6. F-30 stays open with F-47.

The tests needed the fake camera to grow, so it moved out of `conftest.py` into
`plugin/tests/fake_camera.py`, where a scripted entry can be a frame, an exception, or `None`, and
`None` means what the driver means by it. That distinction is the whole of F-16.

What no test can cover: whether a real stop actually releases the interface on the real device. The
way to know is a stop, an immediate start, and no unplug in between.

### Phase 4: dual model

Auto-detect P1 and P3, migrate the plugin name with a `migration` block, reconcile docs (F-23), and
render the webcam fragment's aspect ratio and display name from `config[]` (F-24).

Exit: one package streams from either camera with no user configuration.

Status: shipped in 0.4.0. `detect_camera_model` probes the USB bus for each supported model's product
ID, taken from the driver's own configs so no product ID is written down twice, and the session drives
whichever it finds. With nothing attached it raises `CameraNotFoundError` rather than assuming a P1
and failing deeper in, and the backoff from F-17 turns that into a quiet retry instead of a log flood.
Six tests cover a P1, a P3, an empty bus, an unrelated device on the same bus (the printer's own USB
carries MCU links), a session with no camera, and a session picking up the P3's 256x192 geometry.
Testing this needed the doubles to fake `usb.core.find`, which is the only USB call the streamer makes
without going through the driver.

The P1 half is verified on hardware. The P3 half is verified only against the fake bus, because there
is no P3 here to plug in. What is untested is not the detection, which is four lines, but everything
downstream of a 256x192 frame: the frame size arithmetic and the shutter segment offsets are the
driver's, and the upscale factor is ours and produces a 1024x768 JPEG per frame on the P3 against
640x480 on the P1, which is nearly three times the encode work.

### Phase 5: image pipeline

Six palettes, EMA auto-gain (F-26), DDE, TNR, colorbar overlay, and the performance fixes F-25 and F-27.
A settings surface so palette and overlays change without an SSH session. Resolve CLAHE.

Exit: the feed is stable and readable, palette switching works live, and measured printer CPU is
recorded in the doc for both models.

Status: the pipeline shipped in 0.5.0; the surfaces around it did not. Done: EMA smoothing on the
display bounds (F-26), which is the flicker fix and the change worth looking at first; six palettes
built from two shapes, colour ramps for ironbow and rainbow and per-channel tints for the greys, with
black hot being white hot reversed; temporal noise reduction; an unsharp mask for detail; and F-25,
taking the percentiles on raw counts instead of converting a whole frame to Celsius to find two
numbers. The state that spans frames now lives in a `ThermalRenderer` rather than in module globals,
which is what made the smoothing testable: nineteen tests cover the pipeline and the suite is at 47.

Deferred, each for a reason. Live palette switching needs somewhere to switch it from, which is the
control page, so the palette is a service argument for now. The colorbar needs real temperatures,
which is why `raw_to_celsius` left the hot path but not the plugin. CLAHE stays unanswered until
someone looks at a real print. F-27, the upscale factor, is untouched because it matters most on the
P3 and there is no P3 to measure.

Measured, and the answer was not where anyone was looking. 0.5.0 came in at 62.5% of a core on the
printer, which prompted a stage-by-stage profile: the whole image pipeline, noise reduction, bounds,
normalisation, detail and palette, is 0.33 ms per frame, while the JPEG encode at 4x upscale is
1.16 ms, four fifths of the total. The pipeline was never the cost. The upscale was, and it bought
nothing: the browser scales the tile to fit regardless, so all those pixels added was a second lossy
resampling step.

F-27 is therefore closed in 0.5.2 by encoding at the sensor's own size, with quality raised from 80 to
88 to compensate, which the profile shows is free.

Confirmed on hardware, and the prediction was wrong in an instructive way. Comparing like with like,
the 30 second averages, it went from 64.5% of a core to 43%: a third off, not the three quarters the
profile forecast. The encode was 78% of the work on an aarch64 VM and roughly a third of it on the
printer's SoC, so the stage ratios did not transfer even though the architecture matched. The lesson
for later tuning: the remaining 43% is spread across the USB read, the numpy pipeline and the HTTP
serving, none of which has been measured *on the printer*, and extrapolating from a development
machine has now been wrong once by a factor of two. The upscale survives as `--upscale` for anyone who wants
it back.

Two side effects worth keeping. The tuning constants moved into a frozen `RenderSettings` dataclass,
because the argument count tripped the gate's own limit, and that is the shape the control page will
want anyway: hand the renderer a whole new settings object rather than poking attributes. And the
conftest loader was registering the module in `sys.modules` after executing it rather than before,
which is the wrong order and only surfaced once a dataclass tried to resolve its annotations.

### Phase 5b: the control page

Shipped in 0.6.0, ahead of its place in this plan, because orientation forced it.

The sequence is worth recording. Fluidd can rotate a camera itself, but only from a config file this
plugin owns, so changing it means a reinstall; and the installer did not carry the chosen values into
the rendered config, so every build came up with defaults and a sideways camera could not be corrected
at all. Three rounds went into diagnosing that, including two wrong theories of mine (that Fluidd
ignored `aspect_ratio`, and that server-side rotation would fix the bars) that hardware disproved.

What shipped: a `SettingsStore` behind a lock, persisted as JSON to `$BESPOK3D/var`, a `RendererSource`
that rebuilds the renderer when the revision changes so nothing mutates a renderer the capture thread
is inside, a JSON endpoint at `/thermal/settings`, and a plain form at `/thermal/` with no JavaScript.
Orientation moved into the renderer as a last step, after the pipeline, so the frame kept for noise
reduction cannot change shape mid-run. Fluidd's own rotation is pinned to zero in the fragment, since
setting it in both places rotates twice.

The bars are not fixed and cannot be: a 3:4 picture cannot fill a 4:3 tile, and `aspect_ratio` does
not reshape Fluidd's card. Mounting the camera the other way round is the only real answer, and the
plugin now makes that a preference rather than a constraint.

Confirmed on hardware, 2026-09-14: palette, rotation and both mirrors all change live from the page,
the picture reorients within a second, and `$BESPOK3D/var/thermal-master-settings.json` holds the
chosen values across a restart. The control page also shows the image without bars, since there it
sizes itself rather than fitting someone else's tile.

Still open from Phase 5: CLAHE. The colorbar closed in Phase 5c below.

### Phase 5c: the temperature readout

Shipped in 0.7.0. The colorbar and its companions, which is the last of Phase 5 except CLAHE.

The design question was where to draw it. Burning it into the frame is ugly at 160x120 and costs
encode time; drawing it in the control page from a `/stats` endpoint is crisp and free. Burning it
in won, because the surface that matters is the Fluidd tile, and that is a plain `<img>` with
nowhere to hang an annotation: a readout only the control page can show is a readout nobody sees
during a print. `/stats` ships as well, so the choice costs nothing later.

What shipped: a colorbar down the right edge labelled with the ends of the *display range* rather
than the scene extremes, since the bar exists to say what a colour means; a centre crosshair with
the temperature under it; a marker on the hottest pixel with its temperature; Celsius or Fahrenheit;
an overlay toggle; and `/thermal/stats` carrying the same numbers plus the frame average and the
coldest pixel, with both extremes given as pixel coordinates in the orientation being displayed.

Three things are worth recording.

**Marker coordinates are cross-checked, not derived.** `orient_point` follows `orient` step for
step rather than collapsing to one transform, and the test marks a pixel, orients the frame with
the real function, and asserts the marked pixel is where `orient_point` said. All eight orientation
combinations. A marker that lands on the wrong pixel is worse than no marker, because it looks
authoritative.

**Text drawing nearly cost more than the picture.** Pillow charges about 0.2 ms per `draw.text`
call, and four labels with a shadow each is eight calls: 2.0 ms a frame, against 1.4 ms for the
whole rest of the pipeline including the encode. Caching a rendered tile per character and pasting
took that to 0.19 ms, about nine times cheaper per label. Pillow's `stroke_width` outline was
measured too and is four times worse than a plain draw, so the shadow stays a shadow. The cache is
pinned by a test, because it is not an optimisation detail: without it the readout is unaffordable.

**The overlay pays for its own resolution.** Nine pixel text at the sensor's own size is porridge
after JPEG, so switching the readout on raises the encode until the frame's *short* side reaches
240. The short side, not the width: a rotated camera is 120 across and 160 down, and measuring the
width would triple that frame for no more legibility than doubling it. F-27's saving stays intact
for anyone who turns the readout off.

Statistics are computed for every frame whether or not the overlay is on, so `/stats` always
answers about the frame a client is looking at. Five passes over a frame this small measured inside
the noise floor.

Cost, predicted and then measured, and the prediction was wrong again. I forecast 47% of a core,
reasoning from the printer's own 43% at 1x and 64.5% at 4x: 21.5 points for fifteen extra
frame-sizes of encoding is about 1.4 points each, so doubling should cost four. Hardware said
**57%**, so the readout cost 14 points, three and a half times the forecast.

That is the third extrapolation in this project to miss, and the second to miss by more than a
factor of two, so the conclusion is no longer about this particular number. The model that keeps
failing is *cost is linear in pixels with no fixed term*, and it fails in both directions: it
overestimated what removing the 4x upscale would save (predicted three quarters, got a third) and
underestimated what adding a 2x one would cost. Something in the encode path scales with neither
the pixel count nor the stage ratios of any development machine available here.

The response is a tool rather than another estimate. `scripts/profile-frame-cost.py` times the
pipeline, the 1x encode, the 2x encode and the readout as four cumulative stages, runs under the
plugin's own venv against the installed streamer, and never opens the camera, so it is safe beside
a live service. From here the rule for this repo is that a performance claim about the printer
comes from that script run on the printer, and a number from anywhere else is labelled as such.

Confirmed on hardware, 2026-09-14: the readout renders in the Fluidd tile at 16 fps with the
colorbar, centre crosshair and hotspot marker all correct, `/thermal/stats` agrees with the
picture, and rotation is right. One defect showed up that no synthetic scene had produced: with the
hotspot in the bottom right corner, its label was clamped back inside the frame directly on top of
the colorbar's low label. Fixed in 0.7.1 by giving the colorbar's box to the shared style and
labelling a crowded marker on its left instead, with tests for both sides and for the left edge.

Not done, deliberately. No coldspot marker and no average burned in: both are in `/stats`, and at
this size a fourth and fifth label is clutter rather than information. No ROI, which needs a pointer
and therefore Phase 7.

### Phase 6: device controls

Shipped in 0.8.0. Shutter, gain and emissivity, with the concurrency rule written into the plugin
doc and pinned by tests.

The plan said "a command queue drained by the capture thread" for all three. Reading the driver
first split that into three different problems, and only one of them turned out to need a queue.

**Emissivity never reaches the camera.** `raw_to_celsius_corrected` is a pure function of a raw
value and an `EnvParams`, so emissivity is arithmetic done here, on the way out. That makes it a
rendering setting rather than a device one, and it needs no thread discipline at all. It is applied
to the six temperatures `FrameStats` reports rather than to the frame, because the correction is
monotonic: correcting the hottest raw pixel gives the same answer as correcting all 19,200 and then
taking the hottest, and after F-56 a full-frame float pass is the one thing this processor cannot
be asked for. The average is the single approximation, since a fourth-power curve does not commute
with a mean, and it is documented in the function rather than quietly shipped.

**Gain is a setting, not a command.** Modelling it as a queued action would have been wrong in a way
that only shows up on a replug: a camera that has just been opened is in its own default, and a
queue that has already delivered its message has nothing left to re-send. So `CameraSettings` holds
what the gain should be, with a revision of its own, and `DeviceController` compares that revision
against the last one it applied. Idempotent, so it can be checked every frame for an integer
comparison, and self-healing across a reconnect through `forget_session`. The separate revision is
why changing a palette does not put a control transfer on the USB bus.

**Only the shutter is genuinely an action**, and it coalesces rather than queues: three presses want
a calibration, not three of them, and each one costs a frame. A failure is recorded for the page and
then re-raised, because a control transfer that fails is a camera that has gone rather than a frame
that was slow, and that belongs on the reconnect path.

F-18 is closed by all of the above. The rule it was about is unchanged and now has tests that fail
if it is broken: `trigger_shutter` and `set_gain_mode` write a control transfer and then read the
same bulk endpoint the frame loop reads, so from an HTTP handler either the acknowledgement is
consumed as pixels or a frame is consumed as the acknowledgement, and the stream is desynchronised
for the rest of the session. The fake camera records what it was told, so a command sent from the
wrong thread fails in the suite rather than on hardware, where it presents as a camera that has
started returning nonsense.

`GainMode.AUTO` is not offered. The driver's own comment says the protocol does not implement it:
`set_gain_mode` records the mode and sends nothing, which would be a control that silently does
nothing.

Two user-visible consequences worth stating plainly. The default emissivity is 0.95 rather than 1.0,
so readings are slightly higher than 0.7.x, which applied no correction at all. And the shutter
button works with no JavaScript, because the form posts and redirects and the reload is the report:
by the time the page comes back the capture thread has been round the loop.

Confirmed on hardware, 2026-09-14, and two things came back.

**F-57. The vendored driver's `trigger_shutter` cannot work on a P1.** Fixed in 0.8.1 by not using
it. It sends the command and then reads back the mistimed frame the camera emits, reassembling it
from two segments whose offsets are absolute line counts: `shutter_seg_2_lines` is 800, and
`shutter_seg_2` is that times the sensor width. On the P3 that is 204,812 bytes into a 206,872 byte
buffer and fits. On the P1 it is 128,012 bytes into a buffer of 83,224. The read overruns first,
which is what reached the control page as "memoryview assignment: lvalue and rvalue have different
structures".

The fix sends the two control transfers directly, `COMMANDS["shutter"]` and the status read, and
leaves the frame that follows to the ordinary reader. That is safe because `read_frame` already
resynchronises: it treats a twelve byte read arriving before the end of a frame as an end marker in
the wrong place, drops the partial frame and starts again. So the part of the helper we skipped is
the part that was broken, and the part we kept is model independent.

The driver stays unpatched, per VENDORING.md, and this goes upstream instead. It is worth reporting
carefully: the geometry properties around it are all derived from `sensor_w`, so the two 800s look
like the only measurements in that file that were never generalised from the model they came from.

The fake camera now raises the hardware error from `trigger_shutter` and implements `_send_command`
and `_read_status` instead, so a future change that goes back through the driver's helper fails in
the suite with the same message that came off the printer.

**F-58. The colorbar and the hotspot marker read as contradicting each other.** Fixed in 0.8.1.
Hardware showed a bar labelled 29.2 at the top next to a marker reading 35.8, and the maintainer
reasonably asked which one was lying. Neither: the bar is labelled with the display range, which is
the 2nd and 98th percentiles smoothed over about a second, and the hottest pixel is routinely above
that and drawn in the top colour. The numbers were right and the picture did not say how they
related.

Considered and rejected: labelling the bar with the scene extremes instead. It would make these two
numbers agree and would then be lying about every colour in between, since the palette does not span
the extremes. Widening the percentiles was also rejected, because the clipping is what stops one
glint from washing out the picture, which is the flicker problem F-26 was about.

What shipped instead says the true thing: a red tick on the bar where the hottest pixel falls, and a
red triangle at the end of the bar when it falls past it. The bar keeps meaning what a colour means,
and now shows where the marker sits relative to that.

The same round found the label collision fix from 0.7.1 was too narrow. It reserved the bar's width;
the bar's labels are right-aligned to the margin and are four times wider than the bar, so a hotspot
in the bottom corner produced "35.8C20.9C". The reserved column is now sized to the widest label the
bar can carry, and a flipped label is also pulled left of that column rather than only left of its
own marker, since the marker itself can be inside it.

Exit: each control is exercised on hardware and the stream survives all of them. Gain, emissivity
and the numbers are confirmed; calibration is confirmed as reaching the camera and failing, which is
what produced F-57, and needs one more run on 0.8.1.

### Phase 6b: separate switches for the readout and the ruler

Shipped in 0.9.0. Asked for directly: the readout is currently one switch that turns off the colorbar, the
centre reading and the hotspot marker together. Those are two different things to a person looking
at a tile. The colorbar is a ruler down the edge that says what a colour means; the readings are
numbers drawn over the picture. Wanting the ruler without numbers over the image, or numbers without
a ruler eating five percent of the width, are both reasonable.

Planned shape: replace the single `overlay` flag with `colorbar` and `markers`, both defaulting on,
and derive the encode bump from either being set rather than from the master. A settings file saved
by 0.8.x carries `overlay`, so loading one that has it and not the new keys sets both from it, which
keeps a deliberate off staying off across the upgrade. One test for that migration, and the existing
`test_the_overlay_actually_marks_the_picture` splits into one per surface.

Worth doing before Phase 7, because Phase 7 adds a second tile and the question of what the plain
tile shows becomes a setting people will actually reach for.

Built as planned, with one thing the plan missed and the rendered output caught. The reserved column
that keeps a marker's label off the ruler's labels was still being reserved when the ruler was off,
so a hotspot on the right flipped its number away from a column holding nothing. `overlay_style`
now takes the flag and reserves nothing when there is nothing to avoid. Visible only by looking at
the four combinations side by side, which is why they were rendered rather than reasoned about.

The migration is narrower than "if overlay is present, use it": it applies only when neither new key
is there. A file written by this version carries both and may still carry the old key beside them,
and in that case the old key is a leftover that must not win. Three tests, one per case.

### Phase 6c: the coldest pixel, and a switch per surface

Shipped in 0.10.0, asked for after 0.9.0 landed: a coldspot marker, and each part of the readout
switchable on its own.

The interesting part was not the coldspot. Adding a third marker turned an occasional cosmetic flaw
into the normal case: with the centre crosshair, the hottest pixel and the coldest pixel all drawn,
two of them landing near each other is ordinary, and the rendered output showed "21.2" and "70.9"
written across each other the first time the hotspot passed near the middle. Labels now take the
first position that is inside the picture and clear of the ones already drawn, trying beside the
marker first, then below, then above. A lone marker is unaffected, which has its own test, and a
label with nowhere clear to go is still drawn, because the cross already says where and a tight
number beats no number.

That also collapsed three drawing functions into one. The reticle, the hotspot and the coldspot
differ in which pixel, which temperature, which colour and how long the arms are, and in nothing
else. They were three copies of a placement rule that had already changed twice and was about to
change a third time.

The ruler ticks only the extremes being marked, so switching a marker off takes its tick with it and
the bar cannot contradict the picture.

Two pieces of housekeeping came out of it. The settings migration is now a list of splits applied in
order, `overlay` into `colorbar` and `markers`, then `markers` into the three marker switches, so a
file old enough to need both gets both. And the package facade had gone stale: names added to a
module after the split were simply missing from `__init__.py`, with an AttributeError in whatever
reached for them as the only sign. There is now a test comparing what the modules define against
what the facade exports, which is the sort of thing that should never rely on anyone remembering.

### Phase 7: the embedded page

The WebSocket temperature channel and the client-side features: readout, ROI, hotspots, zoom, reticle,
unit toggle, screenshot, and the page's own rotate and flip. Registered as a second `[webcam]` iframe
tile so it lands in Fluidd next to the plain feed.

Exit: usable on a phone-sized viewport, and closing it drops the temperature channel back to zero cost.

### Phase 7a: the viewer, and reading temperatures off the picture

Shipped in 0.11.0. The first slice of Phase 7: the frame endpoint, the interactive page, and the
tile that carries it.

**The transport decision.** Mouse-over and ROI both need per-pixel temperatures, which `/stats`
cannot give. Three ways were available: ask the server per point, send a downsampled grid, or send
the whole frame. The whole frame won because it is the only one that makes ROI free later, and
because the cost is bandwidth rather than the thing that is actually scarce here. `/frame.bin` is 38
KB for a P1, and producing it is a memory shuffle and four float passes, paid per request rather
than per frame. That last part is the point: the capture path stores raw counts and nothing converts
them until somebody asks, so a tile nobody is looking at costs nothing (F-56 discipline).

Hundredths of a degree in a signed 16 bit integer, little endian, behind a self describing header.
Corrected for emissivity on the way out, so the browser never carries a second copy of the physics,
which was the mistake waiting to happen.

**Geometry moved to its own module.** `orient` was in the pipeline and `orient_point` in
temperature, and the frame encoder needed the first from the second, which would have been a cycle.
They belong together anyway: they are the pair that must not drift, and now they are in one file
with the cross-check test pointing at both.

**What the browser found that Python could not.** Two things, and both are the reason this slice
insisted on the harness first.

The harness itself had rotted. It loaded the entry script by path, and when the code moved into a
package (F-47) that file stopped being the plugin. It had been broken for two releases, silently,
because nothing runs it. It now imports the package the same way the test suite does.

Then the first real run found a defect in the viewer: the first hover after loading never resolved.
The pointer handler turns a position into a pixel, and it cannot while no frame has arrived, so it
discarded the position and nothing recomputed it when the frame came. The page said "reading..."
until the mouse happened to move again. Invisible to every server-side test, obvious within a second
of a real pointer. The page now keeps where the pointer is separately from which pixel that was, and
recomputes on each frame.

**One harness mistake worth recording.** The first version worked out where the letterboxed picture
sits inside the canvas so it could point at a fraction of it. That was wrong twice: it divided by an
image that had not decoded, and even correct it would have been the page's own arithmetic copied, so
a mistake in the mapping would have been made identically on both sides and passed. It now sizes the
window so the picture fills the canvas exactly, asserts that it does, and needs no geometry at all.

### Phase 7b: the ROI box

Shipped in 0.12.0. Drag a rectangle, get live max, min and average inside it, measured in the
browser from the frame it already holds. The printer does no more work for a box than for a hover,
which is what 7a's transport decision bought.

Two behaviours worth stating because they were choices rather than consequences. A region keeps the
frames coming with the pointer away, because a box is a standing question and freezing it the moment
the mouse leaves would defeat the one use that matters: set it on the bed, walk off, come back to a
number. And the region survives a reload, in local storage, because a tile in Fluidd reloads whenever
the page around it navigates and losing the box every time is the difference between a tool and a
toy.

**A second cold-start bug, of exactly the same family as 7a's.** Drawing a box in the first moment
after opening the tile did nothing at all. The press turned a screen position into a frame pixel
immediately, and no frame had arrived to turn it against, so the drag was dropped. The fix is the
same shape as the one before it: hold the screen positions, resolve them to pixels when a frame
exists, and retry a drag that could not be resolved when one arrives.

That is twice now that "this operation needs a frame" has been handled by silently doing nothing.
Worth remembering as a shape rather than two incidents: the viewer's whole job is answering questions
about a frame, and the interesting moment is always the one before the first frame has landed.

The harness now opens a fresh page and drags immediately, which is the case a warmed up page cannot
test and the realistic one for a person who opens the tile to measure something. Found by a
screenshot rather than by a check, which is its own lesson: the first version of the region checks
hovered before dragging and would have passed forever.

### Phase 7c: zoom, pan, screenshot, units

Shipped in 0.13.0. Three of the four were as low risk as predicted. The fourth was not, and the
reason is worth keeping.

**The units button needed a server change first.** The obvious build is to post the unit to
`/settings` the way the control page posts everything else. That would have switched off every part
of the readout, because a posted form cannot distinguish an unticked checkbox from an absent one, so
`"colorbar" in form` reads a form that never mentioned the colorbar as a form saying it is off. That
is the correct reading for the control page, which always sends every field, and catastrophic for
anything sending one.

The alternative considered was having the page read the current settings and post them all back
with the unit replaced. Rejected: it puts knowledge of the form's shape into JavaScript, where it
drifts silently the next time a setting is added.

So `/settings` now also takes JSON, where absent means absent and only named keys are applied. Same
validators either way, because what a setting may be does not depend on how it arrived. That is a
better foundation than the units button needed, and 7d and anything after it inherit it.

**Zoom is expressed as one rectangle, not two.** `imageBox` already answered "where is the picture",
and the overlay and the pointer mapping both went through it. Zoom and pan fold into that rectangle,
and the picture element is then positioned from it explicitly rather than being left to `object-fit`.
One source of truth, so the image and the annotations cannot drift apart when zoomed. The browser
check asserts this against rendered geometry rather than an internal number, because the only failure
that matters is the picture and the overlay disagreeing, and only the rendering shows that.

**Pan is a mode, not a modifier.** Measure mode already uses drag for the region box, and the same
page has to work on a phone where there is no key to hold and both gestures are a finger moving
across the picture.

The screenshot saves at the sensor's own resolution with the region drawn on it, rather than at
whatever size the window happens to be, because a picture of a measurement that does not show what
was measured is not evidence of anything.

### Phase 7 hardware round, 0.14.0

Three findings from the printer, and the first two were one bug.

**F-62. The live tile showed its controls and no camera.** Every browser check ran in a window, and
in a window the page was fine. Fluidd gives an iframe tile about 260 by 340 CSS pixels, where the
toolbar wrapped to three rows and the readout to two, taking 299 of those 340; the stage, being
`flex: 1` with `min-height: 0`, was flexed down to 41 pixels. So the tile showed a toolbar and no
picture, and the controls appeared to do nothing because the thing they controlled had no height.
The maintainer reported it as two separate problems, reasonably, since that is how it looks.

Fixed by giving the picture a floor of 55% and hiding the toolbar below 460 pixels of height, with a
line saying where it went. A tile is for looking at and pointing at, and both work without any of
those controls; zooming and saving are things you do once you have opened it properly, which is
exactly when the room appears. The harness now runs a pass at tile size, which is the lesson: a
responsive page tested at one size is tested at one size.

**F-63. The ruler and the markers disagreed, again.** 0.8.1 answered this by adding a tick and a
triangle to show that an extreme was off the end of the scale. That was true, and it did not work:
the same question came back from the same person looking at a ruler topped 25.3 beside a marker
reading 30.0. Being right about the labels is not the same as being understood, and after the second
report the design is the thing that is wrong.

The bar now spans the scene, coldest at the bottom and hottest at the top, so its ends are the
numbers the markers show and there is nothing left to explain. The colours still come from the
auto-ranged mapping, which means the rows above and below that range come out flat, and that is
honest rather than a compromise: those pixels really are drawn in one colour, so colour really does
stop carrying information there. Two ticks mark where. The end labels take the marker colours, so
the eye ties the number at the top of the ruler to the red cross without being told.

Rejected again: mapping the palette across the true extremes, which would make the bar linear and
bring back the flicker F-26 removed. One glint should not restage the whole picture.

**A process note.** Editing this by slicing the file between two function names deleted five
unrelated functions that happened to live between them, which the suite caught immediately and
`git show HEAD:` restored exactly. Worth preferring anchored replacements over positional slices,
which is how every other edit in this project has been made.

### Phase 7 hardware round, 0.15.0

**F-64. The viewer was blank until the pointer crossed it.** Shipped in 0.13.0 and survived two
hardware rounds. The picture element is positioned by `paint`, which ran on pointermove, resize and
frame arrival, and on none of those at load. A viewer opened with no saved region showed nothing at
all.

It hid because every way of looking at it moved a mouse first. Every screenshot I took moved the
pointer to read a temperature; the maintainer's screenshots had a saved region, which starts the
frame fetching, which paints. The check that catches it now measures the picture's width before
anything touches the page, which is a rule worth generalising: a page that only paints on
interaction looks perfect to any test that interacts.

**F-65. A landscape tile spent its width on nothing.** The tile is wider than tall; a rotated camera
is taller than wide. Stacking the readout underneath took height from the only dimension the picture
could use, leaving it about 110 pixels tall in a 200 pixel tile. The readout now sits beside the
picture when the viewport is wider than the picture needs, decided by the script rather than a media
query, because the picture's shape changes when the camera is rotated and a media query cannot know
that.

Which exposed a third thing: the page could not lay out at all until it knew that shape, and the
only sources were an `<img>` of a multipart stream, which reports no size until a part decodes and
fires no event when one does, and the frame endpoint, which is not fetched until someone points at
something. So the plugin now states the shape in the page it serves. It is the thing producing the
picture; asking the browser to discover it was the wrong way round.

**The ruler, again, and this time smaller.** The tick marking where the auto-ranging stops is gone:
it was drawn on top of a boundary the gradient already draws, since above it the bar is flat and
below it the colour varies. One moving line too many, reported as distracting and redundant on
inspection. The triangles are back at the ends, in the marker colours, now meaning "this end is that
marker" rather than "this extreme is off the scale", and drawn only for a marker that is on.

**A note on a check that was wrong rather than flaky.** "Fit puts the picture back" compared the
width after Fit against one captured before the zoom. Those are different layouts: the panel's height
changes as its own text changes, which changes the stage, so the comparison failed by 52 pixels for
an honest reason. Asserting what Fit means, that the picture is inside the stage and touching it on
one axis, is true whenever it is true and depends on no history. The first instinct was to widen the
tolerance, which would have buried a real 52 pixel discrepancy under a rounding excuse.

**F-66. Two cameras of the same thing, and no way to hide either.** The viewer shipped in 0.11.0 as
a second `[webcam]` entry rather than a replacement, on the argument that the two fail differently:
a plain image renders in anything, a script does not, so a broken viewer still left a camera on the
dashboard. The cost was not priced. Fluidd marks a camera that comes from a config file as managed
by Moonraker and will not let the user touch it, so the dashboard showed both tiles, permanently,
with the plugin as the only place either could be turned off.

My first answer to the report was that Fluidd's own camera selector is the right place to choose,
which was wrong for the same reason the rotation controls are greyed out there, and I had already
written that reason into this file. Reading it back would have been quicker than being told twice.

The fallback was worth keeping, so it moved into the page. `.stage img` now carries `inset: 0` and
`object-fit: contain`, which is a correctly letterboxed picture with no script at all; `paint`
overrides those four properties and releases `right` and `bottom`, which the stylesheet pins and
which would otherwise over-constrain the box and make the width it sets quietly ignored. `paint`
also stopped hiding the picture while the shape was unknown, since hiding it was only affordable
while a second tile existed.

The browser harness grew a context with JavaScript disabled to check it, which first measured the
wrong rectangle: `object-fit` letterboxes the painted content inside the element box, and the
element still covers the whole stage, so the element's bounding box says nothing about where the
picture is. The check now computes the painted rectangle from the natural size, and asserts first
that the page's own script really did not run, because `page.evaluate` still works in that context
and a check that quietly tested the scripted layout would have passed forever.

**F-67. The controls were hidden in the one place they were wanted.** 0.14.0 answered a tile that
showed a toolbar and no camera by hiding the toolbar below 460 pixels of height, which is every
dashboard tile there is. That fixed the picture and made the controls reachable only by opening the
viewer full screen, and left the tile printing "open for tools" where the tools should have been.
Reported as "I always have to open it to actually use the controls", along with the picture being
too small, which was the same budget seen from the other side.

The height was not the problem; the spending was. The readout was taking half the width beside the
picture and three lines underneath it, for four short numbers. It now takes a fixed narrow column
capped at a third, and one line. The toolbar is always shown, compact, under the picture in both
layouts, which needed the picture and the controls to become one flex column so that only the
readout moves to the side. The row fits across a 260 pixel tile once the mode button says "Box"
rather than "Measure" and the zoom percentage hides below 340 pixels of width. Measured: in a
540 by 400 tile the picture is 273 by 364, the same as it was with no toolbar at all; in a 260 by
340 tile it is 174 by 232, against 143 by 190 when the toolbar wrapped to two rows.

The check that pins it is a budget rather than a bound: the picture keeps at least two fifths of the
tile, at both tile shapes. A toolbar that wraps to a second row or a readout that takes three lines
fails it, which is exactly the regression, and it does not care how the space was reclaimed.

Also found here, by reading the markup while changing it: the tile note was malformed. Its `<div>`
was closed before its text, so "open for tools" was loose content in the readout bar and the bar
closed early. The browser papered over it and the screenshot showed it plainly once I looked.

**F-68. The settings page was a one way trip.** The viewer links to it, and in a Fluidd tile that
link navigates the tile itself. An iframe has no browser chrome, so there was no back button, and
the only way to the camera was reloading the dashboard. The control page now carries a relative
`view` link at the top. The browser harness walks the round trip, because a link that goes one way
is only visibly broken from inside a tile.

### Phase 7d: MP4 recording. Shipped in 0.18.0.

MediaRecorder over a canvas that is drawn at the picture's own resolution, which is the resolution
the readout was burned in at. Recording the stage instead would have recorded the letterboxing and
whatever the zoom happened to be, which is a recording of a browser window rather than of a camera.
The still and the clip share one painter, so a saved frame and a saved clip cannot disagree about
what was on screen.

The browser-specific part turned out to be smaller than expected and in a different place. The
formats are offered in preference order, MP4 first because it opens on a phone and in a chat window
without a conversation about codecs, then WebM; the clip is named for the container it actually is
rather than for the one that was asked for. The trap is that `isTypeSupported` is an opinion: the
container the check ran against answers yes to `video/mp4` and no to the same type with an explicit
`avc1` profile string, and a browser is allowed to accept a type there and then refuse to construct
a recorder for it. So the format is chosen by constructing the recorder and keeping the first one
that is built, not by asking.

Two bounds, both because a recording is held in memory until it is stopped: ten minutes stops
itself, and leaving the page stops it rather than dropping it on the floor.

Still owed: this has run in Chromium and not yet in the maintainer's Safari, which is the browser
that decides whether the MP4 preference is worth anything.

**An unreproduced flake, recorded rather than explained away.** One run of the browser harness
failed "an unwatched viewer fetches no frames", which is the promise that an idle tile costs the
printer nothing beyond the stream. It has not recurred in five full harness runs since, nor in
twenty one instrumented page loads. What those loads did show is that a `pointerover` at the origin
fires on every load, because the canvas lands under a stationary cursor at (0, 0), and that no
`pointermove` follows it, so nothing starts fetching. The obvious fix, ignoring a pointermove that
does not move, would therefore have been a fix to something that was not happening. Left alone,
with the check as it is: it encodes a real promise, and widening it would bury the next occurrence.

### Phase 7e: arbitrary spot markers. Shipped in 0.19.0.

Requested from hardware use: place points on the picture and keep reading all of them at once, for
watching several areas of a print at the same time.

**The decision that shaped it, taken by the maintainer.** A spot could live in the browser, like the
region box does, costing the printer nothing per viewer; or in the plugin, drawn into the picture,
costing CPU per spot for everybody. The browser version cannot appear in the dashboard tile, in a
recorded clip, or in a second browser, which is most of the reasons to place one. Asked, and told to
spend the cycles, capped at four. So spots are a render setting: they persist, they are burned into
the stream, and every surface shows the same ones.

How it went together:

- **A spot is a 3 by 3 patch, averaged**, not a pixel. One pixel of a 160 by 120 sensor is noisy and
  hard to land on with a finger. The patch mean carries the same fourth-power approximation the
  frame average does, which over nine adjacent pixels is far below what the sensor can resolve.
- **Two coordinate spaces, and a new inverse.** A spot is placed on the picture as displayed and
  measured in a frame that has not been turned, so `geometry.unorient_point` is the other direction
  from `orient_point`, cross-checked against it for all eight orientations rather than derived
  independently. Mapping the point rather than turning the frame: turning it per request would cost
  a copy for four numbers.
- **Turning the picture drops the spots.** They name places on a picture that just moved, and
  carrying the coordinates across would leave each marker pointing confidently at something it was
  never put on. Dropped visibly beats moved silently. The comparison is between two whole settings
  objects, so the control page posting every field on every apply is not read as a rotation.
- **One request shape.** The viewer posts the whole list it wants, so placing, moving and clearing
  are the same request. An empty list is a clear, which is why the JSON dialect reads the key rather
  than the value.
- **Placed spots get first refusal on a label position**, ahead of the hotspot, coldspot and centre,
  because somebody asked for them by name.

**F-69. Placing a spot before the first frame did nothing, and the harness caught it.** The third
time this family has appeared: a screen position only becomes a pixel once there is a frame to turn
it against, and the first thing anyone does on opening the tile is go straight for the thing they
wanted to measure. F-64 was the same shape for the picture, and the pending drag was the same shape
for the region box. A held click, resolved when the frame lands, alongside the pending drag that was
already there. Worth stating as a rule: anything this page turns into a pixel needs an answer for
"and if there is no frame yet", and the browser harness is the only thing that asks.

**A settings change no longer makes the picture re-settle.** Every placement is a settings change,
and a settings change rebuilt the renderer from nothing, which discarded the smoothed bounds and the
previous frame and cost about a second of visible re-ranging. Placing four spots would have made the
picture breathe four times. The replacement now inherits both from the renderer it replaces, which
is safe because only the capture thread ever asks for one. Nothing carried depends on a setting: the
bounds are raw counts and the kept frame is in the sensor's own orientation.

**The region box now carries its numbers into a saved still and a recorded clip.** Reported from
use: the box was drawn and the numbers were not, so a clip showed where a measurement was taken and
not what it came to. The burned-in readout is about the whole frame, so nothing else in the picture
could supply them.

**F-61. A camera plugged in after boot. Closed, and it was never broken.** Reported as needing a
reboot before a camera attached after start-up would appear. Confirmed from use: it does come up on
its own, it just takes a while, which is the retry loop doing exactly what it was written to do.
Nothing to fix, and nothing was: the diagnosis plan below is kept only because the reasoning about
`usb.core.find` after a libusb init is worth having if the symptom ever comes back for real.

### Phase 7f: the display range, told rather than measured. Shipped in 0.22.0.

The answer to two complaints that turned out to be one: the ruler moving whenever a toolhead
crossed the view, and the bed arriving as a flat colour because the room is in frame too. Both are
the auto-ranging doing its job, and the fix is to let it be switched off.

- **`range_mode` is `auto` or `fixed`.** Fixed carries two temperatures, and the renderer converts
  them to raw counts once per settings change rather than per frame.
- **The conversion is a bisection through the driver**, not a formula. `raw_to_celsius_corrected`
  is the vendored driver's physics, including the emissivity correction, and a hand-inverted copy
  of it here would be a second physics that can drift. It is monotonic, so twenty-four halvings
  land inside a hundredth of a degree, and it runs when the settings change.
- **A told range does not measure one.** `frame_bounds` is not called at all, which is the second
  most expensive step in the pipeline gone: about 0.9 ms a frame. A test asserts it by making the
  function raise, because a silent return of that cost is exactly the kind of thing that creeps
  back.
- **The ruler follows the mode.** On auto it spans the scene, as since 0.15.0. On fixed it spans
  the range, so it is a constant reference, and the triangles get their pre-0.15.0 meaning back:
  something is past this end. That reading only became true again once the scale stopped moving,
  and on fixed they follow the scene rather than the marker switches, since somebody who turned the
  hotspot marker off still needs to know the picture is clipping.
- **"Hold what I see now"** posts through the same `command` field the shutter uses and freezes the
  range the picture is currently using, rounded to a tenth because it lands in a form field a
  person then edits. With no frame yet it changes nothing rather than locking to a guess.

Two housekeeping changes came out of the same work, both of them the rule of three arriving:

- `scripts/refresh-facade.py`, because the package facade had been updated by hand four times in a
  week and each time needed a second round to satisfy ruff's import order. It only adds, since
  removing an export is a decision.
- A `settings_dict` fixture and a shared `palettes` fixture in `conftest.py`. Two tests built a
  settings dictionary from a literal, so every new setting broke tests that had no opinion about
  it, and four files had written the palettes fixture for themselves.

**F-71. The page threw away the answer it asked for.** Reported after the first hardware test of
0.22.0: pressing "Hold what I see now" gave no sign of having worked. It had worked. The control
page posts in the background, which is what keeps the video stream from being torn down on every
change, and the reply carries the whole settings payload, but the script read one field out of it,
the device status line, and dropped the rest.

That was harmless for as long as every change originated in the form, since the form already knew
what it had sent. It stopped being harmless the first time a button changed something the form was
displaying. And the consequence was worse than the missing feedback: the form went on showing
`range_mode=auto` and the old ends, so the next Apply posted those and silently undid the hold.

The fix is one function: after a reply, walk the form and set every control the payload names,
skipping whatever has focus so a reply cannot overwrite what somebody is typing. Selects need care,
because the page writes some option values to a fixed number of decimals and an emissivity of 1 is
the option "1.00", so the spellings are tried in turn rather than special cased by field name.

The browser harness grew three checks, and they were confirmed to fail against the old page with
exactly the symptom that was reported: the select reading "auto", the boxes reading 20.0, and the
mode back to auto after a second Apply. The general lesson is worth keeping: a page that posts in
the background has to accept the answer it gets back, not just the part of it that it expected to
change.

Still open, and deliberately: the decimation and the dead band for auto mode. The maintainer's
call is to live with the modes first, because somebody who ends up in fixed mode most of the time
does not need them.

### Phase 7g: stop working when nobody is watching

Reported from `htop`: the plugin sits at about a third of a core forever, because `stream_frames`
renders and publishes every frame the camera produces whether or not anything has asked for one.
The viewer is careful about this on its own side, it stops fetching temperatures when nobody is
pointing, and the printer was doing the work anyway.

Two states, agreed with the maintainer, and they are not alternatives:

**Measured first, 2026-09-17, at 0.22.1**, with `scripts/measure-cpu-on-printer.sh`:

| state | cost |
| --- | --- |
| nothing open | 40.6% of one core |
| dashboard tile visible, nobody pointing | 42.3% |
| viewer open, pointer moving | 45.1% |

Nine tenths of the cost is paid whether or not anybody is looking, and serving a live viewer adds
4.5 points on top of it. It also cross-checks the profiler: 14.94 ms a frame at 25 fps is 37%, and
the USB read and the loop account for the rest, so two instruments built for different purposes
agree.

**Idle, which needs no switch. Shipped in 0.23.0.** A minute with nobody asking for a picture and the capture loop
stops rendering. Any request wakes it. The detail that makes it safe is to keep reading frames from
the camera and skip only the pipeline: the USB read is mostly waiting rather than CPU, the device
stays in sync, and waking costs one frame instead of a reconnect. A request marks interest and
waits briefly for a fresh frame, because serving what was last published could hand somebody an
hour old picture. The auto-ranging should start clean on waking, since its smoothed bounds are as
old as the idle.

**Confirmed on the printer, 0.23.0:** 4.6% of a core with nothing open, against 40.6% before, a
drop of 89%. The two watched states did not move, 41.9% with a tile visible and 45.7% with somebody
pointing, which is the half that would have been a bug: an open tile idling would mean a dead
picture on the dashboard. What is left is the USB read keeping the camera in step, and that is the
floor until the device is released.

**Off, which is a switch and a deeper state. Shipped in 0.24.0.** It releases the USB device, so
nothing runs and the camera can be unplugged, and it publishes a rendered placeholder as the
current frame: "Stream off" as a title, and a line naming the button that brings it back.
Everything then shows that with no error states, because it arrives through the same path a real
frame does. `/thermal/frame.bin` refuses instead, since there are no temperatures behind a picture
of words, and the viewer says "no frame" rather than reading numbers off it. The switch is on the
settings page and in the viewer toolbar, hidden in a narrow tile as Rec and Spot are, and it
survives a restart: a reboot should not quietly start burning CPU somebody turned off, and the
placeholder is what makes that discoverable.

Three decisions worth writing down, because each had a worse obvious alternative:

- The placeholder is republished twice a second rather than once. Publishing once would leave the
  MJPEG stream stalled on its last part and make every request wait out `wake`'s timeout before
  being answered. Repeating it costs nothing, because the JPEG is encoded once and kept.
- The state is a saved setting rather than a command with a flag beside it. That is what makes it
  survive a restart for free, and it lets the capture loop ask "should I be running" between frames
  as well as between sessions, so Stop is acted on within one frame instead of at the next
  reconnect.
- The viewer re-reads the settings whenever it comes back to the front. The switch is the
  plugin's, so the settings page or another browser can change it while the viewer is not looking,
  and a toolbar button showing the state from before that is worse than one a moment late. It costs
  nothing when nobody returns, which is the constraint this whole phase is under.
- Stopping mid-session returns from `stream_frames` rather than setting a flag somewhere. Returning
  unwinds through `run_capture_session`, whose `finally` is what hands the USB interface back, so
  releasing the camera goes down the path that was already tested rather than a second one written
  for this.

**Confirmed on the printer, 0.24.0, 2026-09-17:** 0.0% of one core over a 30 second window, after
70 seconds of quiet, with the camera switched off and nothing open. Not "small enough to ignore":
the kernel's own counter did not move at all, which is what a process that is sleeping on an event
and doing nothing else looks like. Start and stop were exercised on hardware first.

That completes the sequence for this phase: 40.6% of a core before any of it, 4.6% once the plugin
stopped rendering into an empty room, and 0.0% once it stops reading the camera as well. The
measurement was taken with `scripts/measure-cpu-on-printer.sh`, and the protocol is part of it: one
open tile keeps the plugin fully awake by design, so a sample taken with a dashboard open is a
different number and not a disappointing one.

**And then, separately: the plugin reports its own cost. Shipped in 0.25.0.** It reads
`/proc/self/stat` and puts its share of a core into `/thermal/stats` and onto the control page, so
the question stops needing ssh at all. An honest feature for a plugin whose whole design tension is
what it costs the printer, which is why it was written here rather than smuggled into the change
above.

Two numbers rather than one. Recent, over a window of at least two seconds, is what changes when a
tile is opened or the camera is switched off; since the service started is the fair figure for a
plugin that sleeps most of the day, and it is the one that is available immediately. Below a couple
of seconds the window is mostly scheduling noise, so a request that arrives sooner is answered with
the previous answer rather than with a division nobody should trust.

No thread and no timer behind it: a feature about not working in the background is a poor place to
start a background loop, so a reading is taken on the request that asks for one. The page polls it
every five seconds, which is free in the one place it happens, because a settings page holds a
video stream open and so the plugin is fully awake for as long as anybody is there to read the
number.

### Phase 7h: the repository README. Shipped in 0.25.0.

Reported by the maintainer, and it was worse than one stale section. `README.md` at the root of the
repository still describes the project as it was planned rather than as it is, and somebody
arriving at the repository reads it first.

What is wrong, in the order a reader hits it:

- **The name.** The title is `thermal-p1` and the opening paragraph is about the P1 only. The
  plugin has driven both models since Phase 4, the package has been `thermal-master` since 0.2.0,
  and the repository is `B3_ThermalMaster_P1_P3`.
- **"At 160x120 / ~25fps the work is trivial for the CPU."** It was not. It was 62.5% of a core
  before Phase 5 and 40.6% before Phase 7g, and three phases of this roadmap exist because of it.
  The sentence should say what it actually costs and point at the idle and off behaviour.
- **The layout block.** It lists `files/bin/` as "hand-maintained Python" with no mention of
  `files/lib/`, which is where the whole plugin now lives, and no mention of `plugin/tests/` or
  `files/wheels/`. It names `scripts/pack.sh` and `scripts/generate-atom.mjs`, neither of which
  exists: `b3-builder` replaced both in Phase 0. It does not name `scripts/check.sh`, which is the
  one script a contributor has to run.
- **"Not verified on hardware yet."** Flatly untrue since Phase 2. Every release from 0.5.0 onwards
  has been confirmed on the maintainer's printer, and this roadmap records the measurements. The
  section should be replaced by the hardware status that `plugin/doc/README.md` already carries
  correctly: the P1 is exercised continuously, the P3 is implemented and untried.
- **The vendoring and release sections** are broadly right and worth re-reading against
  `VENDORING.md` and the workflow rather than trusted.

`plugin/doc/README.md`, the one that ships inside the package and is rendered in the app, has been
kept current throughout and is the model for what the root one should say. The two have different
jobs: the shipped one is for somebody using the plugin, the root one is for somebody opening the
repository, and the second has been reading like a plan for a plugin that does not exist yet.

Done as its own pass rather than folded into a feature, because the failure mode is a reader
believing it. The root `README.md` was rewritten: it now names both cameras, states what the plugin
costs with the measured table rather than calling the work trivial, describes the layout that
actually exists including `files/lib/` and the gate, documents the tag-driven release and both
secrets, and replaces "not verified on hardware yet" with the real hardware status. Section 9 of
this file was carrying the same rot, claiming nothing was committed, and has been split into a
current status and the record that follows it.

### Phase 7i: the viewer notices a dead stream. Shipped in 0.26.0, refined in 0.26.1.

The stream is an `<img>` of a never ending multipart response, and an `<img>` says nothing when that
response ends. The viewer therefore has to look for itself, and it has two things to look at: the
picture it is showing, and the plugin's own count of frames published.

**The pieces.**

1. **A new `/health` says how many frames the plugin has published and how long ago the last one
   was.** Three fields: `frame` (the publication count `LatestFrame` already keeps),
   `frame_age_seconds`, and `streaming`, whether the switch is on. This is what separates a dead
   stream from a camera that stopped: in the first the count moves and the picture does not, in
   the second neither does.

   A route of its own rather than two more fields on `/stats`, agreed on 2026-09-28 after reading
   the code. `/stats` answers 503 whenever the last frame carries no measurements, which is always
   the case while the "Stream off" placeholder is showing, so the watchdog would read the camera
   being switched off as the printer not answering. And `/stats` wakes the capture and waits up to
   half a second for a fresh frame on every call, which is right for a client that wants the
   numbers and pointless work for one that only wants to know whether frames are flowing.
   `/health` always answers 200 and never waits.

   It does say that somebody is watching, as the stream does on every part. Without that, a page
   whose stream died would stop being counted as a viewer, the capture would idle after its minute,
   the count would stop, and the watchdog would blame the camera for what is its own dead stream
   and never restart it. A hidden page does not ask, so the capture still idles behind it.

2. **The viewer samples its own picture.** Every two seconds it draws the displayed frame into a
   32 by 24 canvas and compares it with the last sample. The picture is same origin, so reading it
   back is allowed. The cost is one tiny draw every two seconds. It remembers when the picture last
   changed.

3. **The viewer asks the plugin, on the same beat.** It fetches `health`, relative like every other
   URL the page emits, and only while the page is visible: a hidden tab needs no watchdog, and
   becoming visible triggers a check at once.

4. **What it concludes:**

   | Plugin answers | Plugin's count | Picture | Conclusion | Action |
   |---|---|---|---|---|
   | no | | | printer not reachable | badge "printer not answering since HH:MM:SS"; keep asking every 2 s (0.26.0 backed off to 10 s; see F-74) |
   | yes, stream off | | | the placeholder is meant to be still | nothing |
   | yes | moving | changed within 3 s | healthy | clear any badge |
   | yes | moving | unchanged for 3 s | this page's stream is dead | restart it; badge "reconnecting" if it is still stale 3 s later |
   | yes | not moving for 3 s | | the camera stopped sending | badge "no frames from the camera since HH:MM:SS" |

   Restarting sets the `<img>` source to `stream.mjpg?n=<counter>`. The router already drops the
   query string before the lookup, so this reaches the same handler, and the changing URL is what
   makes the browser open a new request. At most one restart every 5 seconds. An `error` event on
   the `<img>` restarts at once, within the same limit.

   Three seconds, agreed on 2026-09-28: at 25 fps it is 75 frames, and the quietest stretch
   measured in F-74's clip changed in 88% of them. Raise it if it proves too eager.

5. **The badge** is a line over the picture, in the style the pointer readout already uses for "no
   frame", with the local time the picture last changed. It clears itself when frames flow again.

6. **Recordings carry it.** While the badge shows, the recorder paints it into the clip, so a clip
   can never pass a frozen stretch off as a still scene. That is the confusion F-74 took an
   afternoon to settle.

**Tests.**

- `/health` answers 200 with `frame`, `frame_age_seconds` and `streaming`, before any frame and
  while the placeholder shows; the count moves with each publish, and the age is the time since
  the last one. It notes interest and does not wait for a frame.
- `stream.mjpg?n=7` routes to the stream handler.
- The rendered page has the watchdog's timings substituted, like the recorder's, and the restart
  URL is relative (the existing no absolute paths test should catch it if not).
- The watchdog's decision table is one pure function taking the last sample time, the count, its
  last change and whether the plugin answered, and returning an action. That keeps the logic in
  one testable place.

**Decided on 2026-09-28:** no JavaScript runtime tests for the decision function yet. The gate
already requires Node for the shared detectors, and `test_page_scripts.py` runs `node --check`
over both pages' scripts, which catches the syntax errors a script inside a Python string invites.
The behaviour is left to the hardware round, as the viewer's has been so far. Revisit if the viewer
gains a second piece of logic worth testing on its own.

**Added after the first hardware clips, 2026-09-28.** Two recordings of wifi drops showed the line
appearing 2.4 s and about 3 s after the freeze, and clearing on the same frame the picture moved
again, with no "reconnecting" in between. Three changes came out of them:

- **The page asks at once when the browser reports its network is back** (the `online` event),
  instead of waiting out the back-off, which could add up to ten seconds to a recovery.
- **Rec is offered only over a moving camera picture.** A clip's size is fixed when it starts, and
  one started over the "Stream off" placeholder kept the placeholder's 320 by 240 for the whole
  clip, squashing the camera's 240 by 320 into it. The button is disabled while the switch is off
  and until the picture has moved since, which is what tells the camera from the still
  placeholder. A running recording is never disabled, because its button is how it stops.
- **Switching the camera off ends a recording and saves it**, whether from this page or, as
  `health` reports it, from anywhere else. A stream that has merely stopped keeps recording,
  because a clip across an outage is how F-74 was caught.
- **`scripts/check-in-browser.py` publishes a picture that changes**, a small grey block in one
  corner cycling through seven shades, with the temperatures untouched. It republished one JPEG,
  which the watchdog rightly takes for a dead stream.

**Decided not to fix:** a recording made inside Fluidd is lost when Fluidd rebuilds its panel. The
page ends the recording on `pagehide`, but the browser discards the page before the clip is
written. Saving it in pieces as it records would fix that, and is not worth it: record from the
viewer opened on its own.

**Hardware round.**

Repeat F-74's tests with 0.26.0 and write down the results:

1. The viewer on its own, laptop wifi off for 30 s: the badge appears, and the picture returns by
   itself within a few seconds of the wifi coming back, with no reload.
2. The same for 90 s, past nginx's 60 s.
3. The same two inside Fluidd.
4. Stream switched off: no badge, the placeholder shows.
5. Camera unplugged while streaming: the camera badge, not a restart loop. Plugged back in: it
   clears once the capture loop reconnects.
6. A recording across test 1: the badge is in the clip.

**Documentation.**

`CHANGELOG.md` 0.26.0; the plugin README's troubleshooting note gains a sentence on the badge; F-74
gains its confirmation paragraph; section 9's list of what is left gains this phase until it ships.

### Phase 8: signed, public, and in the Bespok3d organisation. Shipped in 0.27.0.

Decided on 2026-09-28, once Phase 7i had been confirmed on hardware: private testing is over, and
the plugin is published before the timelapse is built rather than after, so that what already works
reaches people and the timelapse ships as a release of its own.

- **The repository moves to the organisation and is renamed.** Transferred from
  `Mauker1/B3_ThermalMaster_P1_P3` to `Bespok3d/B3_ThermalMaster`, which GitHub redirects from the
  old address, and made public. The new name is the camera family rather than the two models, since
  more Thermal Master drivers may follow. The local working folder keeps its old name.
- **Signed with the organisation's key**, the one every package in the Bespok3d main index carries,
  fingerprint `679939555819fb5f6423dc68c4388e76bfa9b4e0`. A public repository in the organisation
  reads the organisation's `REGISTRY_SIGNING_KEY` and `MAIN_INDEX_TOKEN` directly, so it holds no
  secrets of its own. `publisher` stays `PLACEHOLDER` in source; the signing step stamps it. This
  closes F-5 and F-6 once the first signed release is out.
- **`release.yml` rebuilt from the organisation's template.** The builder is pinned to
  `9322144c063a621191fe50951c803c6f79227277` (main on 2026-09-28) and registers the list itself,
  replacing the separate registration step. The checkout no longer asks for submodules: the
  workflow's token reaches only its own repository, so checking out the private `lib_bespok3d`
  would have failed the first public run.
- **The tests run in CI, twice.** `plugin/tests/run.sh` builds its own environment from
  `requirements.txt` plus pytest and runs the suite, since CI cannot have the gate's private
  submodule. The builder runs it after packing and before releasing, and `tests.yml` runs it on
  every pull request and branch push, with read access only and no secrets, so a pull request from
  a fork can never reach the key. A failing test blocks a merge once the branch rule requires the
  `tests` check.
- **Published as `rc`**, "mostly stable; some rough edges": every release has been confirmed on one
  printer and nobody else has run it yet. Promoted to `stable` in a later release once somebody has.
  A user left on the default `stable` setting does not see an `rc` plugin until they reach for it.
- **Tags are protected** before the first one is pushed: a ruleset on `plugin-*` restricting who
  creates, updates or deletes them to maintainers, because a tag is what publishes a package with
  the organisation's signature. `main` has a ruleset of its own: changes arrive by pull request,
  and the `tests` check has to pass before one merges.

**Shipped on 2026-09-28.** The first run of `tests.yml` failed, and rightly: it found F-75, a
defect no machine the suite had run on before could show. With that fixed and merged to `main`,
the tag `plugin-thermal-master-v0.27.0` built, tested, signed and published the release
`thermal-master-v0.27.0`, and `Bespok3d/main-index` gained `lists/B3_ThermalMaster.json`, listed as
"Thermal Master" under the organisation's fingerprint. In the Bespok3d app the plugin appeared, and
installed and ran on the maintainer's printer once the right version was selected in the app.

### Phase 9: a thermal timelapse, one frame per layer. Built in 0.28.0 to 0.28.6.

The printer's own camera makes a timelapse of each print. A thermal one would show something that
one cannot: heat spreading through the part and the bed layer by layer, and where it cools first.
Sketched on 2026-09-28 and planned on 2026-09-29, below as it was planned. Steps 0 to 2 and the
colour comparison of step 4 are built and were tried on the Pi 4 and the U1 on 2026-09-29 and
2026-09-30, released together in 0.28.6 with four colour scales, clips with their own readout, and
no wait for a U1 print's own clip when the printer is not making one. Step 3, the automatic gain
switch, is built as 0.29.0 and 0.29.1 and was tried on the Pi 4 and the U1, where the camera was
measured to choose where it switches.

**Decided so far.**

- **Layer changes come from Moonraker, not from reading the G-code.** Klipper keeps
  `print_stats.info.current_layer` and `total_layer` when the slicer's layer change G-code sets
  them, and on the U1 with Snapmaker Orca it does. Queried on 2026-09-28 at
  `/printer/objects/query?print_stats`: `null` and `null` with the printer on standby; then, on a
  150 layer cube, `0` of 150 as the print started, `25` about six minutes later and `45` after
  another two and a half, with `state` reading `printing` throughout. Parsing the file would only
  repeat what Klipper already knows. The plugin asks Moonraker, on the printer, about once a second
  while the feature is on.
- **Nothing to press.** With the timelapse switched on in the settings, the plugin wakes the capture
  itself when the layer number changes, exactly as a request for a picture does, and keeps that
  frame. Between layers the capture idles as it does now (Phase 7g), so the cost is one wake per
  layer.
- **The temperatures are kept, not the JPEGs.** A P1 frame is 160 by 120 two byte values, 38.4 KB,
  so the 150 layer cube is about 5.8 MB; a P3 frame is 98.3 KB. Kept raw, every frame can be rendered
  at the end with one range for the whole print, where frames rendered live would each carry their
  own and the clip would flicker in brightness; and the finished timelapse can still be read for
  temperatures.
- **Encoded on the printer once the print has finished**, when it can spare the processor, with
  the U1's own `/usr/bin/ffmpeg` (4.4.4) and `libx264`: there is no hardware encoder ffmpeg can
  reach (1). Settled on 2026-09-29 as a starting point, to be judged on real clips:
  - **640 by 480, four times the P1's frame, scaled up with nearest neighbour** before encoding.
    At the sensor's own size `yuv420p` would keep colour at 80 by 60, which smears a palette, and
    any label drawn into the frame would be unreadable. A P3's 256 by 192 would go to 768 by 576.
  - `-c:v libx264 -preset veryfast -pix_fmt yuv420p -movflags +faststart`, the last so a browser
    can start playing before the file has arrived.
  - Run under `nice -n 19` with `-threads 2`, so a print started straight after is not slowed.
  - Scaled with PIL's nearest neighbour, not `numpy.repeat`, which was twice as slow on the U1.
- **What a clip costs, end to end.** Measured on the U1 on 2026-09-29 with
  `Claude outputs/bench-timelapse-render.py`, the service running and idle, on 150 made up P1
  frames (a warm bed, a part growing on it, a 210 C nozzle crossing) rendered with a told 20 to
  120 C range: the plugin's side is 10 ms a frame (the colour map 3.5 ms, the 4x nearest neighbour
  scale 3.4 ms, the colour bar and hot spot label 3.2 ms), and ffmpeg took 7.3 s of processor and
  about 5 s of wall clock for the 150, under `nice` on two threads, for a file of 82 KB, 109 KB
  with the overlay. That is about 49 ms of ffmpeg a frame, more than the 35 ms of the encoder alone
  in (1), the difference being the conversion from the RGB the plugin hands it. So a 1,000 layer
  print is about a minute in all, against the firmware's three to five for its own clip. Not worth
  optimising.
- **The frame is taken the moment `current_layer` changes**, not after a delay or a wait for the
  park, because some layers are over in seconds (2).
- **How many are kept is a setting, counted in prints: "How many thermal timelapses to keep",
  10 by default.** When a print falls off the end, its clip, its thumbnail and its kept
  temperatures go together, so there is one number to think about. A clip deleted from Fluidd or
  Mainsail simply stops counting. Behind it, two rules that are not settings: kept temperatures
  (38.4 KB a layer on a P1, so about 38 MB for 1,000 layers, two and a half times that on a P3) are
  kept only for the newest prints, enough to render a clip again with another range; and **a free
  space floor that overrides the setting**, since the printer's free space matters more than this
  plugin's own count: below it the oldest kept temperatures go first, then the oldest clips, and no
  new frames are kept until there is room. Only the plugin's own files are ever deleted (4).
- **Nothing gathered is thrown away (5).** A cancelled print keeps its frames and gets its clip:
  timelapses are useful for diagnostics, and a failed print is the one worth looking at. After a
  reboot or a power loss mid-print, the plugin finds kept temperatures with no clip whose print is
  no longer running when it next starts, and makes the clip from what it has. A plugin update
  cannot restart it mid-print, since B3 blocks updates while printing; a crash could, and then the
  same print is still running, so it carries on adding to the same set. With the camera unplugged
  or switched off, every layer passed without it repeats the last picture taken with a band across
  its top, "Camera disconnected, layer N" or "Camera off, layer N", drawn as the viewer draws its
  line over a frozen stream, so the clip keeps its length, holds still where the camera was lost,
  and says so (changed on 2026-09-29 from a separate card, after the Pi 4 run below).
- **The gain can switch itself, once per print (6).** A print starts in the gain the user has set.
  If that is high sensitivity (-20 to 150 C) and the hottest pixel reaches 145 C, typically the
  nozzle coming into view, the plugin switches the camera to wide range (0 to 550 C) and keeps it
  there until the print ends, then puts the user's setting back. One way only: switching back each
  time the nozzle left the view would make the clip jump between two looks, and wide range is
  coarser at bed temperatures. The switch is made as soon as the crossing is seen, and the next
  layer's frame is taken only once the picture has settled after it, so no frame in the clip is
  one the switch disturbed; the frame that saw the crossing is kept, clipped at the ceiling. There
  is one camera and so one gain: the live view switches with it, and the settings page says so,
  for example "Wide range, switched for this print". Every kept frame records the gain it was
  taken in. Not for the conversion: the camera sends 1/64 kelvin in both gains and the driver
  converts both alike, which the bed reading the same in both confirms (below). It is kept because
  the two gains disagree about cool things, so a reading from a clip is only understood with its
  gain beside it. With the switch turned off, the settings page warns when the timelapse is on in
  high sensitivity.
- **What a gain switch does to the picture.** Measured on 2026-09-29 from a viewer recording of
  the bed heating, fixed display range, switched from high sensitivity to wide range at about 1:55
  and back at about 2:06, frame by frame:
  - **To wide range:** no new picture for 0.7 s, then the picture is back at once. The readings
    then drift for about four seconds (the coldest spot 20.2 C, then 16.9, 13.5 and 10.1), and at
    1:59.2, after half a second of nearly still frames that looks like the camera's shutter, they
    step and hold: the coldest spot about 11 C, the centre 19.4 C against 24.8 C before, the hot
    spot on the bed 60.3 C against 58.4 C with the bed still heating. So wide range reads things
    near room temperature eight to ten degrees colder than high sensitivity, and reads the hot bed
    about the same.
  - **Back to high sensitivity:** no new picture for 1.0 s, then within a second the readings are
    back where they were (the coldest 17.5 C then 18.7 C, the centre 23.3 C). The recording stops
    three seconds later, so a later shutter is not ruled out.
  - **Repeated the same day with the bed held steady**, 20 s or more on each side of each switch.
    It confirmed the first and explained it: **every switch is followed by the camera's own shutter
    3.7 s later**, in both directions and in both recordings (3.74 s, 3.69 s, 3.73 s), with the
    readings drifting until it and stepping at it. The first recording ended 3.1 s after its second
    switch, just before that shutter. The picture froze for 0.6 s on the way back. Before, in high
    sensitivity: the coldest spot 20.5 C, the centre 25.1 C, a spot on the frame 30.6 C, the bed's
    hot spot 56.1 C. In wide range after the shutter: 12.1 to 14.1 C, 20.5 to 21.9 C, 28.4 to
    29.7 C and 57.6 to 58.3 C, the cool readings still creeping up about 2 C over 20 s. Back in high
    sensitivity after the shutter: 19.8 to 20.2 C, 24.5 to 24.9 C, 30.0 to 30.5 C and 55.3 to
    56.0 C, within half a degree of where they started. So wide range reads room temperature six to
    eight degrees low and the bed a degree or two high, and high sensitivity comes back exactly.
  - **So the plugin waits 5 s after a switch** before it takes the next frame: the shutter at
    3.7 s and a margin after it. A layer that changes sooner has its frame taken late rather than
    disturbed. Wide range's error on cool things is one more reason for switching one way only,
    and only when the heat calls for it; for a nozzle and a hot bed it does not matter.
- **The colour range is a setting**: as displayed (Phase 7f), a fixed low and high of its own, or
  the whole print's coldest to hottest. The candidate default is a range fixed once the print has
  actually started, so the part is not washed out by what the bed and nozzle do before it; the
  choices are compared on real clips before one is made the default. The kept temperatures make
  the comparison cheap, since one print can be rendered every way.
- **Parking is the slicer's business.** The U1 parks the head for its timelapse only when the
  slicer is told to, so the plugin cannot count on it. The first version takes its frame at the
  layer change as it is.
- **It depends on no other plugin.** Bespok3d's `timelapse` plugin (`Bespok3d/fluidd-timelapse`),
  read on 2026-09-29, takes no pictures: it is a 47 line stub of Moonraker's `timelapse` component
  that registers a `timelapse` file root at `<printer data>/camera`, so Fluidd and Mainsail list the
  clips the U1's firmware writes there, and answers the settings endpoints with "off, no frames".
  So the capture, the frames and the encode are all this plugin's, and it never claims the
  `timelapse` service, which would conflict with that plugin or with `moonraker-timelapse`.
- **Where a finished clip goes, in two layers.** Always in the plugin's own folder, listed on the
  settings page to play or download, which works on any Klipper printer with nothing else
  installed. And, when Moonraker's `/server/files/roots` has a `timelapse` root, a copy there too,
  so it appears in the Timelapse page of Fluidd and of Mainsail. Asked on 2026-09-29: the U1 has
  one, at `/oem/printer_data/camera`, writable, from `fluidd-timelapse` (a second root, `camera`, is
  the same folder read only); the Pi 4 printer, an Ender 2 Pro Max on mainline Klipper, has none,
  and neither front end shows a Timelapse page there. On mainline the Timelapse page therefore needs
  `moonraker-timelapse` installed, which becomes an optional requirement, documented as such: only
  for the clip to show up there, not for the timelapse itself, and without its own capture turned
  on.
- **Named after the firmware's own clips, with `_thermal` added.** The U1 writes three files per
  print, e.g. `Voron_Design_Cube_v7_PLA_23m7s_20260928130941.mp4`: the G-code's name, the print
  time and the print's start in UTC; `<same>.jpg`, about 3 KB; and `<same>_cover.jpg`, about 34 KB.
  Ours is `<same>_thermal.mp4` with `<same>_thermal.jpg`, which sorts beside the normal clip and can
  never collide with a name the firmware writes. The plugin takes the base name from the firmware's
  clip for the same print once it appears, rather than rebuilding it, because the firmware's naming
  has changed: the first clip, `Dragon_Textured_PLA_20260417151828`, has no print time, and some
  are only a number and the start. With no firmware clip to follow, on mainline or with the
  printer's timelapse off, it builds `<G-code name>_<start UTC>_thermal`.
- **H.264 in MP4, as the firmware writes.** The firmware's clip of the 26 second cube, probed on
  2026-09-29: H.264 High profile, `yuv420p`, 1920 by 1080 at about 24 frames a second, two frames,
  muxed by libavformat 58.76, so the firmware encodes with ffmpeg too. The thumbnail is 120 by 90
  and the cover 880 by 495. Our clip is H.264 `yuv420p` in MP4, which is what the browser players in
  both front ends open; a P1 frame is 4:3, as the thumbnail is.
- **The thumbnail is `<same name>.jpg`, in both front ends.** Read in their sources on 2026-09-29:
  Fluidd (`src/store/files/getters.ts`) gives every file in the `timelapse` root the `.jpg` of the
  same base name as its thumbnail and hides every `.jpg` from the list; Mainsail
  (`src/components/panels/Timelapse/TimelapseFilesPanel.vue`) lists only `.mp4` and `.zip`, takes the
  same `.jpg` as the preview, and renames or deletes it with its clip. Neither reads `_cover.jpg`.
  Both screenshots from the U1, 2026-09-29, show the firmware's clips with thumbnails. So the plugin
  writes one `.jpg` rendered from the last frame, beside the clip.
- **The firmware encodes after the print too, and takes minutes.** The cube started at 13:09:41
  UTC, printed for 23m7s, and its clip was written at 13:35:40, about three minutes after the end;
  a seven hour print's, about five. The plugin's encode waits until the firmware's clip for the same
  print has appeared, or a few minutes have passed, so the two never share the processor.

**To test once built.** A finished clip plays from the settings page on both printers; on the U1
it appears, with its thumbnail, in the Timelapse page of Fluidd and of Mainsail; on the Pi 4, the
same in Mainsail once `moonraker-timelapse` is installed, and only on the settings page before. The
U1 half was tried on 2026-09-29 with the test pair from 8: both front ends listed
`..._thermal.mp4` beside the original with its thumbnail and opened it in their player, and since
Moonraker gave the uploaded files `rw` where the firmware's are `r`, Fluidd offered Remove for it.
Also to test: the gain switching once and the clip showing no disturbed frame; a camera unplugged
mid-print leaving "camera disconnected" frames and a clip of the right length; a print cut by a
power loss getting its clip at the next start; a cancelled print getting its clip; the count and
the free space floor each deleting the oldest print's files and nothing else.

**Open questions.**

1. Which encoder to use, and what a clip costs. **Answered on 2026-09-29: `libx264`, and a clip
   costs seconds.** Measured on the U1, idle, with ffmpeg's `-benchmark`, encoding 150 frames of
   `testsrc2` at 24 frames a second, the length of the 150 layer cube:

   | Encoder | Size | Processor time | Wall clock | File |
   | --- | --- | --- | --- | --- |
   | frames only, no encode | 160x120 | 0.06 s | 0.06 s | |
   | `libx264 ultrafast` | 160x120 | 0.30 s | 0.18 s | 232 KB |
   | `libx264 veryfast` | 160x120 | 0.62 s | 0.26 s | 107 KB |
   | frames only, no encode | 640x480 | 0.25 s | 0.25 s | |
   | `libx264 ultrafast` | 640x480 | 2.16 s | 0.85 s | 1.67 MB |
   | `libx264 veryfast` | 640x480 | 5.51 s | 1.93 s | 0.60 MB |

   Net of making the frames, `veryfast` at 640 by 480 is about 35 ms of processor per frame, so a
   1,000 layer print is about 35 s of one core, and on two threads under `nice` well under a
   minute; the firmware takes three to five minutes over its own 1080p clip. `ultrafast` is under
   half the time at nearly three times the file, and the time is not the scarce thing. The files
   are an upper bound: `testsrc2` is a busy moving pattern, and a thermal scene compresses far
   better.

   `h264_v4l2m2m` failed at both sizes with "Could not find a valid device". The U1's
   `/sys/class/video4linux` lists only the camera's capture pipeline (`rkcif`, `rkisp`, the
   `gc2053` sensor and the MIPI receiver), no encoder. Rockchip's encoder is reachable only through
   its own library, which this ffmpeg is not built with; binding `librockchip_mpp.so` directly is
   not worth it for seconds of processor. The plugin's own side, turning kept temperatures into
   frames, was measured the same day; see "What a clip costs, end to end" above.
2. How the frame's moment relates to the layer change. **Answered on 2026-09-29: at the change,**
   since some layers are small. With parking on in the slicer the head may still be on its way to
   the park; that is accepted rather than waited for.
3. Answered on 2026-09-29, see "Where a finished clip goes" and "Named after the firmware's own
   clips" above. Left open: how the settings page lists and offers a finished clip.
4. How much to keep, and what clears it. **Answered on 2026-09-29:** a setting, 10 prints by
   default, with a free space floor over it; see "How many are kept" above.
5. What a cancelled print, a restart or a camera unplug mid-print leaves behind. **Answered on
   2026-09-29:** a clip of whatever was gathered, in every case; see "Nothing gathered is thrown
   away" above.
6. Which range to render with, and in which gain. **Decided on 2026-09-29.** A gain switch
   freezes the picture for up to a second and the camera's own shutter follows 3.7 s after it,
   measured twice, so the plugin waits 5 s after one; see "What a gain switch does to the picture"
   above. Left to do: compare the colour ranges on real clips. See "The gain
   can switch itself" and "The colour range is a setting" above.
7. Answered on 2026-09-29: `fluidd-timelapse` takes no pictures, so there is nothing to share and
   nothing to depend on; see "It depends on no other plugin" above. Separately, on mainline,
   `moonraker-timelapse` takes its frames from a camera's snapshot URL, so it may already make a
   rough thermal timelapse from `/thermal/snapshot.jpg`, flickering because each frame carries its
   own range. Worth one try on the Pi 4 once it is installed there, for comparison.
8. Whether the U1's firmware clears that folder on its own. **Answered on 2026-09-29, as far as
   five months show: it does not.** The folder held 129 clips with 822.6 MB free, and the oldest is
   the printer's first print, 2026-04-17 15:18:28 UTC, to the second. Matched against Moonraker's
   226 prints by start time: 122 completed prints and 7 cancelled ones have a clip, and the 97
   without are prints the maintainer ran with the printer's timelapse off (59 completed, 36
   cancelled, 2 Klipper shutdowns). A test pair, the smallest firmware clip and its `.jpg` uploaded
   through Moonraker as `Cube_PLA_26s_20260921135513_thermal.*`, came through a print with the
   folder otherwise unchanged, and was deleted through Moonraker afterwards. That print wrote no
   clip of its own, so a cleanup run only when the firmware writes a clip is not ruled out, nor one
   run only when the disk is nearly full. Searching the printer for the folder's path found nothing,
   so whatever writes the clips does not name it in plain text. Either way the plugin bounds its own
   use of the disk (4).
9. Whether the printer's screen lists that folder. **Answered on 2026-09-29: it has no timelapse
   list.** The `_cover.jpg` files begin with the prints of 2026-08-17, after a firmware update, and
   every earlier clip has none, so whatever reads them, probably Snapmaker's own app, which was not
   checked, copes without one.

**Also decided on 2026-09-29.** The camera's Off switch wins during a print: those layers get
"camera off" frames, as an unplug gets "camera disconnected" ones. The timelapse is opt in, off
until switched on, since it writes to disk and talks to Moonraker. Every timelapse setting lives
on the plugin's settings page, not in Bespok3d's install configuration: they apply without a
reinstall, and on a printer run without Bespok3d. Free space on the U1: 23 GB on `/userdata`,
where the plugin's own folder goes, and 822 MB on `/oem`, which holds the `timelapse` root, so the
free space floor matters for the copies there. Moonraker reports a folder's free space with its
listing, so the plugin needs no path to check it.

**Moonraker with logins forced.** Asked on the U1 on 2026-09-29, Moonraker answers the plugin
from `127.0.0.1` without a login. Bespok3d's `moonraker-auth` plugin can change that by turning on
`force_logins`, which as far as is known overrides trusted clients. So: when Moonraker refuses, the
settings page says so plainly rather than recording nothing in silence, and offers a field for
Moonraker's API key, which the plugin then sends as `X-Api-Key`. The key is kept in the settings
file and never returned by `GET /thermal/settings`, which says only whether one is set. Whether
Bespok3d offers plugins a supported way in is a question for its maintainer.

**The build plan, in five steps, each on hardware before the next.**

0. **The Pi 4 printer made ready**, since the camera is on it: ffmpeg with `libx264` installed,
   the slicer setting Klipper's layer count (`SET_PRINT_STATS_INFO`), and Moonraker checked to
   answer from `127.0.0.1`. The plugin keeps running there by hand, as in the validation.
1. **Record, encode, keep, and list on the settings page (0.28.0).** A new `moonraker.py`, a small
   client on the standard library with its address from `--moonraker-url` (default
   `http://127.0.0.1:7125`). A new `timelapse.py`: the print tracker (state and layer in, "started",
   "new layer" and "ended, and how" out, plain logic); the frame tap the capture loop offers every
   frame to, which keeps one per requested layer and marks a layer missing after 3 s without one;
   the recording, a folder per print with `meta.json` and one append only `frames.bin` whose
   torn last record is ignored on reading; the encoder (noise reduction off, the timelapse's range,
   the orientation at encode time, 4x nearest neighbour, the colour bar and hot spot label, a
   "camera disconnected" or "camera off" picture for missing layers, `nice -n 19 ffmpeg -threads 2`,
   `<name>_thermal.mp4` and a 120 by 90 `.jpg`); the retention (10 prints, temperatures for the
   newest 2, a 200 MB floor over both); and the worker thread, which also encodes any recording
   whose print has ended when it starts, and resumes one whose print is still running.
   `camera.py` offers each frame; `settings.py`, `page.py` and `server.py` gain the settings (on,
   how many to keep, the range and its low and high, the API key), a Timelapse section with the
   high sensitivity warning and the clips to play, download or delete, and the routes
   `GET /thermal/timelapses` and the files, served with Range so a browser can seek. `cli.py` and
   the manifest gain `--timelapse-dir $BESPOK3D/var/thermal-master-timelapse` and the thread.
   **Built on 2026-09-29 as 0.28.0, not yet run on hardware.** As planned, with these specifics:
   - Modules: `timelapse.py` (the settings, `PrintTracker`, `FrameTap`), `recording.py` (the folder,
     `frames.bin`, `Retention`), `moonraker.py`, `clip.py` (rendering and ffmpeg) and
     `timelapse_service.py` (the two threads). The stream's overlay moved into
     `ThermalRenderer.overlay_for`, so the clip's readout is the tile's own.
   - The capture loop offers every frame through `LatestFrame.offer_raw`, before the idle check;
     the tap copies one only when a layer asked for it.
   - Moonraker is polled every 2 s when no print is running, not 5 s: a run against a stand-in
     Moonraker with short layers missed the first three at 5 s. Once a second while printing.
   - One more frame is taken when the print ends, because the layer change G-code runs at the start
     of a layer and the last layer would otherwise never be seen finished.
   - `frames.bin` records carry a magic, the layer, the kind, the gain code, the size and the
     time; every write is flushed with `fsync`. The clip is written to `clip.mp4.partial` and
     moved into place; ffmpeg's messages go to a temporary file, not a pipe, so a chatty ffmpeg
     cannot block the frames going in.
   - The routes name a clip by query, `/thermal/timelapse.mp4?id=` and `/thermal/timelapse.jpg?id=`,
     keeping routing exact; the id must match the folder pattern before any path is built. Clips
     are served with `Range`, so a browser can seek. `POST /thermal/timelapses` deletes one, and
     refuses the print being recorded or made into a clip.
   - The settings file is checked on the way in against the defaults, so a hand edited value that
     is not allowed falls back to the default, not to itself.
   - Tests: the tracker and tap, the file format with a torn tail, retention in order, the
     Moonraker client against a stand-in (answer, absence, refusal, key), the ranges, a real
     encode counting its frames with ffprobe (skipped without ffmpeg), the service through start,
     layers, end, missing and off cameras, switched off mid-print, left open by a power cut or a
     crash, low disk and no layers, the settings and the key never leaving, and the HTTP routes.
     The browser harness gained a Timelapse run: Apply posts in the background, the key box is
     emptied and says "Saved", the key is nowhere on the page, a print name is shown as text, and
     Delete removes the print and comes back to the list. An end to end run against a stand-in
     Moonraker and a stand-in camera made a 640 by 480 clip of every frame taken, in 0.6 s.
   **Tried on the Pi 4 on 2026-09-29**, the plugin started by hand with `--timelapse-dir`, gain
   high, rotated to portrait:
   - A 75 layer Voron cube: the panel read "layer 27 of 75, 28 frames so far" mid-print, and the
     clip came out 480 by 640, 77 frames, 3.2 s, 649 KB. 77 is one per layer, one for the finished
     part, and one for layer 0: Klipper sets `current_layer` to 0 when `SET_PRINT_STATS_INFO
     TOTAL_LAYER` arrives (`print_stats.py`), which is the first line of the start G-code, so the
     first frame is the bed before it has heated.
   - Its "fixed once started" range was 14.5 to 54.2 C, taken from the layer 2 frame, and the bed
     then read hotter than that for the rest of the print and drew in the end colour: a case for
     the comparison in step 4.
   - A print cancelled right at the start: two frames, layer 0 and the end, and a range of 18.3 to
     22.3 C, because with no frame at layer 2 or later the range fell back to the first frame, the
     cold bed, and the second frame drew almost entirely in the end colour. Fixed the same day,
     before release: with no frame at layer 2 or later the range is the whole print's, with a test
     that a layer 0 frame at 30 C and a last one at 150 C both fall inside it. Layer 0 stays: it
     shows the bed heating up, and no longer sets the range. Confirmed on the Pi 4 the same day by
     moving that print's clip aside and restarting the plugin, which made it again from the kept
     temperatures: 18.3 to 43.3 C, the cold bed and the hot nozzle both readable.
   - Cost during the print, `~/measure.sh` with the tile closed: 4.5% of one core, against 4.1% idle
     in the validation, so the polling and the frame copies are within the noise. 35.3% with the tile
     open, as before.
2. **The Timelapse page in Fluidd and Mainsail.** Upload through Moonraker into the `timelapse`
   root when there is one, named after the firmware's clip for the print (waited for up to ten
   minutes) or `<G-code name>_<start UTC>_thermal`; retention removes only the copies it uploaded.
   `moonraker-timelapse` documented as optional on mainline.
   **Built on 2026-09-29, into the unreleased 0.28.0, not yet run on hardware.** A new
   `publish.py`, and `moonraker.py` gains `has_root`, `file_names`, `free_space` (from the
   directory listing's `disk_usage`), a multipart `upload` and `delete_file`. After a print, the
   clip thread asks whether Moonraker has a `timelapse` root; if it does, and the folder already
   holds clips the firmware made (a name ending in fourteen digits, so `moonraker-timelapse`'s
   `_YYYYmmdd_HHMM` names never count), it waits up to ten minutes, polling every 15 s, for the
   one whose stamp is within 2 s of the recording's start, before encoding. The copy is named
   `<that base>_thermal.mp4` with `_thermal.jpg`, or `<G-code name>_<start UTC>_thermal` without
   one, and what was copied is kept in `meta.json`, so deleting a print, by hand or by `Retention`
   (which now takes the removal as a hook), deletes those copies through Moonraker first. Nothing
   is copied when the folder's disk would fall below the 200 MB floor, and the settings page says
   "Also on the Timelapse page" or why not. On the Pi 4, `moonraker-timelapse` is loaded with
   `[timelapse]` and `enabled: False` in `moonraker.conf` and its `timelapse.cfg` included, because
   it sends Klipper `_SET_TIMELAPSE_SETUP` whenever Klipper is ready and that is an unknown
   command without the macros. Tested with a stand-in folder for the naming, the waiting (found,
   never found, stopped), the floor, a refusal and the removal; the client against a stand-in
   Moonraker for each call; and end to end with a real encode uploaded to a stand-in Moonraker,
   byte for byte the clip.
   **Tried on the Pi 4 on 2026-09-29, with `moonraker-timelapse` loaded as above:** the clip,
   77 frames made in 2.2 s, appeared on Mainsail's Timelapse page with its thumbnail as
   `<G-code name>_<start UTC>_thermal.mp4`, and deleting the print from the settings page took
   it off Mainsail's page too. The same evening the rest of step 1's list: with the camera
   unplugged mid-print the capture reconnected 48 s later and the missed layers got the "Camera
   disconnected" picture, but its title is wider than a portrait 480 by 640 frame and was cut off
   at both sides; the plugin stopped with Ctrl-C mid-print, the print cancelled and the plugin
   started again made the clip of what it had in 1.1 s; and Keep set to 2 left two prints.
   **Changed after that run, the same day:** a missed layer now repeats the last picture with a
   band across its top naming the layer, in the viewer's colours, its text shrunk until it fits
   the frame's width (`with_banner`, `fitted_pixel_height`), and a dark frame with the band when
   nothing had been taken yet. "Forget the saved key" became a button, sent as the command
   `forget-moonraker-key` like calibrating, greyed out while no key is saved; the browser harness
   checks it greys out, comes back with a key, forgets it and greys out again without a reload.

   **Tried on the U1 on 2026-09-29, installed through Bespok3d from a local build:** the log said
   `timelapse kept in /userdata/bespok3d/var/thermal-master-timelapse`, so the manifest's argument
   reached the service; `/usr/bin/ffmpeg` and `/usr/bin/nice` are there. A 100 layer Voron cube in
   Snapmaker Orca with the printer's own timelapse on: the panel counted "layer 51 of 100, 52 frames
   so far"; the plugin cost 5.9% of one core during the print with the tile closed
   (`measure-cpu-on-printer.sh`), against 4.6% idle; after the print the panel said it was waiting
   for the printer's own clip, then making ours; the clip, 102 frames at 480 by 640, took 6.1 s,
   and was on Fluidd's Timelapse page as `Voron_Design_Cube_v7_PLA_12m4s_20260929190706_thermal.mp4`
   beside the firmware's `..._20260929190706.mp4`. The recording's own start was 19:07:05 and the
   firmware's stamp 19:07:06, so the two second tolerance was needed. Deleting the print took our
   copies off Fluidd's page and left the firmware's. Snapmaker's phone app does not list the
   thermal clip; not looked into. Found: the settings page's list stayed at "Recording now, 84
   frames" after the clip was made, until the page was reloaded, and offered Delete for the print
   being recorded, which the service refuses anyway.

   **Changed after the U1 run, the same day:** the list on the settings page offers no Delete for
   the print being recorded; it is fetched again as HTML from `/thermal/timelapses.html`, drawn by
   the same `timelapse_list` the page is drawn with, whenever the timelapse's status line changes,
   and swapped in unless a clip in it is playing (the browser harness checks a clip made while the
   page is open appears without a reload); and the clip holds its last frame for 2 s, through
   ffmpeg's `tpad=stop_mode=clone`, so the recording and the frame count stay one per layer.

   **Snapmaker's phone app: tried on the U1 on 2026-09-30, and closed.** Its "Time lapse camera"
   list shows the firmware's clips by their name without the start stamp, and not the thermal one.
   The firmware writes three files per clip, `<base>.mp4`, `<base>.jpg` and `<base>_cover.jpg`,
   the `.mp4` read-only to Moonraker. Four tries, each uploaded or moved through Moonraker and
   undone afterwards: a `_cover.jpg` beside our clip; a copy named as the firmware names its own,
   `<name>_thermal_<stamp>` with a `.jpg` and a `_cover.jpg`; our video moved in under the
   firmware clip's own name, the original set aside and put back; and that swap again with the
   app's cache cleared. None showed in the app, and with the swap Fluidd played our video while the
   app still played the original. So the app lists and plays the firmware's own records, or copies
   of them in Snapmaker's cloud, not the folder, and no name the plugin chooses reaches it. Not
   worth pursuing: writing into the firmware's records is not something a camera plugin should do.

   **The colour bar, decided on 2026-09-30, released in 0.28.6.** Proposed on 2026-09-29: A, the bar spans
   the range where the colours change, with the triangles saying the scene goes past an end; B, a
   broken bar; and, from a friend of the maintainer's, a log mapping so the whole scene keeps
   colours with most of them at the cool end. Rendered first from three captured frames (a nozzle
   at 89.6 C, one at 187.3 C, and a scene with nothing hot in it, 17.7 to 40.4 C), then built as seven selectable scales on a test branch (0.28.1 to 0.28.4,
   in `CHANGELOG_DEV.md`) and recorded live and as clips of one 252 layer print on the U1:
   - The share of the palette the middle 96% of the scene got, on the hot frame: 10% for a straight
     line over the scene, 31% for a log with a 10 C softness, 39% with 3 C, 85% for a knee at 85%.
   - Live: today's ruler over the scene moved too much, re-labelled every frame by a nozzle reading
     between 86 and 132 C; A held still; linear was too dark to keep; the logs and the 85% knee
     looked good, and a 75% knee started to look as dark as linear.
   - Clips, from start: the bed warmed past the layer 2 range, and today's picture went flat white
     at the bottom by the end; the knee kept it, the strong log next.
   - Chosen: four scales, `stretch` (with A's ruler as the only ruler, which undoes 0.14.0's), `knee`
     at 85% with a 5 C softness, `log-mild` (10 C) and `log-strong` (3 C), all working with a held
     range too: a log keeps to the held temperatures, a knee reaches past them to the hottest.
   - Cost on the U1, a one-off bench of 300 P1 frames through each scale, against a
     14.1 to 14.5 ms frame: worked out per pixel, the logs added 1.8 to 2.8 ms and the knee 1.3 to
     2.9 ms, and htop read 52% of a core against 42% for the stretch; a table of the curve looked up
     with 32 bit passes over the frame saved almost nothing there, and made a straight line 0.5 ms
     dearer. In 0.28.5 the knee is the stretch below its bend and a log only on the pixels above it,
     and the logs look their table up with 16 bit passes. Measured on the U1 on 2026-09-30, against
     13.6 ms for the stretch: the knee 1.2 ms more, the gentle log 1.2 ms, the strong log 1.5 ms.
     About half of each is the colour map and half the ruler, which works out its ends and tick in
     degrees and draws the tick's label every frame. For later: redraw the ruler only when its
     numbers change, which should take off about another 0.5 ms.
   - Tried on the U1 the same day, 0.28.5: the four scales live, followed and held at 20 to 60 C,
     each with the ruler and tick it should have; a clip with the clips' readout all unticked came
     out plain while the tile kept its readout; the copy button matched the Readout panel; the
     scale in the corner and in the name, `..._thermal_knee.mp4` on Fluidd's Timelapse page; the
     timelapse held at its own temperatures with the knee kept the bed's colour steady; a saved
     test build scale came back as the stretch; the (i)s worked on a computer. On an Android phone
     a second tap left the explanation open, because a phone keeps the last thing tapped "hovered",
     and the Timelapse panel ran off the right of the screen, with the (i)s wrapping under their
     selects: a fieldset and a select are as wide as their widest content unless told otherwise.
     Fixed in 0.28.6, with the hover only for pointers that hover, and checked in the browser
     harness at 360 pixels wide with touch.

   **For later: a U1 print's own timelapse, asked for rather than waited on (0.28.4).** On the U1
   `timelapse.is_active` and `print_task_config.time_lapse_camera` were both true for the whole of a
   print started with the timelapse ticked and false for one without, and both false once the print
   had ended; `print_task_config.reprint_info.time_lapse_camera` keeps the last print's value after it
   ends, confirmed on the screen or not (true after a ticked print, false after an unticked one). The
   plugin reads the first two with the status it already asks for during a print, and only waits for
   the firmware's clip when one of them was true. Tried on the U1 on 2026-09-30: an unticked print's
   clip appeared as soon as the print ended, and a ticked one waited for the firmware's.

3. **The automatic gain switch.** A setting, on by default; a "for this print" gain in
   `DeviceController` that wins over the stored one without changing it; at 145 C in high
   sensitivity, wide range, no frame for 5 s, and the user's gain back at the end.
   **Built on 2026-09-30 as 0.29.0 and tried on the Pi 4 and the U1; switching at 195 C since
   0.29.1, from the measurements below.** Only while the timelapse records a print; the threshold
   a setting (`timelapse_auto_gain_celsius`, 195 C by default, 30 to 500), since only one P1 has
   been measured and a P3 or a later camera may differ. The
   frame tap, which already sees every frame the camera sends, compares each frame's hottest count
   with the threshold, converted once with the emissivity so it means what the readout shows, and
   only while armed and in high sensitivity. The first frame past it asks `DeviceController` for
   the override, which the capture thread sends between two frames as it sends the gain, again
   after a reconnect, and the chosen gain again once cleared. The tap takes no frame for 5 s after
   it sees the gain change, and a layer's wait for a frame is pushed on by that window, so a switch
   is never taken for a missing camera. The switch is noted on the recording with its layer, the
   list and the Camera panel say so, a mid-print restart sets it again from the note, and every
   way a print ends clears it. No band in the clip: the note in the list is enough.

   Tried on the Pi 4 with a P1. The switch showed in the live view as it happened, at layer 0, 35 s
   after the print started, at 145 C. The clip made again with the readout drawn in showed the
   nozzle at 150 to 168 C, above high sensitivity's rated 150 C, the room at 9 to 12 C, and a knee
   ruler from 13.8 to 168.4 C bending at 54.2 C, with no frozen or blank layer. A Klipper shutdown
   in the middle of a second print ended its recording as Interrupted, "switched to wide range at
   layer 1", and the Camera panel's line lost the wide range sentence, so the override was cleared.
   With the box unticked, a third print stayed in high sensitivity from start to end, with no
   override and 12 clean layers, and one layer read 160.8 C: high sensitivity reads past its
   rated 150 C.

   Tried on the U1 on 2026-10-02 with the box ticked, at 145 C. The print switched 230.7 s after
   it started, at layer 0, during the routine before the first layer; the temperatures kept show
   the first record in high sensitivity and the other 101 in wide range, the next one 9.8 s after
   the switch reading 169.0 C with the nozzle at the right edge of the picture, under the ruler,
   which hid its marker. Through the print the hottest pixel read 104 to 131 C, most likely the
   silicone sock rather than the tip, which is smaller than a pixel and bare metal. The clip was
   visibly grainier than in high sensitivity, and its colours, fixed at 13.5 to 38.5 C when the
   print started, made the bed, the part and the nozzle one flat colour from about layer 10.
   While its clip waited for the printer's own, the panel read "On. Waiting for a print to start.
   Waiting for the printer's own clip of this print before making ours.", two states in one
   sentence; since 0.29.1 the clip being made is a line of its own and names its print.

   Measured on the U1 the same day, to choose where it switches. A 60 x 60 x 0.6 mm patch of
   black PLA printed in the middle of the bed and left there, read with a box in the viewer, both
   gains 15 s or more after a switch; the room 24.4 to 24.5 C on a thermometer:

   | Bed reports | High, patch | Wide, patch | High, coldest pixel | Wide, coldest pixel |
   | --- | --- | --- | --- | --- |
   | 30 to 31 C | 29.7 C | 24.4 C | 21.5 C | 11.6 C |
   | 45 C | 44.0 C, bare bed | | | |
   | 60 C | 58.7 C | 60.1 C | 24.7 C | 15.5 C |

   And the nozzle, set by hand with a sock on, held close to the camera, the hottest pixel:

   | Nozzle set | High | Wide |
   | --- | --- | --- |
   | 240 C | 187.5 C | 189.5 C |
   | 260 C | 201.5 C | 203.5 C |
   | 280 C | 207.2 C | 215.5 C |
   | 300 C | 207.2 C | 228.5 C |

   High sensitivity reads about 1 C under the bed throughout, which is what a surface on a heated
   bed does, and its coldest pixel matched the thermometer within 0.3 C. Wide range's error moves
   with temperature, about right at 60 C and 5 to 10 C low near the room, so it is not an offset
   to subtract; the driver converts counts the same way in both gains, since the camera sends
   them in the same unit, so the error is the camera's. High sensitivity stops at 207.2 C at
   emissivity 0.95, a raw count of 30410 or 202.0 C before the correction, and agrees with wide
   range within about 2 C below that. So high sensitivity is the better gain for everything but a
   nozzle past about 200 C, and 0.29.1 switches at 195 C: past every PLA reading on the U1, 169 C
   in a print and 194 C held against the camera, and before a reading on its way up is stuck at
   the cap. Wide range only earns its noise and its cool readings for high temperature
   materials. The hottest marker at the right edge was drawn, on top of the ruler, as a thin red
   cross on the ruler's orange; 0.30.0 gives the cross the labels' dark shadow.

   Found on the Pi and fixed in 0.29.1: the note under the Timelapse panel was only drawn with the
   page, so it went on saying the camera would switch after the box was unticked until a reload;
   it now comes back with every answer. And every Apply, and every other button, posted the whole
   form, so an Apply under Image also applied a half finished change under Timelapse. Each Apply
   now sends its own panel as JSON, which already changes only what it names, the other buttons
   send only their command, Enter applies the panel its field is in, and an Apply is greyed out,
   with "Not applied yet" beside it, until its panel has something to apply. An answer updates the
   panel just applied and anything a button changed, and leaves a change being made in another
   panel where it is. Without JavaScript the page works as before.
4. **The colour range comparison.** One real print rendered every way from its kept temperatures,
   and a default chosen. Done for the colour scale, above, by making one print's clip again with
   each; the range mode stays "fixed once the print has started" by default.

   **Decided on 2026-10-02, for 0.30.0: clips get a colour scale of their own, the knee by
   default.** The U1 print of that day, rendered from its kept temperatures with the plugin's own
   clip code, settled it, and showed the question had been put wrongly. The range taken from the
   second layer's frame was 13.5 to 38.5 C, the bed at its top, and the part and the nozzle never
   in it: only 20 to 31 of the 19,200 pixels read above 45 C in layers 30 to 100, the nozzle tip and
   the fresh top of the part, and the 98th percentile that sets a range leaves them out by design.
   So "the whole print" (12.8 to 39.5 C) looked the same as "fixed once started", "hold 20 to 120
   C" and a range up to the print's hottest, 169 C, were nearly black with only the nozzle showing,
   and the knee over the same range was the only one that kept the bed orange and the nozzle and
   the fresh layer apart from it. The rest of the part reads at the bed's temperature from where
   the U1's camera sits, which no range can change. The live picture keeps the stretch;
   `timelapse_colour_scale` is `live` or one of the four, `knee` by default, and a settings file
   from before it gets the knee too. Clips are also enlarged smoothly by default
   (`timelapse_upscale_filter`): they were always drawn in squares, and a clip is made once, at
   the lowest priority, so the live picture's reason to offer sharp hardly applies to it.

## 8. Alternatives considered and rejected

**A v4l2loopback virtual camera.** Upstream ships a UVC driver that presents the camera as
`/dev/video10`, which the existing hardware-accelerated camera plugin could consume. Revision 1 rejected
this as impossible on stock firmware; that reasoning was wrong, since the platform supports kernel
modules directly (`bake` class `docker-ko`, `install.kmodule` with `device_nodes` and `autoload`). It
stays rejected on cost: building v4l2loopback against the U1 kernel is a large lift, and it would not
remove any of this plugin's work, since a libusb capture process still has to feed the loopback device.
The only thing gained is reuse of an MPP pipeline this camera does not need at 160x120.

**Server-side overlays only.** Simpler, works inside the native webcam widget with no iframe, but state
is global across viewers and nothing is interactive. Kept as the fallback if the iframe tile proves
impractical.

**Two separate packages for P1 and P3.** Cleaner per-device identity, but doubles manifests, docs, CI and
release burden for one codebase.

**Streaming raw frames as the only transport.** Perfect client-side flexibility, but roughly 1 MB/s for
the P1 and 2.4 MB/s for the P3, and it abandons the webcam widget that already works.

**Repairing the hand-rolled build scripts.** Revision 1's plan. Rejected once `b3-builder` was understood:
the scripts' entire job is the builder's job, and the class of bug in F-33 cannot occur if nobody hand-
assembles a package.

## 9. Where this stands

**2026-09-28, at 0.27.0.** Phases 0 through 8 are done and every one of them has been confirmed on
the maintainer's printer with a P1 attached. The plugin is public at `Bespok3d/B3_ThermalMaster`,
signed with the organisation's key, listed in the Bespok3d app as a release candidate, and in daily
use: it installs itself as a camera, renders in Fluidd and Mainsail, carries
an interactive viewer, holds a display range, measures spots and regions, records clips, switches
itself off, and reports what it costs.

**Validated on a second printer on 2026-09-29, without Bespok3d.** An Ender 2 Pro Max driven by a
Raspberry Pi 4 Model B (four Cortex-A72 cores, Debian 12, Python 3.11.2, aarch64), which the
Bespok3d adapter does not support yet, so the plugin was started by hand: `plugin/files` and
`requirements.txt` copied over, a venv built from the very wheels the U1 package bakes, and the
entry script run under `sudo`, listening on all addresses on port 8082, since no udev rule
gave the user the camera. Nothing on the printer was configured. It printed
`camera connected: P1, streaming` three seconds after starting; the viewer and the settings page
worked from the port directly, because every URL they emit is relative; two recordings of the
hotend heating from room temperature to 196 C are clean, with no freeze and no watchdog line.
Unplugged, the viewer said "no frames from the camera" and the log showed the I/O
error and retries at 3, 6 and 12 s; plugged back, it reconnected at the next retry and the picture
returned without a reload.

What it costs there was measured the same day with the U1's protocol, each state held alone for
30 s and read from `/proc/<pid>/stat`, "nothing" a minute after the last page closed:

| State | U1, Cortex-A53 | Pi 4, Cortex-A72 |
| --- | --- | --- |
| Camera switched off | 0.0% | 0.1% |
| Nothing watching | 4.6% | 4.1% |
| Dashboard tile | 41.9%, Fluidd | 37.5%, Mainsail with `target_fps` 25 |
| Viewer, pointer moving | 45.7% | 36.4% |

A first look with `top` and the viewer open, the pointer not kept moving, read 28.1 to 33.3%, and
the settings page's own line 28.5%. On the Pi the tile read a little above the viewer, the other
way round from the U1. A Mainsail iframe tile is the viewer's own page, and `target_fps` does not
apply to it: measured again with `target_fps` 15, the tile read 37.8%, the same within a point. So
the difference is within what one 30 s window varies by, or a second consumer Mainsail opened; not
looked into. So everything below the Bespok3d layer runs unchanged on a
second, different Klipper printer. It is still the maintainer running it, so it is not the stranger's install that
promotion to `stable` waits for.

**2026-09-30, at 0.28.6.** Phase 9's timelapse is built: a clip of every print, one frame per
layer, made on the printer after the print, kept under a count and a free space floor, listed on
the settings page and copied to the Timelapse page of Fluidd and Mainsail. With it come four colour
scales for the live picture and the clips (the stretch, a knee and two logs, chosen on the U1 from
seven), the ruler over the colours rather than the scene, clips with their own readout, remaking a
clip with the current colours, no ten minute wait for a U1 print's own clip when the printer says
it is not making one, the settings page's explanations behind an (i) beside each option, a page
that fits a phone, and the version at its foot. All of it was tried on the U1; the timelapse also
on the Pi 4. Built on the branch `feature/timelapse`, with the colour scales tried on
`test/color-bar` as 0.28.1 to 0.28.5 (in `CHANGELOG_DEV.md`). Released on 2026-09-30, on the `rc`
channel, and reinstalled from the store the same day: "Package was unsigned at install" still
shows under a stage build of the Bespok3d app and not under the stable one, a question for its
maintainer rather than for this plugin.

What is left, in the order it is worth doing:

- **Closed: the adapter drop test** from F-72: replug the camera in the hub port farthest from the ethernet
  adapter, holding the hub still, and see whether the adapter drops again. It matters more now that
  the printer is off wifi, since an adapter drop would take it off the network entirely.
  **Run on 2026-09-28: it dropped again.** The camera, on `3-1.3.4`, was unplugged at 3460.2 s
  since boot and plugged back in; as it re-enumerated at 3465.7 s the adapter, `3-1.3.3`,
  disconnected (3465.3 s), came back as a new `eth0` (3468.5 s) and had link at 3471.9 s, so the
  printer was off the network for about 6.6 s. The camera was streaming again at 3472.3 s. The
  hub's supply is 5 V 1 A against the 2.4 A it accepts, so the cause is taken to be electrical, as
  F-72 suspected; retest with a 2.4 A supply. `carrier_changes` read 2 before and after, which
  proves nothing here: the interface is unregistered and registered anew, so its counters start
  again. The camera also logs `error -71` while enumerating, at boot and on replug, which fits a
  marginal supply as well.
  **Retested the same day with a 5 V 2.4 A supply: it still drops, for less time.** The camera was
  unplugged at 14:05:10.4 UTC and plugged back in; the adapter's `eth0` was unregistered at 219.2 s
  since boot (14:05:47.8), registered again at 220.6 s and had link at 223.9 s (14:05:52.6), 4.7 s
  from gone to link against 6.6 s on the 1 A supply. The camera was streaming at 14:05:58.5. The
  maintainer saw no drop from outside, so a drop this short is easy to miss without the kernel log.
  A larger supply is therefore not the whole answer. **Closed on 2026-09-28, left as it is.** The
  U1 has a single USB port, which is why the hub is there at all, so the adapter cannot have a
  port of its own. Replugging the camera is rare, the drop costs about 15 s on the network, and the
  viewer now says what happened and recovers by itself. A hub that switches its ports individually
  might avoid it, and is not worth buying for this.
- **Promotion to `stable`**, once somebody other than the maintainer has run it: a release with
  only the channel changed.
- **Phase 9's remainder**, in section 7:
  - Step 3, the automatic gain switch: built and tried on both printers, and switching at 195 C
    since 0.29.1. A print with a nozzle past 200 C, to see it switch at the new default.
  - 0.30.0, built and not yet on hardware: clips with their own colour scale (the knee) and
    smooth enlarging, and the markers' dark edge.
  - Redrawing a curved scale's ruler only when its numbers change, about 0.5 ms a frame on the U1.
- **Reports owed elsewhere**, written up in section 10: the driver's P1 shutter bug went as a
  comment on upstream issue #17, the filaman card goes privately to its owner, and two reports went
  to Snapmaker on 2026-09-28, one on the U1's wifi and one on Snapmaker Orca. All were drafted
  outside the repo, since a message addressed to another project is spent once it is sent; section
  10 is the version that stays. The installer dropping `userEditable` values was retested on
  2026-09-18, did not reproduce, and is closed.
- **The P3 hardware trial**, whenever one turns up. Everything for it is implemented and none of it
  has met the device.
- **Parked deliberately**: auto-mode range decimation and a dead band, and F-55's fps decay. Both
  wait on somebody actually wanting them.

What follows in this section is the running record, oldest first. It is kept as written rather than
tidied, because what a defect looked like before it was understood is the part that is hard to
reconstruct afterwards.

### The state at 2026-09-13, kept as written

Phase 0 is complete and the gate is green (6/6), but **nothing is committed**. The whole of the work
below is sitting in the working tree on branch `claude`.

Added: `scripts/check.sh`, `CONTRIBUTING.md`, `VENDORING.md`, `plugin/requirements.txt`,
`plugin/tests/conftest.py`, `plugin/tests/test_thermal_colormap.py`, and the `lib_bespok3d` submodule
(absolute URL, because this repo is outside the Bespok3d org where a relative one resolves wrong).

Changed: `.gitignore` now tracks `plugin/files/vendor/p3_camera.py` and its licence while ignoring the
rest and the wheels tree; `scripts/fetch-vendor.sh` is now a vendor verification tool rather than a
build step; `README.md`'s build and vendoring sections match reality; `plugin/files/bin/thermal-p1-stream.py`
has formatting-only lint fixes.

shellcheck now passes on all three scripts, confirmed on the maintainer's machine. The plugin has
still never run against the camera.

Found by that first real run: F-50, the vendor directory shadowing installed packages, and F-51, the
gate venv colliding between the two machines. Both fixed, and the gate is green on both machines
(9/9 on the maintainer's, where shellcheck runs). The vendor directory has since been cleared to the
pinned driver alone (F-31).

Next: Phase 1, the manifest rewrite. It is the change that makes the plugin installable and puts the
camera in Fluidd without the user touching Settings. Everything it needs is settled except the
capability string, where `klipper-generic` is the recommendation (section 6.2).

**F-59. Every settings change bounced the user out to the Fluidd dashboard.** Fixed in 0.8.2, found
by the maintainer asking whether it was meant to happen. It was not.

`apply_settings` answered its POST with `Location: /`. The plugin serves the control page at `/` and
nginx publishes it at `/thermal/` with the prefix stripped on the way in, so the plugin never learns
what the browser called it, and `proxy_redirect` does not rewrite a bare path because it does not
start with the `proxy_pass` value. The browser therefore resolved `/` against the printer's origin
and went to the printer's home page. Directly on port 8082 it worked perfectly, which is why it
survived every test written for it.

The fix is a relative reference, `./#controls`, which resolves against whatever URL the browser
actually asked for: `/thermal/` behind nginx, `/` on a direct connection. The fragment also puts the
page back at the controls instead of at the top. The test now asserts the header is not absolute and
says why, rather than asserting the string it happens to be.

Worth generalising: this plugin is mounted under a prefix it cannot see, so it must never emit an
absolute path of its own. The one remaining place it does is the form's `action`, which is hardcoded
to `/thermal/settings` and so only works behind nginx. That is the mirror image of the same bug and
should become relative too.

A second thing came out of the same question. The redirect reloads the page, which tears down the
MJPEG stream and opens a new one on every change, so the picture blinks out exactly when the
auto-ranging is already re-settling. The page now posts in the background where the browser allows
it and updates only the status line. It is progressive enhancement and nothing depends on it: the
form still works with JavaScript off, and any failure falls back to submitting normally. The wording
of the status line comes from `describe_device` either way, so the two paths cannot drift.

**F-60. The calibrate button silently stopped working, and every test still passed.** Fixed in
0.8.3. Two defects, both introduced by the progressive enhancement in 0.8.2, and both invisible from
Python.

The button posted under a field named `action`. A named form control is exposed as a property of its
own form element, so `<button name="action">` makes `form.action` return that button instead of the
URL. The script read `form.action`, fetched `[object HTMLButtonElement]`, got a 404, and fell back.

The fallback was `form.submit()`, and a programmatic submit does not include the submitter. So the
fallback posted every field except the one that mattered. The page reloaded and honestly reported
that no calibration had been asked for.

Fixed three ways, because any one of them alone leaves the trap set: the field is named `command`,
the script reads `form.getAttribute("action")`, and the pressed button is written into a hidden
input before the handler does anything, so every path out of that handler carries it.

The interesting part is the gap it exposed. This plugin's page had no test that a browser ever ran,
and two bugs walked straight through 180 passing tests. `scripts/check-control-page.py` now serves
the real page against the stand-in camera and drives it in headless Chromium. Its first run found the
shadowing fix working and immediately failed on something else: the deferred half of F-59, the
hardcoded `action="/thermal/settings"`, which meant the page only ever worked behind nginx and put a
404 permanently under the fallback path. Every URL the page emits is now relative, which closes F-59
completely.

One clarification for anyone reading this later and trying to check it. "Only worked behind nginx"
is a statement about the URLs the page emitted, not an invitation to open port 8082 in a browser.
The service binds to 127.0.0.1 by design, so the port is unreachable from anywhere but the printer
itself and refuses the connection: that is the intended posture and not a symptom. The direct mount
point is verified by `scripts/check-in-browser.py`, which serves the page on loopback and drives
it there, and on the printer by curling 127.0.0.1:8082 from its own shell.

The browser check is not in the gate; a browser download is too much to ask of a printer plugin
contributor. What went into the suite instead are the two rules it taught, as assertions on the
rendered HTML: no form control may share a name with a property of `HTMLFormElement`, and the page
may not emit an absolute path. Those are cheap, they run everywhere, and either one would have
caught its bug.

**F-72. The picture froze for three seconds every thirty-one, and it was not the plugin.** Found on
hardware on 2026-09-24, after the printer moved to a USB ethernet adapter on a powered hub shared
with the camera, with its wifi blocked at the router.

What it looked like: the viewer's picture held still for about three seconds and then carried on,
at regular intervals, and a clip recorded at the time captured the same freezes. A ping from the
laptop watching it lost packets at the same moments.

What it was: blocking the printer at the router did not turn its wifi off. `wlan0` joined the
access point, was refused, disconnected and tried again, on a fixed cycle: a drop every 30.97 s,
back up 3.1 s later. Measured with a watcher on the printer printing the UTC time of every wifi
event while a two minute clip was recorded: eight drops fell inside the clip and there were eight
freezes, each starting within a second of a drop and ending at the reconnect, and none of two
seconds or more anywhere else. The laptop's ping lost 11.6%, where three seconds in every
thirty-one predicts about 10%. With the network forgotten on the printer itself, the same test gave
no wifi events, no loss in 150 pings, and no freeze longer than 0.58 s.

Where it bit was not the printer's wired side, which pinged its router twice for two minutes across
several cycles with no loss at all. It was delivery to a laptop on wifi, on the same access point
the printer kept hammering, with TCP's retransmission backoff stretching a sub-second disruption
into a stall of a few seconds. That mechanism is inferred; the correlation and the fix are measured.
It is also the likely cause of the printer's intermittent trouble reaching its cloud service, which
is expected to stop and not yet confirmed.

One theory was wrong on the way. The first clip's first freeze, 3.15 s, matched the plugin's 3.0 s
reconnect delay closely enough to look like a camera reconnect. It was the wifi cycle, and the
numbers coincided. An earlier twenty minute ping from the same laptop lost only 1.7%, less than the
loop predicts; its conditions were not recorded and nothing here depends on it.

What it taught about diagnosing this plugin:

- **A clip that shows a freeze does not implicate the camera.** The viewer records in the browser,
  from the picture it is showing, so a frame that never reached the laptop and a frame the camera
  never produced look identical in the recording.
- **An empty `dmesg` search is not evidence that nothing happened.** The kernel's ring buffer is
  small and a chatty driver empties it: at eight lines a cycle the wifi driver left only the last
  38 minutes, so boot messages and anything from the time of the first clip were already gone.
  The fallback is `/userdata/logs/syslog`, rotated to `syslog.0` and `syslog.1`: it keeps kernel
  lines with wall-clock times, so search it before concluding that nothing happened. (An earlier
  version of this bullet said the printer had no syslog; that was wrong.)
- **The clocks differ.** A saved clip is named with the laptop's local time at the moment it is
  saved, not when it started, and the printer keeps UTC.
- **The plugin's log carries no timestamps**, so its capture errors cannot be tied to a moment.
  That is a real diagnosability gap, recorded here rather than fixed. Since fixed in 0.25.2, and
  confirmed on hardware as recorded below: every line carries UTC to the millisecond and the seconds since
  boot, the clock `dmesg` counts in, and says `bespok3d/thermal-master`; one function writes them
  all and a test fails if anything prints around it; and a line when the camera is streaming again
  gives each outage an end as well as a start.

**The log fix, confirmed on hardware with 0.25.2 the same evening.** From the first stamped line
to six minutes later, UTC advanced 363.66 s and the seconds since boot 363.73 s, so the second
clock really is the one `dmesg` keeps. The server came up and the camera was streaming 3.7 s later,
the first time that interval had ever been visible. Then the camera was pulled from the hub and
plugged back in, and the log and the kernel lined up:

| since boot | from | what happened |
| --- | --- | --- |
| 34911.01 s | plugin | `capture error: [Errno 5] Input/Output Error` |
| 34912.33 s | kernel | the camera, `3-1.3.4`, disconnects |
| 34914.01 s | plugin | the retry, exactly the 3.0 s reconnect delay later: no camera yet |
| 34915.14 s | kernel | **the ethernet adapter, `3-1.3.3`, disconnects** |
| 34915.65 s | kernel | the camera enumerates again |
| 34917.81 s | kernel | the ethernet adapter enumerates again |
| 34923.17 s | plugin | `camera connected: P1, streaming` |

The read fails 1.3 s before the kernel logs the disconnect, which is the right order: the transfer
breaks when the contacts do, the log line waits for the hub to report the port change. The outage
the log records, 12.2 s, is mostly the reconnect backoff doing its job, since the camera was
physically absent for about 3.3 s and the next attempt, after the delay doubled to 6 s, came about
4.4 s after it was back; setup then took 3.2 s. That is the trade the backoff was designed to make,
and it is visible now.

What the stamps also caught, and nothing could have caught before them: **the ethernet adapter on
the same hub dropped off the bus for 2.7 s at the moment the camera was plugged back in**, although
only the camera was touched. Either the camera's inrush current sagged the hub's 5 V supply, a
1 A adapter shared with a gigabit ethernet adapter, or plugging it in jostled the neighbouring
connector. Not yet told apart. The test is to replug the camera in the hub port farthest from the
adapter, holding the hub still: if the adapter drops again the cause is electrical, and a larger
supply or moving the adapter off this hub is the remedy. It matters because any reconnect of the
camera would then briefly take the printer off the network.

Still open when this was written, and settled the same day: short holds of a third to half a second
remained in the clips whose scene was nearly still, and there were none in the one clip with a busy
scene. On a near static thermal picture the video encoder can round away the sensor's noise, so a
frame comparison cannot tell those from real stalls. The first attempt to time the stream itself
found a separate defect instead, F-73, and this entry briefly guessed the holds belonged to it. They
do not. Timed on its own, the stream delivered 25.0 fps with nothing longer than 143 ms between
frames in two minutes, so the holds are on the browser side, most likely the encoder.

**Off wifi at last, 2026-09-27, and why forgetting never held.** Unplugging the ethernet cable to
reach the printer's wifi settings showed it connected to the network it had been told to forget on
2026-09-24. The router's client log has its wifi leaving then and rejoining two minutes later, and connected
from then on, so every retest since, F-73's included, ran on a printer holding an address on both
links. None of the plugin's measurements there obviously depended on the wifi, but a retest
should say which links the printer had.

Traced from the printer's own state:

- **Forgetting leaves the network saved and enabled.** `wpa_cli list_networks` shows it before and
  after, and all three copies of the configuration keep it: `/etc/wpa_supplicant.conf`, its
  persistent copy under `/oem/overlay/upper/etc`, and the screen's own copy under
  `/oem/printer_data/gui`. The network screen showed the wifi switch off while the printer held a
  fresh wifi lease.
- **Something restarts the wifi client about 30 seconds after any disconnect**, rebuilding it from
  `/etc/wpa_supplicant.conf`: disabled by hand with `wpa_cli`, the network was back within
  30 seconds, with new `dhcpcd` and `wpa_supplicant` processes and the file rewritten. What
  triggers the restart was not found; neither `gui` nor `unisrv` calls `ifup`.
- **`disabled=1` in `/etc/wpa_supplicant.conf` holds.** Added there and in the screen's copy, with
  backups in `/userdata` on the printer, the next restart carried it into the runtime copy and the
  printer stayed off wifi. It survived a reboot and a power cycle on 2026-09-28; a firmware update may still replace
  the file.

While both links were up, two things went wrong that were not the plugin's. Traffic to the wifi
address arrived over wifi and its replies left by ethernet, the preferred route: since boot the
wifi interface had received 3.5 million packets and sent 26 thousand. And Snapmaker Orca uploads
print files over the local network to the address it learns from the printer, which was the wifi
one: on 2026-09-27 three of five uploads stalled partway, with Moonraker logging each request and
never the rest of the file. With the wifi off, the slicer's check of that address times out and it
goes through the cloud instead, as it did on 2026-09-24 while the router was blocking the wifi.
Both are in the report to Snapmaker, which this section's finding about forgetting has corrected:
it had offered forgetting the network as the workaround.

**F-73. Two open streams sent each other the same frame as fast as the network would take it.**
Found on hardware on 2026-09-24, while timing the stream to settle what F-72 had left open. Fixed in
0.25.1.

What it looked like: a script timing frames on the stream, with the viewer open at the same time,
counted 99,707 frames in two minutes, 830.9 a second, from a camera that produces 25. A clip the
viewer recorded meanwhile repeated 46% of its paints and held still for more than a quarter of a
second 80 times, against 13 to 17% and about a dozen in quiet clips made without it. Alone, the
same script measured 25.0 fps, a median gap of 40 ms, and nothing longer than 143 ms.

Why: `LatestFrame.wait_next` did a single wait on the frame store's condition and returned whatever
frame was current. That condition is notified by `publish`, and also by `note_interest`, which
every stream calls before every part. So one stream's interest woke another, which resent the
current frame, noted its own interest, and woke the first. The docstring on `wake`, twenty lines
above, already named the trap, that the condition is shared and a single wait takes another
request's interest for a frame, and `wake` loops to avoid it. `wait_next` did not. Any other
request that notes interest, a temperature poll from the viewer among them, also cost every open
stream one duplicate.

The fix: each stream remembers the publication it last sent, and `wait_next` waits until a newer
one exists, looping over wake-ups that are not a frame, as `wake` does. The resend of the current
frame once a timeout when nothing new arrives, which the existing stream test relies on, is kept
exactly. Two regression tests in `test_http_stream.py` open two raw streams and count the parts
that arrive: on the old code one noted interest produced 12,183 parts in half a second where none
were due, and one new frame produced 4,130 where one was. On the fix, 0 and 1.

What it cost in practice: in ordinary use, nothing measurable. On hardware, the settings page, the
Fluidd dashboard tile and Fluidd's full screen view each held one connection to the plugin, with
467 to 559 KB/s leaving the printer and the plugin at 41.3% to 42.9% of a core, in line with the
41.9% recorded for a visible tile. The duplicates are cheap for the plugin itself, because rendering
and encoding happen once per camera frame however many parts go out; the cost lands on nginx, the
network and the browser. That makes the plugin's own CPU figure the wrong instrument for this defect
and the bytes leaving the printer the right one, which is worth remembering for anything else of its
kind. The flood needs two streams at once, and the camera open in two tabs or on two devices is
enough for that.

Confirmed on hardware the same day, with 0.25.1 installed and a process started after the install.
With the Fluidd dashboard and the viewer open side by side: two connections and 891 KB/s leaving the
printer, about twice the single stream's 467. With the timing script alongside an open viewer, the
exact situation that had counted 830 fps: 25.0 fps, a median gap of 40 ms, nothing longer than
67 ms. The plugin used 45.5% of a core with both viewers open, against 41.3% to 42.9% with one, so
a second viewer costs it about three points. There is no measurement of two viewers before the fix
to set against that, so it says what a second viewer costs now, not what the fix saved. With four
viewers open: four connections, 1,766 KB/s, which is 441 KB/s each, and the plugin at 47.3%. The
bandwidth scales with the number of viewers to within about five per cent, as a stream without
duplicates should, and each viewer past the first costs the plugin one to three points, which is
about as fine as a thirty second sample resolves.

One thing noticed on the way and left alone: the comment above `VIEWER_RECORD_FPS` in `viewer.py`
says the camera "runs at about fifteen". It never did. Fifteen was the snapshot tile's poll rate,
Moonraker's default `target_fps`, as recorded beside F-55; the stream has run at the camera's 25
since the tile became an iframe of the viewer in 0.16.0.

**F-74. A network break freezes the picture for good, and only Fluidd ever brought it back.**
Found on 2026-09-27, while chasing a brief slicer disconnect at the start of a print. Fixed in
0.26.0 by Phase 7i, confirmed on hardware on 2026-09-28; the results close this entry.

What it looked like: a clip recorded in the viewer during the print's start froze 33.8 seconds in
and stayed frozen to its end, 66.7 seconds. Measured frame by frame, a live thermal stream is never
identical to the frame before it, whatever the scene is doing: sensor noise and the readouts see
to that. Before the freeze 85 to 97% of consecutive frames differed, 88% even in a stretch where
the bed was barely changing. After it, 0 of 291 did, apart from a single pixel at exactly the
clip's keyframes, which is the encoder. The readings sat at 47.7 and 44.7 while the bed heated, and
the picture stopped in the middle of the toolhead moving through it.

What it was not: the plugin or the printer. The plugin's log, timestamped since 0.25.2, says
nothing for the whole minute, so capture never faltered. The printer's own camera service kept
counting frames on schedule and ran its print-start checks in about 210 ms each; the kernel log
and syslog are empty for the window; Moonraker logged throughout. In the same seconds Moonraker's
websocket from the same browser went without a pong, was closed with 45 seconds elapsed, and the
browser reconnected about a minute after the last pong. The laptop's wifi stayed associated to the
same access point with a strong signal, no transmit failures, no roam and no sleep, and the router
logged nothing. The router's silence is weak evidence: it also logged nothing for the ethernet
adapter's seven second drop during the camera replug on 2026-09-24. What broke the path is not
known. The printer was on the network twice at the time, wired and wifi, because its wifi had
rejoined on its own; that belongs to F-72, which records how it was finally taken off.

A slicer disconnect in the same minute was unrelated: Snapmaker Orca drops its own cloud
connection about 47 seconds after its print dialog connects, reported to Snapmaker separately.

Reproduced the same day by turning the laptop's wifi off and on with the tile open in Fluidd:

| Outage | What the tile did |
|---|---|
| about 30 s | froze, and stayed frozen until the page was reloaded |
| about 30 s, again | came back, when Fluidd reconnected to Moonraker |
| longer | came back after a short while, the same way |

With the browser's network panel open on the tile's frame, the page's one `stream.mjpg` request
ended as a 200 after 11.62 s, having carried 4,935 kB, and no second request followed. The stream
does not survive a break in the path, and the viewer has nothing that notices it ended or asks
again: the `<img>` keeps showing the last part of a finished response. Every recovery seen came
from Fluidd rebuilding its panels when its own websocket reconnected, which does not happen when
Fluidd's connection happens to survive, and never happens for the viewer opened on its own.

nginx's `/thermal/` location has `proxy_buffering off` and `proxy_read_timeout 300s` and sets no
`send_timeout`, so the default of 60 seconds decides how long it holds a browser that stopped
reading. Its access log is off for the Fluidd site and its error log says nothing about the
stream. It also started afresh twice that day, which ends every stream just as surely; the cause
was not looked into.

What it taught about diagnosing this plugin:

- **A frozen clip can be proven frozen.** Compare consecutive frames: a thermal stream that is
  delivering is never pixel identical from one frame to the next, so a run of identical frames is
  a run with no data, whatever the scene.
- **A Moonraker websocket closed with about 45 seconds of pong elapsed usually means the laptop
  slept.** Of 57 such closes on the wired address over 2026-09-24 and 25, 48 began within 9 seconds
  of macOS going to sleep and one during a wifi roam. Seven more fall in the hours F-72's loop was
  running, and one is unexplained.

**Confirmed fixed on 2026-09-28**, with 0.26.0 on the printer and three clips recorded in the
viewer, each measured frame by frame for when the picture stopped and started and when the line
showed:

| Test | Result |
|---|---|
| Laptop wifi off 30 s, tile in Fluidd, recording started over "Stream off" | Froze at 12.5 s into the clip; "printer not answering since 14:27:08" 2.4 s later; picture back and line gone on the same frame at 58.3 s, 45.8 s after the freeze. No "Stream off" line at the clip's start. |
| Laptop wifi off more than 4 minutes, viewer on its own | Froze at about 3 s; the line at about 6 s; back, line gone on the same frame, at about 261.5 s. |
| Laptop wifi off about 90 s, tile in Fluidd | Came back. Fluidd rebuilt its panel, which also lost the recording, so there is no clip. |
| Camera unplugged and replugged, viewer on its own | Froze at the unplug (13:11:25.9 UTC); "printer not answering since 15:11:34" at 15:11:34.1; back at 15:11:50.2. See below. |
| Rec with the camera switched off | Greyed out. |
| Recording across an outage | The line is in the clip, every time. |

Neither wifi clip ever showed "reconnecting": each time the first answer from the printer and a
moving picture arrived together.

The unplug did not test what it set out to. Replugging the camera took the ethernet adapter off
the bus again (section 9, the adapter drop test): off at 13:11:31.0, link back at 13:11:37.7, the
camera streaming at 13:11:38.1, all in UTC, with the clip in local time, two hours ahead. The camera
line needs its count stalled for 4 to 6 s and the network went 5.1 s after the unplug, so the
printer stopped answering first and "no frames from the camera" never had its moment. That line
is covered by the browser check against a stand-in camera, and the hardware test waits for the
larger hub supply.

It did show two things to improve in the watchdog:

- **Recovery lagged the network by 12.5 s**, link back at 15:11:37.7 and picture at 15:11:50.2.
  That fits the back-off: with the laptop's own network up there is no `online` event, and the
  timings match an ask sent just as the link returned failing at its 4 s timeout, then the next
  one waiting 8 s. Asking an unreachable printer every 2 s costs the printer nothing, so the
  back-off buys nothing.
- **"Since" is when a question gave up, not when it was asked.** The line said 15:11:34, the moment
  the first unanswered ask timed out; the network went at about 15:11:31. The time an unanswered
  ask was sent is the better "since".

Both fixed in 0.26.1: the watchdog keeps its 2 s beat while the printer is silent, and dates an
unanswered question by when it was asked.

**Retested on 2026-09-28 with 0.26.1**, camera unplugged and replugged, viewer on its own, the hub
on a 2.4 A supply. Times are UTC; the clip reads two hours ahead.

| Time | What happened |
|---|---|
| 14:05:10.4 | camera unplugged; the plugin logs the I/O error, the picture freezes |
| 14:05:16.9 | "no frames from the camera since 14:05:12", 5.7 s after the freeze, and no restart |
| 14:05:47 | the viewer's question goes unanswered |
| 14:05:46 | the adapter's `eth0` is unregistered as the camera goes back in, and `dhcpcd` deletes the printer's address and its routes |
| 14:05:51 | `eth0` has link again; `dhcpcd` waits 1.2 s, then asks to rebind its lease |
| 14:05:51.7 | "printer not answering since 14:05:47", dated by when that question was asked |
| 14:05:56 | the second request is acknowledged; three ARP probes for the address follow |
| 14:05:58.5 | the plugin logs the camera streaming again |
| 14:06:01 | `dhcpcd` adds the address and its routes back: the printer is reachable again |
| 14:06:03.3 | the picture moves and the line clears |

Network times are from the printer's syslog, to the second, which puts the kernel's 219.2 s about
1.5 s earlier than the plugin log's own uptime stamps do; the picture's times are the clip's, from
the laptop's clock.

The camera line is confirmed on hardware, and so is the new "since". So is the recovery: the
picture was back 2.3 s after the printer had its address again, one beat of the watchdog and a
restart. Almost all of the outage was the printer's own network coming back: a 4.7 s adapter drop
became 15 s without an address, 10 s of it after the link had returned, spent rebinding a lease
whose first request went unanswered and then probing the address three times before using it.
Stopping the adapter drop would be the cure, and cannot be had on a printer with one USB port
(section 9); shortening `dhcpcd`'s return, with a static address or no ARP probing, would only trim
it, and would change the printer's own configuration.

The last of the round, the same afternoon: switching the camera off during a recording ended the
recording and saved it on its own. The 5.4 s clip moves until 5.0 s and ends on the camera's last
frame, with no "Stream off" placeholder in it.

**F-75. For the first minute after boot, a store nobody had asked anything of counted as watched.
Fixed before 0.27.0, found by CI.** The first run of `tests.yml` on GitHub's runner, 2026-09-28,
failed `test_asking_for_health_counts_as_watching` on its opening assertion: a fresh `LatestFrame`
said it was wanted. It keeps the time of the last request on the monotonic clock and started it at
zero, and that clock counts from boot. On a machine up for more than the idle minute, zero reads as
long ago; the runner had booted less than a minute before the test ran, so zero read as a moment
ago. The older test of the same thing, `test_a_store_nobody_has_asked_anything_of_is_not_wanted`,
passed on the same run only because it ran later, by which time the runner was past its minute.
Every machine the suite had run on before had been up for longer.

On the printer the cost was small and real: a plugin started within a minute of boot rendered
frames for nobody until the minute ran out. The last request now starts at minus infinity, which is
"never" whatever the clock says, and a regression test fakes the clock at five seconds after boot.
The time of the last published frame also starts at zero and is safe, because nothing reads it
before a frame exists.

What it taught: **a test that depends on how long the machine has been up passes everywhere it has
always run.** A fresh CI runner is the one place it is guaranteed to be young.

**F-76. The first release's page in the Bespok3d app showed no documentation and no changelog,
and the manifest had no publication dates. Two things, only one of them ours.** After 0.27.0
installed from the app, 2026-09-28, the plugin's page offered Overview, Doc, Config, Licence and
Install log, with the Doc tab reading "No bundled documentation for this plugin yet" and no
Changelog or Versions tab, where Bespok3d's own `rfid-creality` shows all three.

The package was not the cause: the `.b3` carries `doc/README.md` and `doc/CHANGELOG.md`, both in
the packed manifest's `files[]`, and the release's index entry has a `doc_url` and a `changelog_url`
that both answer 200. Compared field by field with `rfid-creality`'s entry, the one real difference
was the dates: ours had `published_at` and `updated_at` as empty strings, and the list's `updated`
was empty too. The builder's documentation says both are stamped at build time and are never
written by hand, in `doc/anatomy-of-the-manifest.md` and `doc/anatomy-of-a-b3-file.md`; the builder
at `9322144` stamped neither. unlucio confirmed on 2026-09-29 that they are manifest fields the
plugin writes, as `rfid-creality` does, so the builder behaves as intended and its documentation is
what is wrong. 0.27.1 writes both, with a test that they are present, dated and in order, and the
README's release steps say to move `updated_at` with each release.

The empty tabs were the app's cache. With 0.27.1 published the page still showed them empty, and
after the app was closed and opened again it showed the README under Doc, both changelog entries
under Changelog, and a Versions tab. So whether the dates played any part is not known: 0.27.0 may
have shown its documentation after a restart too. The page's "Published" date read 2026-09-29, the
day of the 0.27.1 release, against a `published_at` of 2026-09-28, so the app takes that date from
somewhere other than the manifest field. The page also carries "Package was unsigned at install",
because the plugin was first installed on that printer from a hand-built package; switching to the
signed 0.27.1 keeps that record, and a fresh install from the store should clear it.

What it taught: **after publishing, restart the app before believing what its plugin page says.**
A page that reads as broken may only be stale.

**F-77. The Bespok3d app's Doc tab cannot show a published plugin's images, and nothing in this
repository can make it.** 0.27.2 added four images to `doc/README.md`, referred to as
`images/hero.jpg` and so on. GitHub shows them; the app's Doc tab, on 2026-09-29, showed the new
README's text with a broken image and its alt text in place of each.

The package was not the cause: the `.b3` carries all four under `doc/images/`. Read in
Bespok3d-desktop's source, three things together rule out every fix on this side:

- The Doc tab fetches the README text from the release's `doc_url`, but resolves an image path only
  through `DOC_ASSETS` (`src/renderer/src/data/catalog/shape.ts`), a table built when the app is
  built, from the doc media of the plugins in the app's sibling `plugins/` tree. That is why
  `camera-hw-accel`'s `images/usb_cam.png` shows and ours does not. It never opens the `.b3`.
- The renderer's Content Security Policy (`src/renderer/index.html`) is `img-src 'self' data:`, so
  an absolute link, to `raw.githubusercontent.com` or to a release asset, is blocked as well.
- The Markdown renderer runs react-markdown's `defaultUrlTransform`, which removes `data:` links,
  so an image embedded in the README is dropped too. GitHub would not show one either.

The release carries only the README, the CHANGELOG, the `.b3`, `index.json` and its signature, so
`images/hero.jpg` beside the README asset answers 404 in any case. The README keeps its relative
paths, which are right for GitHub and for any future fix that reads them from the package. The
report went to unlucio on 2026-09-29 (10.6).

What it taught: **a published plugin's Doc tab shows text only, for now.** Check it again after each
app release that mentions documentation.

## 10. Reports owed elsewhere

Six defects found while building this plugin, none of which belongs to this repository. 10.1 went
as a comment on an issue that already exists, and 10.3 goes as a private message. 10.2 was retested
on 2026-09-18 and is not reproducible, so it is closed rather than sent, and kept here with the
retest that closed it. 10.4 and 10.5 went to Snapmaker on 2026-09-28, after the plugin had been a
suspect in each. 10.6 went to unlucio on 2026-09-29.

They are written up here because the finding is the expensive part and it is the part that
evaporates: each one cost hours to diagnose, the workaround is already in this codebase, and the
diagnosis exists nowhere else. Two of the three were wrong in their specifics when they were
re-checked against the code and the data, which is the argument for writing them down in a form that
can be re-checked at all. Two of the first three, that is; the Snapmaker reports were corrected
several times before they went, for the same reason.

### 10.1 The vendored driver's `trigger_shutter` is broken on a P1

**Where it goes:** [jvdillon/p3-ir-camera](https://github.com/jvdillon/p3-ir-camera), the upstream
of `plugin/files/vendor/p3_camera.py`. Pinned here at `e3205dca5727682ff2d903585d1dce5a1d19f1f6`.

**It is already reported, and not by us.** Issue #17, "Error with trigger shutter", opened
2026-03-21 by brandonrwin: same exception, same line, same P1, same commit, on macOS rather than on
a printer. It is open, it has no comments, and no pull request addresses it. So this is a comment on
#17, not a new issue, and what we contribute is the model constant analysis, the second defect
below, and confirmation on a second platform.

**What happens:** calling `trigger_shutter` on a Thermal Master P1 raises `memoryview assignment:
lvalue and rvalue have different structures`, and no calibration happens.

**Why, corrected 2026-09-18.** Both shutter constants are measurements of what a P3 emits and they
scale to a P1 by sensor width only, but they fail in a different order than this section first
recorded.

The raise comes from the read loop. `trigger_shutter` reads until it has `frame_read_size +
shutter_seg_1` bytes into a buffer allocated at exactly that size, so the headroom for the mistimed
partial frame is `shutter_seg_1`, which is `shutter_seg_1_lines * sensor_w` with the line count
hardcoded at 36 for both models. On a P1 the camera's real post shutter emission does not fit that
assumption, the last read returns more than the buffer has room for, and
`frame_buf_view[pos:next_pos] = chunk_buf_view[:n]` assigns a longer rvalue into a shorter slice.
That is the only memoryview assignment in the method.

The `shutter_seg_2` arithmetic this section used to blame is real but is the second defect, not the
first. 800 lines is 204,812 bytes on a P3 and sits inside its 206,872 byte buffer; on a P1 it is
128,012 into a buffer of 83,224, which is 44,788 bytes past the end. A memoryview slice with out of
range bounds clamps rather than raising, so that alone never threw: it would return an empty second
segment and a silently short frame. Nobody sees it today because the read raises first. Fix only the
read, which is what #17 proposes, and a P1 stops raising and starts returning a truncated frame with
no way for the caller to tell.

One more correction, because it changes what the fix should be. #17 says `read_frame` "properly
constrains reads" and `trigger_shutter` does not. Neither constrains: both call
`dev.read(0x81, chunk_buf, 10000)` with the whole chunk buffer. What `read_frame` has is the resync
guard above its assignment, which restarts at `pos = 0` when a read lands at or past the end of the
frame without being a 12 byte end marker. That guard is what keeps it from overrunning, so bounding
the read without adding the guard trades a loud failure for a quiet one.

**Suggested fix for upstream:** drop the read-back, since `read_frame` already recovers from a
mistimed frame by exactly that guard, which makes the method model independent by making it do less
and retires both defects at once. Failing that, give the read the guard rather than only a bound,
and derive both segment offsets from the model config with an offset that does not fit the buffer
rejected instead of clamped.

**What this plugin does instead:** `fire_shutter` in `plugin/files/lib/thermal_master/camera.py`
sends `COMMANDS["shutter"]` and reads the status, which is all a calibration needs, and lets the
ordinary reader resynchronise over the frame that follows. That reaches past an underscore
deliberately: the alternative is copying the endpoint, the request numbers and the timeout out of
the driver, where they would rot the next time the pin moves. The driver is pinned and is not ours
to edit (`VENDORING.md`).

**Confirmed on:** a P1, `3474:45c2`, on a Snapmaker U1. Not tried on a P3, where it presumably
works, since the constants were measured there. What is not measured anywhere, ours or #17's, is the
P1's actual post shutter emission size: both of us stopped calling the method rather than
instrumenting it.

### 10.2 Closed, not sent: `userEditable` values reaching a templated file

**Status: retested 2026-09-18 and not reproducible. No report goes anywhere.** It is kept here in
full, because a finding that was reasonable at the time and did not survive a retest is worth more
on the page than deleted, and because the retest is a one minute recipe if it ever comes back.

**What was believed:** that a `config[]` entry declared `userEditable: true` is presented to the
user at install time, the user sets it, and the file templated from it comes out holding the
manifest's `default` instead of the value the user chose. It would have gone to the Bespok3d daemon
and installer, whoever owns the install path that templates `install.place[]` entries, and never to
`b3-builder`: the package was built correctly and carried the right template.

**How it was found, 2026-09-14, during Phase 5b.** This plugin's `files/webcam.conf.tmpl` is a
Moonraker `[webcam]` fragment templated from two config values, `THERMAL_CAMERA_NAME` and
`THERMAL_CAMERA_ASPECT`, both `userEditable`. The camera was mounted the other way round, so the
picture needed rotating, and Fluidd can only rotate a camera from a config file this plugin owns:
the correction therefore had to arrive through a reinstall. Every reinstall came up with the
defaults, so the sideways camera could not be corrected at all, by any route available to a user.

Three rounds went into diagnosing that, and two of the theories were wrong before this one was
adopted: that Fluidd ignores `aspect_ratio`, and that rotating server side would fix the letterbox
bars. Both were disproved on hardware. What was recorded as certain is that the values entered did
not reach the rendered config. The mechanism behind that was never established, because the
workaround below had already made it not matter.

**The retest, 2026-09-18, on the maintainer's own printer.** Before: the values sat at their
defaults, which he had accepted at install, so the placed file agreeing with them proved nothing.
He then reinstalled through the app choosing a name and a shape that are not the defaults. After:

| | before | after |
| --- | --- | --- |
| `usr/local/plugins/thermal-master/user_vars.json` | `Thermal`, `4:3` | `IR`, `3:4` |
| the placed `moonraker/thermal-master.cfg` | `[webcam Thermal]`, `4:3` | `[webcam IR]`, `3:4` |

Capture and templating are both correct. The chosen value is stored and it reaches the rendered
file.

**And the plugin has not changed in any way that could explain it.** The `config[]` block, the
`requires.variables` block, the `$THERMAL_CAMERA_NAME` and `$THERMAL_CAMERA_ASPECT` substitutions
and the `moonraker-config` place entry with `render: true` are identical in the packages for 0.6.0,
0.7.0 and 0.25.0, compared by reading the manifests out of `dist/`. Nothing this repo ships accounts
for the difference, so either the daemon changed between 14 and 18 September, or what happened on
14 September had a cause that was never found and this section named the wrong culprit. Both remain
open; there is no way to choose between them from here, and a maintainer should not be sent a defect
his installer does not exhibit.

One thing could not be pinned down: the daemon's version. `/userdata/bespok3d/etc/version` reads
`0.0.1`, which is not it, since the catalogue publishes `bespok3d-daemon 0.14.0` and the index was
last assembled on 31 August. So this is "not reproducible on 2026-09-18", not "fixed in version X".

**How to retest it in a minute**, if a templated value ever comes out wrong again:

```sh
ssh <printer> 'cat /userdata/bespok3d/usr/local/plugins/thermal-master/user_vars.json
grep -E "^\[webcam|^aspect_ratio" /oem/printer_data/config/bespok3d/moonraker/thermal-master.cfg'
```

Reinstall with a name and a shape that are not the defaults and run it again. The two outputs
agreeing with what was chosen is the working case. `user_vars.json` right and the placed file wrong
puts the fault in templating; `user_vars.json` wrong puts it in capture, before templating is
reached. Knowing which is the difference between a useful report and this one.

**What this plugin does instead, and would keep doing either way:** it stopped depending on
install-time configuration for anything correctable. Orientation, palette, mirroring and the rest
moved into the plugin's own settings page and a `SettingsStore` persisted to `$BESPOK3D/var`, and
Fluidd's own rotation is pinned to zero in the fragment. The two config values that remain are the
camera's display name and the tile shape, neither of which breaks the plugin if it comes out as the
default. That was the right design regardless of whose defect this was.

### 10.3 A filaman card polls an endpoint nobody serves, forever

**Where it goes:** the **filaman** plugin, privately. It is a private plugin with no public URL, so
this is a message to its owner rather than an issue. Not `fluidd-plugin`, even though the offending
file's own header says so: that misattribution is part of the defect, because it sends anyone
debugging this to the wrong repository, and `Bespok3d/fluidd-plugin` does have the
`scripts/patch-fluidd.sh` the header names and nothing whatsoever to do with this card.

**Measured, 2026-09-18, and the first write up of this section was wrong about the size of it.** It
recorded 39,081 requests, which was one rotated log out of nine. Reading all nine gives **647,327**.
The 13 August start date was right.

Every figure below can be re-derived from the maintainer's log backups without any tool of ours: the
orphan is every line containing `server/filaman/status` logged `404`, the window is the first and
last of those timestamps, the share is that count against the file's total lines, and the retention
comparison is the span each 10 MB rotated file covers, before 13 August against after. The script
written to do it analyses another plugin's defect and has no business in this repo, so it goes to
filaman's owner with the report rather than being kept here.

**What happens:** on a printer where filaman is not installed, every open Fluidd tab issues
`GET /server/filaman/status` every five seconds, for as long as the tab stays open, forever.

**Three defects, and the third is what makes the others permanent.**

*It has never been answered.* All 647,327 requests are logged 404, from the first at 09:50 on
13 August to the last at 08:28 on 14 September. The filaman component itself loads at 18:36:54 that
first day and again at 18:46:58 after a restart, and it comes up healthy: post init runs, it
discovers all four filament sensors by name, it sets a spool, it registers an announcements feed.
The polling continues through both loads without pausing, still 404, for about nine hours. So the
card is not asking for something that used to exist; it asks for a route the component does not
serve, and has since the day it arrived. This was nearly claimed on the wrong evidence: Moonraker
logs no request it answers, 647,354 lines record a 404 and not one records a 200 while 101, 201,
503 and 500 all appear, so a working poll would be invisible. The finding rests on 404s recorded
during confirmed component uptime, never on an absence of successes.

*The poll never stops.* `b3d-filaman-card.js` documents itself in its own header as inert on a
printer without the filaman component. It is not. `whenDocumentIsReady` calls `setInterval(poll,
5000)` unconditionally, and the failure handler calls only `removeCardElement` and never clears the
timer. So the card removes itself from the page, which is why nobody sees anything wrong, and goes
on polling from a document it has already removed itself from. The timers also appear to stack
rather than running one per tab: across the nine files the observed rate runs from 1.05 to 2.34
times what a single five second timer could produce, and in one file 22,925 requests land under a
second apart. That last part is inference and is marked as such in the report; the card's source
could not be re-read, because the file was gone from the printer before the measuring started.

*The orphan.* An earlier filaman version instrumented the fluidd plugin's bundle: it appended
`<script src="./b3d-filaman-card.js"></script>` to `index.html` and installed
`b3d-filaman-card.js` beside it. filaman 0.2.1 ships neither, so upgrading or uninstalling leaves
both behind permanently. The plugin that made the change no longer declares it, so nothing on the
printer knows the change is there to undo. A plugin that modifies a file it does not own has to keep
declaring that modification for as long as any installed version of it might still be on a printer,
or it cannot be cleaned up by anything except a human who already knows.

**What it costs:** 88.0% of every line written to the Moonraker log over that month, and 90.7% of
the bytes, at about 20,000 requests a day. The damage is the log, not the requests. One 10 MB file
held 25 March to 13 August, 141 days; after the card arrived a 10 MB file fills in two to five days,
which quietly destroys the printer's ability to answer any question about last week. That is how it
was found at all. Removing the two leftovers on 14 September took the log from 17,634 lines a day to
611.

**Suggested fixes, smallest first:**

1. Clear the interval in the failure path. One line, and it makes the behaviour match what the
   file's own header already claims.
2. Check `/server/info` for the filaman component before starting the timer at all, which avoids
   even the first request on a printer that does not have it.
3. Work out why the endpoint answers 404 with the component loaded. Until that is understood, 1 and
   2 silence a card that still does not work for anybody.
4. Have filaman keep declaring the files it installed into the fluidd bundle, so an upgrade or an
   uninstall removes them. Without this, the rest fix only printers that receive a new copy of a
   file that is no longer shipped, which is none of them.
5. Correct the header's attribution, which currently points at `fluidd-plugin`'s `patch-fluidd.sh`.

**Cleaning up a printer that already has one:** the two leftovers are `b3d-filaman-card.js` in the
fluidd bundle and the `<script>` tag appended to its `index.html`. Removing both stops the polling.

**A separate small thing found alongside it:** `announcements.py:_fetch_moonlight()` failed to
update the subscription named `filaman` with its own HTTP 404, at both component loads. Unrelated to
the polling, but it is a registered feed that does not resolve.

### 10.4 Sent: the U1's wifi retries, rejoins, and will not stay off

**Where it went:** Snapmaker support, through its own messaging system, on 2026-09-28. With it went
two exports of the printer's system log (2026-09-24 and 2026-09-28, both encrypted for Snapmaker),
the printer's network screen showing the wifi off while it was connected, and the router's log of
the printer's wifi client and its client list, with the network's name and the home's public
address blacked out. The report carries its measurements as an appendix. The thermal camera's clips
were left out, since the plugin is not stock software and the pings show the same outage.

**What it says:**

- Blocked at the router, the printer retries the refused network every 30.97 seconds, forever, and
  is unreachable for about three seconds of each cycle (F-72).
- Neither the off switch nor forgetting the network keeps it off. Forgetting leaves the network
  saved and enabled in `/etc/wpa_supplicant.conf`, its overlay copy and the screen's own copy, and
  the firmware restarts `wpa_supplicant` and `dhcpcd` from that file about 30 seconds after any
  disconnect. What triggers the restart was not found.
- With both links up, traffic to the wifi address arrives over wifi and its replies leave by
  ethernet, and Snapmaker Orca uploads print files to the wifi address.
- `/usr/bin/wifi-connect.sh` prints the wifi password to its output. It was found in no log.

**What keeps the maintainer's printer off wifi:** `disabled=1` added to the network in
`/etc/wpa_supplicant.conf` and in `/oem/printer_data/gui/wpa_supplicant.conf`, with backups of both
in `/userdata`. It has survived a reboot and a power cycle. A firmware update may put the files
back, so check after every update: `wpa_cli -i wlan0 list_networks` should show the network
`[DISABLED]`, and `ip -4 addr show wlan0` no address.

### 10.5 Sent: Snapmaker Orca disconnects after the print dialog connects, and uploads stall

**Where it went:** Snapmaker, the same day and the same way, with the slicer's log folder, two
screen recordings, and the 2026-09-28 export of the printer's system log. The report carries its
data as an appendix, with the search patterns that find each finding in the log folder.

**What it says:**

- Over 2026-09-15 to 2026-09-28, 11 of the 13 cloud connections the print dialog opened were
  followed by the slicer disconnecting from the printer 46 to 47 seconds later, the first
  "Connection lost" 18 to 20 seconds after the connection. None of the 13 the device page opened
  were. One suggested cause, unconfirmed: both parts of the app use the same MQTT client ID, and
  AWS IoT Core closes the older of two connections with the same ID.
- Uploads go over the local network to the address the slicer learned from the printer. On
  2026-09-27 three of five stalled partway, with Moonraker logging each request arriving and
  nothing after it; the dialog gave no sign of it.
- With the printer off wifi, the slicer kept its stored wifi address, spent up to 3 seconds
  checking it before each upload, and then went through the cloud.

**Why it is recorded here:** the slicer's disconnect arrived in the same minute as F-74's freeze and
looked like one event with it. It is not the plugin, and it is not the network.

### 10.6 Sent: the Bespok3d app's Doc tab shows no images for a published plugin

**Where it went:** unlucio, by direct message, on 2026-09-29.

**What it says:** the Doc tab resolves image paths only through `DOC_ASSETS`, which holds the doc
media of the plugins built into the app, so a plugin published from its own repository shows its
README's images as broken, though the `.b3` carries them under `doc/images/`. Absolute links are
blocked by `img-src 'self' data:`, and `data:` images are removed by `defaultUrlTransform`, so the
plugin has no way round it. It asked for either the Doc tab to read `doc/` images from the package,
or the release workflow to upload `doc/images/*` with the policy allowing `/releases/download/`.
The finding is F-77.

**Until it is fixed:** nothing. The README keeps its relative paths, and the Doc tab shows alt text
where the images go.
