#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""What kind of cost the service is: each thread's user time and kernel time, over a window.

`measure-cpu-on-printer.sh` says how much of a core the plugin uses. This says where inside the
process that goes, which is the question to answer before changing how the camera is read. Time in
the kernel is the USB transfers themselves and only comes down with fewer or larger reads. Time in
user space is Python, pyusb and copying, and comes down with less of those.

The kernel keeps both figures for every thread in /proc/<pid>/task/<tid>/stat, so they are read
twice and divided by the time in between, exactly as the other script does for the whole process.
It also keeps how often each thread gave up the processor to wait for something. For the thread
that reads the camera that is close to the number of reads it made, which turns "about 13 reads a
frame" from something read off the driver's source into something counted.

Last, it prints what the kernel knows about the camera's USB link: the speed it negotiated and the
packet size of each endpoint.

It reads /proc and /sys and nothing else. It does not open the camera, does not talk to the
service and writes no file, so it is safe to run while the service is streaming.

Run it with `scripts/measure-threads-on-printer.sh <host>`.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import time
from pathlib import Path

CAMERA_VENDOR_ID = "3474"
USB_DEVICES = Path("/sys/bus/usb/devices")

# A thread below this share of a core is counted and not listed. A Python service carries a
# handful of threads that sleep all day (numpy's workers, the HTTP server's accept loop), and a
# table of zeroes buries the two lines that matter.
LISTED_FROM_SHARE = 0.001


@dataclasses.dataclass(frozen=True)
class ThreadReading:
    """What the kernel has charged one thread so far, and how often it has stopped running."""

    name: str
    user_seconds: float
    kernel_seconds: float
    waits: int
    preemptions: int


@dataclasses.dataclass(frozen=True)
class ThreadCost:
    """One thread over the window: shares of one core, and stops a second."""

    thread_id: int
    name: str
    user_share: float
    kernel_share: float
    waits_per_second: float
    preemptions_per_second: float

    @property
    def total_share(self) -> float:
        return self.user_share + self.kernel_share


def context_switches(status_text: str) -> dict[str, int]:
    """The two counters at the end of a /proc status file, by the kernel's own names."""

    counted = {}
    for line in status_text.splitlines():
        label, _, value = line.partition(":")
        if label.endswith("ctxt_switches"):
            counted[label] = int(value)
    return counted


def read_charged(task_directory: Path, ticks_per_second: int) -> ThreadReading | None:
    """One thread's totals, or None for a thread that ended while it was being read.

    The name is in brackets and may hold anything, so the fields are counted from the last closing
    bracket, as `cost.py` does. After it, user and kernel time are the twelfth and thirteenth.
    """

    try:
        stat_line = (task_directory / "stat").read_text()
        status_text = (task_directory / "status").read_text()
    except OSError:
        return None
    fields = stat_line[stat_line.rindex(") ") + 2 :].split()
    switches = context_switches(status_text)
    return ThreadReading(
        name=stat_line[stat_line.index("(") + 1 : stat_line.rindex(")")],
        user_seconds=int(fields[11]) / ticks_per_second,
        kernel_seconds=int(fields[12]) / ticks_per_second,
        waits=switches.get("voluntary_ctxt_switches", 0),
        preemptions=switches.get("nonvoluntary_ctxt_switches", 0),
    )


def read_threads(process_id: int, ticks_per_second: int) -> dict[int, ThreadReading]:
    readings = {}
    for task_directory in Path(f"/proc/{process_id}/task").iterdir():
        reading = read_charged(task_directory, ticks_per_second)
        if reading is not None:
            readings[int(task_directory.name)] = reading
    return readings


def cost_between(
    thread_id: int, started: ThreadReading, finished: ThreadReading, window_seconds: float
) -> ThreadCost:
    return ThreadCost(
        thread_id=thread_id,
        name=finished.name,
        user_share=(finished.user_seconds - started.user_seconds) / window_seconds,
        kernel_share=(finished.kernel_seconds - started.kernel_seconds) / window_seconds,
        waits_per_second=(finished.waits - started.waits) / window_seconds,
        preemptions_per_second=(finished.preemptions - started.preemptions) / window_seconds,
    )


def thread_costs(
    started: dict[int, ThreadReading], finished: dict[int, ThreadReading], window_seconds: float
) -> list[ThreadCost]:
    """Every thread that was there at both ends of the window, the most expensive first."""

    costs = [
        cost_between(thread_id, started[thread_id], finished[thread_id], window_seconds)
        for thread_id in finished
        if thread_id in started
    ]
    return sorted(costs, key=lambda cost: cost.total_share, reverse=True)


def percent(share: float) -> str:
    return f"{share * 100:.1f}%"


def print_thread_table(costs: list[ThreadCost]) -> None:
    listed = [cost for cost in costs if cost.total_share >= LISTED_FROM_SHARE]
    print(f"{'thread':>8}  {'name':<16}{'user':>7}{'kernel':>8}{'total':>8}"
          f"{'waits/s':>10}{'preempted/s':>13}")
    for cost in listed:
        print(f"{cost.thread_id:>8}  {cost.name:<16}{percent(cost.user_share):>7}"
              f"{percent(cost.kernel_share):>8}{percent(cost.total_share):>8}"
              f"{cost.waits_per_second:>10.1f}{cost.preemptions_per_second:>13.1f}")
    quiet_threads = len(costs) - len(listed)
    if quiet_threads:
        print(f"{quiet_threads} more threads used less than "
              f"{percent(LISTED_FROM_SHARE)} of a core each")


def sysfs_value(attribute: Path) -> str:
    try:
        return attribute.read_text().strip()
    except OSError:
        return "?"


def describe_endpoint(endpoint: Path) -> str:
    packet_bytes = sysfs_value(endpoint / "wMaxPacketSize")
    if packet_bytes != "?":
        # The kernel prints this one in hexadecimal with no prefix.
        packet_bytes = str(int(packet_bytes, 16))
    return (f"  {endpoint.parent.name} {endpoint.name}: {sysfs_value(endpoint / 'type')} "
            f"{sysfs_value(endpoint / 'direction')}, packets of {packet_bytes} bytes")


def print_camera_link() -> None:
    """The camera as the kernel sees it. Read from sysfs, so nothing is sent to the device."""

    cameras = [
        vendor.parent
        for vendor in sorted(USB_DEVICES.glob("*/idVendor"))
        if sysfs_value(vendor) == CAMERA_VENDOR_ID
    ]
    if not cameras:
        print("camera on USB: none found with vendor id " + CAMERA_VENDOR_ID)
        return
    for camera in cameras:
        print(f"camera on USB: product {sysfs_value(camera / 'idProduct')}, "
              f"{sysfs_value(camera / 'speed')} Mbit/s, USB {sysfs_value(camera / 'version')}")
        for endpoint in sorted(camera.glob("*/ep_*")):
            print(describe_endpoint(endpoint))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("process_id", type=int, help="the running service's process id")
    parser.add_argument("sample_seconds", type=float, nargs="?", default=30.0)
    parser.add_argument("quiet_seconds", type=float, nargs="?", default=0.0)
    options = parser.parse_args()

    ticks_per_second = os.sysconf("SC_CLK_TCK")
    process_directory = Path(f"/proc/{options.process_id}")
    if options.quiet_seconds > 0:
        print(f"waiting {options.quiet_seconds:.0f}s for the printer to be left alone...",
              file=sys.stderr)
        time.sleep(options.quiet_seconds)

    started_at = time.monotonic()
    threads_started = read_threads(options.process_id, ticks_per_second)
    process_started = read_charged(process_directory, ticks_per_second)
    time.sleep(options.sample_seconds)
    threads_finished = read_threads(options.process_id, ticks_per_second)
    process_finished = read_charged(process_directory, ticks_per_second)
    window_seconds = time.monotonic() - started_at
    if process_started is None or process_finished is None:
        raise SystemExit(f"process {options.process_id} went away while it was being measured")

    print(f"thermal-master, process {options.process_id}, over {window_seconds:.1f} s "
          f"({os.cpu_count()} cores, {ticks_per_second} ticks a second)")
    print()
    print_thread_table(thread_costs(threads_started, threads_finished, window_seconds))
    whole = cost_between(options.process_id, process_started, process_finished, window_seconds)
    print()
    print(f"whole process: {percent(whole.user_share)} user + {percent(whole.kernel_share)} "
          f"kernel = {percent(whole.total_share)} of one core")
    print()
    print_camera_link()


if __name__ == "__main__":
    main()
