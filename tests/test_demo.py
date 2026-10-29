import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat
import demo
import serve_dashboard
from test_serve_dashboard import stop


@pytest.fixture
def built(tmp_path):
    root, home = demo.build_world(tmp_path)
    return type("Built", (), {"base": tmp_path, "root": root, "home": home})


def test_the_demo_world_has_projects_reports_ideas_skills_agents_and_a_routine(built):
    data = serve_dashboard.build_overview(built.root)

    assert [card["label"] for card in data["projects"]] == ["Atlas web app", "Harbour API", "Ledger scripts", "Notes"]
    assert data["problems"] == [] and len(data["inbox"]["ideas"]) == 4
    assert any(card.get("recent") for card in data["reports"]), "one report is less than 24 hours old"
    assert any(card["kind"] == "error" for card in data["reports"]), "one report location is missing, as in real life"
    assert next(c for c in data["projects"] if c["label"] == "Atlas web app")["uncommitted"] == 2
    world = serve_dashboard.world.build_world(built.home, built.root)
    kinds = {node["type"] for node in world["nodes"]}
    assert {"project", "report", "routine", "skill", "agent", "idea", "hub"} <= kinds


def test_the_demo_history_gives_skills_use_counts(built):
    server = serve_dashboard.make_server(built.root, ports=[0], home=built.home, chat_manager=demo.make_manager(built.root, built.home))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        server.ensure_world()
        server.scan.start(force=True)
        server.scan.thread.join(timeout=30)
        counts = server.scan.snapshot()["counts"]
        assert counts and max(value[0] for value in counts.values()) == 14
    finally:
        stop(server)


def test_the_demo_chat_answers_with_a_canned_reply_and_never_looks_for_the_real_command(built, monkeypatch):
    def refuse(*arguments, **keywords):
        raise AssertionError("the demo must not look for the real command")

    monkeypatch.setattr(chat, "find_executable", refuse)
    monkeypatch.setattr(chat, "read_version", refuse)
    monkeypatch.setenv("PATH", "")
    manager = demo.make_manager(built.root, built.home)
    events = []

    manager.run_message("What is unfinished?", None, None, events.append)

    assert events[0]["t"] == "start" and events[-1]["t"] == "done" and events[-1]["cost"] == 0
    answer = "".join(event["text"] for event in events if event["t"] == "text")
    assert "canned" in answer and "uncommitted" in answer


def test_the_demo_world_is_made_under_a_folder_marked_as_ours(tmp_path):
    base = demo.choose_base(tmp_path)

    assert base == tmp_path / ".demo-world" and (base / demo.MARKER).exists()


def test_a_folder_that_is_not_ours_is_never_deleted(tmp_path):
    theirs = tmp_path / ".demo-world"
    theirs.mkdir()
    (theirs / "keep.txt").write_text("mine", encoding="utf-8")

    base = demo.choose_base(tmp_path)

    assert (theirs / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert base != theirs and (base / demo.MARKER).exists()


def test_the_demo_commits_use_a_made_up_identity(built):
    author = subprocess.run(["git", "log", "-1", "--format=%an <%ae>"], cwd=built.base / "work" / "Atlas", capture_output=True, text=True).stdout.strip()

    assert author == "demo <demo@example.invalid>"


def test_a_missing_git_gives_a_plain_message_and_not_a_traceback(tmp_path, monkeypatch, capsys):
    real = demo.choose_base
    monkeypatch.setattr(demo, "choose_base", lambda: real(tmp_path))
    monkeypatch.setattr(demo, "git", lambda *arguments, **keywords: (_ for _ in ()).throw(FileNotFoundError("git")))

    assert demo.main() == 1
    assert "needs git on your path" in capsys.readouterr().err


def test_a_second_run_rebuilds_in_the_same_folder_even_though_git_made_read_only_files(tmp_path):
    first = demo.choose_base(tmp_path)
    demo.build_world(first)

    second = demo.choose_base(tmp_path)

    assert second == first and not (second / "work").exists()
