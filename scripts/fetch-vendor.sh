#!/bin/sh
# Populate plugin/files/vendor/ with the vendored USB driver and the aarch64 runtime deps the
# streamer imports at runtime (numpy, Pillow, pyusb). Run before scripts/pack.sh.
#
# Nothing here is committed: plugin/files/vendor/ is gitignored, exactly like u1-hw-camera's
# build-output bin dir. Requires: curl, pip, unzip.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
VENDOR_DIR="$REPO_DIR/plugin/files/vendor"

JVDILLON_REPO="jvdillon/p3-ir-camera"
JVDILLON_REF="ae3205dca5727682ff2d903585d1dce5a1d19f1f"

# aarch64 / CPython 3.11 to match the U1's system python (/usr/bin/python3) that runs the streamer.
PY_PLATFORM="manylinux2014_aarch64"
PY_VERSION="311"
PY_ABI="cp311"

for cmd in curl pip unzip; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: '$cmd' is required." >&2; exit 1; }
done

rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"

echo "==> Fetching p3_camera.py (pinned $JVDILLON_REF)"
curl -fsSL "https://raw.githubusercontent.com/$JVDILLON_REPO/$JVDILLON_REF/p3_camera.py" \
  -o "$VENDOR_DIR/p3_camera.py"
curl -fsSL "https://raw.githubusercontent.com/$JVDILLON_REPO/$JVDILLON_REF/LICENSE" \
  -o "$VENDOR_DIR/LICENSE.p3-ir-camera"

wheel_dir="$(mktemp -d)"
trap 'rm -rf "$wheel_dir"' EXIT

echo "==> Downloading aarch64 wheels (numpy, Pillow)"
pip download --only-binary=:all: --no-deps \
  --platform "$PY_PLATFORM" --python-version "$PY_VERSION" --implementation cp --abi "$PY_ABI" \
  numpy Pillow -d "$wheel_dir"

echo "==> Downloading pyusb (pure python)"
pip download --no-deps pyusb -d "$wheel_dir"

echo "==> Unpacking wheels into vendor/"
for wheel in "$wheel_dir"/*.whl; do
  echo "    $(basename "$wheel")"
  unzip -q -o "$wheel" -d "$VENDOR_DIR"
done

echo "Vendor populated:"
ls -1 "$VENDOR_DIR"
