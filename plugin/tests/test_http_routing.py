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
    """A GET route is not a POST route: the stream must not be writable by accident."""

    assert set(thermal_streamer.POST_ROUTES) == {"/settings"}


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


def test_neither_accepts_a_post(thermal_streamer):
    assert set(thermal_streamer.POST_ROUTES) == {"/settings"}
