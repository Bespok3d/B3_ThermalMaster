#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Time one frame through the streamer, step by step, on the machine that has to run it.

This exists because extrapolating from a development machine has now been wrong three times in a
row, twice by more than a factor of two: the stage ratios simply do not transfer from an x86 or
Apple laptop, or even from an aarch64 VM, to the printer's SoC. So the numbers get taken where they
matter.

Two tables. The first adds one stage at a time, which is what says how much of a frame the pipeline,
the encode and the readout are each worth. The second times every step inside the pipeline on its
own, which is what says where to start: F-56 named three suspects out of that list and measured
none of them.

It renders a synthetic frame and never opens the camera, so it is safe to run while the service is
streaming. It does compete with that service for the same CPU, so read the numbers as relative
weights between steps rather than as absolute costs.

Run it with `scripts/profile-on-printer.sh <host>`, which finds the interpreter and the entry
script in the running service's own command line and pipes this file in over stdin. Both of those
were hardcoded here once, from another plugin's documented layout, and both were wrong on a printer
where this plugin was running perfectly.
"""

from __future__ import annotations

import argparse
import io
import statistics
import sys
import time
from pathlib import Path

# Straight from the plugin's own virtual environment, which is the interpreter this is run with.
# Through the package facade would be tidier and would also be measuring whatever the facade
# happens to re-export, which is not the point of a profiler.
import numpy as np
from PIL import Image

FRAMES_PER_SECOND = 25.0
DEFAULT_REPEATS = 200
WARMUP_FRAMES = 20
# A ramp rather than a flat field: the percentile bounds and the unsharp mask both do less work on
# a frame with no structure in it, which would flatter the pipeline.
SCENE_LOW_CELSIUS = 22.0
SCENE_HIGH_CELSIUS = 45.0
RAW_UNITS_PER_KELVIN = 64
KELVIN_AT_ZERO_CELSIUS = 273.15


def load_streamer(entry: Path):
    """Import the plugin's package the way the service does.

    By import rather than by loading a file, and from the package rather than from the entry
    script: the entry script stopped being the plugin when the code moved into `thermal_master`
    (F-47), and a profiler that loads it by path measures an almost empty module. The argument
    stays the entry script's path because that is what the running service's command line gives,
    and the two directories beside it are the ones that script itself puts on the path.
    """

    plugin_files = entry.resolve() if entry.is_dir() else entry.resolve().parent.parent
    sys.path.append(str(plugin_files / "lib"))
    sys.path.append(str(plugin_files / "vendor"))
    try:
        import thermal_master
    except ImportError as error:
        raise SystemExit(f"cannot import the plugin from {plugin_files}: {error}") from error
    return thermal_master


def synthetic_frame(width: int, height: int):
    def raw(celsius: float) -> float:
        return (celsius + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN

    ramp = np.linspace(
        raw(SCENE_LOW_CELSIUS), raw(SCENE_HIGH_CELSIUS), width * height, dtype="float32"
    )
    return ramp.reshape((height, width)).astype("uint16")


def milliseconds_per_call(work, repeats: int) -> float:
    for _ in range(WARMUP_FRAMES):
        work()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        work()
        samples.append((time.perf_counter() - started) * 1000.0)
    # The median, because this runs alongside the live service and the mean chases its scheduling.
    return statistics.median(samples)


def report(label: str, milliseconds: float, baseline: float | None) -> None:
    share = milliseconds * FRAMES_PER_SECOND / 10.0
    against = "" if baseline is None else f"   {milliseconds - baseline:+6.2f} ms vs the line above"
    print(f"  {label:<34s} {milliseconds:6.2f} ms   {share:5.1f}% of a core{against}")


def bare_settings(streamer, rotation: int, **overrides):
    """Settings with nothing drawn on the picture, so a stage can be timed without the readout."""

    return streamer.RenderSettings(
        rotation=rotation, colorbar=False, reticle=False, hotspot=False, coldspot=False,
        **overrides,
    )


def stages(streamer, frame, rotation: int):
    palette = streamer.build_palettes()["ironbow"]
    plain = streamer.ThermalRenderer(palette, bare_settings(streamer, rotation))
    doubled = streamer.ThermalRenderer(palette, bare_settings(streamer, rotation, upscale=2))
    marked = streamer.ThermalRenderer(palette, streamer.RenderSettings(rotation=rotation))
    return [
        ("pipeline and statistics only", lambda: plain.render_image(frame)),
        ("plus encode at 1x, no readout", lambda: plain.render_frame(frame)),
        ("plus encode at 2x, no readout", lambda: doubled.render_frame(frame)),
        ("plus the readout drawn on it", lambda: marked.render_frame(frame)),
    ]


def steps(streamer, frame, rotation: int):
    """Every step of one frame, timed on its own against the input it really gets.

    Built in pipeline order, each fed the output of the one before it, so nothing is timed against
    a shape or a dtype it would never see. These do not add up to the cumulative table above:
    calling each one separately loses whatever the cache was holding for its neighbour.
    """

    settings = streamer.RenderSettings(rotation=rotation)
    palette = streamer.build_palettes()["ironbow"]
    previous = frame.copy()
    denoised = streamer.reduce_temporal_noise(frame, previous, settings.noise_reduction_weight)
    bounds = streamer.frame_bounds(denoised)
    normalized = streamer.normalize_to_bytes(denoised, *bounds)
    detailed = streamer.enhance_detail(normalized, settings.detail_strength)
    coloured = np.take(palette, detailed, axis=0)
    oriented = streamer.orient(coloured, rotation, False, False)
    stats = streamer.frame_statistics(
        denoised, bounds, rotation, settings.mirrors, settings.emissivity
    )
    overlay = streamer.Overlay(palette, stats, settings.units)
    upscale = streamer.encode_upscale((oriented.shape[1], oriented.shape[0]), 1, True)
    picture = Image.fromarray(oriented, mode="RGB")
    enlarged = (oriented.shape[1] * upscale, oriented.shape[0] * upscale)
    # One scratch image, drawn onto over and over. Copying it per call would put a memcpy of the
    # whole picture inside every readout measurement, which is the kind of overhead that gets
    # mistaken for the thing being measured.
    scratch = picture.resize(enlarged, Image.Resampling.BILINEAR)
    style = streamer.overlay_style(scratch.size, overlay.colorbar)
    markers = streamer.markers_for(overlay)
    label = streamer.format_temperature(stats.maximum_celsius, settings.units)

    def draw_markers() -> None:
        placed: list = []
        for marker in markers:
            streamer.draw_marker(scratch, marker, overlay, style, placed)

    def save_jpeg() -> None:
        scratch.save(io.BytesIO(), format="JPEG", quality=settings.jpeg_quality)

    return [
        ("noise reduction",
         lambda: streamer.reduce_temporal_noise(
             frame, previous, settings.noise_reduction_weight)),
        ("bounds, two percentiles", lambda: streamer.frame_bounds(denoised)),
        ("statistics and emissivity",
         lambda: streamer.frame_statistics(
             denoised, bounds, rotation, settings.mirrors, settings.emissivity)),
        ("normalise to bytes", lambda: streamer.normalize_to_bytes(denoised, *bounds)),
        ("unsharp mask, whole step",
         lambda: streamer.enhance_detail(normalized, settings.detail_strength)),
        ("of which the 3x3 blur", lambda: streamer.blur_3x3(normalized)),
        ("palette lookup, take", lambda: np.take(palette, detailed, axis=0)),
        ("palette lookup, indexing", lambda: palette[detailed]),
        ("orient", lambda: streamer.orient(coloured, rotation, False, False)),
        ("encode at 1x", lambda: streamer.encode_jpeg(oriented, 1, settings.jpeg_quality)),
        (f"encode at {upscale}x", lambda: streamer.encode_jpeg(
            oriented, upscale, settings.jpeg_quality)),
        (f"  of which the {upscale}x resize",
         lambda: picture.resize(enlarged, Image.Resampling.BILINEAR)),
        ("  the same resize, nearest",
         lambda: picture.resize(enlarged, Image.Resampling.NEAREST)),
        (f"  of which saving the {upscale}x jpeg", save_jpeg),
        ("draw the readout", lambda: streamer.draw_overlay(scratch, overlay)),
        ("  of which the colorbar", lambda: streamer.draw_colorbar(scratch, overlay, style)),
        ("  of which the markers", draw_markers),
        ("  of which one label", lambda: streamer.draw_label(scratch, (10.0, 10.0), label, style)),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("streamer", type=Path, help="path to thermal-master-stream.py")
    parser.add_argument("--width", type=int, default=160, help="sensor width, 160 P1, 256 P3")
    parser.add_argument("--height", type=int, default=120, help="sensor height, 120 P1, 192 P3")
    parser.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270))
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    return parser.parse_args()


def main() -> None:
    options = parse_args()
    streamer = load_streamer(options.streamer)
    frame = synthetic_frame(options.width, options.height)
    print("")
    print(f"thermal-master frame cost, {options.width}x{options.height} rotated {options.rotate}")
    print(f"  each line adds a stage to the one above it, at {FRAMES_PER_SECOND:.0f} fps")
    print("")
    previous = None
    for label, work in stages(streamer, frame, options.rotate):
        milliseconds = milliseconds_per_call(work, options.repeats)
        report(label, milliseconds, previous)
        previous = milliseconds
    print("")
    print("  inside the pipeline, each step timed on its own")
    print("")
    for label, work in steps(streamer, frame, options.rotate):
        report(label, milliseconds_per_call(work, options.repeats), None)
    print("")


if __name__ == "__main__":
    main()
