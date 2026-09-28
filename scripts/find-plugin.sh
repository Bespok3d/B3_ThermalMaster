# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
# Shell to run on the printer that finds the running thermal-master service and says what it is.
#
# Sourced rather than copied, by every script here that has to reach the plugin on the printer.
# The first copy of this took two rounds to get right: it asked whether a command line contained
# "thermal-master-stream", and the command line it was running inside contained that string too, as
# the search pattern, so with an unlucky process id the finder found itself and reported that the
# service was not running while it served happily on port 8082 (F-70). One copy, fixed once.
#
# It is sourced and so has no shebang, which leaves shellcheck unable to infer a shell when it
# checks this file on its own. It runs under the printer's BusyBox ash, so it is checked as sh.
# shellcheck shell=sh
#
# Sets PID, PYTHON and STREAMER on the printer, and says what it found on stderr. It reads no
# standard input, which is what lets a caller pipe a python script through it.
# SC2034: FIND_PLUGIN is read by the scripts that source this file, never inside it.
# SC2016: the single quotes are the point. This is shell for the printer and has to arrive
# unexpanded, because every variable in it names something on the printer, not here.
# shellcheck disable=SC2034,SC2016
FIND_PLUGIN='
for process in /proc/[0-9]*; do
    [ "${process#/proc/}" = "$$" ] && continue
    arguments=$(tr "\0" "\n" < "$process/cmdline" 2>/dev/null) || continue
    STREAMER=$(printf "%s\n" "$arguments" | grep "thermal-master-stream[.]py$") || continue
    PYTHON=$(printf "%s\n" "$arguments" | head -1)
    case "$PYTHON" in
        */python3|*/python) PID=${process#/proc/}; break ;;
        *) PYTHON=""; STREAMER="" ;;
    esac
done
if [ -z "${PYTHON:-}" ] || [ -z "${STREAMER:-}" ]; then
    echo "no thermal-master service is running on this printer" >&2
    exit 1
fi
echo "interpreter: $PYTHON" >&2
echo "streamer:    $STREAMER" >&2
echo "process:     $PID" >&2
'
