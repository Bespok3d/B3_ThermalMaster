# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A finished clip copied into Moonraker's `timelapse` folder, for the Timelapse page (Phase 9).

Fluidd and Mainsail both list that folder on their Timelapse page, with the `.jpg` of the same name
as each clip's thumbnail. Moonraker has the folder when something provides its timelapse component:
Bespok3d's `timelapse` plugin on the U1, `moonraker-timelapse` on mainline Klipper. Without it
there is no page to appear on, and the clip stays on the settings page, which is where it always is.

The copy goes through Moonraker's own upload, so the plugin needs no path and every open page is
told the file is there. It is named after the firmware's clip for the same print where there is
one, with `_thermal` added, so the two sort together: the U1 names its clips after the G-code and
the print's start in UTC, to the second, and a clip whose stamp is within a couple of seconds of
the recording's start is that print's.
"""

from __future__ import annotations

import calendar
import re
import time
from collections.abc import Callable

from .log import log_line
from .moonraker import MoonrakerClient, MoonrakerRefusedError
from .recording import BYTES_PER_MEGABYTE, FREE_SPACE_FLOOR_BYTES, Recording

TIMELAPSE_ROOT = "timelapse"


THERMAL_SUFFIX = "_thermal"


# A clip the U1's firmware made: anything, then the start as fourteen digits. Its thumbnail and
# cover are `.jpg`, so only the `.mp4` is matched, and never one of ours.
FIRMWARE_CLIP_PATTERN = re.compile(r"^(?P<base>.*_(?P<stamp>\d{14}))\.mp4$")


# The firmware writes its clip three to five minutes after a print ends, so the plugin waits for it
# before making its own, both for the name and so that the two encodes never share the processor.
FIRMWARE_WAIT_SECONDS = 600.0


FIRMWARE_POLL_SECONDS = 15.0


# The firmware's stamp and Moonraker's record of the start agreed to the second on the U1; two
# seconds allows for a clock read on either side of the second boundary.
STAMP_TOLERANCE_SECONDS = 2.0


def stamp_seconds(stamp: str) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(stamp, "%Y%m%d%H%M%S")))
    except ValueError:
        return None


def firmware_clips(names: list[str]) -> dict[str, float]:
    """Every clip in the folder the firmware made, by its base name, with its start."""

    found: dict[str, float] = {}
    for name in names:
        match = FIRMWARE_CLIP_PATTERN.match(name)
        if match is None or THERMAL_SUFFIX in match.group("base"):
            continue
        started = stamp_seconds(match.group("stamp"))
        if started is not None:
            found[match.group("base")] = started
    return found


def firmware_base_for(names: list[str], started_at: float) -> str | None:
    """The base name of the firmware's clip of the print that started then, if it is there."""

    return next(
        (
            base
            for base, started in firmware_clips(names).items()
            if abs(started - started_at) <= STAMP_TOLERANCE_SECONDS
        ),
        None,
    )


class Publisher:
    """Copies clips into Moonraker's timelapse folder, and takes them out again."""

    def __init__(
        self,
        client: MoonrakerClient,
        wait: Callable[[float], bool],
        patience: float = FIRMWARE_WAIT_SECONDS,
    ) -> None:
        self._client = client
        # Returns True when the service is stopping, which ends a wait early.
        self._wait = wait
        self._patience = patience

    def available(self) -> bool:
        try:
            return self._client.has_root(TIMELAPSE_ROOT)
        except MoonrakerRefusedError:
            return False

    def base_name(self, recording: Recording) -> str:
        """The firmware's name for this print when it makes clips, waited for; ours otherwise.

        Only waited for on a printer whose folder already holds clips the firmware made. On
        mainline Klipper nothing ever will, and a clip ten minutes late for a name that is never
        coming would be ten minutes for nothing.
        """

        names = self._names()
        if not firmware_clips(names):
            return recording.base_name
        deadline = time.monotonic() + self._patience
        while (found := firmware_base_for(names, recording.started_at)) is None:
            if time.monotonic() >= deadline or self._wait(FIRMWARE_POLL_SECONDS):
                return recording.base_name
            names = self._names()
        return found

    def publish(self, recording: Recording, base: str) -> dict:
        """Copy the clip and its thumbnail in, unless the disk they would go to is short of room."""

        files = [(f"{base}{THERMAL_SUFFIX}.mp4", recording.clip_path)]
        if recording.thumbnail_path.is_file():
            files.append((f"{base}{THERMAL_SUFFIX}.jpg", recording.thumbnail_path))
        size = sum(path.stat().st_size for _, path in files)
        try:
            free = self._client.free_space(TIMELAPSE_ROOT)
            if free is not None and free - size < FREE_SPACE_FLOOR_BYTES:
                floor = FREE_SPACE_FLOOR_BYTES // BYTES_PER_MEGABYTE
                return {"error": f"Not copied to the Timelapse page: less than {floor} MB free."}
            copied = [
                name for name, path in files if self._client.upload(TIMELAPSE_ROOT, name, path)
            ]
        except MoonrakerRefusedError:
            return {"error": "Not copied to the Timelapse page: Moonraker asks for a login."}
        if not copied:
            return {"error": "Not copied to the Timelapse page: Moonraker did not take it."}
        log_line(f"timelapse: {recording.recording_id} is on the Timelapse page as {copied[0]}")
        return {"root": TIMELAPSE_ROOT, "files": copied}

    def unpublish(self, recording: Recording) -> None:
        """Take this print's copies out of the folder. One somebody deleted already is fine."""

        for name in recording.published:
            try:
                self._client.delete_file(TIMELAPSE_ROOT, name)
            except MoonrakerRefusedError:
                return

    def _names(self) -> list[str]:
        try:
            return self._client.file_names(TIMELAPSE_ROOT) or []
        except MoonrakerRefusedError:
            return []
