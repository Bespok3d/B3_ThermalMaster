#!/bin/sh
# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
# The plugin's test suite, for CI: the release Action runs this after packing and before anything is
# released, and tests.yml runs it on every pull request. A non-zero exit stops both.
#
# POSIX sh, because the Action runs it with `sh`, which is dash on the runner. It cannot use the
# repo's gate: that lives in the private lib_bespok3d submodule, which CI cannot check out. So it
# builds what the tests need from the plugin's own pinned requirements, plus pytest, and nothing
# else. Locally, run scripts/check.sh instead, which runs these tests and everything else.
set -eu

cd "$(dirname "$0")/.."

# The tests import the plugin from files/, and a .pyc left under files/ would be packed into the
# .b3, where the daemon refuses any archive member the manifest does not list.
export PYTHONDONTWRITEBYTECODE=1

python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt \
    "pytest>=8.0" "pytest-timeout>=2.3"
python3 -m pytest -q -p no:cacheprovider --timeout=60 tests
