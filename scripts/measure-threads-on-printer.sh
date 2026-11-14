#!/bin/sh
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
# What kind of cost the plugin is on the printer: each thread's user time and kernel time.
#
# measure-cpu-on-printer.sh gives one number for the whole process. This splits it, because the
# two halves are brought down by different things: kernel time is the USB transfers and only
# fewer or larger reads reduce it, user time is Python, pyusb and copying. It also counts how
# often each thread stopped to wait, which for the thread reading the camera is close to its
# number of reads, and prints what the kernel knows about the camera's USB link.
#
#   scripts/measure-threads-on-printer.sh lava            # sample for 30 seconds, starting now
#   scripts/measure-threads-on-printer.sh lava 60 70      # stay quiet for 70 seconds, then sample 60
#
# The protocol is the other script's, and it matters as much here. For the idle figure nothing may
# be watching, on any device: one open tile or viewer keeps the plugin rendering, and that is a
# different measurement. The quiet time is what lets the idle timeout take effect first.
#
# Nothing is copied to the printer. measure-thread-cost.py is piped in over stdin and run by the
# interpreter the service itself runs under. It reads /proc and /sys only, so it never opens the
# camera and is safe to run while the service is streaming.
set -eu

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "usage: $0 <printer-host> [seconds to sample] [seconds of quiet first]" >&2
    exit 2
fi
SAMPLE="${2:-30}"
QUIET="${3:-0}"

HERE="$(cd "$(dirname "$0")" && pwd)"
# The source directive is for a run with -x. The gate checks each script on its own without it,
# so the file cannot be followed from here; it is checked in its own right by the same run.
# shellcheck source=scripts/find-plugin.sh
# shellcheck disable=SC1091
. "$HERE/find-plugin.sh"

# SC2029: expanding here is the intent. The two numbers are this script's arguments and have to
# be written into the command for the remote python to receive them; $PYTHON and $PID stay in
# single quotes because they are the printer's, set there by the finder.
# shellcheck disable=SC2029
ssh "$HOST" "$FIND_PLUGIN"' exec "$PYTHON" - "$PID" '"$SAMPLE $QUIET" < "$HERE/measure-thread-cost.py"
