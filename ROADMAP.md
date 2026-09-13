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

**F-31. Two later drafts sit in the gitignored vendor directory.** `p3_stream.py` and `temp.py` carry
palettes, a colorbar, EMA auto-gain, gain switching and a `/control` page. Harvest what is wanted into
`files/bin/`, then delete them.

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

Rotate and flip stay out of the MJPEG feed: Fluidd and Mainsail already apply per-webcam rotation and
flip, and duplicating it server-side would fight their setting. The embedded page needs its own.

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

Exit: a `.b3` built by `b3-builder` installs, the service starts, `/thermal/stream.mjpg` resolves through
nginx, and the camera appears in Fluidd without the user touching Settings.

### Phase 2: verify on hardware

Nothing in this repo has run against a physical camera. Run the device-trial checklist on a P1 before
anything else is rewritten. This needs the maintainer at the printer: per `CLAUDE.md`, no device-changing
step happens without explicit authorization.

Exit: a known-good baseline, or a list of what actually breaks, which likely reorders everything after
this point.

### Phase 3: runtime robustness

F-13 through F-18, plus F-29 and F-30, each with a regression test against a fake camera object so the
frame-loop failure modes are testable without hardware.

Exit: the streamer survives unplug, replug, a glitched frame, and a stop immediately followed by a start.

### Phase 4: dual model

Auto-detect P1 and P3, migrate the plugin name with a `migration` block, reconcile docs (F-23), and
render the webcam fragment's aspect ratio and display name from `config[]` (F-24).

Exit: one package streams from either camera with no user configuration.

### Phase 5: image pipeline

Six palettes, EMA auto-gain (F-26), DDE, TNR, colorbar overlay, and the performance fixes F-25 and F-27.
A settings surface so palette and overlays change without an SSH session. Resolve CLAHE.

Exit: the feed is stable and readable, palette switching works live, and measured printer CPU is recorded
in the doc for both models.

### Phase 6: device controls

Shutter/NUC, gain mode and emissivity through a command queue drained by the capture thread (F-18), with
the concurrency rule written into the plugin doc.

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

Two things nobody has verified: shellcheck reports as skipped in the assistant's sandbox, so the shell
in this repo has never actually been checked, and the plugin has still never run against the camera.

Next: Phase 1, the manifest rewrite. It is the change that makes the plugin installable and puts the
camera in Fluidd without the user touching Settings. Everything it needs is settled except the
capability string, where `klipper-generic` is the recommendation (section 6.2).
