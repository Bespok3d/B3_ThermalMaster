# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The bytes a browser actually receives.

Everything else about the HTTP layer is tested by calling functions. This starts the real server on
a real socket and reads the real response, because a camera tile that will not render is usually a
malformed multipart body or a missing header, and neither is visible from the inside.
"""

from __future__ import annotations

import http.client
import threading

import pytest

JPEG_MAGIC = b"\xff\xd8"
FAKE_JPEG = JPEG_MAGIC + b"pretend this is a frame" + b"\xff\xd9"


@pytest.fixture
def serving(thermal_streamer):
    """The real ThermalServer on an ephemeral port, with one frame already published."""

    frame_store = thermal_streamer.LatestFrame()
    frame_store.publish(FAKE_JPEG)
    server = thermal_streamer.ThermalServer(("127.0.0.1", 0), frame_store)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, frame_store
    server.shutdown()
    server.server_close()


def connect_to(server, timeout: float = 5.0) -> http.client.HTTPConnection:
    host, port = server.server_address
    return http.client.HTTPConnection(host, port, timeout=timeout)


def test_the_snapshot_is_served_as_a_jpeg(serving):
    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/snapshot.jpg")
    response = connection.getresponse()
    body = response.read()

    assert response.status == 200
    assert response.getheader("Content-Type") == "image/jpeg"
    assert body == FAKE_JPEG
    connection.close()


def test_a_cache_busted_snapshot_is_served_too(serving):
    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/snapshot.jpg?t=1789289601")
    response = connection.getresponse()

    assert response.status == 200
    assert response.read() == FAKE_JPEG
    connection.close()


def test_an_unknown_path_is_a_404(serving):
    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/nothing-here")
    response = connection.getresponse()
    response.read()

    assert response.status == 404
    connection.close()


def test_the_stream_announces_itself_as_multipart(serving):
    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/stream.mjpg")
    response = connection.getresponse()

    assert response.status == 200
    content_type = response.getheader("Content-Type")
    assert content_type == "multipart/x-mixed-replace; boundary=frame"
    connection.close()


def test_the_stream_sends_a_well_formed_part(serving):
    """The shape a browser parses: boundary line, part headers, blank line, exactly N bytes."""

    server, _ = serving
    connection = connect_to(server)
    connection.request("GET", "/stream.mjpg")
    response = connection.getresponse()

    expected = (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(FAKE_JPEG)).encode() + b"\r\n\r\n"
        + FAKE_JPEG
        + b"\r\n"
    )
    received = response.read(len(expected))

    assert received == expected
    connection.close()
