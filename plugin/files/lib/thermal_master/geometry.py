# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Where a pixel ends up once the frame has been turned and mirrored.

Its own module because three others need it, and because the two halves must never disagree:
`orient` moves the image and `orient_point` says where one pixel of it went. A marker drawn from a
point the image did not actually move to is worse than no marker, since it looks authoritative. They
are written to follow each other step for step and are cross-checked by the test suite for all eight
combinations.
"""

from __future__ import annotations

import numpy as np


def orient(
    image: np.ndarray, rotation: int, flip_horizontal: bool, flip_vertical: bool
) -> np.ndarray:
    """Rotate clockwise by whole quarter turns, then mirror.

    numpy rotates anticlockwise, so the sign is flipped: `rotation` is degrees clockwise, which is
    what a person means when they say the picture is on its side. The result is made contiguous
    because a rotation returns a view with negative strides and Pillow will not read that.
    """

    if rotation:
        image = np.rot90(image, k=-(rotation // 90))
    if flip_horizontal:
        image = np.fliplr(image)
    if flip_vertical:
        image = np.flipud(image)
    return np.ascontiguousarray(image)


def rotate_point_clockwise(
    point: tuple[int, int], size: tuple[int, int]
) -> tuple[tuple[int, int], tuple[int, int]]:
    """One quarter turn clockwise. The frame's width and height trade places with it."""

    x, y = point
    width, height = size
    return (height - 1 - y, x), (height, width)


def rotate_point_anticlockwise(
    point: tuple[int, int], size: tuple[int, int]
) -> tuple[tuple[int, int], tuple[int, int]]:
    """One quarter turn back, undoing `rotate_point_clockwise` exactly."""

    x, y = point
    width, height = size
    return (y, width - 1 - x), (height, width)


def orient_point(
    point: tuple[int, int],
    size: tuple[int, int],
    rotation: int,
    mirrors: tuple[bool, bool],
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Where a sensor pixel ends up once the frame has been rotated and mirrored.

    Written to follow `orient` step for step rather than as one derived transform, so the two
    cannot drift apart. A marker that lands somewhere other than the pixel it names is worse than
    no marker at all, because it looks authoritative.
    """

    flip_horizontal, flip_vertical = mirrors
    for _ in range(rotation // 90):
        point, size = rotate_point_clockwise(point, size)
    x, y = point
    width, height = size
    if flip_horizontal:
        x = width - 1 - x
    if flip_vertical:
        y = height - 1 - y
    return (x, y), (width, height)


def unorient_point(
    point: tuple[int, int],
    size: tuple[int, int],
    rotation: int,
    mirrors: tuple[bool, bool],
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Which sensor pixel a point on the displayed picture came from.

    The other direction from `orient_point`, and needed because the two ends of the plugin speak
    different spaces: a viewer clicks on the picture as displayed, and the temperature it wants
    lives in a frame that has not been turned. Undone in the reverse order it was done, flips
    first, and cross-checked against `orient_point` for all eight combinations.
    """

    flip_horizontal, flip_vertical = mirrors
    x, y = point
    width, height = size
    if flip_horizontal:
        x = width - 1 - x
    if flip_vertical:
        y = height - 1 - y
    point, size = (x, y), (width, height)
    for _ in range(rotation // 90):
        point, size = rotate_point_anticlockwise(point, size)
    return point, size
