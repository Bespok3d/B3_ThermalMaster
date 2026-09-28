#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""Add anything the package facade is missing to its imports and its `__all__`.

`thermal_master/__init__.py` re-exports every public name in the package, which is what lets the
tests ask for names rather than for files, and a test fails when it falls behind. Keeping it in
step by hand is a two minute chore that has now been done four times in one week, each time with a
second round to put the new name where ruff's import sorting wanted it.

It only adds. A name that should stop being exported is a deliberate decision and is left to a
person, so this never removes anything and says what it would have.

    python3 scripts/refresh-facade.py
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "plugin" / "files" / "lib" / "thermal_master"


def public_names(module: Path) -> set[str]:
    """Every top level name a module defines, ignoring the private ones."""

    found = set()
    for node in ast.parse(module.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            found.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
    return {name for name in found if not name.startswith("_")}


def sort_key(name: str) -> tuple[int, str, str]:
    """How ruff sorts an import block: constants, then classes, then the rest, case insensitively.

    The case insensitive part is easy to get wrong and produces exactly one line of diff when you
    do: `RenderedFrame` sorts before `RenderSettings` to a reader and after it to `sorted`.
    """

    rank = 0 if name.isupper() else 1 if name[0].isupper() else 2
    return (rank, name.lower(), name)


def rewrite_block(text: str, header: str, names: list[str]) -> str:
    start = text.index(header)
    end = text.index(")\n", start)
    body = "".join(f"    {name},\n" for name in sorted(names, key=sort_key))
    return text[:start] + header + body + text[end:]


def main() -> None:
    facade = PACKAGE / "__init__.py"
    text = facade.read_text()
    exported = set(re.findall(r'^    "([A-Za-z_][A-Za-z0-9_]*)",$', text, re.M))
    added: dict[str, list[str]] = {}

    for module in sorted(PACKAGE.glob("*.py")):
        if module.name == "__init__.py":
            continue
        header = f"from .{module.stem} import (\n"
        if header not in text:
            print(f"no import block for {module.stem}, leaving it alone")
            continue
        start = text.index(header) + len(header)
        end = text.index(")\n", start)
        current = [line.strip().rstrip(",") for line in text[start:end].splitlines() if line.strip()]
        missing = sorted(public_names(module) - set(current), key=sort_key)
        if missing:
            added[module.stem] = missing
        # Rewritten whether or not anything was added, because a block that a person edited by
        # hand is usually a block in the wrong order, and that is the second round this script
        # exists to save.
        text = rewrite_block(text, header, current + missing)

    wanted = sorted(
        exported | {name for names in added.values() for name in names}, key=sort_key
    )
    start = text.index("__all__ = [\n") + len("__all__ = [\n")
    end = text.index("]\n", start)
    text = text[:start] + "".join(f'    "{name}",\n' for name in wanted) + text[end:]
    facade.write_text(text)

    for module, names in added.items():
        print(f"{module}: added {', '.join(names)}")
    if not added:
        print("the facade was already complete")


if __name__ == "__main__":
    main()
