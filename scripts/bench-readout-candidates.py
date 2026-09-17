#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Candidates for the readout half: the upscale, the labels and the colorbar.

The second bench, and the same bargain as the first: every idea measured in one run on the machine
that has to do the work, with the answer it produces printed next to the time it takes. Nothing
here is wired into the plugin.

It exists because the readout half is now the larger one. Measured on the printer at 0.20.0, of a
15.03 ms frame: the 2x resize is 3.06 ms, saving the bigger JPEG 1.81 ms, the colorbar 1.59 ms and
the markers 1.22 ms, of which a single label is 0.24 ms. Five labels a frame is about 1.2 ms, which
makes label drawing the largest single thing inside the drawing, and it was not what the original
finding suspected.

It never opens the camera, so it is safe to run while the service is streaming.

Run it with `scripts/profile-on-printer.sh <host>`, which runs it after the other two.
"""

from __future__ import annotations

import argparse
import io
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

FRAMES_PER_SECOND = 25.0
DEFAULT_REPEATS = 200
WARMUP_FRAMES = 20
RAW_UNITS_PER_KELVIN = 64
KELVIN_AT_ZERO_CELSIUS = 273.15


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
    def raw(celsius: float) -> float:
        return (celsius + KELVIN_AT_ZERO_CELSIUS) * RAW_UNITS_PER_KELVIN

    ramp = np.linspace(raw(22.0), raw(45.0), width * height, dtype="float32").reshape(
        (height, width)
    )
    rows, columns = np.ogrid[:height, :width]
    ramp[((rows - height // 3) ** 2 + (columns - width // 3) ** 2) < 300] = raw(70.0)
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


def compare(name: str, current, candidate, repeats: int, agreement: str) -> None:
    now = milliseconds_per_call(current, repeats)
    then = milliseconds_per_call(candidate, repeats)
    saved = now - then
    print(f"  {name:<34s} {now:6.2f} ms -> {then:6.2f} ms   "
          f"{saved:+6.2f} ms ({saved * FRAMES_PER_SECOND / 10.0:+5.1f}% of a core)")
    print(f"  {'':<34s} {agreement}")


def measure(name: str, work, repeats: int, note: str = "") -> None:
    print(f"  {name:<34s} {milliseconds_per_call(work, repeats):6.2f} ms"
          f"   {'' if not note else note}")


# ---- the candidates -------------------------------------------------------------------------


LABEL_TILES: dict = {}


def label_tile(streamer, text: str, style, colour: tuple[int, int, int]) -> Image.Image:
    """A whole label composed once and kept, rather than six glyph pastes every frame.

    The readout writes one decimal place, so a label only changes when the tenth of a degree does,
    and a scene that is sitting still repeats the same strings for many frames. On a hit this is
    one paste instead of one per character; on a miss it is the same pastes into a small tile plus
    one more, which is what the cold row below measures.
    """

    key = (text, style.pixel_height, colour)
    tile = LABEL_TILES.get(key)
    if tile is not None:
        return tile
    width = max(int(streamer.label_width(text, style.pixel_height)) + 2, 2)
    tile = Image.new("RGBA", (width, style.pixel_height * 2 + 2), (0, 0, 0, 0))
    offset = 0.0
    for character in text:
        glyph = streamer.glyph_tile(character, style.pixel_height, colour)
        tile.paste(glyph, (int(offset), 0), glyph)
        offset += streamer.glyph_advance(character, style.pixel_height)
    LABEL_TILES[key] = tile
    return tile


def draw_label_cached(streamer, image, position, text, style, colour) -> None:
    tile = label_tile(streamer, text, style, colour)
    left, top = streamer.clamp_label(
        position, (float(tile.width), style.line_height), image.size
    )
    image.paste(tile, (left, top), tile)


def draw_label_direct(streamer, image, position, text, style, colour) -> None:
    """Pillow's own text drawing, twice, which is what the glyph cache was built to avoid.

    Worth re-measuring rather than assuming: the cache was justified on a development machine,
    where Pillow charges about 0.2 ms a call, and nothing about this printer's ratios has matched
    that machine yet.
    """

    font = streamer.overlay_font(style.pixel_height)
    left, top = streamer.clamp_label(
        position,
        (streamer.label_width(text, style.pixel_height), style.line_height),
        image.size,
    )
    draw = ImageDraw.Draw(image)
    draw.text((left + 1, top + 1), text, font=font, fill=streamer.OVERLAY_SHADOW_RGB)
    draw.text((left, top), text, font=font, fill=colour)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("streamer", type=Path, help="path to thermal-master-stream.py")
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--height", type=int, default=120)
    parser.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270))
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    options = parser.parse_args()

    streamer = load_streamer(options.streamer)
    frame = synthetic_frame(options.width, options.height)
    settings = streamer.RenderSettings(rotation=options.rotate)
    palette = streamer.build_palettes()["ironbow"]
    renderer = streamer.ThermalRenderer(palette, settings)
    renderer.render_frame(frame)
    image, stats, _ = renderer.render_image(frame)
    overlay = streamer.Overlay(palette, stats, settings.units)
    upscale = streamer.encode_upscale((image.shape[1], image.shape[0]), 1, True)
    picture = Image.fromarray(image, mode="RGB")
    enlarged = (image.shape[1] * upscale, image.shape[0] * upscale)
    scratch = picture.resize(enlarged, Image.Resampling.BILINEAR)
    style = streamer.overlay_style(scratch.size, overlay.colorbar)
    axis = streamer.bar_axis(stats)
    left, top, bar_width, bar_height = style.bar_box
    label = streamer.format_temperature(stats.maximum_celsius, settings.units)
    colour = streamer.HOTSPOT_RGB
    changing = [f"{value / 10.0:.1f}C" for value in range(200, 200 + options.repeats * 2)]
    counter = {"at": 0}

    def fresh_label() -> str:
        counter["at"] = (counter["at"] + 1) % len(changing)
        return changing[counter["at"]]

    print("")
    print(f"readout candidates, {options.width}x{options.height} rotated {options.rotate}, "
          f"median of {options.repeats} calls at {FRAMES_PER_SECOND:.0f} fps")
    print("")

    smooth = picture.resize(enlarged, Image.Resampling.BILINEAR)
    blocky = picture.resize(enlarged, Image.Resampling.NEAREST)
    difference = float(
        np.abs(np.asarray(smooth, dtype=np.int16) - np.asarray(blocky, dtype=np.int16)).mean()
    )
    compare(
        "upscale: nearest not bilinear",
        lambda: picture.resize(enlarged, Image.Resampling.BILINEAR),
        lambda: picture.resize(enlarged, Image.Resampling.NEAREST),
        options.repeats,
        f"pixels differ by {difference:.1f} of 255 on average, edges become steps",
    )
    compare(
        "upscale: numpy repeat",
        lambda: picture.resize(enlarged, Image.Resampling.NEAREST),
        lambda: Image.fromarray(np.repeat(np.repeat(image, upscale, axis=0), upscale, axis=1)),
        options.repeats,
        "same picture as nearest, different library doing it",
    )

    print("")
    compare(
        "one label: cached whole tile",
        lambda: streamer.draw_label(scratch, (10.0, 10.0), label, style, colour),
        lambda: draw_label_cached(streamer, scratch, (10.0, 10.0), label, style, colour),
        options.repeats,
        "identical pixels, one paste instead of one per character",
    )
    compare(
        "one label: cached, always cold",
        lambda: streamer.draw_label(scratch, (10.0, 10.0), fresh_label(), style, colour),
        lambda: draw_label_cached(streamer, scratch, (10.0, 10.0), fresh_label(), style, colour),
        options.repeats,
        "the worst case: a string never seen before, every frame",
    )
    compare(
        "one label: pillow draws the text",
        lambda: streamer.draw_label(scratch, (10.0, 10.0), label, style, colour),
        lambda: draw_label_direct(streamer, scratch, (10.0, 10.0), label, style, colour),
        options.repeats,
        "shadow and text, two calls, no cache at all",
    )

    print("")
    print("  the colorbar, in pieces")
    print("")
    ramp = streamer.bar_gradient(palette, axis, stats, bar_height)
    bar = Image.fromarray(ramp.astype(np.uint8), mode="RGB")
    measure("gradient, the numpy part",
            lambda: streamer.bar_gradient(palette, axis, stats, bar_height), options.repeats)
    measure("gradient to an image and pasted",
            lambda: scratch.paste(
                Image.fromarray(ramp.astype(np.uint8), mode="RGB").resize(
                    (bar_width, bar_height), Image.Resampling.NEAREST),
                (left, top)),
            options.repeats)
    measure("just the paste",
            lambda: scratch.paste(bar.resize((bar_width, bar_height),
                                             Image.Resampling.NEAREST), (left, top)),
            options.repeats)
    measure("the outline rectangle",
            lambda: ImageDraw.Draw(scratch).rectangle(
                (left, top, left + bar_width - 1, top + bar_height - 1), outline=(0, 0, 0)),
            options.repeats)
    measure("the whole colorbar, as it ships",
            lambda: streamer.draw_colorbar(scratch, overlay, style), options.repeats)
    print("")
    # Saved so the JPEG of the last drawing can be looked at if a number here surprises anyone.
    scratch.save(io.BytesIO(), format="JPEG", quality=settings.jpeg_quality)


if __name__ == "__main__":
    main()
