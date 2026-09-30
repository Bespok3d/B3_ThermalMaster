# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""What the timelapse asks Moonraker, over plain HTTP on the printer itself (Phase 9).

The standard library only: two small GETs a second while printing do not justify a dependency.

Moonraker answers a client on the printer without a login on the printers this was tried on, and
Bespok3d's `moonraker-auth` plugin can change that by forcing logins. A refusal is therefore its
own outcome rather than a failure among others, so the settings page can say what happened and
offer the one thing that fixes it, Moonraker's API key, instead of recording nothing in silence.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from .timelapse import PrintStatus

DEFAULT_MOONRAKER_URL = "http://127.0.0.1:7125"


# Short, because the question is asked again a second later and a slow answer is worth less than
# the next one.
MOONRAKER_TIMEOUT_SECONDS = 2.0


REFUSED_STATUSES = (401, 403)


# The two fields after print_stats are a Snapmaker's: whether its firmware is recording a timelapse
# of this print, and whether the print was started with one asked for. Klipper answers an object it
# does not have with an empty one, so on mainline they cost nothing and say nothing.
PRINT_STATS_QUERY = (
    "/printer/objects/query?print_stats=state,filename,info"
    "&timelapse=is_active&print_task_config=time_lapse_camera"
)


NEWEST_JOB_QUERY = "/server/history/list?limit=1&order=desc"


ROOTS_QUERY = "/server/files/roots"


# Uploading a clip of a few megabytes to the same machine takes longer than a status query.
UPLOAD_TIMEOUT_SECONDS = 60.0


class MoonrakerRefusedError(Exception):
    """Moonraker answered, and the answer was that a login is needed."""


@dataclasses.dataclass(frozen=True)
class JobInfo:
    """Moonraker's record of a print: what names a recording and tells one print from another."""

    job_id: str
    filename: str
    started_at: float


def whole_number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parsed_print_status(answer: dict) -> PrintStatus | None:
    status = answer.get("result", {}).get("status", {})
    stats = status.get("print_stats")
    if not isinstance(stats, dict):
        return None
    info = stats.get("info") or {}
    return PrintStatus(
        state=str(stats.get("state") or ""),
        filename=str(stats.get("filename") or ""),
        current_layer=whole_number(info.get("current_layer")),
        total_layer=whole_number(info.get("total_layer")),
        firmware_timelapse=firmware_timelapse(status),
    )


def firmware_timelapse(status: dict) -> bool | None:
    """Whether the printer's firmware is recording its own timelapse, or None where it cannot say.

    Either field will do. Read on a U1 on 2026-09-30, both were true for the whole of a print
    started with the timelapse ticked and false for one without, and both went false once the
    print ended. `is_active` is the one that says what the firmware is doing rather than what was
    asked, which matters on a printer where a macro forces the timelapse on.
    """

    active = (status.get("timelapse") or {}).get("is_active")
    asked = (status.get("print_task_config") or {}).get("time_lapse_camera")
    known = [value for value in (active, asked) if isinstance(value, bool)]
    return any(known) if known else None


def parsed_newest_job(answer: dict) -> JobInfo | None:
    jobs = answer.get("result", {}).get("jobs")
    if not isinstance(jobs, list) or not jobs or not isinstance(jobs[0], dict):
        return None
    job = jobs[0]
    return JobInfo(
        job_id=str(job.get("job_id") or ""),
        filename=str(job.get("filename") or ""),
        started_at=float(job.get("start_time") or 0.0),
    )


class MoonrakerClient:
    """A read of Moonraker's HTTP API, with the key sent when there is one."""

    def __init__(self, base_url: str, api_key: Callable[[], str] = lambda: "") -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def base_url(self) -> str:
        return self._base_url

    def get(self, path: str) -> dict | None:
        """The decoded answer, None when Moonraker cannot be reached, or a refusal raised."""

        return self.send(urllib.request.Request(self._base_url + path))

    def send(
        self, request: urllib.request.Request, timeout: float = MOONRAKER_TIMEOUT_SECONDS
    ) -> dict | None:
        """Any request, with the key when there is one, answered the way `get` describes."""

        key = self._api_key()
        if key:
            request.add_header("X-Api-Key", key)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                answer = json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in REFUSED_STATUSES:
                raise MoonrakerRefusedError(f"Moonraker answered {error.code}") from error
            return None
        except (OSError, ValueError):
            return None
        return answer if isinstance(answer, dict) else None

    def print_status(self) -> PrintStatus | None:
        answer = self.get(PRINT_STATS_QUERY)
        return parsed_print_status(answer) if answer is not None else None

    def newest_job(self) -> JobInfo | None:
        """The newest print in Moonraker's history, which during a print is that print."""

        answer = self.get(NEWEST_JOB_QUERY)
        return parsed_newest_job(answer) if answer is not None else None

    def has_root(self, root: str) -> bool:
        """Whether Moonraker serves a folder of this name, as Fluidd and Mainsail would see it."""

        answer = self.get(ROOTS_QUERY)
        roots = (answer or {}).get("result")
        return isinstance(roots, list) and any(
            isinstance(entry, dict) and entry.get("name") == root for entry in roots
        )

    def file_names(self, root: str) -> list[str] | None:
        """Every file in a root, by the path Moonraker gives it, or None without an answer."""

        answer = self.get(f"/server/files/list?root={urllib.parse.quote(root)}")
        files = (answer or {}).get("result")
        if not isinstance(files, list):
            return None
        return [str(entry.get("path")) for entry in files if isinstance(entry, dict)]

    def free_space(self, root: str) -> int | None:
        """The free space on the disk a root is on, as Moonraker reports it with a listing."""

        answer = self.get(f"/server/files/directory?path={urllib.parse.quote(root)}")
        usage = (answer or {}).get("result", {}).get("disk_usage")
        free = usage.get("free") if isinstance(usage, dict) else None
        return int(free) if isinstance(free, (int, float)) else None

    def upload(self, root: str, name: str, source: Path) -> bool:
        """Put a file into a root through Moonraker, which then tells every page it is there."""

        boundary = f"thermal-master-{uuid.uuid4().hex}"
        body = b"".join(
            [
                f'--{boundary}\r\nContent-Disposition: form-data; name="root"\r\n\r\n'
                f"{root}\r\n".encode(),
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f'filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
                source.read_bytes(),
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        request = urllib.request.Request(
            self._base_url + "/server/files/upload",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return self.send(request, UPLOAD_TIMEOUT_SECONDS) is not None

    def delete_file(self, root: str, name: str) -> bool:
        """Remove a file from a root; False when Moonraker would not, or it was already gone."""

        quoted = urllib.parse.quote(f"{root}/{name}")
        request = urllib.request.Request(
            self._base_url + f"/server/files/{quoted}", method="DELETE"
        )
        return self.send(request) is not None
