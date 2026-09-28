# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""What this plugin is costing the printer, asked of the kernel rather than of a person.

The whole design tension of this plugin is what it costs a four core Cortex-A53 that also has a
printer to run, and until now answering that needed ssh and a shell script. The kernel already
keeps the exact figure: `/proc/self/stat` carries the total user and system time the process has
ever used, so reading it twice and dividing by the elapsed time is the share of a core it used in
between, with no sampling and nothing to install.

Two numbers, because they answer two different questions. Recent is "what is it costing me now",
which is the one that changes when a tile is opened or the camera is switched off. Since start is
"what has it cost me", which is the one that is fair about a plugin that sleeps most of the day.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

# Where the kernel keeps it. A path rather than a hardcoded read, so a test can hand over a file it
# wrote itself and this is exercised rather than mocked.
PROCESS_STAT_FILE = Path("/proc/self/stat")


# The shortest window worth dividing by. Below this the answer is mostly scheduling noise: a single
# frame lands inside it or does not, and the number swings between nothing and half a core for
# reasons that have nothing to do with what the plugin is doing.
MINIMUM_WINDOW_SECONDS = 2.0


def process_cpu_seconds(stat_file: Path) -> float | None:
    """User plus system time this process has ever used, or None where there is no such file.

    The comm field is in brackets and may hold anything, including "\\) ", so the fields are counted
    from the last closing bracket rather than by splitting the whole line. After that, user and
    system time are the twelfth and thirteenth.
    """

    try:
        line = stat_file.read_text()
    except OSError:
        return None
    try:
        fields = line[line.rindex(") ") + 2 :].split()
        ticks = int(fields[11]) + int(fields[12])
    except (ValueError, IndexError):
        return None
    return ticks / os.sysconf("SC_CLK_TCK")


class ProcessCost:
    """The plugin's own CPU cost, sampled when somebody asks and not before.

    No thread and no timer: a feature about not working in the background would be a poor place to
    start a background loop. A reading is taken on the request that asks for one, and a request
    that arrives too soon after the last is answered with the last answer rather than with a window
    too short to divide by.
    """

    def __init__(self, stat_file: Path = PROCESS_STAT_FILE) -> None:
        self._stat_file = stat_file
        self._lock = threading.Lock()
        self._cores = os.cpu_count() or 1
        self._started_at = time.monotonic()
        self._started_used = process_cpu_seconds(stat_file)
        self._sampled_at = self._started_at
        self._sampled_used = self._started_used
        self._recent: float | None = None

    def reading(self) -> dict | None:
        """Both shares and the core count, or None where the kernel does not offer this."""

        used = process_cpu_seconds(self._stat_file)
        if used is None or self._started_used is None:
            return None
        now = time.monotonic()
        with self._lock:
            window = now - self._sampled_at
            if window >= MINIMUM_WINDOW_SECONDS and self._sampled_used is not None:
                self._recent = (used - self._sampled_used) / window
                self._sampled_at = now
                self._sampled_used = used
            recent = self._recent
        alive = now - self._started_at
        since_start = (used - self._started_used) / alive if alive > 0 else None
        return {"core_share": recent, "since_start": since_start, "cores": self._cores}


def describe_cost(reading: dict | None) -> str:
    """One sentence for the settings page, since a page has nowhere to put a dictionary."""

    if reading is None:
        return "This system does not report what the plugin costs."
    share = reading.get("core_share")
    since = reading.get("since_start")
    lifetime = f"{percentage(since)} since the service started."
    if share is None:
        return f"Measuring: ask again in a few seconds. {lifetime}"
    return f"Using {percentage(share)} of one core, {lifetime}"


def percentage(share: float | None) -> str:
    """A share of one core as a percentage, or a dash where there is not one yet."""

    return "-" if share is None else f"{share * 100:.1f}%"
