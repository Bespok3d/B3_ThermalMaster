# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Every line of the plugin's log says when it was written, and who wrote it.

The daemon captures what this process prints and adds nothing of its own, so a line that does not
carry its time has none. On 2026-09-24 that left a column of capture errors unable to say whether
they had anything to do with the freezes being chased (F-72).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = PLUGIN_DIR / "files" / "lib" / "thermal_master"
ENTRY_SCRIPT = PLUGIN_DIR / "files" / "bin" / "thermal-master-stream.py"
STAMPING_MODULE = "log.py"

STAMPED_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z up \d+\.\d{2} bespok3d/thermal-master: "
)


def test_a_line_carries_utc_to_the_millisecond_the_uptime_and_who_wrote_it(thermal_streamer):
    written_at = datetime(2026, 9, 24, 14, 8, 54, 512345, tzinfo=timezone.utc)

    line = thermal_streamer.log_line_text("capture error: gone", written_at, 13837.4412)

    assert line == (
        "2026-09-24T14:08:54.512Z up 13837.44 bespok3d/thermal-master: capture error: gone"
    )


def test_a_local_time_is_written_as_utc(thermal_streamer):
    """The printer keeps UTC and the laptop reading the log usually does not, which is how two
    clocks got confused on 2026-09-24. The line must read the same whichever clock wrote it."""

    central_european_summer = timezone(timedelta(hours=2))
    written_at = datetime(2026, 9, 24, 16, 8, 54, 512000, tzinfo=central_european_summer)

    line = thermal_streamer.log_line_text("stopped", written_at, 1.0)

    assert line.startswith("2026-09-24T14:08:54.512Z ")


def test_a_line_goes_to_standard_error_stamped(thermal_streamer, capsys):
    thermal_streamer.log_line("stopped")

    written = capsys.readouterr()

    assert written.out == ""
    assert STAMPED_LINE.match(written.err), written.err
    assert written.err.endswith("bespok3d/thermal-master: stopped\n")


def test_nothing_reaches_the_log_except_through_the_stamping_function():
    """The daemon keeps whatever this process prints, on either stream, so anything printed any
    other way is an unstamped line. There were five of them, and a sixth would come back quietly."""

    offenders = []
    for source in [ENTRY_SCRIPT, *sorted(PACKAGE_DIR.glob("*.py"))]:
        if source.name == STAMPING_MODULE:
            continue
        for number, line in enumerate(source.read_text().splitlines(), start=1):
            if "print(" in line or "sys.stderr" in line or "sys.stdout" in line:
                offenders.append(f"{source.name}:{number}: {line.strip()}")

    assert offenders == []
