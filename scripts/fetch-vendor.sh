#!/bin/sh
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
# Verify or update the vendored upstream driver against its pinned commit.
#
# The driver is committed to this repo, not fetched at build time, so a clone builds with no network
# and what ships is visible in review. See VENDORING.md. This script is the maintenance tool for that
# arrangement: it checks the committed bytes still match the pin, and moves the pin when upstream has
# something worth taking.
#
#   sh scripts/fetch-vendor.sh            verify the committed files against the pin
#   sh scripts/fetch-vendor.sh --update   re-fetch at PINNED_COMMIT and overwrite them
#
# Verification is two-sided on purpose. It checks the committed files against the recorded hashes,
# which catches a local edit, and it checks what upstream serves at that commit against the same
# hashes, which catches a tag or branch that has been moved out from under the pin.
#
# Requires: curl, and shasum or sha256sum.
set -e

UPSTREAM_REPO="jvdillon/p3-ir-camera"
PINNED_COMMIT="e3205dca5727682ff2d903585d1dce5a1d19f1f6"
DRIVER_SHA256="24e69e23a5a662cd5055e7a2498e185baf81f6aea3545f6a95deb90c8f5a55d8"
LICENSE_SHA256="c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
VENDOR_DIR="$REPO_DIR/plugin/files/vendor"
PROVENANCE_DOC="$REPO_DIR/VENDORING.md"

file_sha256() {
  if command -v shasum > /dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

fetch_upstream_file() {
  curl -fsSL "https://raw.githubusercontent.com/$UPSTREAM_REPO/$PINNED_COMMIT/$1" -o "$2"
}

# Compare one file against its expected hash. Prints the mismatch rather than the whole file, because
# the useful output is which side moved, not the diff.
check_sha256() {
  actual="$(file_sha256 "$1")"
  if [ "$actual" = "$2" ]; then
    echo "    ok    $3"
    return 0
  fi
  echo "    FAIL  $3" >&2
  echo "          expected $2" >&2
  echo "          actual   $actual" >&2
  return 1
}

# VENDORING.md is what a human reads; the constants above are what this script trusts. They are two
# copies of one fact, so the drift between them is checked rather than hoped about.
check_provenance_doc() {
  failures=0
  for expected_value in "$PINNED_COMMIT" "$DRIVER_SHA256" "$LICENSE_SHA256"; do
    grep -q "$expected_value" "$PROVENANCE_DOC" || {
      echo "    FAIL  VENDORING.md does not mention $expected_value" >&2
      failures=$((failures + 1))
    }
  done
  [ "$failures" -eq 0 ] || return 1
  echo "    ok    VENDORING.md agrees with the pin"
}

update_vendored_files() {
  echo "==> Re-fetching at $PINNED_COMMIT"
  fetch_upstream_file "p3_camera.py" "$VENDOR_DIR/p3_camera.py"
  fetch_upstream_file "LICENSE" "$VENDOR_DIR/LICENSE.p3-ir-camera"
  echo "    p3_camera.py          $(file_sha256 "$VENDOR_DIR/p3_camera.py")"
  echo "    LICENSE.p3-ir-camera  $(file_sha256 "$VENDOR_DIR/LICENSE.p3-ir-camera")"
  echo ""
  echo "Files written. Update PINNED_COMMIT and the hashes in this script and in VENDORING.md to the"
  echo "values above, review the diff, and note the move in plugin/doc/CHANGELOG.md."
}

verify_vendored_files() {
  echo "==> Committed files"
  check_sha256 "$VENDOR_DIR/p3_camera.py" "$DRIVER_SHA256" "p3_camera.py"
  check_sha256 "$VENDOR_DIR/LICENSE.p3-ir-camera" "$LICENSE_SHA256" "LICENSE.p3-ir-camera"

  echo "==> Upstream at $PINNED_COMMIT"
  work_dir="$(mktemp -d)"
  trap 'rm -rf "$work_dir"' EXIT
  fetch_upstream_file "p3_camera.py" "$work_dir/p3_camera.py"
  fetch_upstream_file "LICENSE" "$work_dir/LICENSE"
  check_sha256 "$work_dir/p3_camera.py" "$DRIVER_SHA256" "p3_camera.py"
  check_sha256 "$work_dir/LICENSE" "$LICENSE_SHA256" "LICENSE"

  echo "==> Provenance"
  check_provenance_doc

  echo ""
  echo "Vendored driver matches the pin."
}

if [ "${1:-}" = "--update" ]; then
  update_vendored_files
  exit 0
fi

if [ -n "${1:-}" ]; then
  echo "Usage: $0 [--update]" >&2
  exit 1
fi

verify_vendored_files
