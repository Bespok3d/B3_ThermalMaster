# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The interactive viewer's own page: its layout promises, checked against the served HTML.

These are the promises that are easy to break from a stylesheet and impossible to see from a test
that only asks the server for a status code. The picture has to be laid out without a script, the
controls have to be reachable in a tile, and every URL has to stay relative.
"""

from __future__ import annotations

import re


def stylesheet(page: str) -> str:
    return page.split("<style>", 1)[1].split("</style>", 1)[0]


def rule(page: str, selector: str) -> str:
    """The declarations of the first rule with this exact selector."""

    body = stylesheet(page).split(selector + " {", 1)
    assert len(body) == 2, f"no rule for {selector}"
    return body[1].split("}", 1)[0]


def test_the_stylesheet_lays_the_picture_out_without_a_script(thermal_streamer):
    """The fallback that a second camera tile used to be.

    Only one camera is registered now, and it is this page, so with JavaScript off the stylesheet
    has to fit the picture into the stage by itself. The script overrides these four properties on
    every paint; it does not supply them in the first place.
    """

    page = thermal_streamer.render_viewer_page(None)

    declarations = rule(page, ".stage img")
    for expected in ("inset: 0", "width: 100%", "height: 100%", "object-fit: contain"):
        assert expected in declarations, expected


def test_the_tools_are_not_hidden_by_a_breakpoint(thermal_streamer):
    """They used to be, below 460 pixels of height, which is every dashboard tile.

    That put the controls out of reach in the one place they are most wanted and left the tile
    saying "open for tools" instead of showing them. They are compact in a tile now, not absent.
    """

    page = thermal_streamer.render_viewer_page(None)

    # The toolbar itself, not what is inside it: one readout in there does hide on a narrow tile,
    # deliberately, so that the controls fit on one row.
    hidden = re.findall(r"\.tools\s*\{[^}]*display:\s*none", stylesheet(page))
    assert hidden == []
    assert "display: flex" in rule(page, ".tools")


def test_the_tools_sit_under_the_picture(thermal_streamer):
    """In both layouts. Only the readout moves beside the picture, never the controls."""

    page = thermal_streamer.render_viewer_page(None)
    view = page.split('<div class="view">', 1)[1].split("</div>\n  <div", 1)[0]

    assert 'class="stage"' in view
    assert "tools" in view
    assert view.index('class="stage"') < view.index("tools")


def test_the_readout_column_leaves_the_picture_most_of_the_width(thermal_streamer):
    """It took half the tile when it sized itself, which is half the picture's width."""

    declarations = rule(thermal_streamer.render_viewer_page(None), "body.beside .panel")
    width = re.search(r"max-width:\s*(\d+)%", declarations)

    assert width is not None
    assert int(width.group(1)) <= 40


def test_the_viewer_carries_no_absolute_paths_of_its_own(thermal_streamer):
    """Same rule as the control page: the plugin cannot know its mount point.

    The viewer is served at `/view` directly and at `/thermal/view` behind nginx, so a leading
    slash is a guess about which one the browser asked for.
    """

    page = thermal_streamer.render_viewer_page(None)
    referenced = re.findall(r'(?:href|src|action)="([^"]*)"', page)

    assert [url for url in referenced if url.startswith("/")] == []
