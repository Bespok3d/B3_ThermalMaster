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

### 4.0 What is still open

Findings below are kept as written, so the reasoning stays readable, and most now carry their own
resolution note. Closed by Phase 0: F-2, F-3, F-9, F-10, F-12, F-31, F-40 (partly), F-42, F-43, F-48,
F-49, F-50, F-51. Closed by Phase 1: F-11, F-20, F-22, F-23, F-24, F-32, F-33, F-34, F-35, F-36, F-37,
F-38, F-39, F-41, F-44, and the rest of F-40; F-45 keeps Apache-2.0 and now ships a per-plugin
`doc/LICENSE`, and F-46's capability and exclusivity metadata is declared.

Genuinely still open: F-5 and F-6 (signing, Phase 8), F-7 and F-8 (withdrawn, the udev file is gone),
F-13 through F-18 (runtime correctness, Phase 3), F-25 through F-30 (streamer design, Phase 5), and
F-47 (mypy, waiting on the streamer being split into an importable module).

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
the first time the builder runs with a signing key.

**F-6. Packages ship unsigned.** Signing needs a GPG keypair with the private key in the
`REGISTRY_SIGNING_KEY` secret. Until then packages install but show as unknown publisher.

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

**F-47. The streamer cannot be type-checked while it is one hyphenated script.** mypy derives a
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

**F-56. The numpy pipeline, not the JPEG encode, is what this plugin spends its time on.** Open, and
it invalidates the assumption the last three rounds of tuning were built on.

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

Where the pipeline time plausibly goes, in order of suspicion, none of it measured yet:

- `frame_bounds` calls `np.percentile` twice, and each call sorts or partitions all 19,200 pixels.
  One call asking for both percentiles would halve it, and taking them from a 2x2 subsample would
  quarter that again, at no cost to a display range that is smoothed over a second anyway.
- `enhance_detail` and `blur_3x3` make about six full-frame passes in float32, on data that arrived
  as uint8.
- `reduce_temporal_noise` casts to float32 and back for what is, at the default weight, an average
  of two integers.

The readout is the other half of the story: 6.82 ms of the 16.62, split about evenly between the
doubled encode and the drawing. The drawing is 3.31 ms on the printer against 0.19 ms on a
development machine, so pasting glyph tiles with an alpha mask is far more expensive there than the
glyph cache measurements suggested. Worth revisiting whether the tiles can be pasted without a mask,
or composited once into a strip.

Sequencing: fix the pipeline first. It is the largest single stage, it is paid whether or not the
readout is on, and unlike the readout it has no toggle.

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
2. **Is CLAHE worth a numpy reimplementation?** A Phase 5 decision, best made by looking at a real print
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

### 6.4 What CLAHE is, and why it is a question

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

Exit: each control is exercised on hardware and the stream survives all of them.

### Phase 7: the embedded page

The WebSocket temperature channel and the client-side features: readout, ROI, hotspots, zoom, reticle,
unit toggle, screenshot, and the page's own rotate and flip. Registered as a second `[webcam]` iframe
tile so it lands in Fluidd next to the plain feed.

Exit: usable on a phone-sized viewport, and closing it drops the temperature channel back to zero cost.

### Phase 8: release readiness

Signing (F-6) and the `publisher` fingerprint check (F-5), channel promotion from `experiment` toward
`testing` and `stable` as evidence accumulates, a full doc pass, and a device trial across both models.

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

## 9. Where this stands, 2026-09-13

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
