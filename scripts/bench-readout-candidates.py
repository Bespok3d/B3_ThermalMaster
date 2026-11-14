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

Added on 2026-11-14: the readout drawn once and kept, as patches pasted every frame, which is
what both "redraw the ruler only when its numbers change" and "refresh the readout a few times
a second" come down to. It is measured at the size the service sends, which for a smoothly
enlarged P3 is not the size the older rows above it use.

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
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw

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
    start_allocator_as_the_service_does(thermal_master)
    return thermal_master


def start_allocator_as_the_service_does(streamer) -> None:
    """Ask the C library to keep freed memory, if the plugin under test does so when it starts.

    The service asks once, in `main`, which nothing here calls (F-79). Left out, every number
    below would be for a process set up differently from the one that serves the picture. A
    version from before 0.30.3 has nothing to ask with and is measured as it runs, unasked.
    """

    ask = getattr(streamer, "keep_freed_memory", None)
    if ask is None:
        return
    kept = ask(streamer.glibc_mallopt())
    print(f"allocator: {'keeps' if kept else 'was not asked to keep'} freed memory, "
          "as the service on this machine does", file=sys.stderr)


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


# ---- the readout drawn once and kept --------------------------------------------------------
#
# Two ideas, measured side by side because the second is the first with a clock on it. The ruler
# could be drawn once and pasted until one of its numbers changes; with the range following the
# scene its ends move most frames, so how often that helps depends on what the camera is looking
# at. Or the whole readout, the ruler and the markers, could be worked out a few times a second
# and pasted in between, as the maker's phone app appears to: then how often it is drawn is a
# number somebody chose, and the markers trail what they point at by up to that long.


def kept_patch(size: tuple[int, int], draw_onto, cropped: bool = True):
    """What `draw_onto` draws, lifted off the picture: a patch with its coverage, and its corner.

    Drawn twice with the plugin's own functions, once on black and once on white. Where the two
    agree the drawing is solid, where they differ by everything it is absent, and in between it
    covers that share of the pixel. So the colour and the coverage come out without this knowing
    how anything was drawn, and whatever the plugin draws next is covered without a change here.
    Cropped to what was drawn, so that pasting it touches only those pixels.
    """

    on_black = Image.new("RGB", size, (0, 0, 0))
    on_white = Image.new("RGB", size, (255, 255, 255))
    draw_onto(on_black)
    draw_onto(on_white)
    # Found by Pillow before numpy sees anything, so the arithmetic below runs over the patch
    # and not over the picture: the two differ by 255 exactly where nothing was drawn.
    untouched = ImageChops.difference(on_white, on_black)
    box = ImageChops.invert(untouched).getbbox() if cropped else (0, 0, size[0], size[1])
    if box is None:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0, 0)
    black = np.asarray(on_black.crop(box), dtype=np.float32)
    white = np.asarray(on_white.crop(box), dtype=np.float32)
    coverage = 255.0 - (white - black).mean(axis=2, keepdims=True)
    colour = np.where(coverage > 0.0, black * 255.0 / np.maximum(coverage, 1.0), 0.0)
    layer = np.clip(np.rint(np.concatenate([colour, coverage], axis=2)), 0, 255).astype(np.uint8)
    return Image.fromarray(layer), (box[0], box[1])


def paste_patches(image: Image.Image, patches: list) -> None:
    for patch, corner in patches:
        image.paste(patch, corner, patch)


def draw_ruler(streamer, image: Image.Image, overlay, style) -> list:
    """The ruler as the plugin draws it, and the rectangles its ticks' labels took."""

    if overlay.mapping is not None:
        return list(streamer.draw_curved_colorbar(image, overlay, style, overlay.mapping))
    streamer.draw_colorbar(image, overlay, style)
    return []


def draw_markers(streamer, image: Image.Image, overlay, style, tick_labels: list) -> None:
    placed = list(tick_labels)
    for marker in streamer.markers_for(overlay):
        streamer.draw_marker(image, marker, overlay, style, placed)


def kept_markers(streamer, size: tuple[int, int], overlay, style, tick_labels: list) -> list:
    """One patch a marker, each placed clear of the labels of the ones before it."""

    placed = list(tick_labels)
    patches = []
    for marker in streamer.markers_for(overlay):
        clear_of = list(placed)
        patches.append(kept_patch(
            size,
            lambda image, marker=marker, clear_of=clear_of: streamer.draw_marker(
                image, marker, overlay, style, list(clear_of)
            ),
        ))
        streamer.draw_marker(Image.new("RGB", size), marker, overlay, style, placed)
    return patches


def kept_readout(streamer, size: tuple[int, int], overlay, style) -> list:
    """The whole readout as patches: what a refresh would build, and every frame then paste."""

    if not overlay.colorbar:
        return kept_markers(streamer, size, overlay, style, [])
    tick_labels = draw_ruler(streamer, Image.new("RGB", size), overlay, style)
    ruler = kept_patch(size, lambda image: draw_ruler(streamer, image, overlay, style))
    return [ruler, *kept_markers(streamer, size, overlay, style, tick_labels)]


def agreement_of(backdrop: Image.Image, draw_onto, patches: list) -> str:
    drawn = backdrop.copy()
    draw_onto(drawn)
    pasted = backdrop.copy()
    paste_patches(pasted, patches)
    difference = np.abs(
        np.asarray(drawn, dtype=np.int16) - np.asarray(pasted, dtype=np.int16)
    ).max(axis=2)
    covered = sum(patch.size[0] * patch.size[1] for patch, _ in patches)
    return (f"{int((difference > 0).sum())} pixels differ, by at most {int(difference.max())} "
            f"of 255; {len(patches)} pastes over {covered} pixels")


REFRESHES_A_SECOND = (1.0, 5.0)


def refreshed_lines(build_name: str, pasting: float, building: float, every_frame: float) -> None:
    """What a frame costs on average when the readout is built this often and pasted otherwise."""

    for refreshes in REFRESHES_A_SECOND:
        averaged = pasting + building * refreshes / FRAMES_PER_SECOND
        saved = every_frame - averaged
        name = f"{refreshes:.0f} a second, {build_name}"
        print(f"  {name:<34s} {every_frame:6.2f} ms -> {averaged:6.2f} ms   "
              f"{saved:+6.2f} ms ({saved * FRAMES_PER_SECOND / 10.0:+5.1f}% of a core)")


class ReadoutScene(NamedTuple):
    """One frame's readout and the picture it goes onto, at the size the service sends."""

    streamer: Any
    overlay: Any
    style: Any
    backdrop: Image.Image

    @property
    def size(self) -> tuple[int, int]:
        return self.backdrop.size

    def draw_ruler(self, image: Image.Image) -> list:
        return draw_ruler(self.streamer, image, self.overlay, self.style)

    def draw_readout(self, image: Image.Image) -> None:
        self.streamer.draw_overlay(image, self.overlay)

    def kept_readout(self) -> list:
        return kept_readout(self.streamer, self.size, self.overlay, self.style)


def readout_scene(streamer, renderer, frame, picture: Image.Image) -> ReadoutScene | None:
    """The scene this renderer's settings give, or None when they switch the readout off."""

    size = Image.open(io.BytesIO(renderer.render_frame(frame).jpeg)).size
    _, stats, _ = renderer.render_image(frame)
    overlay = renderer.overlay_for(stats)
    if overlay is None:
        return None
    style = streamer.overlay_style(size, overlay.colorbar)
    return ReadoutScene(streamer, overlay, style, picture.resize(size, Image.Resampling.BILINEAR))


def compare_kept_ruler(name: str, scene: ReadoutScene, repeats: int) -> None:
    canvas = scene.backdrop.copy()
    ruler = [kept_patch(scene.size, scene.draw_ruler)]
    compare(
        name,
        lambda: scene.draw_ruler(canvas),
        lambda: paste_patches(canvas, ruler),
        repeats,
        agreement_of(scene.backdrop, scene.draw_ruler, ruler),
    )


def compare_kept_markers(scene: ReadoutScene, repeats: int) -> None:
    streamer, overlay, style = scene.streamer, scene.overlay, scene.style
    canvas = scene.backdrop.copy()
    tick_labels = scene.draw_ruler(Image.new("RGB", scene.size))
    markers = kept_markers(streamer, scene.size, overlay, style, tick_labels)
    compare(
        "the markers: kept, one paste each",
        lambda: draw_markers(streamer, canvas, overlay, style, tick_labels),
        lambda: paste_patches(canvas, markers),
        repeats,
        agreement_of(
            scene.backdrop,
            lambda image: draw_markers(streamer, image, overlay, style, tick_labels),
            markers,
        ),
    )


def compare_kept_readout(scene: ReadoutScene, repeats: int) -> None:
    """The whole readout as patches, and as one layer the size of the picture."""

    canvas = scene.backdrop.copy()
    readout = scene.kept_readout()
    whole_layer = [kept_patch(scene.size, scene.draw_readout, cropped=False)]
    for name, patches in (
        ("the whole readout: kept patches", readout),
        ("the whole readout: one kept layer", whole_layer),
    ):
        compare(
            name,
            lambda: scene.draw_readout(canvas),
            lambda patches=patches: paste_patches(canvas, patches),
            repeats,
            agreement_of(scene.backdrop, scene.draw_readout, patches),
        )


def refreshed_readout_costs(scene: ReadoutScene, repeats: int) -> None:
    """What a refresh costs, and from it what a frame costs at one and five refreshes a second."""

    canvas = scene.backdrop.copy()
    readout = scene.kept_readout()
    every_frame = milliseconds_per_call(lambda: scene.draw_readout(canvas), repeats)
    pasting = milliseconds_per_call(lambda: paste_patches(canvas, readout), repeats)
    building = milliseconds_per_call(scene.kept_readout, repeats)
    print(f"  {'building the kept patches':<34s} {building:6.2f} ms   paid on a refresh only")
    print(f"  {'':<34s} every piece drawn twice here, to lift it off the picture; a build")
    print(f"  {'':<34s} written for the plugin would draw it once, so both are worked out")
    print("")
    print("  the readout refreshed that often, a frame on average")
    print("")
    refreshed_lines("built as here", pasting, building, every_frame)
    refreshed_lines("built in one draw", pasting, every_frame, every_frame)


def kept_readout_candidates(streamer, palette, frame, picture: Image.Image, options) -> None:
    """The readout drawn every frame against drawn once, for the stretch and for a knee's ruler."""

    settings = streamer.RenderSettings(rotation=options.rotate)
    scene = readout_scene(streamer, streamer.ThermalRenderer(palette, settings), frame, picture)
    if scene is None:
        print("  the readout is switched off in these settings, nothing to measure")
        return
    print(f"  the readout on a {scene.size[0]}x{scene.size[1]} picture, drawn every frame or "
          "drawn once and pasted")
    print("")
    compare_kept_ruler("the ruler: kept, one paste", scene, options.repeats)
    compare_kept_markers(scene, options.repeats)
    compare_kept_readout(scene, options.repeats)
    print("")
    refreshed_readout_costs(scene, options.repeats)
    print("")
    # A curved scale's ruler also works out a tick and writes its label, every frame.
    knee = streamer.RenderSettings(rotation=options.rotate, colour_scale="knee")
    knee_scene = readout_scene(streamer, streamer.ThermalRenderer(palette, knee), frame, picture)
    if knee_scene is not None:
        compare_kept_ruler("the knee's ruler: kept, one paste", knee_scene, options.repeats)


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
    # Where the colorbar's remaining time might be. Its measured pieces come to about a quarter of
    # what the whole thing costs, and the two labels account for another half of it, which leaves
    # something unexplained. Every one of these functions builds its own drawing context, and the
    # readout builds six or seven a frame between the bar, its two triangles and the markers.
    measure("building an ImageDraw", lambda: ImageDraw.Draw(scratch), options.repeats)
    measure("one triangle on the bar",
            lambda: streamer.mark_bar(scratch, True, style, streamer.HOTSPOT_RGB),
            options.repeats)
    measure("the outline rectangle",
            lambda: ImageDraw.Draw(scratch).rectangle(
                (left, top, left + bar_width - 1, top + bar_height - 1), outline=(0, 0, 0)),
            options.repeats)
    measure("the whole colorbar, as it ships",
            lambda: streamer.draw_colorbar(scratch, overlay, style), options.repeats)
    print("")
    kept_readout_candidates(streamer, palette, frame, picture, options)
    print("")
    # Saved so the JPEG of the last drawing can be looked at if a number here surprises anyone.
    scratch.save(io.BytesIO(), format="JPEG", quality=settings.jpeg_quality)


if __name__ == "__main__":
    main()
