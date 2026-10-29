import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat
import serve_dashboard
from test_build_dashboard import make_index_dir
from test_serve_dashboard import as_json, send, stop, tree_state
from test_world import make_skill

FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")


def wait_until(check, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def build(tmp_path, monkeypatch, tested=True, **limits):
    home = tmp_path / "home"
    make_skill(home / "skills", "alpha", description="An alpha skill.")
    project = tmp_path / "work" / "Atlas"
    project.mkdir(parents=True)
    index_dir = make_index_dir(tmp_path, [("Atlas", project)], [("SEO", project / "reports" / "YYYY-MM-DD.md")], inbox="# Inbox\n\n- an idea\n")
    monkeypatch.setenv("FAKE_PIDFILE", str(tmp_path / "pid.txt"))
    monkeypatch.setenv("FAKE_ARGSFILE", str(tmp_path / "args.json"))
    base = {"min_gap_seconds": 0, "heartbeat_seconds": 60}
    base.update(limits)
    manager = chat.ChatManager(
        index_dir, home, lambda: serve_dashboard.indexed_projects(index_dir), launcher=[sys.executable, "-I", FAKE],
        executable="C:/fake/claude.exe", version=lambda: "1.2.3",
        tested=lambda: {"1.2.3"} if tested else set(), limits=base,
    )
    server = serve_dashboard.make_server(index_dir, ports=[0], home=home, chat_manager=manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return SimpleNamespace(
        server=server, port=server.server_address[1], token=server.token, index_dir=index_dir, home=home, tmp=tmp_path,
        project=project, manager=manager,
    )


@pytest.fixture
def chatapp(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch)
    yield app
    stop(app.server)


def post_chat(app, payload=None, **kwargs):
    body = kwargs.pop("body", None)
    if body is None:
        body = json.dumps(payload if payload is not None else {"message": "Hello"}).encode("utf-8")
    return send(app, "POST", "/api/chat", body=body, **kwargs)


def events_of(response):
    return [json.loads(line) for line in response.body.decode("utf-8").splitlines() if line.strip()]


def node_id(app, kind, name):
    nodes = as_json(send(app, "GET", "/api/world"))["nodes"]
    return next(n["id"] for n in nodes if n["type"] == kind and n["name"] == name)


def args(app):
    return json.loads((app.tmp / "args.json").read_text(encoding="utf-8"))


def test_a_valid_message_streams_events_as_lines_with_the_security_headers(chatapp):
    response = post_chat(chatapp)

    assert response.status == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    names = [e["t"] for e in events_of(response)]
    assert names[0] == "start" and names[-1] == "done"


def test_the_status_endpoint_reports_readiness_the_version_and_cost(chatapp):
    status = as_json(send(chatapp, "GET", "/api/chat"))

    assert status["ready"] is True and status["version"] == "1.2.3" and status["running"] is False
    assert status["session_cost"] == 0
    assert status["model"] == chat.MODEL.capitalize() and status["budget_usd"] == chat.BUDGET_USD and status["message_chars"] == chat.MESSAGE_CAP


@pytest.mark.parametrize(
    "path,method", [("/api/chat", "GET"), ("/api/chat", "POST"), ("/api/chat/stop", "POST")],
)
def test_every_chat_endpoint_needs_the_token(chatapp, path, method):
    body = b"{}" if method == "POST" else b""

    response = send(chatapp, method, path, headers={"X-Console-Token": "wrong"}, body=body)

    assert response.status == 403
    assert not (chatapp.tmp / "pid.txt").exists()


@pytest.mark.parametrize("path,method", [("/api/chat", "GET"), ("/api/chat", "POST"), ("/api/chat/stop", "POST")])
def test_every_chat_endpoint_needs_this_computers_host_name(chatapp, path, method):
    body = b'{"message": "Hi"}' if method == "POST" else b""

    response = send(chatapp, method, path, headers={"Host": "evil.example"}, body=body)

    assert response.status == 403
    assert not (chatapp.tmp / "pid.txt").exists()


def test_a_message_from_another_origin_is_refused(chatapp):
    response = post_chat(chatapp, headers={"Origin": "http://evil.example"})

    assert response.status == 403 and not (chatapp.tmp / "pid.txt").exists()


def test_a_message_that_is_not_json_content_is_refused(chatapp):
    response = post_chat(chatapp, headers={"Content-Type": "text/plain"})

    assert response.status == 415 and not (chatapp.tmp / "pid.txt").exists()


def test_a_message_with_no_length_or_with_chunking_is_refused(chatapp):
    missing = post_chat(chatapp, headers={"Content-Length": None})
    chunked = post_chat(chatapp, headers={"Transfer-Encoding": "chunked"})

    assert missing.status == 411 and chunked.status == 411
    assert not (chatapp.tmp / "pid.txt").exists()


def test_a_body_over_24_kb_is_refused_and_one_just_under_is_read(chatapp):
    big = post_chat(chatapp, body=json.dumps({"message": "x" * 25000}).encode("utf-8"))
    huge_number = post_chat(chatapp, headers={"Content-Length": "99999999"})

    assert big.status == 413 and huge_number.status == 413
    assert not (chatapp.tmp / "pid.txt").exists()


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[1]", b'"text"', b"{}", b'{"message": 5}', b'{"message": ""}', b'{"message": "a\\u0000b"}', b'{"message": "a\\u202eb"}'],
    ids=["not-json", "array", "string", "no-message", "number", "empty", "nul", "bidi"],
)
def test_bad_bodies_are_refused_before_anything_starts(chatapp, body):
    response = post_chat(chatapp, body=body)

    assert response.status == 400
    assert not (chatapp.tmp / "pid.txt").exists()


def test_a_message_over_4000_characters_is_refused_and_exactly_4000_accented_is_accepted(chatapp):
    too_long = post_chat(chatapp, {"message": "e" * 4001})
    accented = post_chat(chatapp, {"message": "\u00e9" * 4000})

    assert too_long.status == 400
    assert accented.status == 200 and events_of(accented)[-1]["t"] == "done"
    assert len(json.dumps({"message": "\u00e9" * 4000}).encode("utf-8")) > 4096


def test_500_newlines_are_accepted(chatapp):
    response = post_chat(chatapp, {"message": "line\n" * 500})

    assert response.status == 200


@pytest.mark.parametrize("node", [5, ["x"], {"id": "x"}, "x" * 301])
def test_a_node_that_is_not_an_id_string_is_refused(chatapp, node):
    response = post_chat(chatapp, {"message": "Hi", "node": node})

    assert response.status == 400 and not (chatapp.tmp / "pid.txt").exists()


def test_a_node_id_that_is_not_in_the_world_is_refused(chatapp):
    response = post_chat(chatapp, {"message": "Hi", "node": "skill:global:does-not-exist"})

    assert response.status == 404 and not (chatapp.tmp / "pid.txt").exists()


@pytest.mark.parametrize("chat_id", [5, "short", "../../etc", "G" * 16, "A" * 16, "0" * 17])
def test_a_chat_id_that_is_not_one_of_ours_is_refused(chatapp, chat_id):
    response = post_chat(chatapp, {"message": "Hi", "chat": chat_id})

    assert response.status == 400 and not (chatapp.tmp / "pid.txt").exists()


def test_the_page_cannot_supply_a_folder_a_flag_or_a_command(chatapp):
    response = post_chat(chatapp, {
        "message": "Hi", "cwd": "C:/Windows", "path": "C:/Windows", "args": ["--dangerously-skip-permissions"],
        "flags": "--allowed-tools Bash", "command": "calc", "model": "opus", "tools": "Bash", "add_dir": "C:/",
    })
    seen = args(chatapp)

    assert response.status == 200
    assert Path(seen["cwd"]).resolve() == chatapp.index_dir.resolve()
    assert "--dangerously-skip-permissions" not in seen["argv"] and "Bash" not in " ".join(seen["argv"])
    assert seen["argv"][seen["argv"].index("--model") + 1] == "sonnet"
    assert "--add-dir" not in seen["argv"]


def test_a_project_node_runs_in_that_projects_folder(chatapp):
    response = post_chat(chatapp, {"message": "Hi", "node": node_id(chatapp, "project", "Atlas")})

    assert response.status == 200
    assert Path(args(chatapp)["cwd"]).resolve() == chatapp.project.resolve()
    assert "Atlas" in events_of(response)[0]["label"] or "read only" in events_of(response)[0]["label"].lower()


def test_a_skill_node_adds_exactly_its_folder(chatapp):
    post_chat(chatapp, {"message": "Hi", "node": node_id(chatapp, "skill", "alpha")})
    seen = args(chatapp)["argv"]

    assert seen.count("--add-dir") == 1
    assert Path(seen[seen.index("--add-dir") + 1]).resolve() == (chatapp.home / "skills" / "alpha").resolve()


def test_stop_ends_a_running_message_and_the_stream_says_stopped(chatapp, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    holder = {}
    worker = threading.Thread(target=lambda: holder.update(response=post_chat(chatapp)))
    worker.start()
    assert wait_until(lambda: chatapp.manager.running)
    assert wait_until(lambda: (chatapp.tmp / "pid.txt").exists())
    time.sleep(0.5)

    stopped = send(chatapp, "POST", "/api/chat/stop", body=b"{}")
    worker.join(20)

    assert stopped.status == 200 and as_json(stopped) == {"stopped": True}
    assert events_of(holder["response"])[-1] == {"t": "stopped"}
    assert chatapp.manager.running is False


def test_stop_with_nothing_running_says_so(chatapp):
    assert as_json(send(chatapp, "POST", "/api/chat/stop", body=b"{}")) == {"stopped": False}


def test_stop_cannot_be_called_with_get(chatapp):
    assert send(chatapp, "GET", "/api/chat/stop").status == 405


def test_a_second_message_while_one_runs_gets_409_and_starts_nothing(chatapp, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    worker = threading.Thread(target=lambda: post_chat(chatapp))
    worker.start()
    assert wait_until(lambda: chatapp.manager.running)
    assert wait_until(lambda: (chatapp.tmp / "pid.txt").exists())
    first_pid = (chatapp.tmp / "pid.txt").read_text()

    second = post_chat(chatapp, {"message": "Again"})
    status = as_json(send(chatapp, "GET", "/api/chat"))
    send(chatapp, "POST", "/api/chat/stop", body=b"{}")
    worker.join(20)

    assert second.status == 409 and "already answering" in as_json(second)["error"]
    assert (chatapp.tmp / "pid.txt").read_text() == first_pid
    assert status["running"] is True


def test_a_dropped_connection_kills_the_running_message(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, heartbeat_seconds=0.3)
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    try:
        body = json.dumps({"message": "Hi"}).encode("utf-8")
        head = (
            f"POST /api/chat HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nX-Console-Token: {app.token}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode("utf-8")
        connection = socket.create_connection(("127.0.0.1", app.port), timeout=10)
        connection.sendall(head + body)
        assert connection.recv(200)
        connection.close()

        assert wait_until(lambda: not app.manager.running, 20)
        pid = int((tmp_path / "pid.txt").read_text())
        assert wait_until(lambda: not alive(pid))
    finally:
        stop(app.server)


def test_closing_the_server_stops_a_running_message(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    worker = threading.Thread(target=lambda: post_chat(app))
    worker.start()
    assert wait_until(lambda: app.manager.running)

    stop(app.server)
    worker.join(20)

    pid = int((tmp_path / "pid.txt").read_text())

    assert not worker.is_alive() and app.manager.running is False
    assert wait_until(lambda: not alive(pid))


def test_without_a_tested_version_chat_is_refused_by_the_endpoint(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, tested=False)
    try:
        response = post_chat(app)
        status = as_json(send(app, "GET", "/api/chat"))

        assert response.status == 409 and "updated" in as_json(response)["error"]
        assert status["ready"] is False and not (tmp_path / "pid.txt").exists()
    finally:
        stop(app.server)


def test_the_real_manager_refuses_when_the_version_file_is_missing(tmp_path):
    index_dir = make_index_dir(tmp_path, [], [])
    server = serve_dashboard.make_server(index_dir, ports=[0], home=tmp_path / "home", gate_file=tmp_path / "no-such-file.txt")
    try:
        assert server.chat.status()["ready"] is False
    finally:
        server.server_close()


def test_the_world_endpoint_and_the_static_table_are_unchanged_by_chat(chatapp):
    assert sorted(serve_dashboard.STATIC_FILES) == ["/", "/app.css", "/app.js", "/graph.js"]
    assert send(chatapp, "GET", "/chat.py").status == 404
    assert send(chatapp, "GET", "/api/world").status == 200


def test_after_a_chat_with_every_kind_of_node_nothing_on_disk_has_changed(chatapp, monkeypatch):
    skip = {"pid.txt", "args.json"}
    folders = [chatapp.index_dir, chatapp.home, chatapp.project]
    before = [{k: v for k, v in tree_state(f).items() if k not in skip} for f in folders]

    post_chat(chatapp, {"message": "Hi"})
    post_chat(chatapp, {"message": "Hi", "node": node_id(chatapp, "project", "Atlas")})
    post_chat(chatapp, {"message": "Hi", "node": node_id(chatapp, "skill", "alpha")})
    monkeypatch.setenv("FAKE_SCENARIO", "tool")
    post_chat(chatapp, {"message": "Read it"})
    send(chatapp, "GET", "/api/chat")
    send(chatapp, "POST", "/api/chat/stop", body=b"{}")
    after = [{k: v for k, v in tree_state(f).items() if k not in skip} for f in folders]

    assert before == after
