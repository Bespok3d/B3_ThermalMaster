# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Asking the C library to keep the memory this process frees, instead of handing it back.

Every frame allocates and frees a handful of whole-frame arrays. glibc keeps freed memory for
reuse up to a threshold, raises that threshold to the largest block freed so far, and gives the top
of the heap back to the system once more than twice that much is free. Which side of those two
marks a frame's arrays fall on therefore depends on the largest thing the process happens to
allocate, and when they fall on the wrong side each of them is handed back and its pages are
faulted in again, every frame.

That is what happened to a P3 in 0.30.2 (F-79). Encoding a 320x240 picture instead of a 512x384 one
lowered the largest block from about 786 KB to about 307 KB, a P3's arrays are about 196 KB each,
and the pipeline went from 11.56 ms a frame to 13.33 ms although nothing in it had changed. With
glibc told to keep what is freed the same pipeline is 10.08 ms, measured on the U1 on 2026-11-12. A
P1's arrays are about 77 KB, under the threshold glibc starts at, so a P1 never met this.

So the plugin says so once, as it starts, rather than leaving it to the size of a picture. What it
costs is that the process holds on to a few megabytes it would otherwise have returned.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from typing import cast

# glibc's own names for the two settings, with the numbers <malloc.h> gives them.
M_TRIM_THRESHOLD = -1
M_MMAP_THRESHOLD = -3


# Blocks up to this size come from the heap, where a freed one is found again, rather than from
# a mapping of their own, which is returned the moment it is freed. Far above anything a frame
# allocates, and setting it at all stops glibc moving the mark by itself.
KEPT_BLOCK_BYTES = 8 * 1024 * 1024


# How much may be free at the top of the heap before glibc gives it back. A frame's arrays add up
# to a megabyte or two, so at this the heap is never trimmed between frames.
HEAP_SLACK_BYTES = 64 * 1024 * 1024


Mallopt = Callable[[int, int], int]


def glibc_mallopt() -> Mallopt | None:
    """glibc's `mallopt`, or None where the C library has none or cannot be asked.

    Looked up in the process itself rather than in a library named here, so nothing is loaded
    that was not already. On macOS, on musl and anywhere else without it there is nothing to
    call and nothing to tune: the thresholds are glibc's.
    """

    try:
        mallopt = ctypes.CDLL(None).mallopt
    except (OSError, TypeError, AttributeError):
        return None
    mallopt.argtypes = (ctypes.c_int, ctypes.c_int)
    mallopt.restype = ctypes.c_int
    return cast("Mallopt", mallopt)


def keep_freed_memory(mallopt: Mallopt | None) -> bool:
    """Tell glibc to keep what this process frees. True when it was told and agreed.

    Both settings are needed: the first keeps a whole-frame array on the heap, and the second
    stops the heap being cut back to nothing behind it. `mallopt` answers 1 when it has taken a
    setting and 0 when it has not.
    """

    if mallopt is None:
        return False
    blocks_kept = mallopt(M_MMAP_THRESHOLD, KEPT_BLOCK_BYTES)
    heap_kept = mallopt(M_TRIM_THRESHOLD, HEAP_SLACK_BYTES)
    return bool(blocks_kept and heap_kept)
