# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""A print's timelapse on the disk: its temperatures, its clip, and what is kept (Phase 9).

One folder per print, named for when it started, holding `meta.json`, the layers as they were
measured in `frames.bin`, and once the print has ended `clip.mp4` and `thumbnail.jpg`. The folder
is the unit of everything: it is what the settings page lists, what a person deletes and what the
count of prints to keep counts.

The temperatures are appended one layer at a time and flushed to the disk as they are written,
because the case this has to survive is the printer losing power mid-print: everything written
before the cut is read back, and a record the cut tore in half is ignored.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import struct
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO

import numpy as np

from .camera import GAIN_HIGH, GAIN_LOW
from .log import log_line
from .timelapse import PRINTING

META_FILE = "meta.json"


FRAMES_FILE = "frames.bin"


CLIP_FILE = "clip.mp4"


THUMBNAIL_FILE = "thumbnail.jpg"


META_FORMAT = 1


# One record per layer: a fixed header and then, for a real frame, the raw counts. Little endian
# throughout, and the magic on every record rather than once per file, so a reader can tell a torn
# tail from a file it does not understand.
RECORD_MAGIC = b"TLR1"


RECORD_HEADER = struct.Struct("<4sIBBHHd")


# What a record holds: a frame, or the reason there is none. A layer passed without a frame still
# gets a record, so the clip keeps its length and shows where the camera was lost.
FRAME_RECORD = 0


CAMERA_MISSING_RECORD = 1


CAMERA_OFF_RECORD = 2


RECORD_KINDS = (FRAME_RECORD, CAMERA_MISSING_RECORD, CAMERA_OFF_RECORD)


# The gain each frame was taken in. Not for the conversion, which is the same in both gains, but
# because the two disagree about cool things, so a reading from a clip is only understood with its
# gain beside it.
GAIN_CODES = {GAIN_HIGH: 0, GAIN_LOW: 1}


UNKNOWN_GAIN_CODE = 255


# A folder name the plugin made, and so the only kind a request may name. Anything else is refused
# before it is joined to a path.
RECORDING_ID_PATTERN = re.compile(r"^\d{8}-\d{6}(-[0-9A-Za-z]+)?$")


# Temperatures are kept for the newest prints only: enough to render a clip again with another
# range, which is what the colour range comparison needs, without keeping 38 MB for every one of
# ten 1,000 layer prints.
KEEP_TEMPERATURES_FOR = 2


# Below this much free space on the disk the recordings are on, the oldest temperatures go first,
# then the oldest clips, and no new frame is kept until there is room. It overrides the count of
# prints to keep, because the printer's free space matters more than this plugin's own history.
BYTES_PER_MEGABYTE = 1024 * 1024


FREE_SPACE_FLOOR_BYTES = 200 * BYTES_PER_MEGABYTE


def free_space(folder: Path) -> int:
    return shutil.disk_usage(folder).free


@dataclasses.dataclass(frozen=True)
class LayerRecord:
    """One layer as it was measured, or the reason it was not."""

    layer: int
    kind: int
    gain: str | None
    taken_at: float
    counts: np.ndarray | None = None


def gain_code(gain: str | None) -> int:
    return GAIN_CODES.get(gain or "", UNKNOWN_GAIN_CODE)


def gain_for_code(code: int) -> str | None:
    return next((gain for gain, known in GAIN_CODES.items() if known == code), None)


def encode_record(record: LayerRecord) -> bytes:
    height, width = record.counts.shape if record.counts is not None else (0, 0)
    header = RECORD_HEADER.pack(
        RECORD_MAGIC, record.layer, record.kind, gain_code(record.gain), width, height,
        record.taken_at,
    )
    if record.counts is None:
        return header
    return header + np.ascontiguousarray(record.counts, dtype="<u2").tobytes()


def read_record(source: BinaryIO) -> LayerRecord | None:
    """The next record, or None at the end of the file or at a record the end cut short."""

    header = source.read(RECORD_HEADER.size)
    if len(header) < RECORD_HEADER.size:
        return None
    magic, layer, kind, code, width, height, taken_at = RECORD_HEADER.unpack(header)
    if magic != RECORD_MAGIC or kind not in RECORD_KINDS:
        return None
    counts = None
    if width and height:
        payload = source.read(width * height * 2)
        if len(payload) < width * height * 2:
            return None
        counts = np.frombuffer(payload, dtype="<u2").reshape(height, width).astype(np.uint16)
    return LayerRecord(layer, kind, gain_for_code(code), taken_at, counts)


def folder_name(started_at: float, job_id: str | None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(started_at))
    safe_job = re.sub(r"[^0-9A-Za-z]", "", job_id or "")
    return f"{stamp}-{safe_job}" if safe_job else stamp


def write_json_atomically(path: Path, content: dict) -> None:
    """Written beside and moved into place, so a cut mid-write leaves the old file whole."""

    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(content, indent=2))
    os.replace(partial, path)


class Recording:
    """One print's folder, and everything said about it."""

    def __init__(self, folder: Path, meta: dict) -> None:
        self.folder = folder
        self.meta = meta

    @classmethod
    def create(cls, root: Path, started_at: float, job_id: str | None, filename: str) -> Recording:
        folder = root / folder_name(started_at, job_id)
        folder.mkdir(parents=True, exist_ok=True)
        recording = cls(
            folder,
            {
                "format": META_FORMAT,
                "job_id": job_id,
                "filename": filename,
                "started_at": started_at,
                "ended_at": None,
                "state": PRINTING,
                "frames": 0,
                "last_layer": None,
                "clip": None,
            },
        )
        recording.save()
        return recording

    @classmethod
    def open(cls, folder: Path) -> Recording | None:
        """A folder's recording, or None for a folder that is not one of ours or is unreadable."""

        if not RECORDING_ID_PATTERN.match(folder.name):
            return None
        try:
            meta = json.loads((folder / META_FILE).read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("format") != META_FORMAT:
            return None
        return cls(folder, meta)

    @property
    def recording_id(self) -> str:
        return self.folder.name

    @property
    def state(self) -> str:
        return str(self.meta.get("state"))

    @property
    def printing(self) -> bool:
        return self.state == PRINTING

    @property
    def started_at(self) -> float:
        return float(self.meta.get("started_at") or 0.0)

    @property
    def frames_path(self) -> Path:
        return self.folder / FRAMES_FILE

    @property
    def clip_path(self) -> Path:
        return self.folder / CLIP_FILE

    @property
    def thumbnail_path(self) -> Path:
        return self.folder / THUMBNAIL_FILE

    @property
    def has_frames(self) -> bool:
        return self.frames_path.is_file()

    @property
    def has_clip(self) -> bool:
        return self.clip_path.is_file()

    @property
    def base_name(self) -> str:
        """The print's name and its start in UTC, the way the U1's firmware names its own clips."""

        stem = Path(str(self.meta.get("filename") or "print")).stem
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(self.started_at))
        return f"{stem}_{stamp}"

    @property
    def firmware_timelapse(self) -> bool | None:
        """Whether the printer was seen making its own clip of this print, if it could say."""

        seen = self.meta.get("firmware_timelapse")
        return seen if isinstance(seen, bool) else None

    def note_firmware_timelapse(self, seen: bool) -> None:
        """Once true, true for good: a print that was being recorded by the firmware still was."""

        noted = self.firmware_timelapse is True or seen
        if self.firmware_timelapse != noted:
            self.meta["firmware_timelapse"] = noted
            self.save()

    @property
    def clip_scale(self) -> str | None:
        """The colour scale the clip was made with, on the test/color-bar branch."""

        scale = (self.meta.get("clip") or {}).get("scale")
        return str(scale) if scale else None

    @property
    def clip_name(self) -> str:
        """What the clip is called when it leaves the printer."""

        return f"{self.base_name}_thermal{named_suffix(self.meta.get('clip') or {})}.mp4"

    @property
    def published(self) -> list[str]:
        """The copies put in Moonraker's `timelapse` folder, by name, so they can be taken out."""

        clip = self.meta.get("clip") or {}
        names = (clip.get("published") or {}).get("files") or []
        return [str(name) for name in names]

    def save(self) -> None:
        write_json_atomically(self.folder / META_FILE, self.meta)

    def append(self, record: LayerRecord) -> None:
        with self.frames_path.open("ab") as frames:
            frames.write(encode_record(record))
            frames.flush()
            os.fsync(frames.fileno())
        self.meta["frames"] = int(self.meta.get("frames") or 0) + 1
        self.meta["last_layer"] = record.layer
        self.save()

    def records(self) -> Iterator[LayerRecord]:
        if not self.has_frames:
            return
        with self.frames_path.open("rb") as frames:
            while (record := read_record(frames)) is not None:
                yield record

    def finish(self, state: str) -> None:
        self.meta["state"] = state
        self.meta["ended_at"] = time.time()
        self.save()

    def note_clip(self, outcome: dict) -> None:
        self.meta["clip"] = outcome
        self.save()

    def drop_frames(self) -> None:
        self.frames_path.unlink(missing_ok=True)

    def summary(self) -> dict:
        """What the settings page and `/timelapses` say about it."""

        clip = self.meta.get("clip") or {}
        return {
            "id": self.recording_id,
            "name": self.clip_name,
            "filename": self.meta.get("filename"),
            "started_at": self.started_at,
            "state": self.state,
            "frames": int(self.meta.get("frames") or 0),
            "has_clip": self.has_clip,
            "clip_bytes": self.clip_path.stat().st_size if self.has_clip else 0,
            "has_frames": self.has_frames,
            "error": clip.get("error"),
            "published_as": self.published[0] if self.published else None,
            "publish_error": (clip.get("published") or {}).get("error"),
            "scale": self.clip_scale,
        }


def named_suffix(outcome: dict) -> str:
    """The scale on the end of a clip's name, when the clip was made with it asked for there."""

    scale = outcome.get("scale")
    return f"_{scale}" if scale and outcome.get("scale_in_name") else ""


def recordings(root: Path) -> list[Recording]:
    """Every recording under the folder, newest first."""

    if not root.is_dir():
        return []
    found = [Recording.open(folder) for folder in root.iterdir() if folder.is_dir()]
    return sorted(
        (recording for recording in found if recording is not None),
        key=lambda recording: recording.started_at,
        reverse=True,
    )


def find_recording(root: Path, recording_id: str) -> Recording | None:
    """A recording named by a request, checked against the pattern before any path is made."""

    if not RECORDING_ID_PATTERN.match(recording_id):
        return None
    folder = root / recording_id
    return Recording.open(folder) if folder.is_dir() else None


def remove_recording(recording: Recording) -> None:
    shutil.rmtree(recording.folder, ignore_errors=True)
    log_line(f"timelapse: removed {recording.recording_id}")


@dataclasses.dataclass
class Retention:
    """What is kept: a count of prints, temperatures for the newest, and a floor under all of it."""

    root: Path
    keep: int
    busy: frozenset[str] = frozenset()
    free: Callable[[Path], int] = free_space
    # How a whole print is removed: by default its folder, and in the service its copies in
    # Moonraker's timelapse folder first.
    remove: Callable[[Recording], None] = remove_recording

    def prune(self) -> None:
        kept = [recording for recording in recordings(self.root) if not self._busy(recording)]
        for recording in kept[self.keep:]:
            self.remove(recording)
        for recording in kept[KEEP_TEMPERATURES_FOR:self.keep]:
            if recording.has_clip:
                recording.drop_frames()
        self._keep_the_floor(kept[: self.keep])

    def _busy(self, recording: Recording) -> bool:
        return recording.printing or recording.recording_id in self.busy

    def _keep_the_floor(self, kept: list[Recording]) -> None:
        """Oldest temperatures first, then oldest whole prints, until there is room again."""

        oldest_first = list(reversed(kept))
        for recording in oldest_first:
            if not self.below_floor():
                return
            if recording.has_clip:
                recording.drop_frames()
        for recording in oldest_first:
            if not self.below_floor():
                return
            self.remove(recording)

    def below_floor(self) -> bool:
        return self.free(self.root) < FREE_SPACE_FLOOR_BYTES
