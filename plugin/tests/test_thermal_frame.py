# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The binary frame the viewer reads its own temperatures out of.

This is the one place the plugin hands raw measurements to something it does not control, so the
format has to be exactly what the reader on the other side assumes: little endian, self describing,
oriented the way the picture is, and with the emissivity correction already applied so the browser
never has to reimplement the physics.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest
from fake_camera import KELVIN_AT_ZERO_CELSIUS, RAW_UNITS_PER_KELVIN

HEADER = 16


def raw_for(celsius: float) -> int:
    return int((celsius + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN)


def read_header(body: bytes):
    magic = body[:4]
    width, height, scale = struct.unpack_from("<HHH", body, 4)
    return magic, width, height, scale


def values_of(thermal_streamer, body: bytes):
    _, width, height, _ = read_header(body)
    return np.frombuffer(body, dtype="<i2", count=width * height, offset=HEADER).reshape(
        (height, width)
    )


@pytest.fixture
def frame():
    return np.full((120, 160), raw_for(30.0), dtype=np.uint16)


def encoded(thermal_streamer, counts, rotation=0, mirrors=(False, False), emissivity=1.0):
    return thermal_streamer.encode_thermal_frame(
        thermal_streamer.ThermalFrame(counts, rotation, mirrors, emissivity)
    )


def test_the_body_says_what_it_is(thermal_streamer, frame):
    magic, width, height, scale = read_header(encoded(thermal_streamer, frame))

    assert magic == b"TMF1"
    assert (width, height) == (160, 120)
    assert scale == 100


def test_the_body_is_exactly_the_header_and_the_pixels(thermal_streamer, frame):
    body = encoded(thermal_streamer, frame)

    assert len(body) == HEADER + 160 * 120 * 2


def test_a_pixel_reads_back_as_the_temperature_it_was(thermal_streamer, frame):
    counts = frame.copy()
    counts[17, 43] = raw_for(75.5)

    values = values_of(thermal_streamer, encoded(thermal_streamer, counts))

    assert values[17, 43] / 100 == pytest.approx(75.5, abs=0.02)


def test_the_frame_arrives_in_the_orientation_the_picture_is_in(thermal_streamer, frame):
    """A viewer maps a pointer onto this, so an unrotated frame would read the wrong pixel."""

    counts = frame.copy()
    counts[17, 43] = raw_for(75.5)
    (x, y), size = thermal_streamer.orient_point((43, 17), (160, 120), 90, (False, False))

    body = encoded(thermal_streamer, counts, rotation=90)
    _, width, height, _ = read_header(body)

    assert (width, height) == size
    assert values_of(thermal_streamer, body)[y, x] / 100 == pytest.approx(75.5, abs=0.02)


def test_the_emissivity_correction_is_already_applied(thermal_streamer, frame):
    """So the browser never has to carry a second copy of the physics."""

    counts = frame.copy()
    counts[10, 10] = raw_for(80.0)

    plain = values_of(thermal_streamer, encoded(thermal_streamer, counts, emissivity=1.0))
    shiny = values_of(thermal_streamer, encoded(thermal_streamer, counts, emissivity=0.5))

    assert shiny[10, 10] > plain[10, 10]


def test_a_temperature_beyond_the_format_is_clamped_not_wrapped(thermal_streamer, frame):
    """Hundredths in a signed short stop at 320 C, and wrapping would report cold instead."""

    counts = frame.copy()
    counts[0, 0] = 65535  # far past anything either sensor can report

    values = values_of(thermal_streamer, encoded(thermal_streamer, counts))

    assert values[0, 0] == 32000


def test_the_values_are_little_endian(thermal_streamer, frame):
    """Stated rather than assumed: the reader is a DataView call that names the byte order."""

    counts = frame.copy()
    counts[0, 0] = raw_for(1.0)
    body = encoded(thermal_streamer, counts)

    assert struct.unpack_from("<h", body, HEADER)[0] == pytest.approx(100, abs=2)
