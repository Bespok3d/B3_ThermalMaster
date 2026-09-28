<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
SPDX-License-Identifier: Apache-2.0
-->

# Contributing

This repo publishes a Bespok3d plugin. Read `CLAUDE.md` before changing anything: it is the contract
for both humans and AI assistants working here, and the gate enforces most of it.

## Environment setup

Clone with the submodule. The gate lives in `lib_bespok3d`, so a clone without it cannot run:

```sh
git clone --recurse-submodules https://github.com/Bespok3d/B3_ThermalMaster.git
```

`lib_bespok3d` is private to the Bespok3d organisation. From outside it, clone without
`--recurse-submodules` and run the tests on their own with `sh plugin/tests/run.sh`, which is what CI
runs on every pull request; a maintainer runs the rest of the gate.

If you already cloned without it:

```sh
git submodule sync --recursive && git submodule update --init --recursive
```

You need Python 3.11 to match the printer, Node.js 20 or newer for the builder, and `shellcheck`.
The gate installs the Python tools it needs; the plugin's own runtime dependencies (numpy, Pillow,
pyusb) have to be importable for the tests to run:

```sh
pip install numpy Pillow pyusb pytest
```

## Running the gate

```sh
bash scripts/check.sh
```

It must be green before a change is done. On a failure, fix the cause. If a detector is genuinely
wrong about a specific line, the fix is a per-instance `# gate-allow <metric>: <reason>` at the
smell, with a reason that answers "why is THIS one acceptable?", never a blanket mute.

## Tests

Tests live in `plugin/tests/` and run against a stand-in driver, so no camera and no printer are
needed. `plugin/tests/conftest.py` explains the awkward part: a stand-in `p3_camera` is registered
before the plugin is imported, because the real driver models a USB device and the plugin imports it
at module scope. The tests then ask the `thermal_master` package for names rather than importing
files, which is what let the code be split into modules without a single test changing.

The stand-in camera reads from a scripted list of frames, where an entry can be a frame to return or
an exception to raise, so a test can describe a glitched frame or a vanished device as plain data.

A fix ships with a regression test in the same change: one that fails on the old behaviour and
passes on the new.

## Where the code lives

`plugin/files/bin/thermal-master-stream.py` is the entry point and nothing else: the manifest names
it, and that name is a legal program and an illegal module, so nothing could import it. It puts two
directories on the path and calls `main`.

The plugin itself is `plugin/files/lib/thermal_master/`:

| module | what it owns |
| --- | --- |
| `palettes` | the colour tables |
| `temperature` | raw counts to Celsius, emissivity, where a pixel ends up on screen |
| `overlay` | the colorbar, the readings, the glyph cache |
| `pipeline` | raw frame to JPEG, and the state that spans frames |
| `camera` | finding, reading and commanding the USB device |
| `settings` | live settings, their file, and what a posted form may change |
| `page` | the control page and its script |
| `server` | routes, the MJPEG stream, the JSON endpoints |
| `cli` | arguments, threads, shutdown |

`__init__.py` re-exports everything public, so the test suite can take the package as one namespace
and ask for names rather than files. That is what let the split happen without rewriting tests.

Two things to know before adding a file. Every URL the page emits must be relative, because nginx
publishes the plugin under a prefix it is never told about. And `plugin/files/lib` needs its
`!plugin/files/lib/` line in `.gitignore`: the standard Python template ignores `lib/`, which
silently matched the entire plugin.

## Checking the control page

The gate covers the plugin's Python. It cannot cover the control page's behaviour in a browser, and
that gap has produced two shipped bugs: a button whose name shadowed a form property, and a fallback
path that dropped the button that was pressed. Both passed every server-side test.

`scripts/check-in-browser.py` serves the real pages against a stand-in camera and drives them in
headless Chromium. For the control page: calibrate reaches the device, the page does not reload
doing it, and a later Apply does not re-fire the last button. For the viewer: it fetches nothing
until pointed at, the temperature it reports matches the pixel under the pointer, and it stops
fetching once nobody is looking.

```sh
pip install playwright && playwright install chromium
python3 scripts/check-in-browser.py
```

Run it after touching either page. Nothing runs it for you, which has already cost something: it
loaded the entry script by path, and when the code moved into a package that stopped being the
plugin, so it sat broken through two releases until the next person to run it found out.

It is not in the gate, because a browser download is a lot to ask of someone working on a printer
plugin. The rules it taught are in the suite instead, as cheap assertions on the rendered HTML: no
form control may share a name with a property of HTMLFormElement, and the page may not emit an
absolute path.

## Building locally

The builder is installed into its own prefix. Do not use `npx b3-builder`: it resolves to whatever
copy npm cached earlier, which silently builds against an out of date manifest schema.

```sh
npm install --prefix ~/.b3-builder github:Bespok3d/b3-builder
~/.b3-builder/node_modules/.bin/b3-builder build \
  --source ./plugin --out dist --atom-repo Bespok3d/<repo> --bake
```

`--bake` is required once the plugin declares Python dependencies, because the printer never runs
pip: CI downloads arm64 wheels into `plugin/files/wheels/` and the daemon installs them offline into
a venv belonging to this plugin alone. Without `--bake` the build packs an empty wheels directory,
and the builder's own gate refuses to pack rather than shipping a plugin that cannot start.

Check what you produced before installing it:

```sh
unzip -l dist/<plugin>-<version>.b3
unzip -p dist/<plugin>-<version>.b3 manifest.json | jq '.files[].path'
```

Nothing may be in the archive that is not in `files[]`: the daemon refuses a package with an
unlisted member, so a stray `.DS_Store` is a failed install, not a cosmetic problem.

## Releasing

Bump `version` in `plugin/manifest.json` and push a tag naming that plugin and that exact number:

```sh
git tag plugin-<name>-v<version>
git push origin <branch> --tags
```

A push to a branch publishes nothing. CI runs the b3-builder Action, which packs and signs the
`.b3`, cuts a release, and registers the atom into the org index.

## Things the gate will not catch

- **Never run git on this repo from an assistant.** The maintainer commits.
- **Never reconfigure a live printer without explicit per-action approval.** Read-only diagnosis is
  fine. A serial port on a printer may be a live Klipper MCU link.
- **Bump the patch version on every build you test,** so you are never wondering which one is
  installed.
