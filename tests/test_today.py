import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_dashboard as overview
import serve_dashboard
from test_build_dashboard import index_state, make_index_dir, make_repo, snapshot
from test_serve_dashboard import as_json, send, start, stop

HOUR = 3600


def age(path, hours):
    stamp = time.time() - hours * HOUR
    os.utime(path, (stamp, stamp))


@pytest.fixture
def lab(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo, ["first"])
    clean = tmp_path / "clean"
    make_repo(clean, ["only"])
    plain_folder = tmp_path / "plain"
    plain_folder.mkdir()
    (plain_folder / "page.txt").write_text("x", encoding="utf-8")
    reports = tmp_path / "reports"
    reports.mkdir()
    fixed = tmp_path / "Updates.md"
    fixed.write_text("TOPSECRET-CONTENT", encoding="utf-8")
    index_dir = make_index_dir(
        tmp_path,
        [("Repo", repo), ("Clean", clean), ("Plain", plain_folder), ("Gone", tmp_path / "missing")],
        [("Weekly", reports / "YYYY-MM-DD.md"), ("Fixed", fixed), ("Absent", tmp_path / "nowhere" / "YYYY-MM-DD.md")],
        inbox="# Inbox\n\n" + "".join(f"- idea {n}\n" for n in range(1, 16)),
    )
    return type("Lab", (), {"tmp": tmp_path, "repo": repo, "clean": clean, "plain": plain_folder, "reports": reports, "fixed": fixed, "index_dir": index_dir})


def card(data, label, group="projects"):
    return next(c for c in data[group] if c["label"] == label)


def test_a_dirty_project_lists_its_count_and_the_first_five_status_lines(lab):
    for n in range(7):
        (lab.repo / f"file{n}.txt").write_text("x", encoding="utf-8")

    result = card(serve_dashboard.build_overview(lab.index_dir), "Repo")

    assert result["uncommitted"] == 7
    assert len(result["dirty"]) == 5 and all(line.startswith("??") for line in result["dirty"])


def test_a_clean_project_has_no_dirty_lines_and_a_plain_folder_has_none_at_all(lab):
    data = serve_dashboard.build_overview(lab.index_dir)

    assert card(data, "Clean")["uncommitted"] == 0 and card(data, "Clean")["dirty"] == []
    assert "dirty" not in card(data, "Plain")


def test_a_project_that_cannot_be_read_stays_in_the_list_as_an_error(lab):
    result = card(serve_dashboard.build_overview(lab.index_dir), "Gone")

    assert result["kind"] == "error" and "does not exist" in result["error"]


def test_status_lines_lose_control_and_invisible_characters_and_are_cut(lab):
    cleaned = overview.plain("?? a\x1b[31mb‮c​d\x00" + "z" * 400, overview.DIRTY_LINE_CAP)

    assert cleaned.startswith("?? a[31mbcd") and len(cleaned) == overview.DIRTY_LINE_CAP
    assert not any(c in cleaned for c in "\x1b‮​\x00")


def test_reports_changed_in_the_last_24_hours_are_listed_with_a_time_and_older_ones_are_not(lab):
    fresh, old, week = lab.reports / "2026-10-22.md", lab.reports / "2026-10-20.md", lab.reports / "2026-10-18.md"
    for path, hours in ((fresh, 2), (old, 30), (week, 72)):
        path.write_text("body", encoding="utf-8")
        age(path, hours)

    result = card(serve_dashboard.build_overview(lab.index_dir), "Weekly", "reports")

    assert [item["name"] for item in result["recent"]] == ["2026-10-22.md"]
    assert len(result["recent"][0]["when"]) == len("2026-10-22 09:30")
    assert len(result["files"]) == 3


def test_the_recent_list_is_capped_at_ten_newest_first(lab):
    for n in range(14):
        path = lab.reports / f"2026-10-{n + 1:02d}.md"
        path.write_text("x", encoding="utf-8")
        age(path, n * 0.5)

    recent = card(serve_dashboard.build_overview(lab.index_dir), "Weekly", "reports")["recent"]

    assert len(recent) == 10 and recent[0]["name"] == "2026-10-01.md"


def test_a_fixed_report_file_is_recent_only_when_it_changed_in_the_last_24_hours(lab):
    age(lab.fixed, 3)
    assert len(card(serve_dashboard.build_overview(lab.index_dir), "Fixed", "reports")["recent"]) == 1

    age(lab.fixed, 40)
    assert card(serve_dashboard.build_overview(lab.index_dir), "Fixed", "reports")["recent"] == []


def test_a_missing_report_location_is_an_error_with_no_recent_list(lab):
    result = card(serve_dashboard.build_overview(lab.index_dir), "Absent", "reports")

    assert result["kind"] == "error" and "recent" not in result


def test_report_contents_never_reach_the_overview(lab):
    (lab.reports / "2026-10-22.md").write_text("TOPSECRET-CONTENT", encoding="utf-8")

    assert "TOPSECRET-CONTENT" not in json.dumps(serve_dashboard.build_overview(lab.index_dir))


def test_report_names_are_cleaned_of_control_characters(lab):
    assert overview.plain("a\x07b‮.md", 120) == "ab.md"


def test_an_overnight_report_with_invisible_characters_in_its_name_arrives_cleaned(lab):
    path = lab.reports / "2026-10-22‮x​.md"
    path.write_text("x", encoding="utf-8")

    recent = card(serve_dashboard.build_overview(lab.index_dir), "Weekly", "reports")["recent"]

    assert [item["name"] for item in recent] == ["2026-10-22x.md"]


def test_status_lines_reach_the_page_cleaned_even_when_git_prints_odd_characters(lab, monkeypatch):
    real = overview.run_git

    def odd(folder, *args):
        if args[:1] == ("status",):
            return subprocess.CompletedProcess(args, 0, "?? a\x1b[31m‮b.txt\n", "")
        return real(folder, *args)

    monkeypatch.setattr(overview, "run_git", odd)

    assert card(serve_dashboard.build_overview(lab.index_dir), "Repo")["dirty"] == ["?? a[31mb.txt"]


def test_all_inbox_ideas_arrive_in_file_order_so_the_page_can_show_the_latest_ten(lab):
    ideas = serve_dashboard.build_overview(lab.index_dir)["inbox"]["ideas"]

    assert ideas[0] == "idea 1" and ideas[-1] == "idea 15" and len(ideas) == 15


def test_the_today_page_adds_no_endpoint_and_no_static_file(lab):
    server = start(lab.index_dir)
    app = type("App", (), {"port": server.server_address[1], "token": server.token})
    try:
        assert sorted(serve_dashboard.STATIC_FILES) == ["/", "/app.css", "/app.js", "/graph.js"]
        assert send(app, "GET", "/api/today").status == 404
        body = send(app, "GET", "/").body.decode("utf-8")
        assert 'id="today-view"' in body and 'id="tab-today"' in body
        assert "dirty" in as_json(send(app, "GET", "/api/overview"))["projects"][0]
    finally:
        stop(server)


def test_building_the_today_data_writes_nothing(lab):
    for n in range(3):
        (lab.repo / f"new{n}.txt").write_text("x", encoding="utf-8")
    before = (snapshot(lab.tmp), index_state(lab.repo), (lab.index_dir / "INBOX.md").read_bytes())

    serve_dashboard.build_overview(lab.index_dir)
    serve_dashboard.build_overview(lab.index_dir)

    assert (snapshot(lab.tmp), index_state(lab.repo), (lab.index_dir / "INBOX.md").read_bytes()) == before
