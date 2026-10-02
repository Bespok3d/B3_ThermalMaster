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
import functools
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .camera import GAIN_HIGH, GAIN_LOW
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
    gain_switch_sentence,
    named_suffix,
    recordings,
    remove_recording,
)
from .temperature import raw_for_celsius
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


@functools.lru_cache(maxsize=4)
def threshold_counts(celsius: float, emissivity: float) -> float:
    """The raw count the gain switch waits for: the temperature, as the readout would show it.

    Cached, because the conversion is a search through the driver's correction and the switch is
    armed again at every look at the print.
    """

    return raw_for_celsius(celsius, emissivity)

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
    # The camera's gain for this print, which wins over the stored one: the device controller's
    # `override_gain`. Nothing by default, for a service with no camera behind it.
    override_gain: Callable[[str | None], None] = lambda _gain: None


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
        # Set on the capture thread by the frame tap when something passes the threshold, and
        # noted on the recording by the next look at the print.
        self._hot_at: float | None = None
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
        self._note_firmware(status)
        self._switch_gain_when_hot(status)
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

        The settings page's "Make the clip again with the current colours": the palette, colour
        scale or clip readout changed since, or a scale to compare. Queued rather than made here,
        since a clip is a minute of work and this is a request.
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
            outcome["published"] = self._publisher.publish(recording, base, named_suffix(outcome))
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

    def _note_firmware(self, status: PrintStatus) -> None:
        """Whether the firmware is making its own clip, read during the print and not after it.

        After it, both fields have gone false on a U1 whether or not there was a clip, so only an
        answer given while printing means anything.
        """

        recording = self._recording
        if recording is None or not status.active or status.firmware_timelapse is None:
            return
        recording.note_firmware_timelapse(status.firmware_timelapse)

    def _switch_gain_when_hot(self, status: PrintStatus) -> None:
        """The automatic switch to wide range: watched for while a print is recorded, one way.

        The tap does the watching, on every frame; this arms it, notes a switch it saw on the
        recording, and keeps the camera in wide range for a print noted as switched, which also
        puts it back after the plugin restarts in the middle of one.
        """

        recording = self._recording
        # Read and left: cleared when the print closes, so a switch seen between this read and
        # a clear could not be lost.
        hot_at = self._hot_at
        settings = self._timelapse_settings()
        if recording is None:
            self._wiring.tap.arm(None)
            return
        if hot_at is not None and recording.gain_switch is None:
            recording.note_gain_switch(
                status.current_layer, hot_at, settings.timelapse_auto_gain_celsius
            )
        if recording.gain_switch is not None:
            self._wiring.tap.arm(None)
            self._wiring.override_gain(GAIN_LOW)
        elif settings.timelapse_auto_gain:
            threshold = threshold_counts(settings.timelapse_auto_gain_celsius, self._emissivity())
            self._wiring.tap.arm(threshold, GAIN_HIGH, self._hot)
        else:
            self._wiring.tap.arm(None)

    def _hot(self) -> None:
        """The tap saw something past the threshold. Called on the capture thread, so it only
        asks: the camera is switched between two frames, and the switch is noted by `step`.
        """

        self._wiring.override_gain(GAIN_LOW)
        self._hot_at = time.time()

    def _emissivity(self) -> float:
        return self._wiring.settings_store.snapshot()[2].emissivity

    def _close(self, recording: Recording, state: str) -> None:
        recording.finish(state)
        if self._recording is recording:
            self._recording = None
            # The print is over, so is its gain: the camera goes back to the one chosen.
            self._wiring.tap.arm(None)
            self._wiring.override_gain(None)
            self._hot_at = None
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
        return (
            f"Recording {name}: layer {status.current_layer}{of_total}, {frames} frames so far."
            + gain_switch_sentence(recording.gain_switch)
        )
