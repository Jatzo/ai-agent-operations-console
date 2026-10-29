"""Reads the work folders and reports named in the CLAUDE.md index: git log and status (never anything that changes a repo),
file modified times and the inbox. It writes nothing."""

import fnmatch
import os
import re
import subprocess
import time
import unicodedata
from datetime import datetime
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
WINDOW_DAYS = 7
GIT_TIMEOUT_SECONDS = 10
PRUNED_FOLDERS = {"node_modules", ".git", "__pycache__", ".venv", ".next"}
WALK_SECONDS = 5
HOLDING_FOLDERS = {"to build"}
NEWEST_SHOWN = 5
SUBJECTS_SHOWN = 3
DIRTY_SHOWN = 5
DIRTY_LINE_CAP = 160
RECENT_HOURS = 24
RECENT_SHOWN = 10
COMMITS_SHOWN = 50
LINES_SHOWN = 100
HIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Cs"}


def plain(text, cap):
    """One line of text with control and invisible characters removed, cut to cap characters."""
    return "".join(c for c in str(text) if unicodedata.category(c) not in HIDDEN_CATEGORIES)[:cap]


class CouldNotRead(Exception):
    pass


def section_lines(text, heading):
    """The lines under '## heading' up to the next '## ' heading, or None if the heading is absent."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == f"## {heading}"), None)
    if start is None:
        return None
    body = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def table_rows(lines):
    """Cell lists for each data row of the markdown table in lines, without its header row."""
    rows = []
    for line in lines:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows[1:]


def first_path(cell):
    found = re.search(r"`([^`]+)`", cell)
    return Path(found.group(1)) if found else None


def parse_projects(text):
    lines = section_lines(text, "Where things are")
    if lines is None:
        raise CouldNotRead("the index has no 'Where things are' heading")
    projects = []
    for cells in table_rows(lines):
        path = first_path(cells[-1])
        if path is None or path.name.lower() in HOLDING_FOLDERS or path.is_file():
            continue
        projects.append((cells[0], path))
    if not projects:
        raise CouldNotRead("the 'Where things are' table has no project rows")
    return projects


def parse_reports(text):
    lines = section_lines(text, "Where each routine leaves its report")
    if lines is None:
        raise CouldNotRead("the index has no 'Where each routine leaves its report' heading")
    reports = []
    for cells in table_rows(lines):
        path = first_path(cells[-1])
        if path is None:
            continue
        if "YYYY-MM-DD" in path.name:
            reports.append((cells[0], path.parent, False, path.name.replace("YYYY-MM-DD", "*")))
        else:
            reports.append((cells[0], path, True, None))
    if not reports:
        raise CouldNotRead("the report table has no rows")
    return reports


def run_git(folder, *args):
    # A repo's own config can name programs: core.fsmonitor runs on status, and gpg.program runs when log.showSignature is on.
    command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "log.showSignature=false", *args]
    try:
        return subprocess.run(
            command, cwd=folder, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=GIT_TIMEOUT_SECONDS,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired:
        raise CouldNotRead(f"git timed out after {GIT_TIMEOUT_SECONDS} seconds in {folder}")
    except OSError as error:
        raise CouldNotRead(f"git could not start in {folder}: {error}")


def git_detail(folder):
    log = run_git(
        folder, "-c", "core.quotepath=false",
        "log", "-n", str(COMMITS_SHOWN), "--date=short", "--format=%h%x09%ad%x09%s",
    )
    empty = log.returncode != 0 and "does not have any commits yet" in log.stderr
    if log.returncode != 0 and not empty:
        raise CouldNotRead(f"git could not read the history of {folder}")
    commits = []
    for line in log.stdout.splitlines():
        short, date, subject = (line.split("\t", 2) + ["", ""])[:3]
        commits.append({"hash": short, "date": date, "subject": subject})
    status = run_git(folder, "-c", "core.quotepath=false", "status", "--porcelain")
    if status.returncode != 0:
        raise CouldNotRead(f"git could not read the status of {folder}")
    lines = [line for line in status.stdout.splitlines() if line.strip()]
    return {
        "commits": commits, "empty": empty,
        "uncommitted": lines[:LINES_SHOWN], "uncommitted_total": len(lines),
    }


def changed_since_window(folder, now):
    """Every file changed in the window, newest first, as (modified time, path under folder)."""
    cutoff = now - WINDOW_DAYS * 86400
    deadline = time.monotonic() + WALK_SECONDS
    found = []
    for root, dirs, files in os.walk(folder):
        if time.monotonic() > deadline:
            raise CouldNotRead(f"{folder} took longer than {WALK_SECONDS} seconds to walk")
        dirs[:] = [name for name in dirs if name not in PRUNED_FOLDERS]
        for name in files:
            try:
                modified = os.path.getmtime(os.path.join(root, name))
            except OSError:
                continue
            if modified > cutoff:
                found.append((modified, os.path.relpath(os.path.join(root, name), folder)))
    found.sort(reverse=True)
    return found


def recent_files(folder, now):
    """Count and newest names of files changed in the window. Pruned folders are never entered."""
    found = changed_since_window(folder, now)
    return len(found), [name for _, name in found[:NEWEST_SHOWN]]


def folder_kind(folder):
    """'git' if the folder is the top of its own repo, 'files' if it is not, so a subfolder never shows its parent's numbers."""
    if not folder.is_dir():
        raise CouldNotRead(f"{folder} does not exist or is not a folder")
    inside = run_git(folder, "rev-parse", "--is-inside-work-tree")
    if inside.returncode == 0:
        top = run_git(folder, "rev-parse", "--show-toplevel")
        own = top.returncode == 0 and Path(top.stdout.strip()).resolve() == folder.resolve()
        return "git" if own else "files"
    if "not a git repository" in inside.stderr:
        return "files"
    raise CouldNotRead(f"git could not read {folder}")


def read_project(label, folder, now):
    card = {"label": label, "path": str(folder)}
    try:
        if folder_kind(folder) == "git":
            log = run_git(folder, "log", f"--since={WINDOW_DAYS} days ago", "--format=%s")
            status = run_git(folder, "status", "--short")
            no_commits_yet = log.returncode != 0 and "does not have any commits yet" in log.stderr
            if (log.returncode != 0 and not no_commits_yet) or status.returncode != 0:
                raise CouldNotRead(f"git could not read {folder}")
            subjects = [line for line in log.stdout.splitlines() if line.strip()]
            dirty = [line for line in status.stdout.splitlines() if line.strip()]
            card.update(
                kind="git", commits=len(subjects), subjects=subjects[:SUBJECTS_SHOWN],
                uncommitted=len(dirty), dirty=[plain(line, DIRTY_LINE_CAP) for line in dirty[:DIRTY_SHOWN]],
            )
        else:
            changed, newest = recent_files(folder, now)
            card.update(kind="files", changed=changed, newest=newest)
    except CouldNotRead as error:
        card.update(kind="error", error=str(error))
    return card


def read_report(label, location, fixed, now, pattern="*"):
    card = {"label": label, "path": str(location)}
    cutoff = now - WINDOW_DAYS * 86400
    recent_cutoff = now - RECENT_HOURS * 3600

    def when(stamp):
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")

    try:
        if fixed:
            if not location.is_file():
                raise CouldNotRead(f"{location} does not exist")
            stamp = location.stat().st_mtime
            card.update(
                kind="fixed", name=location.name, date=datetime.fromtimestamp(stamp).strftime("%Y-%m-%d"),
                recent=[{"name": plain(location.name, 120), "when": when(stamp)}] if stamp > recent_cutoff else [],
            )
        else:
            if not location.is_dir():
                raise CouldNotRead(f"{location} does not exist or is not a folder")
            files = []
            for entry in location.iterdir():
                if fnmatch.fnmatch(entry.name, pattern) and entry.is_file() and entry.stat().st_mtime > cutoff:
                    files.append((entry.stat().st_mtime, entry.name))
            files.sort(reverse=True)
            card.update(
                kind="folder",
                files=[(name, datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")) for stamp, name in files],
                recent=[{"name": plain(name, 120), "when": when(stamp)} for stamp, name in files if stamp > recent_cutoff][:RECENT_SHOWN],
            )
    except (CouldNotRead, OSError) as error:
        card.update(kind="error", error=str(error))
    return card


def read_index(index_dir):
    """The index text, or a reason it could not be read."""
    try:
        return (Path(index_dir) / "CLAUDE.md").read_text(encoding="utf-8"), None
    except OSError as error:
        return None, f"{Path(index_dir) / 'CLAUDE.md'} could not be read: {error}"


def read_inbox(inbox_path):
    try:
        text = inbox_path.read_text(encoding="utf-8")
    except OSError as error:
        return None, f"{inbox_path} could not be read: {error}"
    ideas = []
    under_heading = False
    for line in text.splitlines():
        if line.startswith("#"):
            under_heading = True
        elif under_heading and line.startswith(("- ", "* ")):
            ideas.append(line[2:].strip())
    return ideas, None
