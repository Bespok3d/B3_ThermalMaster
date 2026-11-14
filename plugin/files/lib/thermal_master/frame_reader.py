# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Reading a frame off the camera, a whole frame at a time.

The vendored driver's `read_frame` asks the camera for 16 KiB at a time, because that is what
works on every platform the driver supports. On the wire a frame is two transfers, the start
marker with the pixels and then the end marker, so that is 14 reads for a P3 and 6 for a P1, each
of them a trip through pyusb's Python and into the kernel. The number of reads turned out to be
most of what reading costs. Measured on the U1 on 2026-11-13, in processor time a frame: 3.51 ms
for a P3 and 1.85 ms for a P1 as the driver reads, against 0.88 ms and 0.69 ms as this does
(F-78). The driver's own docstring says that reading a frame the way the camera sends it works
reliably on Linux, which is what a printer runs.

So the frame is read here, through the device the driver opened, and nothing else about the
camera is: finding it, starting the stream, the gain and the shutter are all still the driver's.
This lives beside the driver and not in it, because the driver is pinned upstream source
(VENDORING.md). What it leans on there is `camera.dev` and the geometry in `camera.config`.

Two reads a frame, and no copy between buffers. The first read lands in the buffer the
temperatures are taken from, and the end marker goes into a small buffer of its own, where it is
only looked at. Neither transfer is a whole number of packets long, for either camera, so each
ends in a short packet, and each request is larger than the transfer it is for. A read is
therefore exactly one transfer, and one that is not the size it should be means the reader is out
of step with the camera: there is nothing to realign then, only a frame to give up on, because
the next read starts on a transfer boundary anyway. That is how the mistimed frame after a
calibration is survived, and a stream joined in the middle of a frame.

Every request is a whole number of packets. A bulk read that ends inside a packet is an overflow
error on Linux, and a frame with its two markers is not a whole number of packets, so the buffer
is rounded up.

The driver's reader is kept as the fallback. A camera this cannot get in step with, ten times in
a row, is one that does not send a frame as the two this was measured on do. The log says so once
and the session carries on reading as the driver does, which makes no such assumption.
"""

from __future__ import annotations

import array
import struct
from typing import cast

import numpy as np
from p3_camera import (  # type: ignore[import-not-found]
    FrameMarkerMismatchError,
    P3Camera,
)

from .log import log_line

# The endpoint the stream arrives on and how long a read may wait, both as the driver's own
# `read_frame` has them. The driver writes them as literals, so there is nothing to import, and a
# re-vendor that changed either would have to be followed here (VENDORING.md).
STREAM_ENDPOINT = 0x81


READ_TIMEOUT_MILLISECONDS = 10000


# What the camera's endpoints carry on a high speed link, read from the printer's sysfs for both
# cameras. On a full speed link a packet is 64 bytes, which 512 is a multiple of, so a request
# that is whole at this size is whole there too.
USB_PACKET_BYTES = 512


# The driver's MARKER_DTYPE, as a struct: its own length, a sync byte, the frame's counter, a
# second counter and a wrapping one. Twelve bytes, at the start and at the end of every frame.
FRAME_MARKER = struct.Struct("<BBIIH")


# How many times in a row the reader may be out of step before the session is handed to the
# driver's reader. A calibration or a stream joined mid frame costs two or three. It is half of
# MAX_CONSECUTIVE_FRAME_FAILURES on purpose: the driver's reader gets the other half to produce a
# frame before the capture loop calls the camera stalled.
OUT_OF_STEP_LIMIT = 10


def whole_packets(byte_count: int) -> int:
    """The smallest whole number of packets that holds this many bytes, in bytes."""

    return -(-byte_count // USB_PACKET_BYTES) * USB_PACKET_BYTES


def next_thermal_frame(camera: P3Camera) -> np.ndarray | None:
    """One thermal frame read by the vendored driver, or None when it returned nothing usable.

    A marker mismatch is a glitched frame, not a dead camera: the driver raises on it, and letting
    that reach the reconnect path costs seconds of dead video for a fault the next read clears.
    """

    try:
        _, thermal_raw = camera.read_frame_both()
    except FrameMarkerMismatchError:
        return None
    # The driver carries no annotations, so this is the boundary where its Any becomes a frame.
    return cast("np.ndarray | None", thermal_raw)


class ThermalFrameReader:
    """The frames of one capture session, read whole, or by the driver if that cannot be done.

    Made when a session starts and dropped when it ends, so a camera that had to be left to the
    driver is tried again whole after the next reconnect, which is rare enough to cost nothing.
    Like everything that touches the camera it belongs to the capture thread.
    """

    def __init__(self, camera: P3Camera) -> None:
        config = camera.config
        self._camera = camera
        self._sensor_shape = (int(config.sensor_h), int(config.sensor_w))
        self._temperatures_at = FRAME_MARKER.size + 2 * int(
            config.thermal_row_start * config.sensor_w
        )
        self._marked_frame_bytes = int(config.frame_read_size) - FRAME_MARKER.size
        self._received = array.array("B", bytes(whole_packets(int(config.frame_read_size))))
        self._end_marker = array.array("B", bytes(USB_PACKET_BYTES))
        self._out_of_step_in_a_row = 0
        self._left_to_the_driver = False

    def next_frame(self) -> np.ndarray | None:
        """The next frame's temperatures as an array of their own, or None for a frame given up on.

        None is a frame the reader was out of step for, or one whose two markers disagree: the
        capture loop counts those and calls the camera stalled when there are too many in a row,
        exactly as it does for the driver's.
        """

        if self._left_to_the_driver:
            return next_thermal_frame(self._camera)
        if not self._read_in_step():
            self._note_out_of_step()
            return None
        self._out_of_step_in_a_row = 0
        if not self._markers_agree():
            return None
        return self._temperatures()

    def _read_in_step(self) -> bool:
        """Read the two transfers of a frame, and say whether each was the size it has to be."""

        marked_frame_bytes = int(
            self._camera.dev.read(STREAM_ENDPOINT, self._received, READ_TIMEOUT_MILLISECONDS)
        )
        if marked_frame_bytes != self._marked_frame_bytes:
            return False
        end_marker_bytes = int(
            self._camera.dev.read(STREAM_ENDPOINT, self._end_marker, READ_TIMEOUT_MILLISECONDS)
        )
        return end_marker_bytes == FRAME_MARKER.size

    def _note_out_of_step(self) -> None:
        self._out_of_step_in_a_row += 1
        if self._out_of_step_in_a_row < OUT_OF_STEP_LIMIT:
            return
        self._left_to_the_driver = True
        log_line(
            f"whole frame reads were out of step with the camera {OUT_OF_STEP_LIMIT} times "
            "in a row: reading as the driver does until it is reconnected"
        )

    def _markers_agree(self) -> bool:
        """Whether the two markers carry the same frame counter, which is the driver's own test."""

        _, _, started_count, _, _ = FRAME_MARKER.unpack_from(self._received)
        _, _, ended_count, _, _ = FRAME_MARKER.unpack_from(self._end_marker)
        return bool(started_count == ended_count)

    def _temperatures(self) -> np.ndarray:
        """The temperature half of the frame just read, copied out of the buffer it was read into.

        The copy is the only one a frame gets, and it is what makes the array the caller's own:
        the buffer underneath is read into again for the next frame.
        """

        rows, columns = self._sensor_shape
        counts = np.frombuffer(
            self._received, dtype="<u2", count=rows * columns, offset=self._temperatures_at
        )
        return counts.reshape(self._sensor_shape).copy()
