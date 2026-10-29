"""Runs the project's checks and prints one table: python tools\\prove.py

  python tools\\prove.py              the unit tests, then the browser checks if Playwright is installed
  python tools\\prove.py --mutation   also break each protection on purpose (roughly half an hour)

Every figure comes from the run itself, read from the last line each tool prints. A figure that cannot be read shows
as "unreadable" and counts as a failure, so a changed output format cannot turn into a false pass. Exit code 1 means
something failed, 3 means a tool was missing so a step could not run, 0 means every step that ran passed. The real-Claude
gate is not run here: it spends money and records the Claude Code version the server then accepts, so it stays a
command you type on purpose (python tests\\chat_gate.py). The table shows which version it last recorded. The only
folder the checks write in is .demo-world, which git ignores.
"""

import argparse
import importlib.util
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
GATE_FILE = HERE / "tested_claude_version.txt"
UNIT_SECONDS, BROWSER_SECONDS, MUTATION_SECONDS = 900, 900, 3600


def last_line(text):
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    return lines[-1] if lines else ""


def parse_pytest(text, code):
    """Pass or fail from the exit code, the figure from pytest's last line. A pass with no readable figure is not a pass."""
    last = last_line(text)
    passed = re.search(r"(\d+) passed", last)
    failed = re.search(r"(\d+) (?:failed|errors?)", last)
    if code == 0 and passed:
        return "passed", f"{passed.group(1)} tests"
    if code != 0 and failed:
        return "failed", failed.group(0)
    return "unreadable", last or "no output"


def parse_browser(text, code):
    """The last line is 'N failures'. A crash prints no such line, and a figure that disagrees with the exit code is not trusted."""
    last = last_line(text)
    found = re.fullmatch(r"(\d+) failures", last)
    if found and int(found.group(1)) == 0 and code == 0:
        return "passed", "0 failures"
    if found and int(found.group(1)) > 0 and code != 0:
        return "failed", last
    return "unreadable", last or "no output"


def parse_mutation(text, code):
    """The last line is 'N of M mutations caught.' Only N equal to M counts as a pass."""
    last = last_line(text)
    found = re.fullmatch(r"(\d+) of (\d+) mutations caught\.", last)
    if found and found.group(1) == found.group(2) and code == 0:
        return "passed", f"{found.group(1)} of {found.group(2)} caught"
    if found and found.group(1) != found.group(2) and code != 0:
        return "failed", f"{found.group(1)} of {found.group(2)} caught"
    return "unreadable", last or "no output"


def gate_row(path=GATE_FILE):
    """What the last gate run recorded. Never runs the gate."""
    try:
        versions = [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    except OSError:
        versions = []
    if versions:
        return ("real-Claude gate", "not run here", "last recorded for Claude Code " + ", ".join(versions), "")
    return ("real-Claude gate", "not run here", "no version recorded: python tests\\chat_gate.py", "")


def kill_tree(process):
    """Ends the step and everything it started, so a stuck browser or test run cannot keep the pipe open."""
    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=20)


def run_step(name, command, parser, seconds):
    print(f"running {name} ...", flush=True)
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    try:
        output, _ = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        return (name, "failed", f"timed out after {seconds} seconds", f"{time.monotonic() - started:.0f}")
    result, figure = parser(output, process.returncode)
    return (name, result, figure, f"{time.monotonic() - started:.0f}")


def render(rows):
    header = ("Step", "Result", "Figure", "Seconds")
    widths = [max(len(str(row[column])) for row in [header] + rows) for column in range(4)]
    lines = ["  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip() for row in [header] + rows]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)


def exit_code(rows):
    """1 if anything failed or could not be read, 3 if a tool was missing so a step could not run, otherwise 0."""
    if any(row[1] in ("failed", "unreadable") for row in rows):
        return 1
    return 3 if any(row[1] == "missing" for row in rows) else 0


def not_run(rows):
    """A last line naming every step that did not run, so a short table is never mistaken for a full one."""
    names = [f"{row[0]} ({row[1]})" for row in rows if row[1] in ("skipped", "missing")]
    return "Not run: " + ", ".join(names) if names else ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Runs the project's checks and prints one table. It writes nothing git tracks.")
    parser.add_argument("--mutation", action="store_true", help="also run the mutation checker (roughly half an hour)")
    arguments = parser.parse_args(argv)
    rows = []
    unit = [sys.executable, "-m", "pytest", "tests", "--ignore=tests/browser_check.py", "-q", "-p", "no:cacheprovider"]
    rows.append(run_step("unit tests", unit, parse_pytest, UNIT_SECONDS))
    if importlib.util.find_spec("playwright") is None:
        rows.append(("browser checks", "missing", "Playwright is not installed: pip install -r requirements-dev.txt", ""))
    else:
        with tempfile.TemporaryDirectory() as shots:
            rows.append(run_step("browser checks", [sys.executable, str(HERE / "tests" / "browser_check.py"), shots], parse_browser, BROWSER_SECONDS))
    if arguments.mutation:
        rows.append(run_step("mutation checks", [sys.executable, str(HERE / "tools" / "mutation_check.py")], parse_mutation, MUTATION_SECONDS))
    else:
        rows.append(("mutation checks", "skipped", "add --mutation to run them (roughly half an hour)", ""))
    rows.append(gate_row())
    print()
    print(render(rows))
    if not_run(rows):
        print(not_run(rows))
    return exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())
