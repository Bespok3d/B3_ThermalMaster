#!/bin/sh
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
# What the plugin costs the printer, exactly, over a window you choose.
#
# htop answers this well enough to notice a problem and badly enough to argue about: the number
# moves, it is averaged over whatever interval htop refreshes at, and what comes back from it is a
# screenshot. The kernel already keeps the exact figure. /proc/<pid>/stat carries the total user
# and system time a process has ever used, in clock ticks, so reading it twice and dividing by the
# elapsed time gives the fraction of a core it used in between, with no sampling error and nothing
# to install on the printer.
#
#   scripts/measure-cpu-on-printer.sh lava            # sample for 30 seconds, starting now
#   scripts/measure-cpu-on-printer.sh lava 30 70      # stay quiet for 70 seconds, then sample 30
#
# The protocol matters more than the tool. Nothing may be watching: one open dashboard tile or
# viewer keeps the plugin fully awake, by design, and that is a different measurement. And when
# there is an idle timeout to wait out, the second number gives it time to take effect before the
# sampling starts.
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

# Reading the counter twice on the printer rather than over two ssh connections, so the window is
# the window and not the round trip. The comm field is in brackets and may hold anything, so it is
# cut off by its closing bracket rather than counted as a field: after that, user time and system
# time are the twelfth and thirteenth.
# The single quotes are the point, as in find-plugin.sh: this is shell for the printer, and
# $PID, $QUIET and $SAMPLE are the remote shell's variables rather than this one's.
# shellcheck disable=SC2016
MEASURE='
ticks=$(getconf CLK_TCK 2>/dev/null || echo 100)
cores=$(grep -c "^processor" /proc/cpuinfo)
cpu_ticks() { sed "s/.*) //" "/proc/$PID/stat" | awk "{print \$12 + \$13}"; }
if [ "$QUIET" -gt 0 ]; then
    echo "waiting ${QUIET}s for the printer to be left alone..." >&2
    sleep "$QUIET"
fi
started=$(cpu_ticks)
sleep "$SAMPLE"
finished=$(cpu_ticks)
awk -v a="$started" -v b="$finished" -v t="$SAMPLE" -v k="$ticks" -v c="$cores" "BEGIN {
    core = (b - a) / k / t * 100
    printf \"thermal-master used %.1f%% of one core over %d s, %.1f%% of all %d cores\n\",
           core, t, core / c, c
}"
'

# The two settings travel as environment for the remote shell rather than being pasted into it.
# SC2029: expanding here is the intent. The values are this script's arguments, and they have
# to be written into the command for the remote shell to receive them as its environment.
# shellcheck disable=SC2029
ssh "$HOST" "SAMPLE=$SAMPLE QUIET=$QUIET; $FIND_PLUGIN $MEASURE"
