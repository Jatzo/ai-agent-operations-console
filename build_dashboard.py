"""Builds dashboard.html, a snapshot of the work folders and reports named in the CLAUDE.md index next to this script.

It reads only: git log and status (never anything that changes a repo), file modified times and the
inbox. The one file it writes is dashboard.html next to this script, replaced in one step.
"""

import fnmatch
import html
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
    command = ["git", "--no-optional-locks", *args]
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


def esc(value):
    return html.escape(str(value), quote=True)


STYLE = """
:root{color-scheme:light dark;--paper:#f6f3ec;--card:#fffdf8;--ink:#201d18;--quiet:#6b655a;--line:#ddd6c7;--accent:#2f5d50;--warn:#9a3b1e;--s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px;--s6:32px}
@media (prefers-color-scheme:dark){:root{--paper:#171613;--card:#201e1a;--ink:#ece7da;--quiet:#a39d8e;--line:#37342d;--accent:#8cc2af;--warn:#e89a7d}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 "Segoe UI",system-ui,sans-serif}
main{max-width:72rem;margin:0 auto;padding:var(--s6) var(--s4)}
h1{font-size:1.75rem;margin:0 0 var(--s2)}
h2{font-size:1.125rem;margin:var(--s6) 0 var(--s3);padding-bottom:var(--s2);border-bottom:1px solid var(--line)}
h3{font-size:1rem;margin:0}
p{margin:0}
.note{color:var(--quiet)}
.grid{display:grid;gap:var(--s4);grid-template-columns:repeat(auto-fill,minmax(min(100%,17rem),1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:var(--s4);display:flex;flex-direction:column;gap:var(--s3);min-width:0}
.path{font:0.8125rem/1.4 Consolas,"Cascadia Mono",ui-monospace,monospace;color:var(--quiet);overflow-wrap:anywhere}
dl{display:grid;grid-template-columns:1fr auto;align-items:baseline;gap:var(--s1) var(--s4);margin:0}
dt{color:var(--quiet)}
dd{margin:0;text-align:right;font:600 1rem Consolas,"Cascadia Mono",ui-monospace,monospace}
ul{margin:0;padding-left:var(--s5)}
li{overflow-wrap:anywhere}
.when{color:var(--quiet);font-family:Consolas,"Cascadia Mono",ui-monospace,monospace;font-size:0.8125rem}
.problem{border-color:var(--warn)}
.problem h3,.problem .what{color:var(--warn)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
"""


def figures(pairs):
    rows = "".join(f"<dt>{esc(label)}</dt><dd>{esc(value)}</dd>" for label, value in pairs)
    return f"<dl>{rows}</dl>"


def items(values):
    return "<ul>" + "".join(f"<li>{value}</li>" for value in values) + "</ul>"


def problem_card(card):
    return (
        '<article class="card problem">'
        f"<h3>{esc(card['label'])}</h3>"
        f'<p class="what">Could not read</p><p class="path">{esc(card["path"])}</p>'
        f'<p class="note">{esc(card["error"])}</p></article>'
    )


def project_card(card):
    if card["kind"] == "error":
        return problem_card(card)
    head = f"<h3>{esc(card['label'])}</h3>" f'<p class="path">{esc(card["path"])}</p>'
    if card["kind"] == "git":
        body = figures([(f"Commits, last {WINDOW_DAYS} days", card["commits"]), ("Uncommitted files", card["uncommitted"])])
        if card["subjects"]:
            body += items(esc(subject) for subject in card["subjects"])
    else:
        body = '<p class="note">No git here.</p>'
        body += figures([(f"Files changed, last {WINDOW_DAYS} days", card["changed"])])
        body += items(esc(name) for name in card["newest"]) if card["newest"] else '<p class="note">Nothing changed.</p>'
    return f'<article class="card">{head}{body}</article>'


def report_card(card):
    if card["kind"] == "error":
        return problem_card(card)
    head = f"<h3>{esc(card['label'])}</h3>" f'<p class="path">{esc(card["path"])}</p>'
    if card["kind"] == "fixed":
        body = f'<p>{esc(card["name"])} <span class="when">{esc(card["date"])}</span></p>'
    elif card["files"]:
        body = items(f'{esc(name)} <span class="when">{esc(date)}</span>' for name, date in card["files"])
    else:
        body = f'<p class="note">Nothing in the last {WINDOW_DAYS} days.</p>'
    return f'<article class="card">{head}{body}</article>'


def inbox_card(ideas, error, inbox_path):
    if error:
        return problem_card({"label": "Inbox", "path": str(inbox_path), "error": error})
    body = items(esc(idea) for idea in ideas) if ideas else '<p class="note">Empty.</p>'
    return f'<article class="card">{body}</article>'


def build_page(os_dir, now):
    index_path = os_dir / "CLAUDE.md"
    inbox_path = os_dir / "INBOX.md"
    index_problem = None
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError as error:
        text = None
        index_problem = f"{index_path} could not be read: {error}"

    def index_cards(parse, make_card):
        if text is None:
            return [problem_card({"label": "Could not read the index", "path": str(index_path), "error": index_problem})]
        try:
            return [make_card(row) for row in parse(text)]
        except CouldNotRead as error:
            return [problem_card({"label": "Could not read the index", "path": str(index_path), "error": str(error)})]

    project_cards = index_cards(parse_projects, lambda row: project_card(read_project(row[0], row[1], now)))
    report_cards = index_cards(parse_reports, lambda row: report_card(read_report(row[0], row[1], row[2], now, row[3] or "*")))
    ideas, inbox_error = read_inbox(inbox_path)

    stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    return (
        '<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Work overview</title><style>{STYLE}</style></head><body><main>"
        f'<h1>Work overview</h1><p class="note">Snapshot taken {esc(stamp)}. Run build_dashboard.py again to refresh it.</p>'
        f'<h2>Projects</h2><div class="grid">{"".join(project_cards)}</div>'
        f'<h2>Reports</h2><div class="grid">{"".join(report_cards)}</div>'
        f'<h2>Inbox</h2><div class="grid">{inbox_card(ideas, inbox_error, inbox_path)}</div>'
        "</main></body></html>"
    )


def run(os_dir=CODE_DIR):
    """Write dashboard.html in os_dir and return its path. The output path is never taken from the caller."""
    os_dir = Path(os_dir)
    page = build_page(os_dir, time.time())
    output = os_dir / "dashboard.html"
    temporary = os_dir / "dashboard.html.tmp"
    try:
        temporary.write_text(page, encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


if __name__ == "__main__":
    print(run())
