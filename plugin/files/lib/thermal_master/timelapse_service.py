# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The timelapse as it runs: following the print, keeping a frame a layer, and making the clips.

Two threads. One asks Moonraker about the print, once a second while printing and every two
seconds otherwise, and takes a frame when the layer changes. The other makes clips, one at a time,
so a minute of encoding never stands between a layer change and its frame.

Nothing gathered is thrown away. A cancelled print gets its clip, a print the plugin was switched
off in the middle of gets one of what was taken, and a recording found still open when the plugin
starts, after a reboot or a power loss, gets one too, unless its print is still running, in which
case it is carried on.
"""

from __future__ import annotations

import dataclasses
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .clip import ClipInputs, make_clip
from .moonraker import JobInfo, MoonrakerClient, MoonrakerRefusedError
from .publish import Publisher, published_base
from .recording import (
    BYTES_PER_MEGABYTE,
    CAMERA_MISSING_RECORD,
    CAMERA_OFF_RECORD,
    FRAME_RECORD,
    FREE_SPACE_FLOOR_BYTES,
    LayerRecord,
    Recording,
    Retention,
    find_recording,
    free_space,
    recordings,
    remove_recording,
    scale_suffix,
)
from .timelapse import (
    INTERRUPTED,
    STOPPED,
    CapturedFrame,
    FrameTap,
    LayerReached,
    PrintEnded,
    PrintEvent,
    PrintStarted,
    PrintStatus,
    PrintTracker,
    TimelapseSettings,
)

if TYPE_CHECKING:
    from .settings import SettingsStore

POLL_WHILE_PRINTING_SECONDS = 1.0


POLL_WHILE_IDLE_SECONDS = 2.0


# How long a layer change waits for a frame before it records the camera as missing. The camera
# sends 25 a second, so a frame that has not come in three seconds is not coming.
FRAME_WAIT_SECONDS = 3.0


# How long the clip thread waits for work before looking at the shutdown flag again.
CLIP_QUEUE_POLL_SECONDS = 1.0


REFUSED_SENTENCE = (
    "Moonraker asks for a login, so the timelapse cannot follow the print. Paste Moonraker's API "
    "key below."
)


@dataclasses.dataclass(frozen=True)
class TimelapseWiring:
    """What the timelapse is connected to, gathered so it can be handed over as one thing."""

    root: Path
    client: MoonrakerClient
    settings_store: SettingsStore
    tap: FrameTap
    streaming: Callable[[], bool]
    palettes: dict
    ffmpeg: str | None
    free: Callable[[Path], int] = free_space


def carried_on_by(recording: Recording, job: JobInfo | None, status: PrintStatus) -> bool:
    """Whether a recording left open belongs to the print that is running now."""

    if job is not None and job.job_id:
        return recording.meta.get("job_id") == job.job_id
    return recording.meta.get("filename") == status.filename


class TimelapseService:
    """The timelapse's two threads, and what the settings page is told about them."""

    def __init__(self, wiring: TimelapseWiring) -> None:
        self._wiring = wiring
        self._tracker = PrintTracker()
        self._recording: Recording | None = None
        self._lock = threading.Lock()
        self._sentence = "Starting."
        self._encoding: str | None = None
        self._clip_phase = ""
        self._stopping = threading.Event()
        self._publisher = Publisher(wiring.client, self._stopping.wait)
        self._clips: queue.Queue[str] = queue.Queue()
        # What is in the queue, since a queue cannot be asked. Busy for as long as it waits, so it
        # cannot be deleted, pruned or asked for twice before its turn comes.
        self._queued: set[str] = set()
        self._settled = False
        self._low_disk = False
        self._pruned_keep: int | None = None
        self._handlers: dict[type, Callable[[PrintEvent, PrintStatus], None]] = {
            PrintStarted: self._started,
            LayerReached: self._layer,
            PrintEnded: self._ended,
        }

    @property
    def root(self) -> Path:
        return self._wiring.root

    def run(self, shutdown: threading.Event) -> None:
        """Both threads, until shutdown. The polling one is the one this is called on."""

        threading.Thread(target=self._make_clips, args=(shutdown,), daemon=True).start()
        self.queue_unmade_clips()
        while not shutdown.is_set():
            shutdown.wait(self.step())
        self._stopping.set()

    def step(self) -> float:
        """One look at the print, and how long to wait before the next one."""

        settings = self._timelapse_settings()
        self._prune_if_the_count_changed(settings)
        if not settings.timelapse:
            self._switched_off()
            return POLL_WHILE_IDLE_SECONDS
        try:
            status = self._wiring.client.print_status()
        except MoonrakerRefusedError:
            self._say(REFUSED_SENTENCE)
            return POLL_WHILE_IDLE_SECONDS
        if status is None:
            self._say(f"Moonraker is not answering at {self._wiring.client.base_url}.")
            return POLL_WHILE_IDLE_SECONDS
        self._settle_open_recordings(status)
        for event in self._tracker.update(status):
            self._handlers[type(event)](event, status)
        self._say(self._describe(status))
        return POLL_WHILE_PRINTING_SECONDS if status.active else POLL_WHILE_IDLE_SECONDS

    def status_line(self) -> str:
        with self._lock:
            sentence, encoding, phase = self._sentence, self._encoding, self._clip_phase
        return f"{sentence} {phase}" if encoding else sentence

    def summaries(self) -> list[dict]:
        busy = self._busy()
        return [
            {**recording.summary(), "busy": recording.recording_id in busy}
            for recording in recordings(self.root)
        ]

    def delete(self, recording_id: str) -> bool:
        """Delete one print's timelapse, unless it is being recorded or made into a clip."""

        recording = find_recording(self.root, recording_id)
        if recording is None or recording_id in self._busy():
            return False
        self._remove(recording)
        return True

    def remake(self, recording_id: str) -> bool:
        """Make a finished print's clip again from its temperatures, with the settings of now.

        For the test/color-bar branch, where one print is made into a clip with each scale in turn.
        Queued rather than made here, since a clip is a minute of work and this is a request.
        """

        recording = find_recording(self.root, recording_id)
        if recording is None or recording.printing or not recording.has_frames:
            return False
        if recording_id in self._busy():
            return False
        self._queue(recording_id)
        return True

    def queue_unmade_clips(self) -> None:
        """Every finished recording that has temperatures, no clip, and no reason it cannot."""

        for recording in recordings(self.root):
            unmade = not recording.printing and recording.has_frames and not recording.has_clip
            if unmade and not (recording.meta.get("clip") or {}).get("error"):
                self._queue(recording.recording_id)

    def make_clip_now(self, recording_id: str) -> None:
        """Make one clip on the calling thread. The clip thread's work, and a test's."""

        recording = find_recording(self.root, recording_id)
        if recording is None:
            return
        with self._lock:
            self._encoding = recording_id
            self._queued.discard(recording_id)
        try:
            outcome = self._make_and_publish(recording)
        finally:
            with self._lock:
                self._encoding = None
        recording.note_clip(outcome)
        self.prune()

    def _make_and_publish(self, recording: Recording) -> dict:
        """The clip, and its copy on the Timelapse page when there is one to go on.

        The firmware's clip is waited for first, when the printer makes them, so that the name
        can follow it and the two encodes never run at once.
        """

        publishing = recording.has_frames and self._publisher.available()
        base = published_base(recording) if publishing else None
        if publishing and base is None:
            self._set_phase("Waiting for the printer's own clip of this print before making ours.")
            base = self._publisher.base_name(recording)
        self._set_phase("Making a clip now.")
        outcome = make_clip(recording, self._clip_inputs())
        if base is not None and not outcome.get("error"):
            # The copies from before first: made with another scale, they have another name.
            self._publisher.unpublish(recording)
            outcome["published"] = self._publisher.publish(
                recording, base, scale_suffix(outcome.get("scale"))
            )
        return outcome

    def _queue(self, recording_id: str) -> None:
        with self._lock:
            self._queued.add(recording_id)
        self._clips.put(recording_id)

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._clip_phase = phase

    def _remove(self, recording: Recording) -> None:
        """A whole print: its copies on the Timelapse page first, then its folder."""

        self._publisher.unpublish(recording)
        remove_recording(recording)

    def prune(self) -> None:
        keep = self._timelapse_settings().timelapse_keep
        Retention(
            self.root, keep, frozenset(self._busy()), self._wiring.free, self._remove
        ).prune()

    def _make_clips(self, shutdown: threading.Event) -> None:
        while not shutdown.is_set():
            try:
                recording_id = self._clips.get(timeout=CLIP_QUEUE_POLL_SECONDS)
            except queue.Empty:
                continue
            self.make_clip_now(recording_id)

    def _busy(self) -> set[str]:
        with self._lock:
            busy = {self._encoding} if self._encoding else set()
            busy |= self._queued
        if self._recording is not None:
            busy.add(self._recording.recording_id)
        return busy

    def _timelapse_settings(self) -> TimelapseSettings:
        return self._wiring.settings_store.timelapse_snapshot()

    def _clip_inputs(self) -> ClipInputs:
        _, palette_name, live = self._wiring.settings_store.snapshot()
        return ClipInputs(
            self._wiring.palettes[palette_name], live, self._timelapse_settings(),
            self._wiring.ffmpeg,
        )

    def _say(self, sentence: str) -> None:
        with self._lock:
            self._sentence = sentence

    def _prune_if_the_count_changed(self, settings: TimelapseSettings) -> None:
        if settings.timelapse_keep == self._pruned_keep:
            return
        self._pruned_keep = settings.timelapse_keep
        self.prune()

    def _switched_off(self) -> None:
        """Off on the settings page: a print being recorded keeps what it has and gets its clip."""

        if self._recording is not None:
            self._close(self._recording, STOPPED)
        self._tracker = PrintTracker()
        self._say("Off. Switch it on to record one frame per layer of every print.")

    def _settle_open_recordings(self, status: PrintStatus) -> None:
        """Once, at the first answer: recordings left open by a reboot, with no print running."""

        if self._settled:
            return
        self._settled = True
        if status.active:
            return
        for recording in recordings(self.root):
            if recording.printing:
                self._close(recording, INTERRUPTED)

    def _started(self, _event: PrintEvent, status: PrintStatus) -> None:
        job = self._newest_job()
        left_open = [recording for recording in recordings(self.root) if recording.printing]
        carried_on = next((rec for rec in left_open if carried_on_by(rec, job, status)), None)
        for recording in left_open:
            if recording is not carried_on:
                self._close(recording, INTERRUPTED)
        self._recording = carried_on or Recording.create(
            self.root,
            job.started_at if job is not None and job.started_at else time.time(),
            job.job_id if job is not None else None,
            status.filename,
        )

    def _layer(self, event: PrintEvent, _status: PrintStatus) -> None:
        if isinstance(event, LayerReached):
            self._take(event.layer)

    def _ended(self, event: PrintEvent, _status: PrintStatus) -> None:
        recording = self._recording
        if recording is None or not isinstance(event, PrintEnded):
            return
        # The layer change G-code runs at the start of each layer, so the last one never has a
        # frame of its own. This is it: the part as it was finished, or as it was left.
        if recording.meta.get("frames"):
            self._take(int(recording.meta.get("last_layer") or 0) + 1)
        self._close(recording, event.state)

    def _close(self, recording: Recording, state: str) -> None:
        recording.finish(state)
        if self._recording is recording:
            self._recording = None
        self._queue(recording.recording_id)

    def _newest_job(self) -> JobInfo | None:
        try:
            return self._wiring.client.newest_job()
        except MoonrakerRefusedError:
            return None

    def _take(self, layer: int) -> None:
        recording = self._recording
        if recording is None:
            return
        self._low_disk = self._wiring.free(self.root) < FREE_SPACE_FLOOR_BYTES
        if self._low_disk:
            return
        self._wiring.tap.request()
        frame = self._wiring.tap.collect(FRAME_WAIT_SECONDS)
        recording.append(self._record(layer, frame))

    def _record(self, layer: int, frame: CapturedFrame | None) -> LayerRecord:
        if frame is not None:
            return LayerRecord(layer, FRAME_RECORD, frame.gain, frame.taken_at, frame.counts)
        why = CAMERA_MISSING_RECORD if self._wiring.streaming() else CAMERA_OFF_RECORD
        return LayerRecord(layer, why, None, time.time())

    def _describe(self, status: PrintStatus) -> str:
        recording = self._recording
        if recording is None or not status.active:
            return "On. Waiting for a print to start."
        if self._low_disk:
            floor = FREE_SPACE_FLOOR_BYTES // BYTES_PER_MEGABYTE
            return f"Not recording: the printer's disk has less than {floor} MB free."
        name = status.filename or "the print"
        if status.current_layer is None:
            return (
                f"Printing {name}, but the slicer is not sending layer numbers, so no frame has "
                "been taken. The plugin's README says what to add to the slicer."
            )
        of_total = f" of {status.total_layer}" if status.total_layer else ""
        frames = int(recording.meta.get("frames") or 0)
        return f"Recording {name}: layer {status.current_layer}{of_total}, {frames} frames so far."
