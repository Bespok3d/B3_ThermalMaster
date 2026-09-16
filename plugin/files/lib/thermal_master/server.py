# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: the stream, the snapshot, the statistics, the settings and the page.

Clients we do not control decorate these URLs, so the query string is discarded before the lookup
and the match on what is left is exact, never a prefix.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlparse

from .camera import SHUTTER_ACTION, SHUTTER_FIELD, SHUTTER_PENDING
from .page import describe_device, render_control_page
from .palettes import build_palettes
from .settings import camera_settings_from_form, settings_from_form
from .temperature import DEFAULT_UNITS, encode_thermal_frame
from .viewer import render_viewer_page

if TYPE_CHECKING:
    from .camera import DeviceController
    from .settings import SettingsStore
    from .temperature import FrameStats, ThermalFrame


DEFAULT_BIND = "127.0.0.1"


DEFAULT_PORT = 8082


# Where the browser is sent after a post, and it has to be relative. The plugin serves the page at
# "/" and nginx publishes it at "/thermal/", stripping the prefix on the way in, so the plugin never
# learns what the browser called it. An absolute "/" therefore sent anyone using the printer's web
# interface to the printer's home page on every change, which is the Fluidd dashboard. A relative
# reference resolves against the URL the browser asked for, so it lands on the control page whether
# that is /thermal/ or a direct connection to the port. The fragment puts it back at the controls
# rather than at the top.
SETTINGS_REDIRECT = "./#controls"


STREAM_WAIT_SECONDS = 1.0


MJPEG_BOUNDARY = "frame"


# Request path to handler name. Clients we do not control decorate these URLs: Fluidd and Mainsail
# add a cache-busting parameter to a snapshot, mjpg-streamer clients add an action, so the query
# string is discarded before the lookup. Exact match on what is left, never a prefix.
ROUTES = {
    "/snapshot.jpg": "serve_snapshot",
    "/stream.mjpg": "serve_stream",
    "/settings": "serve_settings",
    "/stats": "serve_stats",
    "/frame.bin": "serve_thermal_frame",
    "/view": "serve_viewer_page",
    "/": "serve_control_page",
}


POST_ROUTES = {"/settings": "apply_settings"}


class LatestFrame:
    def __init__(self) -> None:
        self._jpeg: bytes | None = None
        self._stats: FrameStats | None = None
        self._thermal: ThermalFrame | None = None
        self._published_count = 0
        self._updated = threading.Condition()

    @property
    def published_count(self) -> int:
        """How many frames have ever been published: a working session from a failing one."""

        with self._updated:
            return self._published_count

    def publish(
        self,
        jpeg: bytes,
        stats: FrameStats | None = None,
        thermal: ThermalFrame | None = None,
    ) -> None:
        with self._updated:
            self._jpeg = jpeg
            self._stats = stats
            self._thermal = thermal
            self._published_count += 1
            self._updated.notify_all()

    def latest_stats(self) -> FrameStats | None:
        """What the last published frame measured, or None before there was one."""

        with self._updated:
            return self._stats

    def latest_thermal(self) -> ThermalFrame | None:
        """The last frame's measurements, for a client that wants to read its own temperatures."""

        with self._updated:
            return self._thermal

    def snapshot(self) -> bytes | None:
        with self._updated:
            return self._jpeg

    def wait_next(self, timeout: float) -> bytes | None:
        with self._updated:
            self._updated.wait(timeout)
            return self._jpeg


def resolve_route(request_path: str) -> str | None:
    """The handler a raw request path asks for, or None if nothing serves it."""

    return ROUTES.get(urlparse(request_path).path)


class ThermalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        frame_store: LatestFrame,
        settings_store: SettingsStore | None = None,
        palettes: dict | None = None,
        device: DeviceController | None = None,
    ) -> None:
        super().__init__(address, ThermalRequestHandler)
        self.frame_store = frame_store
        self.settings_store = settings_store
        self.palettes = palettes if palettes is not None else build_palettes()
        self.device = device


class ThermalRequestHandler(BaseHTTPRequestHandler):
    # BaseHTTPRequestHandler types `self.server` as the base class, so everything this plugin
    # hangs off it has to be narrowed here. One cast in one place, rather than an ignore on
    # every use of it.
    @property
    def thermal_server(self) -> ThermalServer:
        return cast("ThermalServer", self.server)

    @property
    def frames(self) -> LatestFrame:
        return self.thermal_server.frame_store

    @property
    def settings_store(self) -> SettingsStore | None:
        return self.thermal_server.settings_store

    @property
    def palettes(self) -> dict:
        return self.thermal_server.palettes

    @property
    def device(self) -> DeviceController | None:
        return self.thermal_server.device

    def do_GET(self) -> None:
        route = resolve_route(self.path)
        if route is None:
            self.send_error(404)
            return
        getattr(self, route)()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler names it
        route = POST_ROUTES.get(urlparse(self.path).path)
        if route is None:
            self.send_error(404)
            return
        getattr(self, route)()

    def serve_viewer_page(self) -> None:
        """The interactive page. Needs no settings store, so it is served whatever else is wired."""

        self.send_html(render_viewer_page())

    def serve_thermal_frame(self) -> None:
        """Every pixel's temperature, as bytes, for a viewer that reads its own values.

        Converted here rather than in the browser so the emissivity correction has one
        implementation. Converted per request rather than per frame so it costs nothing at all
        unless somebody is actually looking at it, which is the whole reason the capture path stores
        raw counts.
        """

        thermal = self.frames.latest_thermal()
        if thermal is None:
            self.send_error(503, "no frame yet")
            return
        body = encode_thermal_frame(thermal)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_stats(self) -> None:
        """The numbers behind the picture, for anything that would rather draw its own overlay."""

        stats = self.frames.latest_stats()
        if stats is None:
            self.send_error(503, "no frame yet")
            return
        units = DEFAULT_UNITS
        if self.settings_store is not None:
            _, _, settings = self.settings_store.snapshot()
            units = settings.units
        payload = stats.as_dict(units)
        if self.device is not None:
            payload["device"] = self.device.status()
        self.send_json(payload)

    def settings_payload(self, settings_store: SettingsStore) -> dict:
        """The settings, plus one sentence on what the camera is doing and whether to ask again.

        The sentence comes from the same function that renders it into the page, so the wording
        cannot drift between the version a browser with JavaScript sees and the version one
        without it sees.

        The store is passed in rather than read off the handler, because on the handler it is
        optional: a server can be built without one, and both callers already check. Taking it as
        an argument is what makes that check provable here instead of assumed.
        """

        payload = settings_store.as_dict()
        status = self.device.status() if self.device is not None else None
        payload["device"] = describe_device(status)
        payload["pending"] = bool(
            status is not None and status.get("shutter", {}).get("state") == SHUTTER_PENDING
        )
        return payload

    def serve_settings(self) -> None:
        if self.settings_store is None:
            self.send_error(503, "settings are not available")
            return
        self.send_json(self.settings_payload(self.settings_store))

    def apply_settings(self) -> None:
        """Accept a posted form, then send the browser back to the page it came from."""

        if self.settings_store is None:
            self.send_error(503, "settings are not available")
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        _, current_palette, current = self.settings_store.snapshot()
        palette_name, settings = settings_from_form(form, self.palettes, current)
        self.settings_store.update(palette_name or current_palette, settings)
        _, current_camera = self.settings_store.camera_snapshot()
        camera = camera_settings_from_form(form, current_camera)
        if camera != current_camera:
            self.settings_store.update_camera(camera)
        # A button, not a setting: the capture thread picks this up between two frames.
        if self.device is not None and SHUTTER_ACTION in form.get(SHUTTER_FIELD, []):
            self.device.request_shutter()
        # A page with JavaScript posts in the background and wants the new state back, so that the
        # video stream is not torn down and reopened every time a palette changes. A page without
        # it gets the redirect, and both paths end up at the same place.
        if "application/json" in self.headers.get("Accept", ""):
            self.send_json(self.settings_payload(self.settings_store))
            return
        self.send_response(303)
        self.send_header("Location", SETTINGS_REDIRECT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_html(self, markup: str) -> None:
        page = markup.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page)

    def serve_control_page(self) -> None:
        if self.settings_store is None:
            self.serve_stream()
            return
        status = self.device.status() if self.device is not None else None
        self.send_html(
            render_control_page(self.settings_store.as_dict(), sorted(self.palettes), status)
        )

    def send_json(self, payload: dict) -> None:
        body = json.dumps(payload, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_snapshot(self) -> None:
        jpeg = self.frames.snapshot()
        if jpeg is None:
            self.send_error(503, "no frame yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.end_headers()
        self.wfile.write(jpeg)

    def serve_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        self.end_headers()
        self.stream_parts()

    def stream_parts(self) -> None:
        # The client closing the stream surfaces as a broken pipe; that ends this request quietly.
        try:
            self.write_parts_until_disconnect()
        except (BrokenPipeError, ConnectionResetError):
            return

    def write_parts_until_disconnect(self) -> None:
        while True:
            jpeg = self.frames.wait_next(STREAM_WAIT_SECONDS)
            if jpeg is not None:
                self.write_one_part(jpeg)

    def write_one_part(self, jpeg: bytes) -> None:
        self.wfile.write(f"--{MJPEG_BOUNDARY}\r\n".encode())
        self.wfile.write(b"Content-Type: image/jpeg\r\n")
        self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
        self.wfile.write(jpeg)
        self.wfile.write(b"\r\n")

    def log_message(self, format: str, *args: object) -> None:
        return
