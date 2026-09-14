#!/usr/bin/env python3
"""Capture the Thermal Master P1 over libusb and serve it as MJPEG.

The USB protocol lives in the vendored p3_camera driver; this module only colormaps the
16-bit thermal frame and serves /stream.mjpg + /snapshot.jpg over HTTP.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# The vendored USB protocol driver sits beside this file rather than in the plugin's environment,
# because it is an upstream source file and not a package (see VENDORING.md). numpy, Pillow and
# pyusb come from the environment, so this goes on the END of sys.path: at the front, anything left
# unpacked in the vendor directory wins against the installed package of the same name, which on
# the printer's own architecture swaps a dependency silently and anywhere else fails to load.
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
sys.path.append(str(VENDOR_DIR))

import numpy as np  # noqa: E402
import usb.core  # noqa: E402
from p3_camera import (  # noqa: E402
    VID,
    FrameMarkerMismatchError,
    Model,
    P3Camera,
    get_model_config,
)
from PIL import Image  # noqa: E402

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8082
JPEG_QUALITY = 80
UPSCALE_FACTOR = 4
NORMALIZE_LOW_PERCENTILE = 2.0
NORMALIZE_HIGH_PERCENTILE = 98.0
PALETTE_STEPS = 256

# Raw sensor counts are sixty-fourths of a Kelvin, so this floor is half a degree. Without it, a
# camera staring at a uniform surface divides by a span of zero.
MIN_SPAN_RAW_COUNTS = 32.0

# How fast the displayed range chases the scene, per frame. The bounds used to be recomputed from
# scratch every frame, so the whole image re-scaled whenever anything warm entered or left the view,
# which reads as a constant flicker. At 25 fps this settles a full swing in about a second.
BOUNDS_SMOOTHING = 0.15

# Averaging each frame with the one before halves the per-pixel sensor noise. Higher is more
# responsive and noisier; 1.0 disables it.
NOISE_REDUCTION_WEIGHT = 0.5

# Unsharp mask strength. Thermal scenes are naturally soft, and a little edge emphasis makes the
# difference between seeing a part and seeing a warm blob. 0 disables it.
DETAIL_STRENGTH = 0.3
# How many reads in a row may come back empty before the camera counts as stalled rather than slow.
# At the idle sleep below this is a fifth of a second of nothing, where a healthy camera delivers
# twenty-five frames a second.
MAX_CONSECUTIVE_FRAME_FAILURES = 20
INITIAL_RECONNECT_DELAY_SECONDS = 3.0
MAX_RECONNECT_DELAY_SECONDS = 60.0
RECONNECT_BACKOFF_FACTOR = 2.0
SHUTDOWN_GRACE_SECONDS = 5.0

# Both cameras speak the same protocol and differ only in sensor size, so the plugin drives
# whichever is plugged in rather than being told. The product IDs come from the driver's own model
# configs, so there is one place that knows them and it is not this file.
SUPPORTED_MODELS = (Model.P1, Model.P3)
FRAME_IDLE_SLEEP_SECONDS = 0.01
STREAM_WAIT_SECONDS = 1.0
MJPEG_BOUNDARY = "frame"

# Request path to handler name. Clients we do not control decorate these URLs: Fluidd and Mainsail
# add a cache-busting parameter to a snapshot, mjpg-streamer clients add an action, so the query
# string is discarded before the lookup. Exact match on what is left, never a prefix.
ROUTES = {
    "/snapshot.jpg": "serve_snapshot",
    "/stream.mjpg": "serve_stream",
    "/": "serve_stream",
}

# Palettes defined two ways, because they come in two shapes. A ramp interpolates between colour
# stops; a tint scales a grey ramp per channel, which is what makes military green and sepia warm.
PALETTE_RAMPS = {
    "ironbow": (
        (0.0, (0, 0, 0)),
        (0.25, (60, 0, 110)),
        (0.5, (180, 40, 90)),
        (0.75, (250, 150, 30)),
        (1.0, (255, 255, 200)),
    ),
    "rainbow": (
        (0.0, (0, 0, 140)),
        (0.25, (0, 180, 255)),
        (0.5, (0, 200, 60)),
        (0.75, (255, 220, 0)),
        (1.0, (190, 0, 0)),
    ),
}
PALETTE_TINTS = {
    "white-hot": (1.0, 1.0, 1.0),
    "military": (0.3, 1.0, 0.2),
    "sepia": (1.0, 0.7, 0.4),
}
DEFAULT_PALETTE = "ironbow"


def build_ramp_palette(stops: tuple) -> np.ndarray:
    stop_positions = np.array([position for position, _ in stops], dtype=np.float32)
    stop_colors = np.array([color for _, color in stops], dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, PALETTE_STEPS, dtype=np.float32)
    palette = np.empty((PALETTE_STEPS, 3), dtype=np.uint8)
    for channel in range(3):
        ramped_channel = np.interp(ramp, stop_positions, stop_colors[:, channel])
        palette[:, channel] = ramped_channel.astype(np.uint8)
    return palette


def build_tint_palette(tint: tuple) -> np.ndarray:
    grey = np.linspace(0.0, 255.0, PALETTE_STEPS, dtype=np.float32)
    palette = np.empty((PALETTE_STEPS, 3), dtype=np.uint8)
    for channel, scale in enumerate(tint):
        palette[:, channel] = (grey * scale).astype(np.uint8)
    return palette


def build_palettes() -> dict:
    """Every palette by name. Black hot is white hot read backwards, which is all it ever was."""

    palettes = {name: build_ramp_palette(stops) for name, stops in PALETTE_RAMPS.items()}
    palettes.update({name: build_tint_palette(tint) for name, tint in PALETTE_TINTS.items()})
    palettes["black-hot"] = palettes["white-hot"][::-1].copy()
    return palettes


def reduce_temporal_noise(
    current: np.ndarray, previous: np.ndarray | None, weight: float
) -> np.ndarray:
    """Average this frame with the last one, which halves per-pixel sensor noise."""

    if previous is None or weight >= 1.0:
        return current
    blended = weight * current.astype(np.float32) + (1.0 - weight) * previous.astype(np.float32)
    return blended.astype(np.uint16)


def frame_bounds(frame: np.ndarray) -> tuple[float, float]:
    """The raw counts to map to the ends of the palette.

    Percentiles rather than min and max, so one dead pixel or one glint does not take the whole
    range with it. Taken on raw counts rather than Celsius: the conversion is monotonic, so the
    percentiles land on the same pixels either way, and this skips converting the whole frame to
    float just to find two numbers.
    """

    return (
        float(np.percentile(frame, NORMALIZE_LOW_PERCENTILE)),
        float(np.percentile(frame, NORMALIZE_HIGH_PERCENTILE)),
    )


def smooth_bounds(
    previous: tuple[float, float] | None, current: tuple[float, float], smoothing: float
) -> tuple[float, float]:
    """Ease the displayed range towards the scene instead of snapping to it every frame."""

    if previous is None:
        return current
    return (
        smoothing * current[0] + (1.0 - smoothing) * previous[0],
        smoothing * current[1] + (1.0 - smoothing) * previous[1],
    )


def normalize_to_bytes(frame: np.ndarray, low: float, high: float) -> np.ndarray:
    span = max(high - low, MIN_SPAN_RAW_COUNTS)
    normalized = (frame.astype(np.float32) - low) / span
    return (np.clip(normalized, 0.0, 1.0) * (PALETTE_STEPS - 1)).astype(np.uint8)


def blur_3x3(image: np.ndarray) -> np.ndarray:
    """A separable 1-2-1 blur. Two passes over the frame, no image library needed."""

    padded = np.pad(image.astype(np.float32), 1, mode="edge")
    horizontal = (padded[:, :-2] + 2.0 * padded[:, 1:-1] + padded[:, 2:]) / 4.0
    return (horizontal[:-2, :] + 2.0 * horizontal[1:-1, :] + horizontal[2:, :]) / 4.0


def enhance_detail(image: np.ndarray, strength: float) -> np.ndarray:
    """Unsharp mask: add back a fraction of what a blur removed, which is the edges."""

    if strength <= 0.0:
        return image
    sharpened = image.astype(np.float32) + strength * (image.astype(np.float32) - blur_3x3(image))
    return np.clip(sharpened, 0.0, 255.0).astype(np.uint8)


class ThermalRenderer:
    """Raw sensor frames in, colourmapped RGB out, carrying the state that spans frames.

    Two things have to persist between frames and so cannot live in a function: the smoothed
    display bounds, and the previous frame the noise reduction averages against.
    """

    def __init__(
        self,
        palette: np.ndarray,
        bounds_smoothing: float = BOUNDS_SMOOTHING,
        noise_reduction_weight: float = NOISE_REDUCTION_WEIGHT,
        detail_strength: float = DETAIL_STRENGTH,
    ) -> None:
        self._palette = palette
        self._bounds_smoothing = bounds_smoothing
        self._noise_reduction_weight = noise_reduction_weight
        self._detail_strength = detail_strength
        self._bounds: tuple[float, float] | None = None
        self._previous_frame: np.ndarray | None = None

    @property
    def bounds(self) -> tuple[float, float] | None:
        """The smoothed display range in raw counts, or None before the first frame."""

        return self._bounds

    def render(self, thermal_raw: np.ndarray) -> np.ndarray:
        denoised = reduce_temporal_noise(
            thermal_raw, self._previous_frame, self._noise_reduction_weight
        )
        self._previous_frame = denoised
        self._bounds = smooth_bounds(self._bounds, frame_bounds(denoised), self._bounds_smoothing)
        normalized = normalize_to_bytes(denoised, *self._bounds)
        return self._palette[enhance_detail(normalized, self._detail_strength)]


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
        self._published_count = 0
        self._updated = threading.Condition()

    @property
    def published_count(self) -> int:
        """How many frames have ever been published: a working session from a failing one."""

        with self._updated:
            return self._published_count

    def publish(self, jpeg: bytes) -> None:
        with self._updated:
            self._jpeg = jpeg
            self._published_count += 1
            self._updated.notify_all()

    def snapshot(self) -> bytes | None:
        with self._updated:
            return self._jpeg

    def wait_next(self, timeout: float) -> bytes | None:
        with self._updated:
            self._updated.wait(timeout)
            return self._jpeg


class CameraStalledError(Exception):
    """The camera is still connected but has stopped producing usable frames."""


class CameraNotFoundError(Exception):
    """No supported thermal camera is on the USB bus."""


def detect_camera_model() -> Model | None:
    """Which supported camera is plugged in, or None if none is.

    Probing beats configuring: the P1 and the P3 differ only in sensor size, the driver already
    knows both, and a user who has to pick from a list is a user who can pick wrong and get a plugin
    that fails in a way the printer cannot explain.
    """

    for model in SUPPORTED_MODELS:
        product_id = get_model_config(model).pid
        if usb.core.find(idVendor=VID, idProduct=product_id) is not None:
            return model
    return None


def next_thermal_frame(camera: P3Camera) -> np.ndarray | None:
    """One thermal frame, or None when the camera returned nothing usable.

    A marker mismatch is a glitched frame, not a dead camera: the driver raises on it, and letting
    that reach the reconnect path costs seconds of dead video for a fault the next read clears.
    """

    try:
        _, thermal_raw = camera.read_frame_both()
    except FrameMarkerMismatchError:
        return None
    return thermal_raw


def stream_frames(
    camera: P3Camera,
    frame_store: LatestFrame,
    renderer: ThermalRenderer,
    shutdown: threading.Event,
) -> None:
    """Publish frames until the camera stalls or a shutdown is asked for.

    Empty reads are tolerated in ones and twos, because that is what a glitch looks like, and
    raised as a stall once there have been enough in a row. The distinction matters: the driver
    returns nothing at all when streaming has silently stopped, and the previous version of this
    loop treated that as a slow frame, so the stream froze on its last good image with the
    process still reporting itself healthy.
    """

    consecutive_failures = 0
    while not shutdown.is_set():
        thermal_raw = next_thermal_frame(camera)
        if thermal_raw is not None:
            consecutive_failures = 0
            frame_store.publish(encode_jpeg(renderer.render(thermal_raw)))
            continue
        consecutive_failures += 1
        if consecutive_failures >= MAX_CONSECUTIVE_FRAME_FAILURES:
            raise CameraStalledError(f"no usable frame in {consecutive_failures} reads")
        time.sleep(FRAME_IDLE_SLEEP_SECONDS)


def release_camera(camera: P3Camera) -> None:
    """Hand the USB interface back.

    stop_streaming resets the interface's alternate setting and disconnect releases the claim.
    Skipping either leaves the device claimed, and the next start cannot open it: from the outside
    that looks like a camera that needs unplugging. Both are best-effort, because this runs while
    unwinding from a failure and a camera that has already been pulled raises from both.
    """

    with contextlib.suppress(Exception):
        camera.stop_streaming()
    with contextlib.suppress(Exception):
        camera.disconnect()


def run_capture_session(
    frame_store: LatestFrame, renderer: ThermalRenderer, shutdown: threading.Event
) -> None:
    model = detect_camera_model()
    if model is None:
        raise CameraNotFoundError("no Thermal Master camera on the USB bus")
    camera = P3Camera(config=get_model_config(model))
    camera.connect()
    camera.init()
    camera.start_streaming()
    try:
        stream_frames(camera, frame_store, renderer, shutdown)
    finally:
        release_camera(camera)


def next_reconnect_delay(current_delay: float) -> float:
    """Back off after a failed session, up to a ceiling.

    A camera that is simply not plugged in fails instantly and forever, and a fixed retry turns that
    into a log line every three seconds until someone notices.
    """

    return min(current_delay * RECONNECT_BACKOFF_FACTOR, MAX_RECONNECT_DELAY_SECONDS)


def capture_loop(
    frame_store: LatestFrame, renderer: ThermalRenderer, shutdown: threading.Event
) -> None:
    """Keep a capture session running: an unplug or a read error reconnects, it never exits."""

    reconnect_delay = INITIAL_RECONNECT_DELAY_SECONDS
    while not shutdown.is_set():
        frames_before_session = frame_store.published_count
        try:
            run_capture_session(frame_store, renderer, shutdown)
            return
        except Exception as error:  # noqa: BLE001
            print(f"thermal-master: capture error: {error}", file=sys.stderr, flush=True)
        if frame_store.published_count > frames_before_session:
            reconnect_delay = INITIAL_RECONNECT_DELAY_SECONDS
        shutdown.wait(reconnect_delay)
        reconnect_delay = next_reconnect_delay(reconnect_delay)


def resolve_route(request_path: str) -> str | None:
    """The handler a raw request path asks for, or None if nothing serves it."""

    return ROUTES.get(urlparse(request_path).path)


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
        route = resolve_route(self.path)
        if route is None:
            self.send_error(404)
            return
        getattr(self, route)()

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
    parser.add_argument("--palette", default=DEFAULT_PALETTE, choices=sorted(build_palettes()))
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
    renderer = ThermalRenderer(build_palettes()[options.palette])
    frame_store = LatestFrame()
    shutdown = threading.Event()
    worker = threading.Thread(
        target=capture_loop, args=(frame_store, renderer, shutdown), daemon=True
    )
    worker.start()
    server = ThermalServer((options.bind, options.port), frame_store)
    install_shutdown_handlers(shutdown, server)
    listening_on = f"thermal-master: serving http://{options.bind}:{options.port}/stream.mjpg"
    print(listening_on, file=sys.stderr, flush=True)
    server.serve_forever()
    # Give the capture thread its chance to put the camera down before the process goes away. It is
    # a daemon thread, so without this the interpreter exits from under it mid-read.
    worker.join(SHUTDOWN_GRACE_SECONDS)
    print("thermal-master: stopped", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
