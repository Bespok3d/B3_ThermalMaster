#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Render a captured frame several ways, side by side, to settle a question about what to ship.

Written to answer the CLAHE question (section 6.4) and kept because the answer could change: if a
camera is ever pointed at a hotend, or a scene turns up that global ranging genuinely cannot serve,
this re-runs on the new capture in one command rather than being rebuilt from memory.

It reads `/thermal/frame.bin`, which is every pixel as hundredths of a degree in the orientation the
picture is displayed in:

    curl -fsS http://<printer>/thermal/frame.bin -o reference/frames/scene.bin
    python3 scripts/compare-rendering.py reference/frames/scene.bin --clahe --range 90 99

The panels are built from the temperatures rather than from raw counts, so "today" here is a very
close approximation of the live picture rather than a byte-identical copy: the percentiles land on
the same pixels, and the count to temperature curve is near enough linear across a scene's span to
leave the mid-tones where they were.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

LOW_PERCENTILE, HIGH_PERCENTILE = 2.0, 98.0
DETAIL_STRENGTH = 0.3
UPSCALE = 3
MINIMUM_SPAN_CELSIUS = 0.5
BACKGROUND = (13, 15, 18)
LABEL_RGB = (232, 232, 234)


def load_palettes(repository: Path):
    """The plugin's own palettes, by file rather than through the package.

    Importing `thermal_master.palettes` pulls in the package facade, which imports the vendored USB
    driver, which needs pyusb and a camera. This comparison runs on a laptop with a saved frame, so
    it takes the one module it needs and leaves the rest alone.
    """

    import importlib.util

    source = repository / "plugin" / "files" / "lib" / "thermal_master" / "palettes.py"
    spec = importlib.util.spec_from_file_location("thermal_master_palettes", source)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load the palettes from {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_palettes()


def decode(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if data[:4] != b"TMF1":
        raise SystemExit(f"{path} is not a thermal frame")
    width, height, scale = struct.unpack("<3H", data[4:10])
    values = np.frombuffer(data[16:16 + width * height * 2], dtype="<i2")
    return values.reshape((height, width)).astype(np.float32) / scale


def ranged(celsius: np.ndarray, low: float, high: float) -> np.ndarray:
    span = max(high - low, MINIMUM_SPAN_CELSIUS)
    return (np.clip((celsius - low) / span, 0.0, 1.0) * 255.0).astype(np.uint8)


def auto_ranged(celsius: np.ndarray) -> np.ndarray:
    low, high = np.percentile(celsius, (LOW_PERCENTILE, HIGH_PERCENTILE))
    return ranged(celsius, float(low), float(high))


def blur_3x3(image: np.ndarray) -> np.ndarray:
    padded = np.pad(image.astype(np.float32), 1, mode="edge")
    horizontal = (padded[:, :-2] + 2.0 * padded[:, 1:-1] + padded[:, 2:]) / 4.0
    return (horizontal[:-2, :] + 2.0 * horizontal[1:-1, :] + horizontal[2:, :]) / 4.0


def enhance(image: np.ndarray, strength: float = DETAIL_STRENGTH) -> np.ndarray:
    sharpened = image.astype(np.float32) + strength * (image.astype(np.float32) - blur_3x3(image))
    return np.clip(sharpened, 0.0, 255.0).astype(np.uint8)


def clahe(image: np.ndarray, grid: tuple[int, int] = (8, 8), clip: float = 2.0,
          bins: int = 256) -> np.ndarray:
    """Contrast limited adaptive histogram equalisation, in numpy, for the comparison only.

    Every tile is equalised against its own histogram, clipped so that a flat tile cannot stretch
    its noise across the whole palette, and the four tile mappings around each pixel are blended so
    the tiles do not show their seams.
    """

    height, width = image.shape
    rows, columns = grid
    tile_height = int(np.ceil(height / rows))
    tile_width = int(np.ceil(width / columns))
    padded = np.pad(
        image,
        ((0, rows * tile_height - height), (0, columns * tile_width - width)),
        mode="edge",
    )
    tiles = padded.reshape(rows, tile_height, columns, tile_width).transpose(0, 2, 1, 3)
    flat = tiles.reshape(rows * columns, tile_height * tile_width)
    offsets = (np.arange(rows * columns)[:, None] * bins).astype(np.int64)
    histogram = np.bincount(
        (offsets + flat).ravel(), minlength=rows * columns * bins
    ).reshape(rows * columns, bins).astype(np.float32)
    limit = max(clip * (tile_height * tile_width) / bins, 1.0)
    excess = np.maximum(histogram - limit, 0.0).sum(axis=1, keepdims=True)
    histogram = np.minimum(histogram, limit) + excess / bins
    cumulative = np.cumsum(histogram, axis=1)
    lowest = cumulative[:, :1]
    span = np.maximum(cumulative[:, -1:] - lowest, 1e-6)
    lookup = np.clip((cumulative - lowest) / span * 255.0, 0, 255).reshape(rows, columns, bins)

    down = np.clip((np.arange(height) + 0.5) / tile_height - 0.5, 0, rows - 1)
    across = np.clip((np.arange(width) + 0.5) / tile_width - 0.5, 0, columns - 1)
    top = np.floor(down).astype(int)
    left = np.floor(across).astype(int)
    bottom = np.minimum(top + 1, rows - 1)
    right = np.minimum(left + 1, columns - 1)
    weight_down = (down - top)[:, None]
    weight_across = (across - left)[None, :]
    upper = (lookup[top[:, None], left[None, :], image] * (1 - weight_across)
             + lookup[top[:, None], right[None, :], image] * weight_across)
    lower = (lookup[bottom[:, None], left[None, :], image] * (1 - weight_across)
             + lookup[bottom[:, None], right[None, :], image] * weight_across)
    return np.clip(upper * (1 - weight_down) + lower * weight_down, 0, 255).astype(np.uint8)


def texture(image: np.ndarray) -> float:
    """What a blur removes. On a surface that is really flat, this is the sensor's noise."""

    return float(np.std(image.astype(np.float32) - blur_3x3(image)))


def contact_sheet(panels: list, palette: np.ndarray, size: tuple[int, int]) -> Image.Image:
    width, height = size
    sheet = Image.new(
        "RGB", ((width * UPSCALE + 8) * len(panels), height * UPSCALE + 26), BACKGROUND
    )
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(panels):
        coloured = Image.fromarray(np.take(palette, image, axis=0), mode="RGB").resize(
            (width * UPSCALE, height * UPSCALE), Image.Resampling.NEAREST
        )
        left = index * (width * UPSCALE + 8)
        sheet.paste(coloured, (left, 24))
        draw.text((left + 4, 7), label, fill=LABEL_RGB)
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("frames", type=Path, nargs="+", help="captured .bin frames")
    parser.add_argument("--palette", default="ironbow")
    parser.add_argument("--clahe", action="store_true", help="add three CLAHE settings")
    parser.add_argument("--range", type=float, nargs=2, metavar=("LOW", "HIGH"),
                        help="add a panel ranged on these temperatures in Celsius")
    parser.add_argument("--out", type=Path, default=Path("."), help="where the sheets are written")
    options = parser.parse_args()

    repository = Path(__file__).resolve().parent.parent
    palette = load_palettes(repository)[options.palette]
    for path in options.frames:
        celsius = decode(path)
        panels = [("today: whole frame", enhance(auto_ranged(celsius)))]
        if options.clahe:
            base = auto_ranged(celsius)
            panels += [
                ("CLAHE 8x8 clip 2", enhance(clahe(base, (8, 8), 2.0))),
                ("CLAHE 8x8 clip 4", enhance(clahe(base, (8, 8), 4.0))),
                ("CLAHE 4x4 clip 3", enhance(clahe(base, (4, 4), 3.0))),
            ]
        if options.range:
            low, high = options.range
            panels.append((f"linear, ranged {low:.0f} to {high:.0f} C",
                           enhance(ranged(celsius, low, high))))
        height, width = celsius.shape
        sheet = contact_sheet(panels, palette, (width, height))
        destination = options.out / f"{path.stem}-comparison.png"
        sheet.save(destination)
        low, high = np.percentile(celsius, (LOW_PERCENTILE, HIGH_PERCENTILE))
        print(f"{path.stem}: {celsius.min():.1f} to {celsius.max():.1f} C, "
              f"auto-range {low:.1f} to {high:.1f}")
        print("   " + " | ".join(f"{label}: texture {texture(image):.1f}"
                                 for label, image in panels))
        print(f"   written to {destination}")


if __name__ == "__main__":
    main()
