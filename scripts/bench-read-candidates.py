#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Candidates for the read half: getting a frame off the camera and the temperatures out of it.

The third bench, and unlike the other two THIS ONE OPENS THE CAMERA. Only one process can hold the
USB device, and the driver's `connect` takes the interfaces away from whoever has them, so running
this under a streaming service would pull the camera out from under it. It therefore asks the
service first and refuses unless the stream is switched off (the Stop button, which is what makes
the service release the device). Switch it back on afterwards.

Why it exists: with nobody watching, reading the camera is all the plugin does, and that costs
10.3% of a core for a P3 against 4.6% for a P1 (2026-11-12, 0.30.0). Three things in the vendored
driver's read path could be behind it, and none of them had been measured:

- `read_frame` asks for 16 KiB at a time, so a frame is many trips through pyusb and the kernel.
  Its own docstring says a frame is really two or three transfers, and that reading it that way
  "only works reliably on Linux". The printer is Linux.
- The frame is copied several times on the way: each piece into the frame buffer, the buffer into
  a new `bytes`, and that sliced again before the temperatures are copied out of it.
- `read_frame_both`, which the plugin calls, also builds the infrared picture, and the plugin
  throws it away.

Nothing here edits the driver or is wired into the plugin. Every candidate uses the camera object
the driver opened, and reads through the same `dev.read` the driver does. What proves itself goes
into the plugin beside the driver (VENDORING.md), and is what gets proposed upstream.

Every request for more than one packet is a multiple of the packet size. A bulk read that ends in
the middle of a packet is an overflow error on Linux, which is one reading of the "overflow" the
driver's docstring reports from whole frame reads on macOS.

What is compared is processor time a frame, not wall time: the camera sends a frame every 40 ms
whatever the reader does, so every candidate takes 40 ms a frame and what differs is how much of
it the processor spent working. Time the kernel spends in interrupts is charged to no thread, so
the machine's own interrupt share is printed beside each candidate as well.

Run it with `scripts/bench-read-on-printer.sh <host>`.
"""

from __future__ import annotations

import argparse
import array
import dataclasses
import json
import os
import resource
import struct
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import numpy as np

ENDPOINT_IN = 0x81
READ_TIMEOUT_MILLISECONDS = 10000
USB_PACKET_BYTES = 512
SIXTY_FOUR_KIB = 65536

# The driver's MARKER_DTYPE, as a struct: length, sync, cnt1, cnt2, cnt3.
MARKER_LAYOUT = struct.Struct("<BBIIH")
START_SYNCS = (0x8C, 0x8D)
END_SYNCS = (0x8E, 0x8F)

DEFAULT_FRAMES = 200
DEFAULT_ROUNDS = 2
# Enough for a reader that starts in the middle of a frame to find the start of one, and for the
# first cold pass through the code, before anything is timed or counted.
WARMUP_FRAMES = 5
HEALTH_TIMEOUT_SECONDS = 3.0

# How many times in a row a reader may find itself out of step before it gives up. The camera's
# own shutter puts any reader out of step for a frame or two; a reader whose idea of a frame does
# not match what this camera sends would otherwise try for ever, holding the camera as it did.
OUT_OF_STEP_LIMIT = 50


class NeverInStepError(Exception):
    """A reader's idea of how a frame arrives does not match what the camera sends."""


def load_plugin_and_driver(entry: Path):
    """The plugin's package and the vendored driver, imported the way the service imports them."""

    plugin_files = entry.resolve() if entry.is_dir() else entry.resolve().parent.parent
    sys.path.append(str(plugin_files / "lib"))
    sys.path.append(str(plugin_files / "vendor"))
    try:
        import p3_camera
        import thermal_master
    except ImportError as error:
        raise SystemExit(f"cannot import the plugin from {plugin_files}: {error}") from error
    start_allocator_as_the_service_does(thermal_master)
    return thermal_master, p3_camera


def start_allocator_as_the_service_does(streamer) -> None:
    """Ask the C library to keep freed memory, if the plugin under test does so when it starts.

    The service asks once, in `main`, which nothing here calls (F-79). It matters more here than
    in the other benches: the driver makes a new `bytes` of a whole frame for every frame, and
    what that costs is exactly what the setting changes.
    """

    ask = getattr(streamer, "keep_freed_memory", None)
    if ask is None:
        return
    kept = ask(streamer.glibc_mallopt())
    print(f"allocator: {'keeps' if kept else 'was not asked to keep'} freed memory, "
          "as the service on this machine does", file=sys.stderr)


# --------------------------------------------------------------------------------------------
# The guard: never open a camera the service is using.
# --------------------------------------------------------------------------------------------


def service_health_url(service_process_id: int, streamer) -> str:
    """Where the running service answers, read from its own command line."""

    command_line = Path(f"/proc/{service_process_id}/cmdline").read_bytes().decode()
    arguments = command_line.split("\0")

    def value_after(flag: str, default: object) -> object:
        return arguments[arguments.index(flag) + 1] if flag in arguments else default

    bind = value_after("--bind", streamer.DEFAULT_BIND)
    port = value_after("--port", streamer.DEFAULT_PORT)
    return f"http://{bind}:{port}/health"


def refuse_unless_stream_is_off(service_process_id: int, streamer) -> None:
    health_url = service_health_url(service_process_id, streamer)
    try:
        with urllib.request.urlopen(health_url, timeout=HEALTH_TIMEOUT_SECONDS) as answer:
            health = json.load(answer)
    except (OSError, ValueError) as error:
        raise SystemExit(
            f"could not ask the service whether it is streaming ({health_url}: {error}), "
            "so the camera was not opened"
        ) from error
    if health.get("streaming") is not False:
        raise SystemExit(
            "the service is streaming, so it holds the camera, and opening it here would take "
            "it away. Press Stop on the plugin's settings page first, run this again, and "
            "press Start when it has finished."
        )


# --------------------------------------------------------------------------------------------
# Temperatures out of a frame.
# --------------------------------------------------------------------------------------------


def temperatures_copied_from(frame_buffer, config) -> np.ndarray:
    """The temperature half of a frame, as an array of its own.

    `frame_buffer` begins with the start marker, as the driver's own frames do. One copy, of the
    half that is wanted: the view is made straight onto the buffer, with no slice of the whole
    frame first, and the copy is what lets the buffer be read into again.
    """

    values = np.frombuffer(
        frame_buffer,
        dtype="<u2",
        count=config.sensor_h * config.sensor_w,
        offset=MARKER_LAYOUT.size + 2 * config.thermal_row_start * config.sensor_w,
    )
    return values.reshape((config.sensor_h, config.sensor_w)).copy()


# --------------------------------------------------------------------------------------------
# Frame bookkeeping shared by the readers written here.
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass
class ReadTally:
    """What a reader noticed while it read. The driver keeps the same counts in `stats`."""

    out_of_step: int = 0
    marker_faults: int = 0
    frames_dropped: int = 0
    last_wrapping_count: int | None = None
    out_of_step_in_a_row: int = 0

    def note_out_of_step(self) -> None:
        self.out_of_step += 1
        self.out_of_step_in_a_row += 1
        if self.out_of_step_in_a_row >= OUT_OF_STEP_LIMIT:
            raise NeverInStepError(f"out of step {self.out_of_step_in_a_row} times in a row")


def note_markers(tally: ReadTally, start_marker, end_marker, driver) -> None:
    """Check one frame's two markers and look for a gap since the last frame.

    The rule for a gap is the driver's own, from `read_frame`: the wrapping counter moves by about
    forty a frame, and a jump of more than that is frames the reader never saw.
    """

    start_length, start_sync, start_count, _, _ = MARKER_LAYOUT.unpack_from(start_marker)
    _, end_sync, end_count, _, wrapping_count = MARKER_LAYOUT.unpack_from(end_marker)
    well_formed = (
        start_length == MARKER_LAYOUT.size
        and start_sync in START_SYNCS
        and end_sync in END_SYNCS
        and start_count == end_count
    )
    if not well_formed:
        tally.marker_faults += 1
    tally.out_of_step_in_a_row = 0
    previous, tally.last_wrapping_count = tally.last_wrapping_count, wrapping_count
    if previous is None:
        return
    expected = (previous + driver.CNT3_INCREMENT) % driver.CNT3_WRAP
    gap = (wrapping_count - expected) % driver.CNT3_WRAP
    if driver.CNT3_INCREMENT // 2 < gap < driver.CNT3_WRAP - driver.CNT3_INCREMENT:
        tally.frames_dropped += gap // driver.CNT3_INCREMENT


def whole_packets(byte_count: int) -> int:
    """The smallest multiple of the packet size that holds this many bytes."""

    return -(-byte_count // USB_PACKET_BYTES) * USB_PACKET_BYTES


class PiecewiseReader:
    """Read a frame in requests of a given size, into one buffer that is used again.

    The driver's loop with the request size as the only thing that varies, so 16 KiB here against
    the driver's own 16 KiB says what this loop costs, and the larger sizes say what the request
    size is worth. It keeps the driver's rule for falling out of step: a frame ends with a read of
    exactly one marker, and a marker anywhere else, or anything else there, means start again.
    """

    def __init__(self, device, config, driver, request_bytes: int) -> None:
        self.device = device
        self.config = config
        self.driver = driver
        self.tally = ReadTally()
        self.frame_bytes = config.frame_read_size
        self.received = array.array("B", bytes(request_bytes))
        self.frame = bytearray(self.frame_bytes)

    def in_step(self, received_bytes: int, reaches: int) -> bool:
        if reaches < self.frame_bytes:
            return received_bytes != MARKER_LAYOUT.size
        return reaches == self.frame_bytes and received_bytes == MARKER_LAYOUT.size

    def read_temperatures(self) -> np.ndarray:
        frame_view = memoryview(self.frame)
        received_view = memoryview(self.received)
        filled = 0
        while filled < self.frame_bytes:
            received_bytes = self.device.read(ENDPOINT_IN, self.received, READ_TIMEOUT_MILLISECONDS)
            reaches = filled + received_bytes
            if not self.in_step(received_bytes, reaches):
                self.tally.note_out_of_step()
                filled = 0
                continue
            frame_view[filled:reaches] = received_view[:received_bytes]
            filled = reaches
        end_marker_at = self.frame_bytes - MARKER_LAYOUT.size
        note_markers(self.tally, frame_view, frame_view[end_marker_at:], self.driver)
        return temperatures_copied_from(self.frame, self.config)


class WholeFrameReader:
    """Read a frame as the camera sends it: one transfer of marker and pixels, then the end marker.

    Nothing is copied between buffers. The first read lands where the temperatures are taken from,
    and the end marker goes into a small buffer of its own, where it is only looked at.

    That is how upstream's P3_PROTOCOL.md describes a frame, and the census this bench prints first
    is what checks it against the camera plugged in. Neither transfer is a whole number of packets
    long, for either camera, so each ends in a short packet, and every request here is larger than
    the transfer it is for. A read is therefore exactly one transfer, and one that is not the size
    it should be means the reader is out of step: there is nothing to realign then, only a frame
    to give up on, because the next read starts on a boundary anyway.
    """

    def __init__(self, device, config, driver) -> None:
        self.device = device
        self.config = config
        self.driver = driver
        self.tally = ReadTally()
        self.marked_frame_bytes = config.frame_read_size - MARKER_LAYOUT.size
        self.received = array.array("B", bytes(whole_packets(config.frame_read_size)))
        self.end_marker = array.array("B", bytes(USB_PACKET_BYTES))

    def read_one(self, receive_buffer, expected_bytes: int) -> bool:
        received_bytes = self.device.read(ENDPOINT_IN, receive_buffer, READ_TIMEOUT_MILLISECONDS)
        return received_bytes == expected_bytes

    def read_temperatures(self) -> np.ndarray:
        while True:
            in_step = self.read_one(self.received, self.marked_frame_bytes) and self.read_one(
                self.end_marker, MARKER_LAYOUT.size
            )
            if in_step:
                break
            self.tally.note_out_of_step()
        note_markers(self.tally, self.received, self.end_marker, self.driver)
        return temperatures_copied_from(self.received, self.config)


class DriverReader:
    """The vendored driver as it is, with what it does after the read as the thing that varies."""

    def __init__(self, camera, driver, both_pictures: bool) -> None:
        self.camera = camera
        self.driver = driver
        self.both_pictures = both_pictures
        self.tally = ReadTally()
        # The driver's own counts start again with each candidate, as they do with each stream.
        camera.stats = driver.FrameStats()

    def read_temperatures(self) -> np.ndarray | None:
        try:
            if self.both_pictures:
                _, temperatures = self.camera.read_frame_both()
                return temperatures
            return self.driver.extract_thermal_data(
                self.camera.read_frame(), config=self.camera.config
            )
        except self.driver.FrameMarkerMismatchError:
            return None
        finally:
            self.tally.marker_faults = self.camera.stats.marker_mismatches
            self.tally.frames_dropped = self.camera.stats.frames_dropped


# --------------------------------------------------------------------------------------------
# The candidates, and how one is measured.
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Candidate:
    label: str
    make_reader: Callable[[], object]


def candidates_for(camera, driver) -> list[Candidate]:
    config = camera.config
    return [
        Candidate("driver, both pictures (as shipped)",
                  lambda: DriverReader(camera, driver, both_pictures=True)),
        Candidate("driver read, temperatures only",
                  lambda: DriverReader(camera, driver, both_pictures=False)),
        Candidate("own loop, 16 KiB requests",
                  lambda: PiecewiseReader(camera.dev, config, driver, driver.FRAME_READ_CHUNK)),
        Candidate("own loop, 64 KiB requests",
                  lambda: PiecewiseReader(camera.dev, config, driver, SIXTY_FOUR_KIB)),
        Candidate("own loop, whole frame requests",
                  lambda: PiecewiseReader(camera.dev, config, driver,
                                          whole_packets(config.frame_read_size))),
        Candidate("whole frame, no copy between buffers",
                  lambda: WholeFrameReader(camera.dev, config, driver)),
    ]


class RecordingDevice:
    """Stands in front of the USB device for a frame or two, to write down what each read returned.

    Only for the census. The timed runs read through the device itself, because a wrapper costs a
    call a read and the reads are what is being timed.
    """

    def __init__(self, device) -> None:
        self.device = device
        self.read_sizes: list[int] = []

    def read(self, endpoint, receive_buffer, timeout):
        received_bytes = self.device.read(endpoint, receive_buffer, timeout)
        self.read_sizes.append(received_bytes)
        return received_bytes


def run_lengths(read_sizes: list[int]) -> str:
    """`16384 x12, 1036, 12` rather than fourteen numbers."""

    runs: list[list[int]] = []
    for read_size in read_sizes:
        if runs and runs[-1][0] == read_size:
            runs[-1][1] += 1
            continue
        runs.append([read_size, 1])
    return ", ".join(f"{size} x{count}" if count > 1 else str(size) for size, count in runs)


def reads_of_one_frame(camera, candidate: Candidate) -> str | None:
    """What the camera handed back, read by read, for one frame read in step.

    None for a candidate that never got in step, after printing what it was handed instead: that
    is a candidate built on a wrong idea of how this camera sends a frame, and the sizes it saw
    are what say how it really does.
    """

    actual_device = camera.dev
    recording = RecordingDevice(actual_device)
    camera.dev = recording
    try:
        reader = candidate.make_reader()
        for _ in range(WARMUP_FRAMES):
            reader.read_temperatures()
        recording.read_sizes.clear()
        reader.read_temperatures()
    except NeverInStepError:
        print(f"  {candidate.label:<38}NEVER IN STEP, left out. Its last reads returned: "
              f"{run_lengths(recording.read_sizes[-OUT_OF_STEP_LIMIT:])}")
        return None
    finally:
        camera.dev = actual_device
    return run_lengths(recording.read_sizes)


def interrupt_seconds(ticks_per_second: int) -> float:
    """Time the whole machine has spent in interrupts, hard and soft, since it started."""

    totals = Path("/proc/stat").read_text().splitlines()[0].split()
    return (int(totals[6]) + int(totals[7])) / ticks_per_second


@dataclasses.dataclass(frozen=True)
class Measurement:
    label: str
    frames: int
    wall_seconds: float
    processor_seconds: float
    user_seconds: float
    kernel_seconds: float
    interrupt_seconds: float
    tally: ReadTally
    unusable_frames: int

    def per_frame_milliseconds(self, seconds: float) -> float:
        return seconds / self.frames * 1000.0


def measure(candidate: Candidate, frames: int, ticks_per_second: int) -> Measurement:
    """Read `frames` frames with one candidate and say what the thread reading them was charged.

    The thread's own processor clock is exact. Its split into user and kernel time comes from the
    kernel's tick accounting, which is a sample a hundred times a second, so the two halves are
    rougher than their sum and are printed as shares of it.
    """

    reader = candidate.make_reader()
    for _ in range(WARMUP_FRAMES):
        reader.read_temperatures()
    counted_from = dataclasses.replace(reader.tally)
    unusable_frames = 0
    usage_before = resource.getrusage(resource.RUSAGE_THREAD)
    interrupts_before = interrupt_seconds(ticks_per_second)
    processor_before = time.thread_time()
    wall_before = time.perf_counter()
    for _ in range(frames):
        if reader.read_temperatures() is None:
            unusable_frames += 1
    wall_seconds = time.perf_counter() - wall_before
    processor_seconds = time.thread_time() - processor_before
    interrupts_after = interrupt_seconds(ticks_per_second)
    usage_after = resource.getrusage(resource.RUSAGE_THREAD)
    return Measurement(
        label=candidate.label,
        frames=frames,
        wall_seconds=wall_seconds,
        processor_seconds=processor_seconds,
        user_seconds=usage_after.ru_utime - usage_before.ru_utime,
        kernel_seconds=usage_after.ru_stime - usage_before.ru_stime,
        interrupt_seconds=interrupts_after - interrupts_before,
        tally=ReadTally(
            out_of_step=reader.tally.out_of_step - counted_from.out_of_step,
            marker_faults=reader.tally.marker_faults - counted_from.marker_faults,
            frames_dropped=reader.tally.frames_dropped - counted_from.frames_dropped,
        ),
        unusable_frames=unusable_frames,
    )


def kernel_share(measurement: Measurement) -> str:
    charged = measurement.user_seconds + measurement.kernel_seconds
    if charged <= 0:
        return "-"
    return f"{measurement.kernel_seconds / charged * 100:.0f}%"


def print_measurement(measurement: Measurement) -> None:
    faults = (measurement.tally.out_of_step + measurement.tally.marker_faults
              + measurement.tally.frames_dropped + measurement.unusable_frames)
    print(f"  {measurement.label:<38}"
          f"{measurement.per_frame_milliseconds(measurement.processor_seconds):>9.3f}"
          f"{measurement.processor_seconds / measurement.wall_seconds * 100:>9.1f}%"
          f"{kernel_share(measurement):>8}"
          f"{measurement.per_frame_milliseconds(measurement.wall_seconds):>9.2f}"
          f"{measurement.interrupt_seconds / measurement.wall_seconds * 100:>9.1f}%"
          f"{faults:>8}")
    if faults:
        print(f"      out of step {measurement.tally.out_of_step}, bad markers "
              f"{measurement.tally.marker_faults}, frames dropped "
              f"{measurement.tally.frames_dropped}, unusable {measurement.unusable_frames}")


def print_round(round_number: int, measurements: list[Measurement]) -> None:
    print()
    print(f"round {round_number}, {measurements[0].frames} frames a candidate")
    print(f"  {'candidate':<38}{'cpu ms':>9}{'of core':>10}{'kernel':>8}"
          f"{'wall ms':>9}{'machine':>10}{'faults':>8}")
    print(f"  {'':<38}{'a frame':>9}{'':>10}{'share':>8}"
          f"{'a frame':>9}{'irq':>10}{'':>8}")
    for measurement in measurements:
        print_measurement(measurement)


def first_sound_frame(camera, driver) -> bytes:
    """A frame as the driver returns it, past the glitched ones a fresh stream may start with."""

    for _ in range(OUT_OF_STEP_LIMIT):
        try:
            return camera.read_frame()
        except driver.FrameMarkerMismatchError:
            continue
    raise SystemExit("the driver returned no sound frame, so there is nothing to compare")


def confirm_same_temperatures(camera, driver) -> None:
    """One frame's bytes through every way of taking the temperatures out, which must all agree."""

    frame = first_sound_frame(camera, driver)
    _, from_both = driver.extract_both(frame, config=camera.config)
    from_driver = driver.extract_thermal_data(frame, config=camera.config)
    from_here = temperatures_copied_from(frame, camera.config)
    agree = np.array_equal(from_both, from_driver) and np.array_equal(from_both, from_here)
    print(f"same temperatures from every extraction of one frame: {'yes' if agree else 'NO'}")
    print(f"  start marker {frame[:MARKER_LAYOUT.size].hex()}, {len(frame)} bytes with it")
    if not agree:
        raise SystemExit("the extractions disagree, so nothing below would be comparing like "
                         "with like")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("streamer", type=Path, help="path to thermal-master-stream.py")
    parser.add_argument("--service-pid", type=int, required=True,
                        help="the running service, which is asked whether it holds the camera")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    options = parser.parse_args()

    streamer, driver = load_plugin_and_driver(options.streamer)
    refuse_unless_stream_is_off(options.service_pid, streamer)
    model = streamer.detect_camera_model()
    if model is None:
        raise SystemExit("no Thermal Master camera on the USB bus")
    ticks_per_second = os.sysconf("SC_CLK_TCK")

    camera = driver.P3Camera(config=driver.get_model_config(model))
    camera.connect()
    try:
        camera.init()
        camera.start_streaming()
        config = camera.config
        print(f"camera: {model.upper()}, {config.sensor_w} by {config.sensor_h}, a frame is "
              f"{config.frame_read_size} bytes with both markers")
        confirm_same_temperatures(camera, driver)
        print()
        print("what the camera returns for each read of one frame")
        candidates = []
        for candidate in candidates_for(camera, driver):
            read_sizes = reads_of_one_frame(camera, candidate)
            if read_sizes is None:
                continue
            print(f"  {candidate.label:<38}{read_sizes}")
            candidates.append(candidate)

        for round_number in range(1, options.rounds + 1):
            # Forwards, then backwards, so no candidate is always the one running when the
            # camera's own shutter fires or when something else on the printer wakes up.
            ordered = candidates if round_number % 2 else list(reversed(candidates))
            measured = {
                candidate.label: measure(candidate, options.frames, ticks_per_second)
                for candidate in ordered
            }
            print_round(round_number, [measured[candidate.label] for candidate in candidates])
    finally:
        streamer.release_camera(camera)
    print()
    print("camera released. Press Start on the settings page to bring the stream back.")


if __name__ == "__main__":
    main()
