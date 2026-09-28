#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
# This plugin's own gate: it must pass from this repo's root, with no sibling repo cloned except
# lib_bespok3d. Exits non-zero on any failure.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The shared gate helpers and the detectors that enforce a workspace-wide rule live in one place.
# See lib_bespok3d/tooling/README.md. This is the only line that knows where they are.
B3D_TOOLING="${B3D_TOOLING:-$REPO_ROOT/lib_bespok3d/tooling}"
# lib_bespok3d is a submodule. A clone made without it leaves an empty directory here, so say what
# is actually wrong instead of letting every check below fail on a missing file.
if [ ! -f "$B3D_TOOLING/gate-lib.sh" ] || [ ! -f "$B3D_TOOLING/release-trigger-detector.mjs" ] || [ ! -f "$B3D_TOOLING/manifest-origin-detector.mjs" ]; then
    echo "The shared gate helpers are missing or older than the checks this gate runs:" >&2
    echo "the lib_bespok3d submodule is not checked out, or is pinned to an older commit." >&2
    echo "Run this once from the repo root, then try again:" >&2
    echo "  git submodule sync --recursive && git submodule update --init --recursive" >&2
    echo "See CONTRIBUTING.md for the full environment setup." >&2
    exit 1
fi

# shellcheck source=/dev/null
. "$B3D_TOOLING/gate-lib.sh"

cd "$REPO_ROOT" || exit 1

PLUGIN_DIR="$REPO_ROOT/plugin"

# The shared tool venv carries lint and test tools only, by design: "a package whose own tests need a
# runtime library declares that library in its own repo" (lib_bespok3d/tooling/python-tools.txt), and
# one venv is shared across every plugin repo that has no runtime dependencies. This plugin has three,
# and its tests import two of them, so the toolchain is pointed at a venv this repo owns before it is
# built. The shared one stays exactly what the other repos share.
#
# The name carries the platform because this tree is edited from more than one machine: a checkout
# shared between a macOS host and a Linux VM otherwise has each one delete and rebuild the other's
# venv on every run, and a stale symlink to a missing interpreter reads as a confusing failure rather
# than as the platform mismatch it is.
export PYTHONDONTWRITEBYTECODE=1
B3D_TOOLS_VENV="$REPO_ROOT/.venv-$(uname -s)-$(uname -m)"
B3D_PY="$B3D_TOOLS_VENV/bin/python"

echo ""
echo "B3_ThermalMaster gate"

b3d_python_tools

# Runtime pins on top of the shared tool set. Idempotent, and quiet on the common path where the venv
# already has them.
if ! "$B3D_PY" -c "import numpy, PIL, usb" > /dev/null 2>&1; then
    echo "  Adding the plugin's runtime dependencies to the gate venv..."
    "$B3D_TOOLS_VENV/bin/pip" install --quiet -r "$PLUGIN_DIR/requirements.txt" || exit 2
fi

run_check "pytest" pytest_in_dir "$PLUGIN_DIR" tests
run_check "ruff"   ruff_in_dir "$PLUGIN_DIR" files/bin files/lib tests
# Scoped to files/lib, which is the whole plugin. The entry script cannot be checked and does not
# need to be: mypy derives a module name from the filename and "thermal-master-stream" is not a
# legal one, which is exactly why the code moved out of it (F-47). What is left in that file is a
# path setup and a call. u1-remote-screen checks only its underscore-named modules for the same
# reason.
run_check "mypy"   mypy_in_dir "$PLUGIN_DIR" files/lib

release_trigger_check "$REPO_ROOT"
manifest_origin_check "$REPO_ROOT"
workflow_pinning_check "$REPO_ROOT"

# Scoped to what this repo authors, which is the guard's documented usage: "each repo runs this over
# its own trees and passes its own scope on the command line". The repo root would also walk the
# vendored driver tree and the upstream viewer clone checked out beside it, neither of which is ours
# to rewrite and both of which carry em-dashes. A path that does not exist is skipped, so naming
# files/udev here is safe whether or not it survives the manifest rewrite.
# --suffix adds to the guard's defaults, which cover code and prose but not the config formats this
# plugin ships: an em-dash in an nginx location or a Moonraker fragment is as much a Rule Zero
# violation as one in a comment. The same goes for the workflows, which the defaults skip too.
em_dash_check --suffix .conf --suffix .tmpl --suffix .yml \
    "$PLUGIN_DIR/files" \
    "$PLUGIN_DIR/doc" "$PLUGIN_DIR/tests" "$PLUGIN_DIR/manifest.json" "$PLUGIN_DIR/requirements.txt" \
    "$REPO_ROOT/scripts" "$REPO_ROOT/README.md" "$REPO_ROOT/CLAUDE.md" "$REPO_ROOT/AGENTS.md" \
    "$REPO_ROOT/CONTRIBUTING.md" "$REPO_ROOT/SECURITY.md" "$REPO_ROOT/ROADMAP.md" \
    "$REPO_ROOT/VENDORING.md" "$REPO_ROOT/NOTICE" "$REPO_ROOT/CHANGELOG_DEV.md" "$REPO_ROOT/.github"

# Same reasoning: the viewer clone ships its own shell scripts, which are not this repo's to lint.
shellcheck_repo "$REPO_ROOT/scripts" "$PLUGIN_DIR/files"

gate_summary || exit 1
