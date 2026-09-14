#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Time one frame through the streamer, stage by stage, on the machine that has to run it.

This exists because extrapolating from a development machine has now been wrong three times in a
row, twice by more than a factor of two: the stage ratios simply do not transfer from an x86 or
Apple laptop, or even from an aarch64 VM, to the printer's SoC. So the numbers get taken where they
matter.

It renders a synthetic frame and never opens the camera, so it is safe to run while the service is
streaming. It does compete with that service for the same CPU, so read the numbers as relative
weights between stages rather than as absolute costs.

On the printer:

    STREAMER=$(tr '\\0' '\\n' < /proc/$(pgrep -f thermal-master-stream | head -1)/cmdline \\
        | grep 'stream[.]py$')
    /userdata/bespok3d/venv-plugins/thermal-master/bin/python3 \\
        scripts/profile-frame-cost.py "$STREAMER" --rotate 270
"""

from __future__ import annotations

import argparse
import importlib.util
import statistics
import sys
import time
from pathlib import Path

FRAMES_PER_SECOND = 25.0
DEFAULT_REPEATS = 200
WARMUP_FRAMES = 20
# A ramp rather than a flat field: the percentile bounds and the unsharp mask both do less work on
# a frame with no structure in it, which would flatter the pipeline.
SCENE_LOW_CELSIUS = 22.0
SCENE_HIGH_CELSIUS = 45.0
RAW_UNITS_PER_KELVIN = 64
KELVIN_AT_ZERO_CELSIUS = 273.15


def load_streamer(path: Path):
    spec = importlib.util.spec_from_file_location("thermal_stream_profiled", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load a streamer from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def synthetic_frame(numpy_module, width: int, height: int):
    def raw(celsius: float) -> float:
        return (celsius + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN

    ramp = numpy_module.linspace(
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


def stages(streamer, frame, rotation: int):
    settings = streamer.RenderSettings
    palette = streamer.build_palettes()["ironbow"]

    def renderer(**overrides):
        return streamer.ThermalRenderer(palette, settings(rotation=rotation, **overrides))

    plain = renderer(overlay=False)
    doubled = renderer(overlay=False, upscale=2)
    marked = renderer(overlay=True)
    return [
        ("pipeline and statistics only", lambda: plain.render_image(frame)),
        ("plus encode at 1x, no readout", lambda: plain.render_frame(frame)),
        ("plus encode at 2x, no readout", lambda: doubled.render_frame(frame)),
        ("plus the readout drawn on it", lambda: marked.render_frame(frame)),
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
    frame = synthetic_frame(streamer.np, options.width, options.height)
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


if __name__ == "__main__":
    main()
