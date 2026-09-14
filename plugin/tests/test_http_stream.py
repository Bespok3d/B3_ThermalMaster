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
    assert 'action="/thermal/settings"' in body
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
        status = posting(connection, "action=shutter&palette=ironbow&rotation=0")

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
        body = "palette=sepia&rotation=90&action=shutter"
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
