#!/usr/bin/env python3
"""Capture the Thermal Master P1 over libusb and serve it as MJPEG.

The USB protocol lives in the vendored p3_camera driver; this module only colormaps the
16-bit thermal frame and serves /stream.mjpg + /snapshot.jpg over HTTP.
"""

from __future__ import annotations

import argparse
import io
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Vendored aarch64 deps (numpy, Pillow, pyusb) and the p3_camera driver sit next to this file,
# not in the shared daemon venv, so they must be on sys.path before the third-party imports.
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
sys.path.insert(0, str(VENDOR_DIR))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from p3_camera import Model, P3Camera, get_model_config, raw_to_celsius  # noqa: E402

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8082
JPEG_QUALITY = 80
UPSCALE_FACTOR = 4
NORMALIZE_LOW_PERCENTILE = 2.0
NORMALIZE_HIGH_PERCENTILE = 98.0
MIN_TEMPERATURE_SPAN_CELSIUS = 0.5
PALETTE_STEPS = 256
RECONNECT_DELAY_SECONDS = 3.0
FRAME_IDLE_SLEEP_SECONDS = 0.01
STREAM_WAIT_SECONDS = 1.0
MJPEG_BOUNDARY = "frame"

PALETTE_STOPS = (
    (0.0, (0, 0, 0)),
    (0.25, (60, 0, 110)),
    (0.5, (180, 40, 90)),
    (0.75, (250, 150, 30)),
    (1.0, (255, 255, 200)),
)


def build_ironbow_palette() -> np.ndarray:
    stop_positions = np.array([position for position, _ in PALETTE_STOPS], dtype=np.float32)
    stop_colors = np.array([color for _, color in PALETTE_STOPS], dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, PALETTE_STEPS, dtype=np.float32)
    palette = np.empty((PALETTE_STEPS, 3), dtype=np.uint8)
    for channel in range(3):
        palette[:, channel] = np.interp(ramp, stop_positions, stop_colors[:, channel]).astype(np.uint8)
    return palette


def colormap_thermal(thermal_raw: np.ndarray, palette: np.ndarray) -> np.ndarray:
    celsius = raw_to_celsius(thermal_raw)
    low = np.percentile(celsius, NORMALIZE_LOW_PERCENTILE)
    high = np.percentile(celsius, NORMALIZE_HIGH_PERCENTILE)
    span = max(high - low, MIN_TEMPERATURE_SPAN_CELSIUS)
    normalized = np.clip((celsius - low) / span, 0.0, 1.0)
    palette_index = (normalized * (PALETTE_STEPS - 1)).astype(np.uint8)
    return palette[palette_index]


def encode_jpeg(rgb_frame: np.ndarray) -> bytes:
    image = Image.fromarray(rgb_frame, mode="RGB")
    height, width = rgb_frame.shape[0], rgb_frame.shape[1]
    if UPSCALE_FACTOR != 1:
        image = image.resize((width * UPSCALE_FACTOR, height * UPSCALE_FACTOR), Image.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


class LatestFrame:
    def __init__(self) -> None:
        self._jpeg: bytes | None = None
        self._updated = threading.Condition()

    def publish(self, jpeg: bytes) -> None:
        with self._updated:
            self._jpeg = jpeg
            self._updated.notify_all()

    def snapshot(self) -> bytes | None:
        with self._updated:
            return self._jpeg

    def wait_next(self, timeout: float) -> bytes | None:
        with self._updated:
            self._updated.wait(timeout)
            return self._jpeg


def stream_frames(camera: P3Camera, frame_store: LatestFrame, palette: np.ndarray) -> None:
    while True:
        _, thermal_raw = camera.read_frame_both()
        if thermal_raw is None:
            time.sleep(FRAME_IDLE_SLEEP_SECONDS)
            continue
        frame_store.publish(encode_jpeg(colormap_thermal(thermal_raw, palette)))


def run_capture_session(frame_store: LatestFrame, palette: np.ndarray) -> None:
    camera = P3Camera(config=get_model_config(Model.P1))
    camera.connect()
    camera.init()
    camera.start_streaming()
    try:
        stream_frames(camera, frame_store, palette)
    finally:
        camera.disconnect()


def capture_loop(frame_store: LatestFrame, palette: np.ndarray) -> None:
    # A USB unplug or read error should reconnect and keep the stream alive, not kill the process.
    while True:
        try:
            run_capture_session(frame_store, palette)
        except Exception as error:  # noqa: BLE001
            print(f"thermal-p1: capture error: {error}", file=sys.stderr, flush=True)
            time.sleep(RECONNECT_DELAY_SECONDS)


class ThermalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], frame_store: LatestFrame) -> None:
        super().__init__(address, ThermalRequestHandler)
        self.frame_store = frame_store


class ThermalRequestHandler(BaseHTTPRequestHandler):
    @property
    def frames(self) -> LatestFrame:
        return self.server.frame_store  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        routes = {
            "/snapshot.jpg": self.serve_snapshot,
            "/stream.mjpg": self.serve_stream,
            "/": self.serve_stream,
        }
        handler = routes.get(self.path)
        if handler is None:
            self.send_error(404)
            return
        handler()

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream the Thermal Master P1 as MJPEG.")
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> None:
    options = parse_args()
    palette = build_ironbow_palette()
    frame_store = LatestFrame()
    worker = threading.Thread(target=capture_loop, args=(frame_store, palette), daemon=True)
    worker.start()
    server = ThermalServer((options.bind, options.port), frame_store)
    print(f"thermal-p1: serving http://{options.bind}:{options.port}/stream.mjpg", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
