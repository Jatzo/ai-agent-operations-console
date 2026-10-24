"""Starts the AI agent operations console on a made-up set of projects, skills and history, with a canned chat.

Nothing here reads your own folders, and no real Claude is called: the chat is answered by demo_claude.py.
To use your own folders, run serve_dashboard.py with --root and --home instead.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import chat
import serve_dashboard

CODE_DIR = Path(__file__).resolve().parent
MARKER = ".console-demo"
GIT = ["git", "-c", "user.name=demo", "-c", "user.email=demo@example.invalid", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]

SKILLS = {
    "plan-feature": ("Turns a request into a short plan with the files it touches.", ["review-change", "write-tests"]),
    "review-change": ("Reads a change like the person who will be paged when it breaks.", ["write-tests"]),
    "write-tests": ("Writes the test that fails when the code is wrong.", []),
    "write-release-notes": ("Drafts release notes from the commit history.", ["review-change"]),
    "tidy-inbox": ("Sorts loose ideas into projects.", []),
    "explain-code": ("Explains a file in plain words.", []),
    "triage-bugs": ("Ranks open bugs by how many people they hurt.", ["plan-feature"]),
    "check-links": ("Finds broken links in the docs.", []),
    "summarise-week": ("Summarises the week across every project.", ["write-release-notes"]),
    "draft-reply": ("Drafts a reply to a customer message.", []),
    "rename-things": ("Suggests clearer names for functions and files.", ["explain-code"]),
    "find-dead-code": ("Lists code nothing calls.", ["review-change"]),
}
AGENTS = {"reviewer": "Reviews a change and does not edit it.", "researcher": "Compares options and cites sources.", "builder": "Builds a small feature and its tests."}
USE = {"plan-feature": 14, "review-change": 9, "write-tests": 6, "write-release-notes": 3, "tidy-inbox": 1, "explain-code": 5}
SESSIONS = 60
IDEAS = ["Add a dark mode to Atlas", "Write a short guide to the Harbour API", "Try a weekly digest email", "Clean up the Ledger import script"]


def git(folder, *arguments):
    subprocess.run(GIT + list(arguments), cwd=folder, check=True, capture_output=True, timeout=60)


def make_repo(folder, subjects, dirty):
    folder.mkdir(parents=True)
    git(folder, "init", "-q")
    for number, subject in enumerate(subjects):
        (folder / f"file{number}.txt").write_text(f"{subject}\n", encoding="utf-8")
        git(folder, "add", "-A")
        git(folder, "commit", "-q", "-m", subject)
    for name in dirty:
        (folder / name).write_text("work in progress\n", encoding="utf-8")


def make_writable_and_retry(function, path, error):
    """Git marks its object files read only, and Windows will not delete a read only file."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def choose_base(parent=CODE_DIR):
    """The folder the demo world is built in: .demo-world inside this project, so no personal path shows on screen.

    Only a folder this script made is wiped and reused. If something else is already there, the temp folder is used.
    """
    wanted = Path(parent) / ".demo-world"
    try:
        if wanted.exists():
            if not (wanted / MARKER).exists():
                raise OSError("a folder that is not ours is already there")
            shutil.rmtree(wanted, onexc=make_writable_and_retry)
        wanted.mkdir(parents=True)
        (wanted / MARKER).write_text("made by demo.py\n", encoding="utf-8")
        return wanted
    except OSError:
        base = Path(tempfile.mkdtemp(prefix="console-demo-"))
        (base / MARKER).write_text("made by demo.py\n", encoding="utf-8")
        return base


def build_world(base):
    """Writes the made-up projects, index, inbox, reports, skills, agents, routines and history. Returns (root, home)."""
    root, home, work, reports = base / "root", base / "claude-home", base / "work", base / "reports"
    make_repo(work / "Atlas", ["Add the project list page", "Show uncommitted work first", "Fix the empty inbox message"], ["notes.md", "draft-homepage.html"])
    make_repo(work / "Harbour", ["Add a health check", "Return clearer errors"], ["TODO.txt"])
    make_repo(work / "Ledger", ["Import the March statement"], [])
    (work / "Notes").mkdir(parents=True)
    (work / "Notes" / "meeting.md").write_text("Notes from this week.\n", encoding="utf-8")
    (reports / "weekly").mkdir(parents=True)
    fresh = reports / "weekly" / (time.strftime("%Y-%m-%d") + ".md")
    older = reports / "weekly" / (time.strftime("%Y-%m-%d", time.localtime(time.time() - 5 * 86400)) + ".md")
    fresh.write_text("Weekly report.\n", encoding="utf-8")
    older.write_text("An older weekly report.\n", encoding="utf-8")
    stamp = time.time() - 5 * 86400
    os.utime(older, (stamp, stamp))
    (reports / "Status.md").write_text("Status.\n", encoding="utf-8")
    root.mkdir()
    index = [
        "# Demo index", "", "## Where things are", "", "| What | Where |", "|---|---|",
        f"| Atlas web app | `{work / 'Atlas'}` |", f"| Harbour API | `{work / 'Harbour'}` |",
        f"| Ledger scripts | `{work / 'Ledger'}` |", f"| Notes | `{work / 'Notes'}` |", "",
        "## Where each routine leaves its report", "", "| Routine | When | Report |", "|---|---|---|",
        f"| Weekly report | Mondays | `{reports / 'weekly' / 'YYYY-MM-DD.md'}` |",
        f"| Status | Hourly | `{reports / 'Status.md'}` |",
        f"| Release notes | Fridays | `{reports / 'release-notes' / 'YYYY-MM-DD.md'}` |", "",
    ]
    (root / "CLAUDE.md").write_text("\n".join(index), encoding="utf-8")
    (root / "INBOX.md").write_text("# Inbox\n\n" + "".join(f"- {idea}\n" for idea in IDEAS), encoding="utf-8")

    for name, (description, friends) in SKILLS.items():
        folder = home / "skills" / name
        folder.mkdir(parents=True)
        body = "".join(f"Pairs well with `{friend}`.\n" for friend in friends)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")
    (home / "agents").mkdir(parents=True)
    for name, description in AGENTS.items():
        (home / "agents" / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nUsed by the `review-change` skill.\n", encoding="utf-8")
    routine = home / "scheduled-tasks" / "weekly-report"
    routine.mkdir(parents=True)
    (routine / "SKILL.md").write_text(
        f"---\nname: weekly-report\ndescription: Runs weekly-report\n---\nWork in {work / 'Atlas'}. Use the `summarise-week` skill and save the report to {reports / 'weekly'}.\n",
        encoding="utf-8",
    )
    lines = []
    for skill, count in USE.items():
        lines += [json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": skill}}]}}, separators=(",", ":"))] * count
    lines += [json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Agent", "input": {"subagent_type": "reviewer", "prompt": "x"}}]}}, separators=(",", ":"))] * 4
    history = home / "projects" / "atlas"
    history.mkdir(parents=True)
    for number in range(SESSIONS):
        chunk = [json.dumps({"type": "user", "message": "hello"}, separators=(",", ":"))] + lines[number::SESSIONS]
        (history / f"session-{number:02d}.jsonl").write_bytes(("\n".join(chunk) + "\n").encode("utf-8"))
    return root, home


def make_manager(root, home):
    """A chat manager that answers with demo_claude.py. The real command is never looked for, so nothing real can run."""
    return chat.ChatManager(
        root, home, lambda: serve_dashboard.indexed_projects(root),
        launcher=[sys.executable, "-I", str(CODE_DIR / "demo_claude.py")], executable="demo",
        version=lambda: "demo", tested=lambda: {"demo"}, limits={"min_gap_seconds": 0.5},
    )


def main():
    base = choose_base()
    try:
        root, home = build_world(base)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"The demo could not build its made-up projects ({error}). It needs git on your path.", file=sys.stderr)
        return 1
    print(f"Made-up world built in {base}")
    try:
        server = serve_dashboard.make_server(root, serve_dashboard.PORTS, home, chat_manager=make_manager(root, home))
    except OSError as error:
        print(f"Could not start: no free port from {serve_dashboard.PORTS[0]} to {serve_dashboard.PORTS[-1]} ({error}).", file=sys.stderr)
        return 1
    serve_dashboard.serve(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
