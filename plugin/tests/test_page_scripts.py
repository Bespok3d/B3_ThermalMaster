# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The pages' scripts parse, checked by Node rather than by reading the page as text.

Both pages carry their script inside a Python string, with values substituted into it, and the
first thing that ever parses it is a browser. A syntax error there is a dead viewer that every
other test here passes, because they read the page as text. Node is already a requirement of the
gate, for the shared detectors, so `node --check` adds nothing new to install. Outside the gate,
with no Node on the path, these tests skip and say why.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed; the gate requires it")


def scripts(page: str) -> list[str]:
    """The body of every script element on a page, in order."""

    return [block.split(">", 1)[1].split("</script>", 1)[0] for block in page.split("<script")[1:]]


def node_check(source: str, file: Path) -> subprocess.CompletedProcess[str]:
    file.write_text(source)
    # check=False because a failing parse is the answer being asked for, not an error to raise.
    return subprocess.run(
        [str(NODE), "--check", str(file)], capture_output=True, text=True, check=False
    )


@needs_node
def test_a_script_with_a_syntax_error_fails_the_check(tmp_path):
    """The check itself, proven able to fail, so a passing run below means something."""

    result = node_check("function broken( {\n", tmp_path / "broken.js")

    assert result.returncode != 0


@needs_node
@pytest.mark.parametrize("shape", [None, (240, 320)])
def test_the_viewer_script_parses(thermal_streamer, tmp_path, shape):
    """Rendered both with and without the picture's shape, the two ways the plugin serves it."""

    found = scripts(thermal_streamer.render_viewer_page(shape))

    assert len(found) == 1
    result = node_check(found[0], tmp_path / "viewer.js")
    assert result.returncode == 0, result.stderr


@needs_node
def test_the_control_page_script_parses(thermal_streamer, tmp_path):
    store = thermal_streamer.SettingsStore("ironbow", thermal_streamer.RenderSettings(), None)
    found = scripts(thermal_streamer.render_control_page(store.as_dict(), ["ironbow"]))

    assert len(found) == 1
    result = node_check(found[0], tmp_path / "control.js")
    assert result.returncode == 0, result.stderr
