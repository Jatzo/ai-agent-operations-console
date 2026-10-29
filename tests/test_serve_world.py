import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serve_dashboard
import world
from test_build_dashboard import make_index_dir
from test_serve_dashboard import as_json, send, start, stop, tree_state
from test_world import agent_line, command_line, make_agent, make_routine, make_skill, skill_line, write_transcript

SECRET = "SECRET-TOKEN-98765"


@pytest.fixture
def worldapp(tmp_path):
    home = tmp_path / "home"
    make_skill(home / "skills", "ponytail-review", description="Reviews a change. " + "<script>x</script>\x07")
    make_skill(home / "skills", "no-ai-slop", body="Pairs with `ponytail-review`.")
    make_agent(home, "reviewer")
    project = tmp_path / "work" / "Atlas"
    project.mkdir(parents=True)
    make_routine(home, "weekly-check", f"Work in {project}.")
    index_dir = make_index_dir(tmp_path, [("Atlas", project)], [("SEO", project / "reports" / "YYYY-MM-DD.md")], inbox="# Inbox\n\n- an idea\n")
    write_transcript(home / "projects", "p/session.jsonl", [
        skill_line("ponytail-review"), skill_line("ponytail-review"), skill_line(SECRET),
        agent_line("reviewer"), command_line(SECRET), agent_line(SECRET),
        json.dumps({"type": "user", "message": {"content": f"private: {SECRET} password hunter2"}}),
    ])
    server = serve_dashboard.make_server(index_dir, ports=[0], home=home)
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(
        server=server, port=server.server_address[1], token=server.token, index_dir=index_dir, home=home, tmp=tmp_path,
        inbox=index_dir / "INBOX.md",
    )
    stop(server)


def get_world(app, fresh=False):
    response = send(app, "GET", "/api/world" + ("?fresh=1" if fresh else ""))
    assert response.status == 200
    return as_json(response)


def run_scan_through_the_api(app):
    response = send(app, "POST", "/api/scan", body=b"{}")
    assert response.status == 202, response.body
    app.server.scan.thread.join(timeout=20)
    return as_json(send(app, "GET", "/api/scan"))


def test_the_world_endpoint_returns_the_graph_without_any_file_text(worldapp):
    data = get_world(worldapp)

    types = {node["type"] for node in data["nodes"]}
    assert {"project", "report", "routine", "skill", "agent", "idea", "hub"} <= types
    assert all("text" not in node for node in data["nodes"])
    assert data["scan"]["state"] == "idle"
    project = next(node for node in data["nodes"] if node["type"] == "project")
    assert len(project["key"]) == 12


def test_the_project_key_in_the_world_opens_the_drill_down(worldapp):
    project = next(node for node in get_world(worldapp)["nodes"] if node["type"] == "project")

    response = send(worldapp, "GET", "/api/project?id=" + project["key"])

    assert response.status == 200
    assert as_json(response)["label"] == "Atlas"


def test_the_world_and_scan_endpoints_need_the_token_and_the_host(worldapp):
    for path in ("/api/world", "/api/scan"):
        assert send(worldapp, "GET", path, headers={"X-Console-Token": None}).status == 403
        assert send(worldapp, "GET", path, headers={"Host": "evil.example"}).status == 403
        assert send(worldapp, "GET", path).status == 200


SCAN_REFUSALS = [
    ("no token", {"X-Console-Token": None}, 403),
    ("wrong token", {"X-Console-Token": "x" * 43}, 403),
    ("foreign origin", {"Origin": "http://evil.example"}, 403),
    ("origin null", {"Origin": "null"}, 403),
    ("wrong media type", {"Content-Type": "text/plain"}, 415),
    ("missing length", {"Content-Length": None}, 411),
    ("negative length", {"Content-Length": "-1"}, 400),
]


@pytest.mark.parametrize("name, headers, expected", SCAN_REFUSALS, ids=[r[0] for r in SCAN_REFUSALS])
def test_a_scan_request_with_one_fault_is_refused_and_no_scan_starts(worldapp, name, headers, expected):
    response = send(worldapp, "POST", "/api/scan", headers=headers, body=b"{}")

    assert response.status == expected
    assert worldapp.server.scan.thread is None


def test_get_cannot_start_a_scan_and_other_methods_are_refused(worldapp):
    assert send(worldapp, "PUT", "/api/scan", body=b"{}").status == 405
    assert send(worldapp, "DELETE", "/api/world").status == 405
    assert send(worldapp, "POST", "/api/world", body=b"{}").status == 405
    assert worldapp.server.scan.thread is None


def test_a_scan_adds_use_counts_to_the_world_and_a_quick_second_scan_is_refused(worldapp):
    state = run_scan_through_the_api(worldapp)

    assert state["state"] == "done" and state["files_done"] == state["files_total"] == 1
    assert "counts" not in state
    nodes = {node["id"]: node for node in get_world(worldapp)["nodes"]}
    assert nodes["skill:global:ponytail-review"]["use"] == 2
    assert nodes["agent:global:reviewer"]["use"] == 1
    assert nodes["skill:global:no-ai-slop"]["use"] == 0
    assert send(worldapp, "POST", "/api/scan", body=b"{}").status == 429


def test_a_planted_secret_never_comes_out_of_any_endpoint(worldapp):
    run_scan_through_the_api(worldapp)

    bodies = [
        send(worldapp, "GET", path).body.decode("utf-8").lower()
        for path in ("/api/world", "/api/world?fresh=1", "/api/scan", "/api/overview")
    ]

    for body in bodies:
        assert SECRET.lower() not in body
        assert "hunter2" not in body
        assert "private:" not in body


def test_a_hostile_description_reaches_the_api_cleaned_and_capped(worldapp):
    nodes = {node["id"]: node for node in get_world(worldapp)["nodes"]}

    desc = nodes["skill:global:ponytail-review"]["desc"]

    assert "\x07" not in desc
    assert desc.startswith("Reviews a change. <script>")
    assert len(desc) <= 300


def test_the_cap_is_applied_to_what_the_page_receives_and_says_what_was_cut(worldapp, monkeypatch):
    monkeypatch.setattr(world, "MAX_NODES", 6)

    data = get_world(worldapp)

    assert len(data["nodes"]) == 6
    assert data["cut"]["nodes"] > 0
    ids = {node["id"] for node in data["nodes"]}
    assert all(edge["a"] in ids and edge["b"] in ids for edge in data["edges"])


def test_a_new_idea_shows_in_the_world_straight_away(worldapp):
    get_world(worldapp)

    response = send(worldapp, "POST", "/api/inbox", body=json.dumps({"text": "fresh idea"}).encode("utf-8"))

    assert response.status == 201
    labels = [node["label"] for node in get_world(worldapp)["nodes"] if node["type"] == "idea"]
    assert "fresh idea" in labels


def test_the_world_is_cached_so_repeat_requests_do_not_rebuild_it(worldapp, monkeypatch):
    calls = []
    real = world.build_world

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(world, "build_world", counting)

    for _ in range(5):
        get_world(worldapp)
    get_world(worldapp, fresh=True)

    assert len(calls) == 1


def test_after_every_world_and_scan_endpoint_only_the_inbox_has_changed(worldapp):
    before = tree_state(worldapp.tmp)

    get_world(worldapp)
    run_scan_through_the_api(worldapp)
    send(worldapp, "GET", "/api/scan")
    get_world(worldapp, fresh=True)
    send(worldapp, "POST", "/api/inbox", body=json.dumps({"text": "one change"}).encode("utf-8"))

    after = tree_state(worldapp.tmp)
    changed = {name for name in after if after[name] != before.get(name)}
    assert changed == {str(Path("os") / "INBOX.md")}
    assert set(after) == set(before)


def test_stopping_the_server_stops_the_scan_and_the_threads_do_not_outlive_it(tmp_path):
    home = tmp_path / "home"
    for number in range(30):
        write_transcript(home / "projects", f"p/{number}.jsonl", [skill_line("no-ai-slop")] * 300)
    make_skill(home / "skills", "no-ai-slop")
    index_dir = make_index_dir(tmp_path, [("Atlas", tmp_path)], [("SEO", tmp_path / "YYYY-MM-DD.md")])
    server = start(index_dir) if False else serve_dashboard.make_server(index_dir, ports=[0], home=home)
    server.world_view()
    server.scan.start()

    server.server_close()

    assert not server.scan.thread.is_alive()
    assert server.scan.snapshot()["state"] in ("stopped", "done")


def test_the_static_files_include_the_graph_script_and_nothing_else_new(worldapp):
    assert sorted(serve_dashboard.STATIC_FILES) == ["/", "/app.css", "/app.js", "/graph.js"]
    assert send(worldapp, "GET", "/graph.js").status == 200
    assert send(worldapp, "GET", "/world.py").status == 404
    assert send(worldapp, "GET", "/web/graph.js").status == 404


def test_the_scan_request_errors_do_not_talk_about_ideas(worldapp):
    bad_type = send(worldapp, "POST", "/api/scan", headers={"Content-Type": "text/plain"}, body=b"{}")
    bad_body = send(worldapp, "POST", "/api/scan", body=b"[1]")

    assert bad_type.status == 415 and bad_body.status == 400
    assert "idea" not in as_json(bad_type)["error"].lower()
    assert "idea" not in as_json(bad_body)["error"].lower()
