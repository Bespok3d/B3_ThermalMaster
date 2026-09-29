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
import urllib.request
from collections.abc import Callable

from .timelapse import PrintStatus

DEFAULT_MOONRAKER_URL = "http://127.0.0.1:7125"


# Short, because the question is asked again a second later and a slow answer is worth less than
# the next one.
MOONRAKER_TIMEOUT_SECONDS = 2.0


REFUSED_STATUSES = (401, 403)


PRINT_STATS_QUERY = "/printer/objects/query?print_stats=state,filename,info"


NEWEST_JOB_QUERY = "/server/history/list?limit=1&order=desc"


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
    stats = answer.get("result", {}).get("status", {}).get("print_stats")
    if not isinstance(stats, dict):
        return None
    info = stats.get("info") or {}
    return PrintStatus(
        state=str(stats.get("state") or ""),
        filename=str(stats.get("filename") or ""),
        current_layer=whole_number(info.get("current_layer")),
        total_layer=whole_number(info.get("total_layer")),
    )


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

        request = urllib.request.Request(self._base_url + path)
        key = self._api_key()
        if key:
            request.add_header("X-Api-Key", key)
        try:
            with urllib.request.urlopen(request, timeout=MOONRAKER_TIMEOUT_SECONDS) as reply:
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
