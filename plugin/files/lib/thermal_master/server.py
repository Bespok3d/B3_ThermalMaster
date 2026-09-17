# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: the stream, the snapshot, the statistics, the settings and the page.

Clients we do not control decorate these URLs, so the query string is discarded before the lookup
and the match on what is left is exact, never a prefix.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlparse

from .camera import LOCK_RANGE_ACTION, SHUTTER_ACTION, SHUTTER_FIELD, SHUTTER_PENDING
from .page import describe_device, render_control_page
from .palettes import build_palettes
from .settings import (
    camera_settings_from_form,
    camera_settings_from_json,
    locked_range,
    settings_from_form,
    settings_from_json,
)
from .temperature import DEFAULT_UNITS, encode_thermal_frame
from .viewer import render_viewer_page

if TYPE_CHECKING:
    from .camera import CameraSettings, DeviceController
    from .pipeline import RenderSettings
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


# How long the plugin keeps rendering after the last request for a picture. A dashboard tile holds
# the stream open continuously, so this never trips while one is on screen; it is about the hours
# when nobody has the printer open at all. Measured before this existed: 40.6% of a core with
# nothing watching, against 45.1% with somebody actively pointing at the picture, so nine tenths of
# what the plugin cost was work nobody had asked for.
IDLE_AFTER_SECONDS = 60.0


# A frame older than this is not worth serving to somebody who just asked. It has to be longer than
# the gap between frames on a healthy camera and shorter than anything a person would call stale.
FRESH_ENOUGH_SECONDS = 1.0


# How long a request waits for the capture loop to wake up and render one. Two frames at the
# camera's rate, so a wake is invisible, and a camera that is not producing frames at all answers
# with what it has rather than hanging.
WAKE_WAIT_SECONDS = 0.5


class LatestFrame:
    """The current frame, and whether anybody has asked for one lately.

    The second half is what lets the capture loop stop rendering into an empty room. The interest
    lives here rather than in the server, because the loop and the handlers already share exactly
    this object and nothing else.
    """

    def __init__(self) -> None:
        self._jpeg: bytes | None = None
        self._stats: FrameStats | None = None
        self._thermal: ThermalFrame | None = None
        self._published_count = 0
        self._seen_count = 0
        self._published_at = 0.0
        self._asked_at = 0.0
        self._updated = threading.Condition()

    @property
    def published_count(self) -> int:
        """How many frames have ever been published: a working session from a failing one."""

        with self._updated:
            return self._published_count

    @property
    def seen_count(self) -> int:
        """Frames read from the camera, rendered or not.

        What the reconnect backoff counts, because a session that idled for an hour and then hit an
        error read plenty of frames and published none of them, and backing off as though the
        camera had never worked would make an unplug take minutes to notice.
        """

        with self._updated:
            return self._seen_count

    def note_read(self) -> None:
        """A frame arrived from the camera and was not rendered, because nobody is looking."""

        with self._updated:
            self._seen_count += 1

    def note_interest(self) -> None:
        """Somebody asked for a picture or for what it measured."""

        with self._updated:
            self._asked_at = time.monotonic()
            self._updated.notify_all()

    def wanted(self, within: float = IDLE_AFTER_SECONDS) -> bool:
        """Whether anything has asked recently enough to be worth rendering for."""

        with self._updated:
            return time.monotonic() - self._asked_at <= within

    def fresh(self, within: float = FRESH_ENOUGH_SECONDS) -> bool:
        """Whether the last frame is recent enough to answer a request with."""

        with self._updated:
            return self._fresh_enough(within)

    def wake(self, timeout: float = WAKE_WAIT_SECONDS) -> None:
        """Ask for frames, and give the capture loop a moment to produce one.

        Without the wait, the first request after an idle hour is answered with the picture from
        the beginning of that hour: the loop would wake up and render, a fraction of a second too
        late to be in this response.
        """

        self.note_interest()
        deadline = time.monotonic() + timeout
        with self._updated:
            # Waited for in a loop rather than once, because the condition is shared: another
            # request saying it is interested notifies it too, and a single wait would take that
            # for a frame and answer with the stale one it was trying to avoid.
            while not self._fresh_enough():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._updated.wait(remaining)

    def _fresh_enough(self, within: float = FRESH_ENOUGH_SECONDS) -> bool:
        return self._jpeg is not None and time.monotonic() - self._published_at <= within

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
            self._seen_count += 1
            self._published_at = time.monotonic()
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


@dataclasses.dataclass(frozen=True)
class RequestedChanges:
    """What one posted body asked for, whichever dialect it arrived in.

    A tuple until a fifth thing joined it, at which point the call site stopped saying what any of
    the positions meant.
    """

    palette_name: str | None
    settings: RenderSettings
    camera: CameraSettings
    shutter: bool
    lock_range: bool


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
        self.serve(route)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler names it
        route = POST_ROUTES.get(urlparse(self.path).path)
        if route is None:
            self.send_error(404)
            return
        self.serve(route)

    def serve(self, route: str) -> None:
        """Run a handler, and treat a client that left as the ordinary event it is.

        Every reader here is a browser that can close a tab mid-answer: the viewer fetches a frame
        every fifth of a second, and a tile closing between the headers and the body is normal
        rather than exceptional. Without this each one writes a traceback to the service log, and a
        log full of routine tracebacks is a log nobody reads when something real happens. The MJPEG
        stream has always swallowed its own; this covers every other handler, including the ones
        not written yet.
        """

        try:
            getattr(self, route)()
        except (BrokenPipeError, ConnectionResetError):
            return

    def serve_viewer_page(self) -> None:
        """The interactive page. Needs no settings store, so it is served whatever else is wired."""

        measured = self.frames.latest_stats()
        shape = (measured.width, measured.height) if measured else None
        self.send_html(render_viewer_page(shape))

    def serve_thermal_frame(self) -> None:
        """Every pixel's temperature, as bytes, for a viewer that reads its own values.

        Converted here rather than in the browser so the emissivity correction has one
        implementation. Converted per request rather than per frame so it costs nothing at all
        unless somebody is actually looking at it, which is the whole reason the capture path stores
        raw counts.
        """

        self.frames.wake()
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

        self.frames.wake()
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

    def requested_settings(self, settings_store: SettingsStore) -> RequestedChanges:
        """What a posted body asks for: a palette, render settings, camera settings, a button.

        Two dialects with one meaning. The control page posts a form, where a checkbox that is off
        is simply absent, so absent has to mean off. Anything changing a single setting posts JSON,
        where absent means absent and everything else is left alone. Reading them apart here keeps
        that difference in one place rather than spread through the handler.
        """

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", "replace")
        _, _, current = settings_store.snapshot()
        _, camera = settings_store.camera_snapshot()
        if "application/json" in self.headers.get("Content-Type", ""):
            payload = json.loads(body or "{}")
            palette_name, settings = settings_from_json(payload, self.palettes, current)
            asked = payload.get(SHUTTER_FIELD)
            return RequestedChanges(
                palette_name,
                settings,
                camera_settings_from_json(payload, camera),
                asked == SHUTTER_ACTION,
                asked == LOCK_RANGE_ACTION,
            )
        form = parse_qs(body)
        palette_name, settings = settings_from_form(form, self.palettes, current)
        commands = form.get(SHUTTER_FIELD, [])
        return RequestedChanges(
            palette_name,
            settings,
            camera_settings_from_form(form, camera),
            SHUTTER_ACTION in commands,
            LOCK_RANGE_ACTION in commands,
        )

    def apply_settings(self) -> None:
        """Accept a posted form, then send the browser back to the page it came from."""

        if self.settings_store is None:
            self.send_error(503, "settings are not available")
            return
        _, current_palette, _ = self.settings_store.snapshot()
        _, current_camera = self.settings_store.camera_snapshot()
        asked = self.requested_settings(self.settings_store)
        settings = asked.settings
        # Freezing the range is a button rather than two typed numbers, because the numbers worth
        # freezing are the ones the picture is already using. They come from the frame that was
        # last rendered, which is the one that was on screen when the button was pressed.
        if asked.lock_range:
            settings = locked_range(settings, self.frames.latest_stats())
        self.settings_store.update(asked.palette_name or current_palette, settings)
        if asked.camera != current_camera:
            self.settings_store.update_camera(asked.camera)
        # A button, not a setting: the capture thread picks this up between two frames.
        if self.device is not None and asked.shutter:
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
        self.frames.wake()
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
        self.frames.note_interest()
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
            # Said on every part rather than once at the start: a stream held open for an hour is
            # an hour of somebody watching, and the interest has to keep up with the clock or the
            # capture loop would idle underneath a tile that is plainly on screen.
            self.frames.note_interest()
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
