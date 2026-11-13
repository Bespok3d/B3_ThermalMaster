# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The plugin's own frame reader: a whole frame in two reads, beside the vendored driver.

It replaced fourteen reads a frame with two (F-78), and what it must not do in exchange is
return different temperatures, lose its place in the stream for good, or leave a camera that
frames differently with no picture at all. So it is held against the vendored driver itself over
the same bytes, put out of step the ways a camera really does it, and made to give up.

The endpoint under it is `fake_camera.StandInEndpoint`, which hands data over in packets and ends
a read at a short one, as Linux does, and fails a read that a packet does not fit into.
"""

from __future__ import annotations

import array

import numpy as np
import pytest
from fake_camera import (
    USB_PACKET_BYTES,
    Emission,
    StandInCamera,
    StandInEndpoint,
    frame_transfers,
    get_model_config,
    thermal_frame,
)

LEFT_TO_THE_DRIVER = "reading as the driver does"


def lines_saying_the_driver_took_over(capsys) -> list[str]:
    return [line for line in capsys.readouterr().err.splitlines() if LEFT_TO_THE_DRIVER in line]


def test_a_frame_is_two_requests_and_each_is_a_whole_number_of_packets(thermal_streamer):
    """Fourteen reads became two, and a request that ends inside a packet overflows on Linux."""

    camera = StandInCamera(scripted_frames=[thermal_frame(24.0, 40.0)])
    reader = thermal_streamer.ThermalFrameReader(camera)

    temperatures = reader.next_frame()

    assert np.array_equal(temperatures, thermal_frame(24.0, 40.0))
    assert len(camera.dev.requested_sizes) == 2
    assert all(requested % USB_PACKET_BYTES == 0 for requested in camera.dev.requested_sizes)


def test_a_frame_handed_over_is_not_changed_by_the_next_read(thermal_streamer):
    """The buffer is read into again for every frame, so what is returned has to be a copy."""

    camera = StandInCamera(scripted_frames=[thermal_frame(24.0, 40.0), thermal_frame(60.0, 90.0)])
    reader = thermal_streamer.ThermalFrameReader(camera)

    first = reader.next_frame()
    reader.next_frame()

    assert np.array_equal(first, thermal_frame(24.0, 40.0))


@pytest.mark.parametrize("model", ["p1", "p3"])
def test_it_returns_what_the_vendored_driver_returns(thermal_streamer, vendored_driver, model):
    """The same bytes through the real driver and through the reader, frame for frame.

    The camera objects are the driver's own, with the driver's own geometry, so this also fails
    if the reader asks `config` for something the driver does not call by that name.
    """

    config = vendored_driver.get_model_config(model)
    random_values = np.random.default_rng(7)
    frames = [
        random_values.integers(0, 65536, size=(config.sensor_h, config.sensor_w), dtype=np.uint16)
        for _ in range(4)
    ]

    def camera_sending_the_frames():
        emissions = iter(
            [frame_transfers(config, frame, number) for number, frame in enumerate(frames, 1)]
        )
        # What the driver's `connect` leaves behind, without a device to connect to.
        return vendored_driver.P3Camera(
            config=config,
            dev=StandInEndpoint(lambda: next(emissions)),
            streaming=True,
            _frame_buf=array.array("B", bytes(config.frame_buffer_size)),
            _chunk_buf=array.array("B", bytes(vendored_driver.FRAME_READ_CHUNK)),
        )

    read_by_the_driver = camera_sending_the_frames()
    reader = thermal_streamer.ThermalFrameReader(camera_sending_the_frames())

    for sent in frames:
        _, from_the_driver = read_by_the_driver.read_frame_both()
        from_the_reader = reader.next_frame()
        assert from_the_reader.dtype == from_the_driver.dtype
        assert np.array_equal(from_the_reader, from_the_driver)
        assert np.array_equal(from_the_reader, sent)


@pytest.mark.parametrize("model", ["p1", "p3"])
def test_the_stand_in_camera_has_the_drivers_geometry(thermal_streamer, vendored_driver, model):
    """Every other test here reads frames laid out by the stand-in, so it has to match."""

    stood_in = get_model_config(model)
    real = vendored_driver.get_model_config(model)

    for geometry in (
        "pid",
        "sensor_w",
        "sensor_h",
        "frame_rows",
        "frame_size",
        "frame_read_size",
        "thermal_row_start",
    ):
        assert getattr(stood_in, geometry) == getattr(real, geometry), geometry
    assert thermal_streamer.FRAME_MARKER.size == vendored_driver.MARKER_SIZE
    assert thermal_streamer.FRAME_MARKER.size == vendored_driver.MARKER_DTYPE.itemsize


def test_a_stream_joined_in_the_middle_of_a_frame_costs_only_that_frame(thermal_streamer):
    """What the first reads of a session find: the rest of a frame, then its end marker."""

    interrupted = frame_transfers(get_model_config(), thermal_frame(), frame_number=1)
    rest_of_a_frame = Emission([interrupted[0][40 * USB_PACKET_BYTES :], interrupted[1]])
    camera = StandInCamera(scripted_frames=[rest_of_a_frame, thermal_frame(30.0, 50.0)])
    reader = thermal_streamer.ThermalFrameReader(camera)

    read = [reader.next_frame() for _ in range(3)]

    assert read[0] is None
    assert read[1] is None
    assert np.array_equal(read[2], thermal_frame(30.0, 50.0))


def test_a_mistimed_frame_is_given_up_and_the_next_one_read(thermal_streamer):
    """After a calibration the camera sends more than a frame, in transfers of other sizes.

    The first here is longer than the request and an exact number of packets, so the read comes
    back full and the rest runs on into the transfer behind it. Nothing may overflow, and the
    frame after it has to arrive whole.
    """

    config = get_model_config()
    request_bytes = thermal_streamer.whole_packets(config.frame_read_size)
    mistimed = Emission(
        [bytes(request_bytes + 4 * USB_PACKET_BYTES), bytes(2 * USB_PACKET_BYTES + 12)]
    )
    camera = StandInCamera(
        scripted_frames=[thermal_frame(24.0, 40.0), mistimed, thermal_frame(30.0, 50.0)]
    )
    reader = thermal_streamer.ThermalFrameReader(camera)

    read = [reader.next_frame() for _ in range(4)]

    assert np.array_equal(read[0], thermal_frame(24.0, 40.0))
    assert read[1] is None
    assert read[2] is None
    assert np.array_equal(read[3], thermal_frame(30.0, 50.0))


def test_a_frame_whose_markers_disagree_is_given_up(thermal_streamer):
    """The driver raises on this one. Read here, it is a frame that is not handed over."""

    glitched = thermal_streamer.FrameMarkerMismatchError("cnt1 mismatch")
    camera = StandInCamera(scripted_frames=[glitched, thermal_frame(30.0, 50.0)])
    reader = thermal_streamer.ThermalFrameReader(camera)

    assert reader.next_frame() is None
    assert np.array_equal(reader.next_frame(), thermal_frame(30.0, 50.0))


def test_glitched_frames_do_not_hand_the_session_to_the_driver(thermal_streamer, capsys):
    """A right sized frame with the wrong markers is in step: the driver would do no better."""

    glitched = thermal_streamer.FrameMarkerMismatchError("cnt1 mismatch")
    script = [glitched] * thermal_streamer.OUT_OF_STEP_LIMIT + [thermal_frame()]
    camera = StandInCamera(scripted_frames=script)
    reader = thermal_streamer.ThermalFrameReader(camera)

    read = [reader.next_frame() for _ in script]

    assert np.array_equal(read[-1], thermal_frame())
    assert len(camera.dev.requested_sizes) == 2 * len(script)
    assert lines_saying_the_driver_took_over(capsys) == []


def test_a_camera_it_cannot_get_in_step_with_is_left_to_the_driver(thermal_streamer, capsys):
    """The fallback: a camera that does not frame as the two measured ones do still gets read.

    Said once in the log, and from then on the endpoint is not asked for whole frames again.
    """

    never_a_frame = [None] * thermal_streamer.OUT_OF_STEP_LIMIT
    script = [*never_a_frame, thermal_frame(24.0, 40.0), thermal_frame(30.0, 50.0)]
    camera = StandInCamera(scripted_frames=script)
    reader = thermal_streamer.ThermalFrameReader(camera)

    read = [reader.next_frame() for _ in script]

    assert all(frame is None for frame in read[: len(never_a_frame)])
    assert np.array_equal(read[-2], thermal_frame(24.0, 40.0))
    assert np.array_equal(read[-1], thermal_frame(30.0, 50.0))
    assert len(camera.dev.requested_sizes) == len(never_a_frame)
    assert len(lines_saying_the_driver_took_over(capsys)) == 1


def test_a_frame_read_in_step_forgives_the_reads_before_it(thermal_streamer, capsys):
    """Out of step now and then is a calibration, not a camera that frames differently."""

    almost_given_up = [None] * (thermal_streamer.OUT_OF_STEP_LIMIT - 1)
    script = [*almost_given_up, thermal_frame(24.0, 40.0), *almost_given_up, thermal_frame()]
    camera = StandInCamera(scripted_frames=script)
    reader = thermal_streamer.ThermalFrameReader(camera)

    read = [reader.next_frame() for _ in script]

    assert np.array_equal(read[len(almost_given_up)], thermal_frame(24.0, 40.0))
    assert np.array_equal(read[-1], thermal_frame())
    assert lines_saying_the_driver_took_over(capsys) == []


def test_the_driver_gets_its_turn_before_the_camera_is_called_stalled(thermal_streamer):
    """The capture loop gives up on a session after so many frames with nothing in them.

    Handing over to the driver has to happen inside that allowance with room left, or a camera
    the driver could have read is reconnected over and over instead.
    """

    assert thermal_streamer.OUT_OF_STEP_LIMIT * 2 <= thermal_streamer.MAX_CONSECUTIVE_FRAME_FAILURES
