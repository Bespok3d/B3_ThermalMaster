# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Which request paths reach which endpoint.

A camera is fetched by clients we do not control, and they decorate URLs. Fluidd and Mainsail append
a cache-busting parameter to a snapshot so the browser cannot serve a stale one, and mjpg-streamer
clients append an action. Routing on the raw request path rejects all of those with a 404 while the
plain stream keeps working, which shows up in the UI as a camera tile that renders frames and is
still labelled an error.
"""

from __future__ import annotations


def test_a_bare_snapshot_path_routes_to_the_snapshot(thermal_streamer):
    assert thermal_streamer.resolve_route("/snapshot.jpg") == "serve_snapshot"


def test_a_cache_busted_snapshot_still_routes_to_the_snapshot(thermal_streamer):
    assert thermal_streamer.resolve_route("/snapshot.jpg?t=1789289601") == "serve_snapshot"


def test_an_mjpg_streamer_style_action_still_routes_to_the_stream(thermal_streamer):
    assert thermal_streamer.resolve_route("/stream.mjpg?action=stream") == "serve_stream"


def test_the_root_path_routes_to_the_control_page(thermal_streamer):
    """The endpoint Fluidd links to is the page a person opens, not a bare stream."""

    assert thermal_streamer.resolve_route("/") == "serve_control_page"


def test_settings_are_posted_to_their_own_path(thermal_streamer):
    assert thermal_streamer.POST_ROUTES["/settings"] == "apply_settings"


def test_nothing_else_accepts_a_post(thermal_streamer):
    """A GET route is not a POST route: the stream must not be writable by accident.

    The settings, and the list of timelapses, which is where one is deleted. Nothing else.
    """

    assert set(thermal_streamer.POST_ROUTES) == {"/settings", "/timelapses"}


def test_the_timelapses_have_their_own_paths(thermal_streamer):
    assert thermal_streamer.resolve_route("/timelapses") == "serve_timelapses"
    assert thermal_streamer.resolve_route("/timelapses.html") == "serve_timelapse_list"
    assert thermal_streamer.resolve_route("/timelapse.mp4?id=20260929-120000") == (
        "serve_timelapse_clip"
    )
    assert thermal_streamer.resolve_route("/timelapse.jpg?id=20260929-120000") == (
        "serve_timelapse_thumbnail"
    )
    assert thermal_streamer.POST_ROUTES["/timelapses"] == "change_timelapses"


def test_a_clip_is_named_by_query_not_by_path(thermal_streamer):
    """A path under the list is not a route, so no request can walk the folder by path."""

    assert thermal_streamer.resolve_route("/timelapses/20260929-120000/clip.mp4") is None


def test_an_unknown_path_has_no_route(thermal_streamer):
    assert thermal_streamer.resolve_route("/etc/passwd") is None


def test_a_path_that_merely_starts_with_a_known_one_has_no_route(thermal_streamer):
    """Discarding the query must not turn into prefix matching."""

    assert thermal_streamer.resolve_route("/snapshot.jpg.bak") is None


def test_the_statistics_have_their_own_path(thermal_streamer):
    assert thermal_streamer.resolve_route("/stats") == "serve_stats"


def test_the_statistics_are_read_only(thermal_streamer):
    """The numbers come out of the camera; nothing on the network may post them back."""

    assert "/stats" not in thermal_streamer.POST_ROUTES


def test_the_thermal_frame_has_its_own_path(thermal_streamer):
    assert thermal_streamer.resolve_route("/frame.bin") == "serve_thermal_frame"


def test_the_viewer_has_its_own_path(thermal_streamer):
    assert thermal_streamer.resolve_route("/view") == "serve_viewer_page"


def test_the_watchdog_has_its_own_path(thermal_streamer):
    assert thermal_streamer.resolve_route("/health") == "serve_health"


def test_a_restarted_stream_still_routes_to_the_stream(thermal_streamer):
    """The viewer reopens a dead stream under a new URL, and the new URL must be the same stream.

    A changing query string is what makes a browser open a new request rather than reuse the one
    that ended (Phase 7i, F-74).
    """

    assert thermal_streamer.resolve_route("/stream.mjpg?n=7") == "serve_stream"


def test_neither_accepts_a_post(thermal_streamer):
    for read_only in ("/view", "/frame.bin", "/health", "/stream.mjpg"):
        assert read_only not in thermal_streamer.POST_ROUTES


def test_a_client_that_leaves_mid_answer_is_not_an_error(thermal_streamer):
    """A browser closing a tab is routine, and a log full of routine tracebacks hides real ones.

    The viewer asks for a frame five times a second, so this is the most ordinary way a request
    can end. Checked at the routing level rather than per handler, because that is where the
    guarantee has to hold for handlers nobody has written yet.
    """

    class LeavingClient(thermal_streamer.ThermalRequestHandler):
        def __init__(self) -> None:  # the real one talks to a socket, and this never does
            self.served = False

        def serve_thermal_frame(self) -> None:
            self.served = True
            raise BrokenPipeError(32, "Broken pipe")

    handler = LeavingClient()
    handler.serve("serve_thermal_frame")

    assert handler.served is True


def test_a_handler_that_fails_for_its_own_reasons_still_raises(thermal_streamer):
    """The guard is for the client leaving, not for silencing the plugin's own faults."""

    import pytest

    class BrokenHandler(thermal_streamer.ThermalRequestHandler):
        def __init__(self) -> None:
            pass

        def serve_stats(self) -> None:
            raise ValueError("the statistics are wrong")

    with pytest.raises(ValueError):
        BrokenHandler().serve("serve_stats")
