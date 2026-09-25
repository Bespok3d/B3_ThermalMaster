# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The plugin's log, written from one place so that every line says when it was written.

The daemon captures this process's output into `$BESPOK3D/var/log/thermal-master.log` and adds
nothing of its own, so a line that does not carry its time has none. F-72 is what that cost: a
column of capture errors that could not be tied to the freezes they might have explained.

Every line carries two clocks. UTC, with the `Z` saying so, because the printer keeps UTC and the
laptop reading the log usually does not. And the seconds since boot, because that is what `dmesg`
counts in, near enough on a machine that never sleeps, so a capture error can be set beside a USB
reset in the kernel log without converting anything.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

# Who wrote the line. Everything on the printer logs somewhere in common, so the first half says it
# came from a Bespok3d plugin and the second says which one. Lower case, as in the daemon's own
# paths, and the plugin's name is still followed by the colon anything grepping for it expects.
LOG_ORIGIN = "bespok3d/thermal-master"


def log_line_text(message: str, written_at: datetime, seconds_since_boot: float) -> str:
    """One line of the log, for a moment given rather than taken, so that it can be tested."""

    utc = written_at.astimezone(timezone.utc)
    stamp = utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
    return f"{stamp} up {seconds_since_boot:.2f} {LOG_ORIGIN}: {message}"


def log_line(message: str) -> None:
    """Write one line to the plugin's log, stamped with now."""

    stamped = log_line_text(message, datetime.now(timezone.utc), time.monotonic())
    print(stamped, file=sys.stderr, flush=True)
