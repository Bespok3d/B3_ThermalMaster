# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The bytes a browser actually receives.

Everything else about the HTTP layer is tested by calling functions. This starts the real server on
a real socket and reads the real response, because a camera tile that will not render is usually a
malformed multipart body or a missing header, and neither is visible from the inside.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time

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


STREAM_PART_BOUNDARY = b"--frame\r\n"

# Long enough for a handler to be parked in its wait, short enough that no stream reaches the one
# second timeout that resends the current frame before a test has finished counting.
STREAMS_SETTLE_SECONDS = 0.1
QUIET_STREAM_SECONDS = 0.2
COUNTING_GIVES_UP_SECONDS = 0.5


def open_stream(server) -> socket.socket:
    """A raw connection that has asked for the stream, so a test can count exactly what arrives."""

    host, port = server.server_address
    stream = socket.create_connection((host, port), timeout=5.0)
    stream.sendall(b"GET /stream.mjpg HTTP/1.1\r\nHost: thermal\r\n\r\n")
    return stream


def parts_arriving(stream: socket.socket) -> int:
    """How many parts arrive before the stream falls quiet, or counting gives up."""

    stream.settimeout(QUIET_STREAM_SECONDS)
    deadline = time.monotonic() + COUNTING_GIVES_UP_SECONDS
    pending = b""
    parts = 0
    while time.monotonic() < deadline:
        try:
            chunk = stream.recv(65536)
        except TimeoutError:
            break
        if not chunk:
            break
        pending += chunk
        parts += pending.count(STREAM_PART_BOUNDARY)
        # Kept short of a whole boundary, so one that straddles two reads is counted once.
        pending = pending[-(len(STREAM_PART_BOUNDARY) - 1):]
    return parts


def test_interest_noted_elsewhere_sends_open_streams_nothing(serving):
    """Every stream notes its interest before each part, on the condition frames are announced on.

    A stream that took somebody else's interest for a new frame resent the current one, and noted
    its own interest in turn, which woke the other: two open streams sent each other the same frame
    as fast as the network drained it, 830 parts a second on hardware against the camera's 25
    (F-73). Interest is not a frame, and must bring an open stream no part at all.
    """

    server, frame_store = serving
    first, second = open_stream(server), open_stream(server)
    time.sleep(STREAMS_SETTLE_SECONDS)

    frame_store.note_interest()

    assert parts_arriving(first) == 0
    assert parts_arriving(second) == 0
    first.close()
    second.close()


def test_a_new_frame_reaches_every_open_stream_exactly_once(serving):
    """The other half, so that a stream silenced for good would not pass for a fixed one."""

    server, frame_store = serving
    first, second = open_stream(server), open_stream(server)
    time.sleep(STREAMS_SETTLE_SECONDS)

    frame_store.publish(FAKE_JPEG)

    assert parts_arriving(first) == 1
    assert parts_arriving(second) == 1
    first.close()
    second.close()


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


@pytest.fixture
def serving_with_settings(thermal_streamer, tmp_path):
    """The server as it actually runs, settings store and all."""

    frame_store = thermal_streamer.LatestFrame()
    frame_store.publish(FAKE_JPEG)
    palettes = thermal_streamer.build_palettes()
    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(), tmp_path / "settings.json"
    )
    server = thermal_streamer.ThermalServer(("127.0.0.1", 0), frame_store, store, palettes)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, store
    server.shutdown()
    server.server_close()


def test_the_settings_endpoint_reports_json(serving_with_settings):
    server, _ = serving_with_settings
    connection = connect_to(server)

    connection.request("GET", "/settings")
    response = connection.getresponse()
    payload = json.loads(response.read())

    assert response.status == 200
    assert payload["palette"] == "ironbow"
    connection.close()


def test_posting_settings_changes_them_and_redirects_back(serving_with_settings):
    server, store = serving_with_settings
    connection = connect_to(server)
    body = "palette=sepia&rotation=270&flip_vertical=on"

    connection.request(
        "POST", "/settings", body, {"Content-Type": "application/x-www-form-urlencoded"}
    )
    response = connection.getresponse()
    response.read()

    assert response.status == 303
    location = response.getheader("Location")

    # Relative, and this is the whole point of the assertion. The plugin serves the page at "/" and
    # nginx publishes it at "/thermal/" with the prefix stripped, so an absolute "/" resolved to the
    # printer's home page in a browser and every settings change bounced the user into Fluidd.
    assert not location.startswith("/")
    assert location.endswith("#controls")
    assert store.as_dict() == {**store.as_dict(), "palette": "sepia", "rotation": 270}
    connection.close()


def test_the_root_path_serves_the_control_page(serving_with_settings):
    server, _ = serving_with_settings
    connection = connect_to(server)

    connection.request("GET", "/")
    response = connection.getresponse()
    body = response.read().decode()

    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert 'action="settings"' in body
    assert 'id="controls"' in body
    connection.close()


def test_the_stream_cannot_be_posted_to(serving_with_settings):
    server, _ = serving_with_settings
    connection = connect_to(server)

    connection.request("POST", "/stream.mjpg", "", {"Content-Length": "0"})
    response = connection.getresponse()
    response.read()

    assert response.status == 404
    connection.close()


def test_the_root_path_falls_back_to_the_stream_without_a_settings_store(serving):
    """The server is constructed without a store in the tests above, and must still serve."""

    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/")
    response = connection.getresponse()
    response.read(16)

    assert response.status == 200
    assert response.getheader("Content-Type").startswith("multipart/x-mixed-replace")
    connection.close()


def test_the_statistics_endpoint_answers_before_any_frame_has_measurements(serving):
    """The fixture publishes a frame with no statistics, which is what a bare publish looks like."""

    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/stats")
    response = connection.getresponse()
    response.read()

    assert response.status == 503
    connection.close()


def test_the_statistics_endpoint_serves_what_the_last_frame_measured(thermal_streamer, serving):
    server, frame_store = serving
    stats = thermal_streamer.FrameStats(
        minimum_celsius=20.0,
        maximum_celsius=60.0,
        average_celsius=30.0,
        centre_celsius=35.0,
        range_low_celsius=21.0,
        range_high_celsius=58.0,
        hotspot=(12, 34),
        coldspot=(1, 2),
        width=160,
        height=120,
    )
    frame_store.publish(FAKE_JPEG, stats)
    connection = connect_to(server)

    connection.request("GET", "/stats")
    response = connection.getresponse()
    payload = json.loads(response.read())

    assert response.status == 200
    assert response.getheader("Content-Type") == "application/json"
    assert payload["maximum"] == 60.0
    assert payload["hotspot"] == {"x": 12, "y": 34}
    connection.close()


def health_of(server) -> tuple[int, dict]:
    connection = connect_to(server)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_health_answers_before_any_frame(thermal_streamer):
    """The watchdog asks from the first moment, and "nothing yet" is an answer, not an error."""

    server = thermal_streamer.ThermalServer(("127.0.0.1", 0), thermal_streamer.LatestFrame())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, payload = health_of(server)
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert payload == {"frame": 0, "frame_age_seconds": None, "streaming": True}


def test_health_answers_where_the_statistics_do_not(serving):
    """A frame without measurements, which is what the "Stream off" placeholder is.

    `/stats` answers 503 to it, and a watchdog reading that would take the camera being switched
    off for the printer not answering.
    """

    server, _ = serving

    status, payload = health_of(server)

    assert status == 200
    assert payload["frame"] == 1


def test_health_counts_every_publication_and_says_how_old_the_last_is(serving):
    server, frame_store = serving
    frame_store.publish(FAKE_JPEG)
    frame_store.publish(FAKE_JPEG)

    _, payload = health_of(server)

    assert payload["frame"] == 3
    assert 0 <= payload["frame_age_seconds"] < 1.0


def test_health_does_not_wait_for_a_fresh_frame(thermal_streamer, serving):
    """The fixture's frame goes stale, and nothing will publish another.

    `/stats` would wait out its half second wake here; the watchdog only wants to know what there
    is.
    """

    server, _ = serving
    time.sleep(thermal_streamer.FRESH_ENOUGH_SECONDS + 0.05)

    started = time.monotonic()
    status, _ = health_of(server)

    assert status == 200
    assert time.monotonic() - started < 0.25


def test_asking_for_health_counts_as_watching(thermal_streamer, serving):
    """A page whose stream died is still somebody watching.

    Without this the capture would idle under it, the count would stop, and the watchdog would
    blame the camera for its own dead stream instead of reopening it.
    """

    server, frame_store = serving
    assert frame_store.wanted() is False

    health_of(server)

    assert frame_store.wanted() is True


def test_health_says_when_the_camera_is_switched_off(thermal_streamer):
    """The placeholder is meant to be still, and the watchdog must know not to act on it."""

    import dataclasses

    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    store.update_camera(dataclasses.replace(thermal_streamer.CameraSettings(), streaming=False))
    device = thermal_streamer.DeviceController(store)
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), thermal_streamer.LatestFrame(), store,
        thermal_streamer.build_palettes(), device,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, payload = health_of(server)
    finally:
        server.shutdown()
        server.server_close()

    assert payload["streaming"] is False


def posting(connection, body: str) -> int:
    connection.request(
        "POST", "/settings", body,
        {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))},
    )
    response = connection.getresponse()
    response.read()
    return response.status


def test_pressing_calibrate_queues_a_shutter_without_touching_the_camera(thermal_streamer):
    """The request arrives on a handler thread, and no command may be sent from there."""

    import fake_camera

    frame_store = thermal_streamer.LatestFrame()
    frame_store.publish(FAKE_JPEG)
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    device = thermal_streamer.DeviceController(store)
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), frame_store, store, thermal_streamer.build_palettes(), device
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    camera = fake_camera.StandInCamera()
    connection = connect_to(server)
    try:
        status = posting(connection, "command=shutter&palette=ironbow&rotation=0")

        assert status == 303
        assert device.status()["shutter"]["state"] == "pending"
        assert camera.shutter_triggers == 0
        device.apply(camera)
        assert camera.shutter_triggers == 1
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_posting_a_gain_records_it_without_sending_it(thermal_streamer):
    frame_store = thermal_streamer.LatestFrame()
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    device = thermal_streamer.DeviceController(store)
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), frame_store, store, thermal_streamer.build_palettes(), device
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = connect_to(server)
    try:
        assert posting(connection, "gain=low&palette=ironbow&rotation=0") == 303

        assert store.camera_snapshot()[1].gain == "low"
        assert device.status()["gain"] is None
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_a_client_that_asks_for_json_is_not_redirected(thermal_streamer):
    """The page posts in the background so the video stream is not torn down on every change."""

    frame_store = thermal_streamer.LatestFrame()
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    device = thermal_streamer.DeviceController(store)
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), frame_store, store, thermal_streamer.build_palettes(), device
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = connect_to(server)
    try:
        body = "palette=sepia&rotation=90&command=shutter"
        connection.request(
            "POST", "/settings", body,
            {"Content-Type": "application/x-www-form-urlencoded",
             "Content-Length": str(len(body)), "Accept": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 200
        assert payload["palette"] == "sepia"
        assert payload["rotation"] == 90
        assert payload["pending"] is True
        assert "Calibration requested" in payload["device"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_the_settings_mirror_carries_the_same_device_sentence(thermal_streamer):
    """One wording, so a browser with JavaScript and one without cannot disagree."""

    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    device = thermal_streamer.DeviceController(store)
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), thermal_streamer.LatestFrame(), store,
        thermal_streamer.build_palettes(), device,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = connect_to(server)
    try:
        connection.request("GET", "/settings")
        payload = json.loads(connection.getresponse().read())

        assert payload["device"] == thermal_streamer.describe_device(device.status())
        assert payload["pending"] is False
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_the_frame_endpoint_waits_for_a_frame(serving):
    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/frame.bin")
    response = connection.getresponse()
    response.read()

    assert response.status == 503
    connection.close()


def test_the_frame_endpoint_serves_the_measurements(thermal_streamer, serving):
    import numpy as np

    server, frame_store = serving
    counts = np.full((120, 160), 20000, dtype=np.uint16)
    frame_store.publish(
        FAKE_JPEG, None, thermal_streamer.ThermalFrame(counts, 0, (False, False), 1.0)
    )
    connection = connect_to(server)

    connection.request("GET", "/frame.bin")
    response = connection.getresponse()
    body = response.read()

    assert response.status == 200
    assert response.getheader("Content-Type") == "application/octet-stream"
    assert body[:4] == b"TMF1"
    assert len(body) == 16 + 160 * 120 * 2
    connection.close()


def test_the_viewer_page_is_served_without_a_settings_store(serving):
    """It needs no settings, so a half-wired server still serves something usable."""

    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/view")
    response = connection.getresponse()
    body = response.read().decode()

    assert response.status == 200
    assert 'id="surface"' in body
    assert "frame.bin" in body
    connection.close()


def test_a_json_post_leaves_unmentioned_settings_alone(thermal_streamer):
    """End to end, because the trap is in the handler choosing a dialect, not in the parser."""

    store = thermal_streamer.SettingsStore(
        "ironbow",
        thermal_streamer.RenderSettings(colorbar=True, reticle=True, hotspot=True, coldspot=True),
        None,
    )
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), thermal_streamer.LatestFrame(), store, thermal_streamer.build_palettes()
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = connect_to(server)
    try:
        body = json.dumps({"units": "fahrenheit"})
        connection.request(
            "POST", "/settings", body,
            {"Content-Type": "application/json", "Accept": "application/json",
             "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 200
        assert payload["units"] == "fahrenheit"
        assert payload["colorbar"] is True
        assert payload["coldspot"] is True
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_a_form_post_still_means_absent_is_off(thermal_streamer):
    """The other dialect is unchanged, and that difference is the point of having two."""

    store = thermal_streamer.SettingsStore(
        "ironbow", thermal_streamer.RenderSettings(coldspot=True), None
    )
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), thermal_streamer.LatestFrame(), store, thermal_streamer.build_palettes()
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = connect_to(server)
    try:
        assert posting(connection, "palette=ironbow&rotation=0") == 303

        assert store.as_dict()["coldspot"] is False
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_the_viewer_page_is_told_the_picture_shape(thermal_streamer, serving):
    """So it can lay itself out before the stream has decoded anything.

    The stream is an img of a never-ending multipart response: it reports no size until a part has
    decoded and fires no event when one does. The plugin is the thing producing the picture, so it
    knows the answer and says so rather than leaving the page to discover it.
    """

    import numpy as np

    server, frame_store = serving
    counts = np.full((120, 160), 20000, dtype=np.uint16)
    renderer = thermal_streamer.ThermalRenderer(
        thermal_streamer.build_palettes()["ironbow"],
        thermal_streamer.RenderSettings(rotation=90),
    )
    rendered = renderer.render_frame(counts)
    frame_store.publish(rendered.jpeg, rendered.stats, rendered.thermal)
    connection = connect_to(server)

    connection.request("GET", "/view")
    body = connection.getresponse().read().decode()

    # Rotated a quarter turn, so the picture is 120 across and 160 down.
    assert 'data-width="120"' in body
    assert 'data-height="160"' in body
    connection.close()


def test_the_viewer_page_is_served_before_any_frame_exists(serving):
    """And without the hint, rather than with a made up one."""

    server, _ = serving
    connection = connect_to(server)

    connection.request("GET", "/view")
    body = connection.getresponse().read().decode()

    assert "data-width" not in body
    assert 'id="surface"' in body
    connection.close()
