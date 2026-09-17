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
# Sets PID, PYTHON and STREAMER on the printer, and says what it found on stderr. It reads no
# standard input, which is what lets a caller pipe a python script through it.
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
