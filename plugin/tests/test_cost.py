# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The plugin reporting what it costs the printer.

The whole design tension of this plugin is what it costs a four core Cortex-A53 that also has a
printer to run, and answering it used to need ssh and a shell script. The kernel keeps the figure
exactly, so the only things that can go wrong here are the parsing and the arithmetic, and both are
the kind of wrong that still produces a plausible number.

The clock is replaced rather than waited on, because a test that sleeps for the measuring window is
a test nobody runs.
"""

from __future__ import annotations

import os

import pytest

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


def write_stat(path, user_seconds: float, system_seconds: float = 0.0, comm: str = "python3"):
    """A /proc/<pid>/stat line with the two fields that matter and placeholders for the rest."""

    fields = ["0"] * 14
    fields[0] = "S"
    fields[11] = str(int(user_seconds * CLOCK_TICKS))
    fields[12] = str(int(system_seconds * CLOCK_TICKS))
    path.write_text(f"6089 ({comm}) " + " ".join(fields) + "\n")
    return path


@pytest.fixture
def clock(thermal_streamer, monkeypatch):
    """A monotonic clock the test moves by hand."""

    now = {"at": 1000.0}
    monkeypatch.setattr(thermal_streamer.cost.time, "monotonic", lambda: now["at"])
    return now


def test_it_counts_user_and_system_time(thermal_streamer, tmp_path):
    stat = write_stat(tmp_path / "stat", user_seconds=3.0, system_seconds=1.0)

    assert thermal_streamer.process_cpu_seconds(stat) == pytest.approx(4.0)


def test_a_bracket_in_the_process_name_does_not_confuse_it(thermal_streamer, tmp_path):
    """The comm field may hold anything, which is why the fields are counted from the last one."""

    stat = write_stat(tmp_path / "stat", user_seconds=2.0, comm="odd) name")

    assert thermal_streamer.process_cpu_seconds(stat) == pytest.approx(2.0)


def test_a_system_without_the_file_reports_nothing(thermal_streamer, tmp_path):
    """Development happens on machines that have no /proc, and this has to be absent, not broken."""

    assert thermal_streamer.process_cpu_seconds(tmp_path / "missing") is None


def test_a_line_it_cannot_read_reports_nothing(thermal_streamer, tmp_path):
    stat = tmp_path / "stat"
    stat.write_text("not a stat line at all\n")

    assert thermal_streamer.process_cpu_seconds(stat) is None


def test_there_is_no_recent_share_before_a_window_has_passed(thermal_streamer, tmp_path, clock):
    """Dividing by nothing gives an answer, and it is always wrong."""

    stat = write_stat(tmp_path / "stat", user_seconds=1.0)
    meter = thermal_streamer.ProcessCost(stat)

    reading = meter.reading()

    assert reading["core_share"] is None
    assert reading["cores"] >= 1


def test_a_window_gives_the_share_of_a_core(thermal_streamer, tmp_path, clock):
    stat = write_stat(tmp_path / "stat", user_seconds=1.0)
    meter = thermal_streamer.ProcessCost(stat)

    clock["at"] += 10.0
    write_stat(stat, user_seconds=3.5)

    assert meter.reading()["core_share"] == pytest.approx(0.25)


def test_asking_again_too_soon_repeats_the_last_answer(thermal_streamer, tmp_path, clock):
    """A window shorter than a couple of seconds is mostly scheduling noise."""

    stat = write_stat(tmp_path / "stat", user_seconds=0.0)
    meter = thermal_streamer.ProcessCost(stat)
    clock["at"] += 10.0
    write_stat(stat, user_seconds=5.0)
    settled = meter.reading()["core_share"]

    clock["at"] += 0.1
    write_stat(stat, user_seconds=5.0)

    assert settled == pytest.approx(0.5)
    assert meter.reading()["core_share"] == pytest.approx(0.5)


def test_since_start_averages_over_the_life_of_the_service(thermal_streamer, tmp_path, clock):
    """The fair number for a plugin that sleeps most of the day, and the one always available."""

    stat = write_stat(tmp_path / "stat", user_seconds=0.0)
    meter = thermal_streamer.ProcessCost(stat)

    clock["at"] += 100.0
    write_stat(stat, user_seconds=4.0)

    assert meter.reading()["since_start"] == pytest.approx(0.04)


def test_a_system_that_cannot_answer_says_so_rather_than_guessing(thermal_streamer, tmp_path):
    meter = thermal_streamer.ProcessCost(tmp_path / "missing")

    assert meter.reading() is None
    assert "does not report" in thermal_streamer.describe_cost(None)


def test_the_sentence_names_both_numbers(thermal_streamer):
    said = thermal_streamer.describe_cost(
        {"core_share": 0.046, "since_start": 0.021, "cores": 4}
    )

    assert "4.6% of one core" in said
    assert "2.1% since the service started" in said


def test_the_sentence_admits_it_is_still_measuring(thermal_streamer):
    said = thermal_streamer.describe_cost(
        {"core_share": None, "since_start": 0.021, "cores": 4}
    )

    assert said.startswith("Measuring")


def test_the_control_page_shows_it(thermal_streamer, settings_dict):
    page = thermal_streamer.render_control_page(
        settings_dict(), ["ironbow"], None, "Using 4.6% of one core."
    )

    assert 'id="plugin-cost"' in page
    assert "Using 4.6% of one core." in page


def test_a_page_built_without_a_reading_still_renders(thermal_streamer, settings_dict):
    """The page is also built by tests and by anything wiring a server up by hand."""

    page = thermal_streamer.render_control_page(settings_dict(), ["ironbow"])

    assert "does not report" in page
