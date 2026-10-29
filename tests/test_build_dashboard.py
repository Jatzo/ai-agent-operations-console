import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_dashboard as overview

HOSTILE = "<script>alert(1)</script>"
DAY = 86400


def make_index(projects, reports=()):
    lines = ["# Index", "", "## Where things are", "", "| What | Where |", "|---|---|"]
    lines += [f"| {label} | `{path}` |" for label, path in projects]
    lines += ["", "## Commands", "", "| Command | Does |", "|---|---|", "| `/os` | Looks. |", ""]
    lines += ["## Where each routine leaves its report", "", "| Routine | When | Report |", "|---|---|---|"]
    lines += [f"| {label} | Mondays | `{path}` |" for label, path in reports]
    lines += ["", "## State of a project", "", "Read it from the project.", ""]
    return "\n".join(lines)


def make_index_dir(tmp_path, projects, reports=(), inbox="# Inbox\n\nOne line per idea.\n"):
    index_dir = tmp_path / "os"
    index_dir.mkdir()
    (index_dir / "CLAUDE.md").write_text(make_index(projects, reports), encoding="utf-8")
    (index_dir / "INBOX.md").write_text(inbox, encoding="utf-8")
    return index_dir


def make_repo(folder, subjects):
    folder.mkdir()
    git = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"]
    subprocess.run(git + ["init", "-q"], cwd=folder, check=True)
    for subject in subjects:
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", subject], cwd=folder, check=True)


def age_file(path, days):
    stamp = time.time() - days * DAY
    os.utime(path, (stamp, stamp))


def snapshot(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if ".git" not in p.parts)


def index_state(repo):
    index = repo / ".git" / "index"
    if not index.exists():
        return None
    stat = index.stat()
    return (stat.st_mtime_ns, stat.st_size)


def test_a_git_folder_shows_its_commit_count_and_newest_subjects(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo, ["first", "second", "third", "fourth"])
    (repo / "notes.txt").write_text("x", encoding="utf-8")

    card = overview.read_project("Repo", repo, time.time())

    assert card["kind"] == "git"
    assert card["commits"] == 4
    assert card["subjects"] == ["fourth", "third", "second"]
    assert card["uncommitted"] == 1


def test_git_is_always_run_in_the_c_locale_so_its_messages_can_be_read(tmp_path, monkeypatch):
    seen = {}

    def fake(*args, **kwargs):
        seen.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(overview.subprocess, "run", fake)

    overview.run_git(tmp_path, "status")

    assert seen.get("LC_ALL") == "C"


def test_a_repo_with_no_commits_yet_shows_zero_commits_not_an_error(tmp_path):
    repo = tmp_path / "fresh"
    make_repo(repo, [])

    card = overview.read_project("Fresh", repo, time.time())

    assert card["kind"] == "git"
    assert card["commits"] == 0
    assert card["subjects"] == []


def test_a_folder_without_git_counts_recent_files_and_skips_node_modules(tmp_path):
    folder = tmp_path / "plain"
    (folder / "node_modules").mkdir(parents=True)
    (folder / "node_modules" / "planted.js").write_text("x", encoding="utf-8")
    (folder / "new.txt").write_text("x", encoding="utf-8")
    (folder / "old.txt").write_text("x", encoding="utf-8")
    age_file(folder / "old.txt", 30)

    card = overview.read_project("Plain", folder, time.time())

    assert card["kind"] == "files"
    assert card["changed"] == 1
    assert card["newest"] == ["new.txt"]


def test_two_files_with_the_same_name_in_different_folders_are_told_apart(tmp_path):
    folder = tmp_path / "plain"
    (folder / "a").mkdir(parents=True)
    (folder / "b").mkdir()
    (folder / "a" / "page.html").write_text("x", encoding="utf-8")
    (folder / "b" / "page.html").write_text("x", encoding="utf-8")

    card = overview.read_project("Plain", folder, time.time())

    assert sorted(card["newest"]) == [os.path.join("a", "page.html"), os.path.join("b", "page.html")]


def test_a_missing_folder_says_it_could_not_be_read(tmp_path):
    card = overview.read_project("Gone", tmp_path / "nowhere", time.time())

    assert card["kind"] == "error"
    assert "nowhere" in card["error"]


def test_a_git_command_that_times_out_says_it_could_not_be_read(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    make_repo(repo, ["one"])

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)

    monkeypatch.setattr(overview.subprocess, "run", slow)

    card = overview.read_project("Repo", repo, time.time())

    assert card["kind"] == "error"
    assert "timed out" in card["error"]




def test_files_and_holding_folders_in_the_index_are_skipped(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    holding = tmp_path / "To Build"
    holding.mkdir()
    inbox = tmp_path / "notes.txt"
    inbox.write_text("x", encoding="utf-8")
    text = make_index([("Real", real), ("Ideas", holding), ("Inbox", inbox)])

    projects = overview.parse_projects(text)

    assert [label for label, _ in projects] == ["Real"]


def test_report_paths_become_a_folder_or_a_fixed_file(tmp_path):
    text = make_index(
        [],
        [
            ("Dated", tmp_path / "reports" / "YYYY-MM-DD.md"),
            ("Fixed", tmp_path / "Business" / "Updates.md"),
        ],
    )

    reports = overview.parse_reports(text)

    assert reports[0] == ("Dated", tmp_path / "reports", False, "*.md")
    assert reports[1] == ("Fixed", tmp_path / "Business" / "Updates.md", True, None)


def test_a_report_folder_lists_only_files_from_the_last_7_days(tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "2026-10-20.md").write_text("x", encoding="utf-8")
    (folder / "2026-09-01.md").write_text("x", encoding="utf-8")
    age_file(folder / "2026-09-01.md", 50)

    card = overview.read_report("Seo", folder, False, time.time())

    assert [name for name, _ in card["files"]] == ["2026-10-20.md"]





def test_a_subfolder_of_a_repo_does_not_show_the_parents_numbers(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo, ["one", "two"])
    sub = repo / "sub"
    sub.mkdir()
    (sub / "page.txt").write_text("x", encoding="utf-8")

    card = overview.read_project("Sub", sub, time.time())

    assert card["kind"] == "files"
    assert card["newest"] == ["page.txt"]


def test_a_walk_that_runs_out_of_time_says_it_could_not_be_read(tmp_path, monkeypatch):
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(overview, "WALK_SECONDS", -1)

    card = overview.read_project("Plain", folder, time.time())

    assert card["kind"] == "error"
    assert "took longer" in card["error"]



def test_only_bullets_under_a_heading_count_as_inbox_ideas(tmp_path):
    inbox = tmp_path / "INBOX.md"
    lines = ["- stray before the heading", "# Inbox", "", "Some text.", "- first idea", "* second idea", ""]
    inbox.write_text("\n".join(lines), encoding="utf-8")

    ideas, error = overview.read_inbox(inbox)

    assert error is None
    assert ideas == ["first idea", "second idea"]


def test_a_dated_report_folder_lists_only_files_that_match_its_name_pattern(tmp_path):
    folder = tmp_path / "outputs"
    folder.mkdir()
    (folder / "review-2026-10-20.md").write_text("x", encoding="utf-8")
    (folder / "something-else.md").write_text("x", encoding="utf-8")

    card = overview.read_report("Review", folder, False, time.time(), "review-*.md")

    assert [name for name, _ in card["files"]] == ["review-2026-10-20.md"]


def test_reading_a_repo_never_runs_the_program_its_own_config_names(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo, ["one"])
    marker = tmp_path / "ran.txt"
    hook = tmp_path / "hook.py"
    hook.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    subprocess.run(["git", "config", "core.fsmonitor", f'"{sys.executable}" "{hook.as_posix()}"'], cwd=repo, check=True)
    subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True)
    assert marker.exists(), "the setup must make plain git run the program, or this test proves nothing"
    marker.unlink()

    overview.run_git(repo, "status", "--porcelain")

    assert not marker.exists()


def test_an_index_with_a_missing_heading_is_could_not_read_not_zero_rows():
    with pytest.raises(overview.CouldNotRead):
        overview.parse_projects("# Index" + chr(10) + chr(10) + "Nothing useful." + chr(10))


def test_a_table_with_no_rows_is_could_not_read_not_a_blank_section():
    text = make_index([], [])

    with pytest.raises(overview.CouldNotRead, match="has no project rows"):
        overview.parse_projects(text)
    with pytest.raises(overview.CouldNotRead, match="report table has no rows"):
        overview.parse_reports(text)
