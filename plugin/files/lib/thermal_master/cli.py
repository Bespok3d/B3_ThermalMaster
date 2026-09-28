# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Starting the whole thing: arguments, the capture thread, the server, and stopping cleanly."""

from __future__ import annotations

import argparse
import signal
import threading
from pathlib import Path

from .camera import DEFAULT_GAIN, VALID_GAINS, CameraSettings, DeviceController, capture_loop
from .log import log_line
from .palettes import DEFAULT_PALETTE, build_palettes
from .pipeline import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_UPSCALE,
    VALID_ROTATIONS,
    RenderSettings,
)
from .server import DEFAULT_BIND, DEFAULT_PORT, LatestFrame, ThermalServer
from .settings import RendererSource, SettingsStore
from .temperature import DEFAULT_EMISSIVITY, DEFAULT_UNITS, VALID_UNITS

SHUTDOWN_GRACE_SECONDS = 5.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream the Thermal Master P1 as MJPEG.")
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--palette", default=DEFAULT_PALETTE, choices=sorted(build_palettes()))
    parser.add_argument("--upscale", type=int, default=DEFAULT_UPSCALE)
    parser.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY)
    parser.add_argument("--rotate", type=int, default=0, choices=VALID_ROTATIONS)
    parser.add_argument("--flip-horizontal", action="store_true")
    parser.add_argument("--flip-vertical", action="store_true")
    parser.add_argument("--no-colorbar", action="store_true")
    parser.add_argument("--no-reticle", action="store_true")
    parser.add_argument("--no-hotspot", action="store_true")
    parser.add_argument("--no-coldspot", action="store_true")
    # Kept as the one switch it used to be, so an older service definition still means something.
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument("--units", default=DEFAULT_UNITS, choices=VALID_UNITS)
    parser.add_argument("--gain", default=DEFAULT_GAIN, choices=VALID_GAINS)
    parser.add_argument("--emissivity", type=float, default=DEFAULT_EMISSIVITY)
    parser.add_argument("--settings-file", default=None)
    return parser.parse_args()


def install_shutdown_handlers(shutdown: threading.Event, server: ThermalServer) -> None:
    """Unwind on the signal the daemon stops a service with.

    SIGTERM's default disposition kills the process outright, so the capture session never unwinds
    and the USB interface is left claimed with its alternate setting still set. The next start then
    cannot claim the camera. server.shutdown blocks until serve_forever returns and so cannot be
    called from the thread running it, which is why it goes on a thread of its own.
    """

    def request_shutdown(_received_signal: int, _frame: object) -> None:
        shutdown.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, request_shutdown)


def main() -> None:
    options = parse_args()
    palettes = build_palettes()
    # The command line supplies the starting point; anything saved from the control page overrides
    # it, so a restart keeps whatever the last person chose.
    settings_store = SettingsStore(
        options.palette,
        RenderSettings(
            upscale=options.upscale,
            jpeg_quality=options.jpeg_quality,
            rotation=options.rotate,
            flip_horizontal=options.flip_horizontal,
            flip_vertical=options.flip_vertical,
            colorbar=not (options.no_colorbar or options.no_overlay),
            reticle=not (options.no_reticle or options.no_overlay),
            hotspot=not (options.no_hotspot or options.no_overlay),
            coldspot=not (options.no_coldspot or options.no_overlay),
            units=options.units,
            emissivity=options.emissivity,
        ),
        Path(options.settings_file) if options.settings_file else None,
        CameraSettings(gain=options.gain),
    )
    renderer_source = RendererSource(settings_store, palettes)
    frame_store = LatestFrame()
    shutdown = threading.Event()
    device = DeviceController(settings_store)
    worker = threading.Thread(
        target=capture_loop, args=(frame_store, renderer_source, shutdown, device), daemon=True
    )
    worker.start()
    server = ThermalServer(
        (options.bind, options.port), frame_store, settings_store, palettes, device
    )
    install_shutdown_handlers(shutdown, server)
    log_line(f"serving http://{options.bind}:{options.port}/stream.mjpg")
    server.serve_forever()
    # Give the capture thread its chance to put the camera down before the process goes away. It is
    # a daemon thread, so without this the interpreter exits from under it mid-read.
    worker.join(SHUTDOWN_GRACE_SECONDS)
    log_line("stopped")
