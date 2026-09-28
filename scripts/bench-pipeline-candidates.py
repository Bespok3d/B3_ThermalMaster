#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Candidate replacements for the pipeline's expensive steps, timed and checked side by side.

The companion to `profile-frame-cost.py`, which says where the time goes. This one says what to do
about it, and it exists in this shape because the printer is not a machine anyone develops on: a
round trip to measure one idea costs a day, so every idea for a step is measured in the same run,
against the same frame, next to the answer it produces.

Nothing here is wired into the plugin. A candidate earns its way in by being faster on the printer
and by agreeing with what it replaces, and both are printed below rather than assumed. A candidate
that is faster and disagrees is a different picture, not an optimisation.

It never opens the camera, so it is safe to run while the service is streaming, and it competes
with that service for the CPU, so the comparisons are what matter rather than the absolute times.

Run it with `scripts/profile-on-printer.sh <host>`, which runs this after the profiler.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np

FRAMES_PER_SECOND = 25.0
DEFAULT_REPEATS = 200
WARMUP_FRAMES = 20
RAW_UNITS_PER_KELVIN = 64
KELVIN_AT_ZERO_CELSIUS = 273.15
SCENE_LOW_CELSIUS = 22.0
SCENE_HIGH_CELSIUS = 45.0
# Fixed point for the integer unsharp mask. The blur is carried at 16 times its value, so the
# strength is scaled by 4096 and shifted back by 12: the multiply then lands within a quarter of a
# percent of the float version, and the result is a byte either way.
DETAIL_FRACTION_BITS = 12


def load_streamer(entry: Path):
    plugin_files = entry.resolve() if entry.is_dir() else entry.resolve().parent.parent
    sys.path.append(str(plugin_files / "lib"))
    sys.path.append(str(plugin_files / "vendor"))
    try:
        import thermal_master
    except ImportError as error:
        raise SystemExit(f"cannot import the plugin from {plugin_files}: {error}") from error
    return thermal_master


def synthetic_frame(width: int, height: int):
    """A ramp with a warm blob in it.

    The ramp is what the profiler uses; the blob is here because two of these candidates are about
    edges, and a pure ramp has exactly one of them.
    """

    def raw(celsius: float) -> float:
        return (celsius + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN

    ramp = np.linspace(
        raw(SCENE_LOW_CELSIUS), raw(SCENE_HIGH_CELSIUS), width * height, dtype="float32"
    ).reshape((height, width))
    rows, columns = np.ogrid[:height, :width]
    blob = ((rows - height // 3) ** 2 + (columns - width // 3) ** 2) < (min(width, height) // 6) ** 2
    ramp[blob] = raw(SCENE_HIGH_CELSIUS + 25.0)
    return ramp.astype("uint16")


def milliseconds_per_call(work, repeats: int) -> float:
    for _ in range(WARMUP_FRAMES):
        work()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        work()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


# ---- the candidates -------------------------------------------------------------------------
#
# Each is written to be read beside the function it would replace, not to be clever.


def bounds_one_call(frame, low_percentile: float, high_percentile: float):
    """Both percentiles from one call, so the frame is partitioned once rather than twice."""

    low, high = np.percentile(frame, (low_percentile, high_percentile))
    return (float(low), float(high))


def bounds_subsampled(frame, low_percentile: float, high_percentile: float):
    """Both percentiles from every other row and column, which is a quarter of the pixels.

    Defensible because the answer feeds a display range that is eased towards over about a second:
    a percentile of a quarter of a smooth thermal scene is the same number to well inside what that
    smoothing hides.
    """

    sample = frame[::2, ::2]
    low, high = np.percentile(sample, (low_percentile, high_percentile))
    return (float(low), float(high))


def blend_integer(current, previous):
    """The default half-and-half average, without going through float32 and back.

    Only valid for a weight of one half, which is the default and the only value anything sets.
    """

    return ((current.astype(np.uint32) + previous.astype(np.uint32)) >> 1).astype(np.uint16)


def blur_integer(image):
    """The same separable 1-2-1 blur, in integers, carried at 16 times its value.

    The two passes multiply by four each, so dividing at the end is one shift instead of two float
    divisions over the whole frame. Kept as int16: the worst case after both passes is 255 times
    16, which is 4080.
    """

    padded = np.pad(image, 1, mode="edge").astype(np.int16)
    horizontal = padded[:, :-2] + 2 * padded[:, 1:-1] + padded[:, 2:]
    return horizontal[:-2, :] + 2 * horizontal[1:-1, :] + horizontal[2:, :]


def enhance_detail_integer(image, strength: float):
    """The unsharp mask in integers, on the bytes it was handed."""

    if strength <= 0.0:
        return image
    scaled = np.left_shift(image.astype(np.int32), 4)
    detail = scaled - blur_integer(image).astype(np.int32)
    multiplier = int(round(strength * (1 << DETAIL_FRACTION_BITS) / 16))
    sharpened = image.astype(np.int32) + np.right_shift(detail * multiplier, DETAIL_FRACTION_BITS)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def palette_take(palette, indices):
    """The same lookup through `take`, which for a gather like this may take a different path."""

    return np.take(palette, indices, axis=0)


# ---- the comparisons ------------------------------------------------------------------------


def compare(name: str, current, candidate, repeats: int, agreement: str) -> None:
    now = milliseconds_per_call(current, repeats)
    then = milliseconds_per_call(candidate, repeats)
    saved = now - then
    share = saved * FRAMES_PER_SECOND / 10.0
    print(f"  {name:<32s} {now:6.2f} ms -> {then:6.2f} ms   "
          f"{saved:+6.2f} ms ({share:+5.1f}% of a core)")
    print(f"  {'':<32s} {agreement}")


def counts_as_degrees(counts: float) -> str:
    return f"{counts / RAW_UNITS_PER_KELVIN:.3f} C"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("streamer", type=Path, help="path to thermal-master-stream.py")
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--height", type=int, default=120)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    options = parser.parse_args()

    streamer = load_streamer(options.streamer)
    frame = synthetic_frame(options.width, options.height)
    previous = synthetic_frame(options.width, options.height)
    previous[:, :] = np.roll(previous, 3, axis=1)
    settings = streamer.RenderSettings()
    palette = streamer.build_palettes()["ironbow"]
    low_percentile = streamer.NORMALIZE_LOW_PERCENTILE
    high_percentile = streamer.NORMALIZE_HIGH_PERCENTILE

    denoised = streamer.reduce_temporal_noise(frame, previous, settings.noise_reduction_weight)
    bounds = streamer.frame_bounds(denoised)
    normalized = streamer.normalize_to_bytes(denoised, *bounds)
    detailed = streamer.enhance_detail(normalized, settings.detail_strength)

    print("")
    print(f"pipeline candidates, {options.width}x{options.height}, "
          f"median of {options.repeats} calls at {FRAMES_PER_SECOND:.0f} fps")
    print("")

    one_call = bounds_one_call(denoised, low_percentile, high_percentile)
    drift = max(abs(one_call[0] - bounds[0]), abs(one_call[1] - bounds[1]))
    compare(
        "bounds: one percentile call",
        lambda: streamer.frame_bounds(denoised),
        lambda: bounds_one_call(denoised, low_percentile, high_percentile),
        options.repeats,
        f"same numbers to {counts_as_degrees(drift)}",
    )

    sampled = bounds_subsampled(denoised, low_percentile, high_percentile)
    drift = max(abs(sampled[0] - bounds[0]), abs(sampled[1] - bounds[1]))
    compare(
        "bounds: quarter of the pixels",
        lambda: streamer.frame_bounds(denoised),
        lambda: bounds_subsampled(denoised, low_percentile, high_percentile),
        options.repeats,
        f"range ends move by up to {counts_as_degrees(drift)}",
    )

    integer_blend = blend_integer(frame, previous)
    float_blend = streamer.reduce_temporal_noise(frame, previous, 0.5)
    worst = int(np.abs(integer_blend.astype(np.int32) - float_blend.astype(np.int32)).max())
    compare(
        "noise reduction: integers",
        lambda: streamer.reduce_temporal_noise(frame, previous, 0.5),
        lambda: blend_integer(frame, previous),
        options.repeats,
        f"worst pixel differs by {worst} counts ({counts_as_degrees(worst)})",
    )

    integer_detail = enhance_detail_integer(normalized, settings.detail_strength)
    worst = int(np.abs(integer_detail.astype(np.int32) - detailed.astype(np.int32)).max())
    changed = float(np.mean(integer_detail != detailed) * 100.0)
    compare(
        "unsharp mask: integers",
        lambda: streamer.enhance_detail(normalized, settings.detail_strength),
        lambda: enhance_detail_integer(normalized, settings.detail_strength),
        options.repeats,
        f"worst pixel differs by {worst} of 255, {changed:.1f}% of pixels differ at all",
    )

    same = bool(np.array_equal(palette_take(palette, detailed), palette[detailed]))
    compare(
        "palette lookup: take",
        lambda: palette[detailed],
        lambda: palette_take(palette, detailed),
        options.repeats,
        f"identical output: {same}",
    )
    print("")


if __name__ == "__main__":
    main()
