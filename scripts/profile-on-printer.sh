#!/bin/sh
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
# Run a profiling script on the printer, against the plugin that is actually running there.
#
# Written because the obvious invocation is a guess in two places: which interpreter the service
# runs under, and where its files landed. Both are recorded in the running process, so neither is
# guessed here: the plugin's own command line is read out of /proc and used as is. The first
# attempt at this hardcoded the venv path from another plugin's example and failed with
# "No such file or directory" on a printer where the plugin was working perfectly.
#
# Nothing is copied to the printer. The script is piped in over stdin and the remote python reads
# it from there, so this leaves no file behind, installs nothing, and never opens the camera: it is
# safe to run while the service is streaming.
#
#   scripts/profile-on-printer.sh lava
#   scripts/profile-on-printer.sh lava --rotate 270
#
# Any extra arguments are passed to the profiler. The candidate bench takes none of them, so it is
# run without.
set -eu

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "usage: $0 <printer-host> [profiler arguments]" >&2
    exit 2
fi
shift

HERE="$(cd "$(dirname "$0")" && pwd)"

# The discovery half lives in one file, because two copies of it is how the bug it already had
# comes back in only one of them.
# The source directive is for a run with -x. The gate checks each script on its own without it,
# so the file cannot be followed from here; it is checked in its own right by the same run.
# shellcheck source=scripts/find-plugin.sh
# shellcheck disable=SC1091
. "$HERE/find-plugin.sh"

run_remote() {
    script="$1"
    shift
    echo ""
    echo "=== $(basename "$script") on $HOST ==="
    # shellcheck disable=SC2029 # the remote half is expanded on the printer on purpose
    ssh "$HOST" "$FIND_PLUGIN"' exec "$PYTHON" - "$STREAMER" '"$*" < "$script"
}

run_remote "$HERE/profile-frame-cost.py" "$@"
run_remote "$HERE/bench-pipeline-candidates.py"
run_remote "$HERE/bench-readout-candidates.py" "$@"
