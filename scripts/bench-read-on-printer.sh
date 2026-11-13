#!/bin/sh
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
# Run the read bench on the printer: every way of getting a frame off the camera, side by side.
#
# UNLIKE profile-on-printer.sh, THIS OPENS THE CAMERA. Only one process can hold the USB device,
# so the stream has to be switched off first: press Stop on the plugin's settings page, which makes
# the service release the camera, run this, and press Start when it has finished. The bench asks
# the running service before it touches anything and refuses while the stream is on, so getting
# the order wrong costs a refusal and not a stream pulled out from under the service.
#
# With the stream off there is no picture and no timelapse frame, so not during a print that is
# being recorded.
#
#   scripts/bench-read-on-printer.sh lava
#   scripts/bench-read-on-printer.sh lava --frames 400 --rounds 4
#
# It takes about two minutes with the defaults: six candidates, two rounds, two hundred frames
# each at the camera's twenty-five a second. Let it finish; it puts the camera down when it does.
#
# Nothing is copied to the printer. bench-read-candidates.py is piped in over stdin and run by the
# interpreter the service itself runs under, against the plugin and the driver installed there.
set -eu

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "usage: $0 <printer-host> [--frames N] [--rounds N]" >&2
    exit 2
fi
shift

HERE="$(cd "$(dirname "$0")" && pwd)"
# The source directive is for a run with -x. The gate checks each script on its own without it,
# so the file cannot be followed from here; it is checked in its own right by the same run.
# shellcheck source=scripts/find-plugin.sh
# shellcheck disable=SC1091
. "$HERE/find-plugin.sh"

# SC2029: the remote half is expanded on the printer on purpose. $PYTHON, $STREAMER and $PID are
# the printer's, set there by the finder; only this script's own arguments are written in here.
# shellcheck disable=SC2029
ssh "$HOST" "$FIND_PLUGIN"' exec "$PYTHON" - "$STREAMER" --service-pid "$PID" '"$*" \
    < "$HERE/bench-read-candidates.py"
