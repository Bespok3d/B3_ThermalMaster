# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""What the timelapse asks Moonraker, against a stand-in Moonraker on a local port.

The three outcomes are kept apart because the settings page says different things for each: an
answer, no answer, and a refusal. A refusal is the one that can be fixed from the page, with the
API key, so it must never be mistaken for Moonraker simply not being there.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

PRINTING_ANSWER = {
    "result": {
        "eventtime": 1.0,
        "status": {
            "print_stats": {
                "state": "printing",
                "filename": "cube.gcode",
                "info": {"total_layer": 75, "current_layer": 12},
            }
        },
    }
}


NEWEST_JOB_ANSWER = {
    "result": {
        "count": 1,
        "jobs": [{"job_id": "0002F1", "filename": "cube.gcode", "start_time": 1790000000.5,
                  "status": "in_progress"}],
    }
}


class StandInMoonraker(BaseHTTPRequestHandler):
    answers: dict = {}
    refuse_without: str | None = None
    keys_seen: list = []

    def do_GET(self):  # noqa: N802 - the name BaseHTTPRequestHandler calls
        self.keys_seen.append(self.headers.get("X-Api-Key"))
        if self.refuse_without and self.headers.get("X-Api-Key") != self.refuse_without:
            self.send_error(401)
            return
        answer = self.answers.get(self.path)
        if answer is None:
            self.send_error(404)
            return
        body = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    received: list = []

    def do_POST(self):  # noqa: N802 - the name BaseHTTPRequestHandler calls
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.received.append((self.path, self.headers.get("Content-Type"), body))
        self.answer({"result": {"action": "create_file"}})

    def do_DELETE(self):  # noqa: N802 - the name BaseHTTPRequestHandler calls
        self.received.append((self.path, None, b""))
        if "missing" in self.path:
            self.send_error(404)
            return
        self.answer({"result": {"action": "delete_file"}})

    def answer(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


@pytest.fixture
def moonraker(thermal_streamer):
    StandInMoonraker.answers = {
        thermal_streamer.PRINT_STATS_QUERY: PRINTING_ANSWER,
        thermal_streamer.NEWEST_JOB_QUERY: NEWEST_JOB_ANSWER,
    }
    StandInMoonraker.answers.update({
        thermal_streamer.ROOTS_QUERY: {"result": [{"name": "gcodes"}, {"name": "timelapse"}]},
        "/server/files/list?root=timelapse": {"result": [{"path": "cube_20260921141320.mp4"},
                                                          {"path": "cube_20260921141320.jpg"}]},
        "/server/files/directory?path=timelapse": {"result": {"disk_usage": {"free": 862000000}}},
    })
    StandInMoonraker.refuse_without = None
    StandInMoonraker.keys_seen = []
    StandInMoonraker.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), StandInMoonraker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_print_and_its_layer_are_read(thermal_streamer, moonraker):
    status = thermal_streamer.MoonrakerClient(moonraker).print_status()

    assert status == thermal_streamer.PrintStatus("printing", "cube.gcode", 12, 75)
    assert status.active


def test_the_newest_job_names_the_print(thermal_streamer, moonraker):
    job = thermal_streamer.MoonrakerClient(moonraker).newest_job()

    assert job == thermal_streamer.JobInfo("0002F1", "cube.gcode", 1790000000.5)


def test_layers_the_slicer_never_sent_are_none(thermal_streamer, moonraker):
    StandInMoonraker.answers[thermal_streamer.PRINT_STATS_QUERY] = {
        "result": {"status": {"print_stats": {"state": "standby", "filename": "",
                                              "info": {"total_layer": None,
                                                       "current_layer": None}}}}
    }

    status = thermal_streamer.MoonrakerClient(moonraker).print_status()

    assert status.current_layer is None and status.total_layer is None
    assert not status.active


def test_a_moonraker_that_is_not_there_is_none(thermal_streamer):
    assert thermal_streamer.MoonrakerClient("http://127.0.0.1:9").print_status() is None


def test_a_login_moonraker_asks_for_is_a_refusal_not_an_absence(thermal_streamer, moonraker):
    StandInMoonraker.refuse_without = "a-made-up-key"

    with pytest.raises(thermal_streamer.MoonrakerRefusedError):
        thermal_streamer.MoonrakerClient(moonraker).print_status()


def test_the_key_is_sent_when_there_is_one(thermal_streamer, moonraker):
    StandInMoonraker.refuse_without = "a-made-up-key"

    status = thermal_streamer.MoonrakerClient(moonraker, lambda: "a-made-up-key").print_status()

    assert status is not None
    assert StandInMoonraker.keys_seen == ["a-made-up-key"]


def test_no_key_header_goes_when_none_is_set(thermal_streamer, moonraker):
    thermal_streamer.MoonrakerClient(moonraker).print_status()

    assert StandInMoonraker.keys_seen == [None]


def test_the_timelapse_folder_is_found_among_the_roots(thermal_streamer, moonraker):
    client = thermal_streamer.MoonrakerClient(moonraker)

    assert client.has_root("timelapse")
    assert not client.has_root("camera")


def test_the_folders_files_and_free_space_are_read(thermal_streamer, moonraker):
    client = thermal_streamer.MoonrakerClient(moonraker)

    assert client.file_names("timelapse") == ["cube_20260921141320.mp4", "cube_20260921141320.jpg"]
    assert client.free_space("timelapse") == 862000000


def test_a_clip_is_uploaded_into_the_folder_by_name(thermal_streamer, moonraker, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"\x00clip bytes\xff")

    assert thermal_streamer.MoonrakerClient(moonraker).upload(
        "timelapse", "cube thermal.mp4", source
    )

    ((path, content_type, body),) = StandInMoonraker.received
    assert path == "/server/files/upload"
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="root"\r\n\r\ntimelapse\r\n' in body
    assert b'filename="cube thermal.mp4"' in body
    assert b"\x00clip bytes\xff" in body


def test_a_file_is_deleted_by_its_quoted_path(thermal_streamer, moonraker):
    client = thermal_streamer.MoonrakerClient(moonraker)

    assert client.delete_file("timelapse", "cube thermal+1.mp4")
    assert not client.delete_file("timelapse", "missing.mp4")
    assert StandInMoonraker.received[0][0] == "/server/files/timelapse/cube%20thermal%2B1.mp4"
