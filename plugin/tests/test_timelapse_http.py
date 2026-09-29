# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The timelapse over HTTP: the list, the clip a browser can seek in, deleting one, the settings.

Against the real server on an ephemeral port. The clip is a file of known bytes rather than a real
video, because what is under test is which bytes go out, not what they show.
"""

from __future__ import annotations

import http.client
import json
import threading
import urllib.parse

import pytest

STARTED = 1_790_000_000.0
CLIP_BYTES = bytes(range(256)) * 40


@pytest.fixture
def serving(thermal_streamer, tmp_path, palettes):
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    service = thermal_streamer.TimelapseService(
        thermal_streamer.TimelapseWiring(
            root=tmp_path, client=thermal_streamer.MoonrakerClient("http://127.0.0.1:9"),
            settings_store=store, tap=thermal_streamer.FrameTap(), streaming=lambda: True,
            palettes=palettes, ffmpeg=None,
        )
    )
    server = thermal_streamer.ThermalServer(
        ("127.0.0.1", 0), thermal_streamer.LatestFrame(), store, palettes
    )
    server.timelapses = service
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, store
    server.shutdown()
    server.server_close()


def finished(streamer, root):
    recording = streamer.Recording.create(root, STARTED, "0002F1", "cube.gcode")
    recording.finish("complete")
    recording.clip_path.write_bytes(CLIP_BYTES)
    return recording


def ask(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=5.0)
    connection.request(method, path, body=body, headers=headers or {})
    reply = connection.getresponse()
    answer = (reply.status, dict(reply.getheaders()), reply.read())
    connection.close()
    return answer


def test_the_list_names_every_print(thermal_streamer, serving, tmp_path):
    server, _ = serving
    recording = finished(thermal_streamer, tmp_path)

    status, _, body = ask(server, "GET", "/timelapses")
    listed = json.loads(body)

    assert status == 200
    assert [entry["id"] for entry in listed["timelapses"]] == [recording.recording_id]
    assert listed["timelapses"][0]["clip_bytes"] == len(CLIP_BYTES)
    assert listed["status"]


def test_the_clip_is_served_whole(thermal_streamer, serving, tmp_path):
    server, _ = serving
    recording = finished(thermal_streamer, tmp_path)

    status, headers, body = ask(server, "GET", f"/timelapse.mp4?id={recording.recording_id}")

    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"
    assert body == CLIP_BYTES


def test_a_browser_seeking_gets_the_part_it_asked_for(thermal_streamer, serving, tmp_path):
    server, _ = serving
    recording = finished(thermal_streamer, tmp_path)

    status, headers, body = ask(
        server, "GET", f"/timelapse.mp4?id={recording.recording_id}",
        headers={"Range": "bytes=100-199"},
    )

    assert status == 206
    assert headers["Content-Range"] == f"bytes 100-199/{len(CLIP_BYTES)}"
    assert body == CLIP_BYTES[100:200]


def test_a_download_is_named_for_the_print(thermal_streamer, serving, tmp_path):
    server, _ = serving
    recording = finished(thermal_streamer, tmp_path)

    _, headers, _ = ask(server, "GET", f"/timelapse.mp4?id={recording.recording_id}&download=1")

    assert headers["Content-Disposition"] == f'attachment; filename="{recording.clip_name}"'


def test_a_clip_that_is_not_there_is_a_404(serving):
    server, _ = serving

    for wanted in ("20260921-141320", "../settings", ""):
        status, _, _ = ask(server, "GET", f"/timelapse.mp4?id={urllib.parse.quote(wanted)}")
        assert status == 404


def test_a_print_is_deleted_from_the_page_button(thermal_streamer, serving, tmp_path):
    server, _ = serving
    recording = finished(thermal_streamer, tmp_path)

    status, headers, _ = ask(
        server, "POST", "/timelapses", body=f"delete={recording.recording_id}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert status == 303
    assert headers["Location"] == "./#timelapses"
    assert not recording.folder.exists()


def test_a_print_is_deleted_by_json(thermal_streamer, serving, tmp_path):
    server, _ = serving
    recording = finished(thermal_streamer, tmp_path)

    status, _, body = ask(
        server, "POST", "/timelapses", body=json.dumps({"delete": recording.recording_id}),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    assert status == 200
    assert json.loads(body)["timelapses"] == []


def test_the_timelapse_is_switched_on_through_the_settings(serving):
    server, store = serving

    ask(server, "POST", "/settings",
        body=json.dumps({"timelapse": True, "moonraker_api_key": "a-made-up-key"}),
        headers={"Content-Type": "application/json"})
    status, _, body = ask(server, "GET", "/settings")

    assert store.timelapse_snapshot().timelapse is True
    assert store.timelapse_snapshot().moonraker_api_key == "a-made-up-key"
    assert b"a-made-up-key" not in body
    assert json.loads(body)["moonraker_api_key_set"] is True
    assert "timelapse_status" in json.loads(body)


def test_a_service_without_a_timelapse_folder_says_so(thermal_streamer, palettes):
    server = thermal_streamer.ThermalServer(("127.0.0.1", 0), thermal_streamer.LatestFrame())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, _, body = ask(server, "GET", "/timelapses")
    finally:
        server.shutdown()
        server.server_close()

    assert json.loads(body) == {
        "status": thermal_streamer.TIMELAPSE_UNAVAILABLE, "timelapses": []
    }


def test_a_range_header_is_read_the_way_a_video_player_sends_it(thermal_streamer):
    requested_range = thermal_streamer.requested_range

    assert requested_range("bytes=0-", 1000) == (0, 999)
    assert requested_range("bytes=500-", 1000) == (500, 999)
    assert requested_range("bytes=990-2000", 1000) == (990, 999)
    assert requested_range("bytes=-100", 1000) == (900, 999)
    assert requested_range("bytes=2000-", 1000) is None
    assert requested_range("bytes=0-1,5-9", 1000) is None
    assert requested_range(None, 1000) is None


def test_the_list_is_served_as_the_page_draws_it(thermal_streamer, serving, tmp_path):
    """For the page to swap in when a clip is finished while it is open."""

    server, _ = serving
    recording = finished(thermal_streamer, tmp_path)

    status, headers, body = ask(server, "GET", "/timelapses.html")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert body.decode() == thermal_streamer.timelapse_list(
        {"timelapses": [recording.summary()]}
    )
