# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The manifest's promises, checked against the files it points at.

None of this needs a printer, and all of it is the kind of mistake that otherwise surfaces as a
plugin that installs cleanly and then does nothing: a service launched with the wrong interpreter, a
path that moved, a URL that drifted apart from the location serving it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((PLUGIN_DIR / "manifest.json").read_text())
SERVICES = MANIFEST["install"]["service"]


def test_the_service_runs_the_plugins_own_interpreter():
    """A plugin with a requirements.txt gets a venv, and must actually launch from it.

    Plain `python3` is the system interpreter, which has whatever the printer happens to have and
    none of what this plugin declared. That failure is invisible until the service tries to import
    something, at which point it exits and the daemon restarts it into the same wall.
    """

    assert (PLUGIN_DIR / "requirements.txt").is_file()
    for service in SERVICES:
        assert service["command"].startswith("$PLUGIN_VENV/"), service["command"]


def test_every_placed_file_exists():
    for placement in MANIFEST["install"]["place"]:
        assert (PLUGIN_DIR / placement["src"]).is_file(), placement["src"]


def test_every_service_argument_that_names_a_plugin_file_points_at_one():
    prefix = "$BESPOK3D_PLUGINS/" + MANIFEST["name"] + "/"
    for service in SERVICES:
        for argument in service["args"]:
            if not argument.startswith(prefix):
                continue
            assert (PLUGIN_DIR / argument[len(prefix):]).exists(), argument


def test_the_registered_camera_urls_are_the_ones_the_proxy_serves():
    """The Moonraker fragment and the nginx location are two files that have to agree.

    Nothing catches a disagreement at install time: the camera registers, the proxy answers 404, and
    the tile reports an error with no clue which of the two moved.
    """

    webcam_fragment = (PLUGIN_DIR / "files/webcam.conf.tmpl").read_text()
    nginx_location = (PLUGIN_DIR / "files/etc/nginx/locations/thermal-master.conf").read_text()

    registered_urls = [
        line.split(":", 1)[1].strip()
        for line in webcam_fragment.splitlines()
        if line.startswith(("stream_url:", "snapshot_url:"))
    ]

    assert len(registered_urls) == 2
    for url in registered_urls:
        assert f"location = {url}" in nginx_location, url


def test_the_declared_ports_are_the_ones_the_service_is_told_to_bind():
    for service in SERVICES:
        bound_port = service["args"][service["args"].index("--port") + 1]
        assert [int(bound_port)] == service["ports"]


def test_every_template_variable_is_declared_and_configurable():
    """A `.tmpl` placeholder with no matching config key renders as the literal `$NAME`.

    Nothing catches that: the plugin installs, Moonraker reads `rotation: $THERMAL_CAMERA_ROTATION`,
    and the camera is quietly broken in a way that points at Moonraker rather than at us.
    """

    declared = {variable["name"] for variable in MANIFEST["requires"]["variables"]}
    configurable = {entry["key"] for entry in MANIFEST["config"]}

    for placement in MANIFEST["install"]["place"]:
        if not placement.get("render"):
            continue
        template = (PLUGIN_DIR / placement["src"]).read_text()
        used = set(re.findall(r"\$([A-Z][A-Z0-9_]*)", template))
        assert used <= declared, f"{placement['src']}: undeclared {sorted(used - declared)}"
        unconfigurable = sorted(used - configurable)
        assert used <= configurable, f"{placement['src']}: unconfigurable {unconfigurable}"


def test_every_select_option_list_contains_its_default():
    for entry in MANIFEST["config"]:
        if entry.get("type") != "select":
            continue
        assert entry["default"] in entry["options"], entry["key"]


def test_the_package_facade_exports_everything_public():
    """The facade is how the tests and anything else see the plugin as one namespace.

    It is also the one thing in the split that goes stale silently: a name added to a module is
    simply missing from the package, and the first sign is an AttributeError in whatever reaches
    for it next. This compares the two rather than trusting anyone to remember.
    """

    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parent.parent / "files" / "lib" / "thermal_master"
    exported = {
        alias.name
        for node in ast.parse((package / "__init__.py").read_text()).body
        if isinstance(node, ast.ImportFrom) and node.level > 0
        for alias in node.names
    }

    defined = set()
    for source in sorted(package.glob("*.py")):
        if source.name == "__init__.py":
            continue
        for node in ast.parse(source.read_text()).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined.update(t.id for t in node.targets if isinstance(t, ast.Name))

    assert not {name for name in defined if not name.startswith("_")} - exported


def test_the_facade_declares_everything_it_imports():
    """__all__ and the imports have to agree, or `from thermal_master import *` lies."""

    import thermal_master

    for name in thermal_master.__all__:
        assert hasattr(thermal_master, name), name
