import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import prove

TOOL = Path(prove.__file__)


@pytest.mark.parametrize(
    "text,code,expected",
    [
        ("....\n467 passed in 144.26s (0:02:24)\n", 0, ("passed", "467 tests")),
        ("3 passed, 1 skipped, 2 warnings in 4.00s\n", 0, ("passed", "3 tests")),
        ("1 failed, 466 passed in 150.00s\n", 1, ("failed", "1 failed")),
        ("2 errors in 1.20s\n", 2, ("failed", "2 errors")),
        ("no tests ran in 0.10s\n", 5, ("unreadable", "no tests ran in 0.10s")),
        ("", 0, ("unreadable", "no output")),
        ("467 passed in 1s\n", 1, ("unreadable", "467 passed in 1s")),
    ],
    ids=["pass", "skips-and-warnings", "failure", "collection-errors", "nothing-ran", "no-output", "exit-code-disagrees"],
)
def test_the_unit_test_figure_comes_from_the_last_line_and_agrees_with_the_exit_code(text, code, expected):
    assert prove.parse_pytest(text, code) == expected


@pytest.mark.parametrize(
    "text,code,expected",
    [
        ("ok    a\n0 failures\n", 0, ("passed", "0 failures")),
        ("FAIL  a\n3 failures\n", 1, ("failed", "3 failures")),
        ("Traceback (most recent call last):\nplaywright._impl._errors.Error: browser not found\n", 1, ("unreadable", "playwright._impl._errors.Error: browser not found")),
        ("0 failures\n", 1, ("unreadable", "0 failures")),
        ("2 failures\n", 0, ("unreadable", "2 failures")),
    ],
    ids=["pass", "failure", "crash-prints-no-figure", "zero-but-failing-exit", "failures-but-clean-exit"],
)
def test_the_browser_figure_is_the_failure_count_and_a_crash_is_unreadable(text, code, expected):
    assert prove.parse_browser(text, code) == expected


@pytest.mark.parametrize(
    "text,code,expected",
    [
        ("CAUGHT  x\n\n61 of 61 mutations caught.\n", 0, ("passed", "61 of 61 caught")),
        ("\n60 of 61 mutations caught.\n", 1, ("failed", "60 of 61 caught")),
        ("The baseline does not pass, so no mutation result can be trusted:\nsomething\n", 2, ("unreadable", "something")),
        ("61 of 61 mutations caught.\n", 1, ("unreadable", "61 of 61 mutations caught.")),
    ],
    ids=["pass", "some-missed", "baseline-failed", "all-caught-but-failing-exit"],
)
def test_the_mutation_figure_passes_only_when_every_mutation_was_caught(text, code, expected):
    assert prove.parse_mutation(text, code) == expected


def test_the_table_lines_up_and_the_exit_code_fails_on_a_failure_or_an_unreadable_figure():
    passed = ("unit tests", "passed", "467 tests", "144")
    skipped = ("browser checks", "skipped", "Playwright is not installed", "")
    rows = [passed, skipped]

    table = prove.render(rows).splitlines()

    assert table[0].split()[:2] == ["Step", "Result"] and set(table[1].replace(" ", "")) == {"-"}
    assert len({line.index("467 tests") if "467 tests" in line else None for line in table} - {None}) == 1
    assert prove.exit_code(rows) == 0
    assert prove.exit_code(rows + [("mutation checks", "failed", "60 of 61 caught", "9")]) == 1
    assert prove.exit_code(rows + [("mutation checks", "unreadable", "no output", "9")]) == 1
    assert prove.exit_code([passed, ("browser checks", "missing", "Playwright is not installed", "")]) == 3
    assert prove.exit_code([("unit tests", "failed", "1 failed", "1"), ("browser checks", "missing", "x", "")]) == 1


def test_a_short_run_names_what_did_not_run():
    rows = [("unit tests", "passed", "467 tests", "1"), ("browser checks", "missing", "x", ""), ("mutation checks", "skipped", "y", "")]

    assert prove.not_run(rows) == "Not run: browser checks (missing), mutation checks (skipped)"
    assert prove.not_run(rows[:1]) == ""


def test_the_gate_row_shows_the_recorded_version_and_never_runs_the_gate(tmp_path):
    recorded = tmp_path / "tested_claude_version.txt"
    recorded.write_text("2.1.292\n", encoding="utf-8")

    assert "2.1.292" in prove.gate_row(recorded)[2]
    assert prove.gate_row(recorded)[1] == "not run here"
    assert "no version recorded" in prove.gate_row(tmp_path / "missing.txt")[2]


def test_a_step_that_runs_too_long_is_reported_as_failed_not_left_hanging():
    row = prove.run_step("slow step", [sys.executable, "-c", "import time; time.sleep(30)"], prove.parse_pytest, 1)

    assert row[1] == "failed" and "timed out" in row[2]


def test_a_step_whose_child_starts_a_grandchild_is_still_stopped_at_the_limit():
    code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"
    started = time.monotonic()

    row = prove.run_step("stuck step", [sys.executable, "-c", code], prove.parse_pytest, 1)

    assert row[1] == "failed" and "timed out" in row[2]
    assert time.monotonic() - started < 25


def test_help_runs_and_names_the_mutation_option():
    done = subprocess.run([sys.executable, str(TOOL), "--help"], capture_output=True, text=True, timeout=30)

    assert done.returncode == 0 and "--mutation" in done.stdout
