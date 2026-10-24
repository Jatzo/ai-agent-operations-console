import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serve_dashboard
from test_build_dashboard import index_state, make_os_dir, make_repo

WEB = Path(serve_dashboard.__file__).resolve().parent / "web"
INBOX_START = "# Inbox\n\nOne line per idea.\n"


def start(os_dir, ports=(0,)):
    server = serve_dashboard.make_server(os_dir, ports=list(ports))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server):
    server.shutdown()
    server.server_close()


@pytest.fixture
def app(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo, ["first", "second"])
    (repo / "pending.txt").write_text("x", encoding="utf-8")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "page.txt").write_text("x", encoding="utf-8")
    os_dir = make_os_dir(
        tmp_path,
        [("Repo", repo), ("Plain", plain)],
        [("Seo", tmp_path / "reports" / "YYYY-MM-DD.md")],
    )
    (os_dir / "INBOX.md").write_bytes(INBOX_START.encode("utf-8"))
    server = start(os_dir)
    yield SimpleNamespace(
        server=server, port=server.server_address[1], token=server.token, os_dir=os_dir, repo=repo, plain=plain,
        inbox=os_dir / "INBOX.md", index=os_dir / "CLAUDE.md", tmp=tmp_path,
    )
    stop(server)


def send(app, method="GET", path="/", headers=None, body=b"", host=True, port=None):
    port = port or app.port
    base = {"Connection": "close", "X-OS-Token": app.token}
    if host:
        base["Host"] = f"127.0.0.1:{port}"
    if method == "POST":
        base["Content-Type"] = "application/json"
        base["Content-Length"] = str(len(body))
    base.update(headers or {})
    lines = [f"{method} {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in base.items() if v is not None]
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body
    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.sendall(raw)
        chunks = []
        while True:
            data = connection.recv(65536)
            if not data:
                break
            chunks.append(data)
    head, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("iso-8859-1").split("\r\n")
    response_headers = {line.split(":", 1)[0].lower(): line.split(":", 1)[1].strip() for line in header_lines}
    return SimpleNamespace(status=int(status_line.split()[1]), headers=response_headers, body=payload)


def as_json(response):
    return json.loads(response.body.decode("utf-8"))


def add_idea(app, text="a new idea", **kwargs):
    body = kwargs.pop("body", None)
    if body is None:
        body = json.dumps({"text": text}).encode("utf-8")
    return send(app, "POST", "/api/inbox", body=body, **kwargs)


def api_get(app, path):
    return send(app, "GET", path)


def project_id(app, label):
    return next(card["id"] for card in as_json(api_get(app, "/api/overview"))["projects"] if card["label"] == label)


def test_a_valid_idea_is_added_so_the_refusal_tests_mean_something(app):
    response = add_idea(app, "write the pricing page")

    assert response.status == 201
    assert app.inbox.read_bytes().endswith(b"- write the pricing page\n")


def test_the_overview_gives_counts_for_a_repo_and_a_no_git_card_for_a_plain_folder(app):
    response = api_get(app, "/api/overview")

    data = as_json(response)
    by_label = {card["label"]: card for card in data["projects"]}
    assert response.status == 200
    assert by_label["Repo"]["commits"] == 2
    assert by_label["Repo"]["uncommitted"] == 1
    assert by_label["Plain"]["kind"] == "files"
    assert all(re.fullmatch(r"[0-9a-f]{12}", card["id"]) for card in data["projects"])
    assert data["reports"][0]["kind"] == "error"


def test_the_drill_down_for_a_repo_lists_its_commits_and_uncommitted_lines(app):
    response = api_get(app, "/api/project?id=" + project_id(app, "Repo"))

    detail = as_json(response)
    assert detail["kind"] == "git"
    assert [commit["subject"] for commit in detail["commits"]] == ["second", "first"]
    assert all(re.fullmatch(r"[0-9a-f]{7,}", commit["hash"]) for commit in detail["commits"])
    assert detail["uncommitted"] == ["?? pending.txt"]


def test_the_drill_down_for_a_plain_folder_lists_changed_files(app):
    detail = as_json(api_get(app, "/api/project?id=" + project_id(app, "Plain")))

    assert detail["kind"] == "files"
    assert detail["files"][0]["path"] == "page.txt"


def test_a_repo_with_no_commits_says_so(tmp_path):
    empty = tmp_path / "empty"
    make_repo(empty, [])
    os_dir = make_os_dir(tmp_path, [("Empty", empty)])
    server = start(os_dir)
    app = SimpleNamespace(server=server, port=server.server_address[1], token=server.token)
    try:
        detail = as_json(api_get(app, "/api/project?id=" + project_id(app, "Empty")))
    finally:
        stop(server)

    assert detail["kind"] == "git"
    assert detail["commits"] == []
    assert detail["empty"] is True


@pytest.mark.parametrize(
    "query, expected",
    [
        ("", 400),
        ("?id=", 400),
        ("?id=abc", 400),
        ("?id=1_0", 400),
        ("?id=" + "\uff11" * 12, 400),
        ("?id=../../etc/passwd", 400),
        ("?id=D:%5CAtlas", 400),
        ("?id=aaaaaaaaaaaa&id=bbbbbbbbbbbb", 400),
        ("?id=0123456789ab", 404),
    ],
)
def test_a_bad_or_unknown_project_id_is_refused_and_never_used_as_a_path(app, query, expected):
    response = api_get(app, "/api/project" + query)

    assert response.status == expected
    assert "error" in as_json(response)


def test_an_id_from_before_the_index_changed_gives_a_clear_message(app):
    old_id = project_id(app, "Plain")
    app.index.write_text(app.index.read_text(encoding="utf-8").replace("| Plain |", "| Moved |"), encoding="utf-8")

    response = api_get(app, "/api/project?id=" + old_id)

    assert response.status == 404
    assert "index changed" in as_json(response)["error"]


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/", 200),
        ("/app.css", 200),
        ("/app.js", 200),
        ("/graph.js", 200),
        ("/app.js?x=1", 200),
        ("/INBOX.md", 404),
        ("/CLAUDE.md", 404),
        ("/../INBOX.md", 404),
        ("/%2e%2e/INBOX.md", 404),
        ("/web/app.js", 404),
        ("/index.html", 404),
        ("/api", 404),
    ],
)
def test_only_the_three_page_files_are_served(app, path, expected):
    assert send(app, "GET", path).status == expected


def test_the_browsers_automatic_icon_request_gets_an_empty_answer_not_an_error(app):
    response = send(app, "GET", "/favicon.ico")

    assert response.status == 204
    assert response.body == b""


def test_the_page_carries_this_runs_token_and_is_never_cached(app):
    page = send(app, "GET", "/")

    assert f'content="{app.token}"'.encode() in page.body
    assert page.headers["cache-control"] == "no-store"


def test_every_response_has_the_security_headers_and_errors_are_json(app):
    ok = send(app, "GET", "/api/overview")
    missing = send(app, "GET", "/nothing")

    for response in (ok, missing, send(app, "GET", "/")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert ok.headers["content-type"].startswith("application/json")
    assert missing.headers["content-type"].startswith("application/json")
    assert b"<html" not in missing.body.lower()


def test_an_idea_is_added_as_one_line_with_nothing_else_changed(app):
    app.inbox.write_bytes(b"# Inbox\n\nOne line per idea.\n")
    before = app.inbox.read_bytes()

    add_idea(app, "second idea")

    assert app.inbox.read_bytes() == before + b"- second idea\n"
    assert b"\r" not in app.inbox.read_bytes()


def test_a_file_that_uses_windows_line_endings_keeps_them(app):
    app.inbox.write_bytes(b"# Inbox\r\n\r\n- one\r\n")

    add_idea(app, "two")

    assert app.inbox.read_bytes() == b"# Inbox\r\n\r\n- one\r\n- two\r\n"


def test_a_file_with_no_trailing_newline_stays_well_formed(app):
    app.inbox.write_bytes(b"# Inbox\n\n- one")

    add_idea(app, "two")

    assert app.inbox.read_bytes() == b"# Inbox\n\n- one\n- two\n"


def test_hostile_text_is_stored_literally_and_returned_as_plain_data(app):
    hostile = "<script>alert(1)</script>"

    add_idea(app, hostile)

    assert f"- {hostile}\n".encode() in app.inbox.read_bytes()
    assert as_json(api_get(app, "/api/overview"))["inbox"]["ideas"] == [hostile]


def test_a_missing_inbox_gives_404_and_is_not_created(app):
    app.inbox.unlink()

    response = add_idea(app, "anything")

    assert response.status == 404
    assert not app.inbox.exists()


def test_an_added_idea_shows_in_the_next_overview_without_waiting_for_the_cache(app):
    api_get(app, "/api/overview")

    add_idea(app, "fresh idea")

    assert as_json(api_get(app, "/api/overview"))["inbox"]["ideas"] == ["fresh idea"]


def valid_text(**changes):
    return json.dumps({"text": "an idea", **changes}).encode("utf-8")


REFUSALS = [
    ("no token", {"headers": {"X-OS-Token": None}}, 403),
    ("wrong token", {"headers": {"X-OS-Token": "x" * 43}}, 403),
    ("foreign origin", {"headers": {"Origin": "http://evil.example"}}, 403),
    ("origin null", {"headers": {"Origin": "null"}}, 403),
    ("origin with the wrong port", {"headers": {"Origin": "http://127.0.0.1:1"}}, 403),
    ("wrong media type", {"headers": {"Content-Type": "text/plain"}}, 415),
    ("missing media type", {"headers": {"Content-Type": None}}, 415),
    ("missing length", {"headers": {"Content-Length": None}}, 411),
    ("chunked", {"headers": {"Content-Length": None, "Transfer-Encoding": "chunked"}, "body": b"0\r\n\r\n"}, 411),
    ("negative length", {"headers": {"Content-Length": "-5"}}, 400),
    ("non-integer length", {"headers": {"Content-Length": "abc"}}, 400),
    ("full-width digit length", {"headers": {"Content-Length": "\uff11\uff12"}}, 400),
    ("huge length", {"headers": {"Content-Length": "999999"}}, 413),
    ("huge digit length", {"headers": {"Content-Length": "9" * 5000}}, 413),
    ("empty text", {"body": valid_text(text="   ")}, 400),
    ("too long", {"body": valid_text(text="a" * 301)}, 400),
    ("line separator", {"body": valid_text(text="one\u2028two")}, 400),
    ("next line", {"body": valid_text(text="one\u0085two")}, 400),
    ("newline", {"body": valid_text(text="one\ntwo")}, 400),
    ("control character", {"body": valid_text(text="one\x07two")}, 400),
    ("zero width character", {"body": valid_text(text="one\u200btwo")}, 400),
    ("lone surrogate", {"body": b'{"text": "one\\ud800two"}'}, 400),
    ("utf-16 body", {"body": json.dumps({"text": "an idea"}).encode("utf-16")}, 400),
    ("not an object", {"body": b'["an idea"]'}, 400),
    ("no text key", {"body": b'{"idea": "an idea"}'}, 400),
    ("text not a string", {"body": b'{"text": 5}'}, 400),
    ("not json", {"body": b"idea"}, 400),
]


@pytest.mark.parametrize("name, request_changes, expected", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_a_request_with_one_fault_is_refused_and_the_inbox_is_unchanged(app, name, request_changes, expected):
    before = app.inbox.read_bytes()
    changes = dict(request_changes)
    body = changes.pop("body", valid_text())
    if name in ("huge length", "huge digit length"):
        body = b"{}"

    response = send(app, "POST", "/api/inbox", body=body, **changes)

    assert response.status == expected
    assert app.inbox.read_bytes() == before


def test_a_media_type_with_a_charset_is_accepted(app):
    response = add_idea(app, headers={"Content-Type": "application/json; charset=utf-8"})

    assert response.status == 201


def test_an_idea_that_would_take_the_inbox_past_its_cap_is_refused_but_one_that_fits_is_added(app):
    cap = serve_dashboard.MAX_INBOX_BYTES
    app.inbox.write_bytes(b"x" * (cap - 9) + b"\n")

    fits = add_idea(app, "ab")
    nearly_full = app.inbox.read_bytes()
    too_big = add_idea(app, "ten chars!")

    assert fits.status == 201
    assert too_big.status == 413
    assert app.inbox.read_bytes() == nearly_full
    assert len(nearly_full) == cap - 3


def test_a_file_with_windows_endings_and_no_final_newline_keeps_them(app):
    app.inbox.write_bytes(b"# Inbox\r\n\r\n- one")

    add_idea(app, "two")

    assert app.inbox.read_bytes() == b"# Inbox\r\n\r\n- one\r\n- two\r\n"


def test_a_malformed_request_gets_a_plain_json_error_with_the_security_headers(app):
    with socket.create_connection(("127.0.0.1", app.port), timeout=5) as connection:
        connection.sendall(b"not a request\r\n\r\n")
        data = b""
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            data += chunk

    head, _, body = data.partition(b"\r\n\r\n")
    assert b" 400 " in head.split(b"\r\n")[0]
    assert b"x-content-type-options: nosniff" in head.lower()
    assert "error" in json.loads(body.decode("utf-8"))
    assert b"<html" not in body.lower()


def test_a_wrong_or_missing_host_is_refused_on_get_and_post(app):
    wrong = {"Host": "evil.example:" + str(app.port)}

    assert send(app, "GET", "/", headers=wrong).status == 403
    assert send(app, "POST", "/api/inbox", headers=wrong, body=valid_text()).status == 403
    assert send(app, "GET", "/", host=False).status == 403
    assert send(app, "GET", "/", headers={"Host": "127.0.0.1"}).status == 403


def test_the_host_is_compared_without_regard_to_case_and_localhost_is_allowed(app):
    assert send(app, "GET", "/", headers={"Host": f"LOCALHOST:{app.port}"}).status == 200
    assert send(app, "GET", "/", headers={"Host": f"localhost:{app.port}"}).status == 200


def test_every_api_get_needs_the_token(app):
    assert send(app, "GET", "/api/overview", headers={"X-OS-Token": None}).status == 403
    assert send(app, "GET", "/api/project?id=0123456789ab", headers={"X-OS-Token": None}).status == 403
    assert send(app, "GET", "/api/overview").status == 200


def test_a_body_that_stalls_is_cut_off_and_the_server_stays_responsive(app, monkeypatch):
    monkeypatch.setattr(serve_dashboard.Handler, "timeout", 1)
    stalled = socket.create_connection(("127.0.0.1", app.port), timeout=5)
    head = (
        f"POST /api/inbox HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nX-OS-Token: {app.token}\r\n"
        "Content-Type: application/json\r\nContent-Length: 50\r\nConnection: close\r\n\r\n{\"te"
    )
    stalled.sendall(head.encode())
    started = time.monotonic()
    try:
        data = stalled.recv(4096)
    except (ConnectionError, socket.timeout):
        data = b""
    finally:
        stalled.close()

    assert time.monotonic() - started < 4
    assert b"201" not in data
    assert send(app, "GET", "/").status == 200


def test_the_server_is_bound_to_127_0_0_1_and_a_second_server_cannot_take_the_same_port(app):
    assert app.server.server_address[0] == "127.0.0.1"

    with pytest.raises(OSError):
        serve_dashboard.make_server(app.os_dir, ports=[app.port])


def test_a_socket_that_asks_to_share_the_port_still_cannot_take_it(app):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError):
            probe.bind(("127.0.0.1", app.port))
    finally:
        probe.close()


def test_a_taken_port_falls_forward_to_the_next_one(app):
    second = serve_dashboard.make_server(app.os_dir, ports=[app.port, 0])
    try:
        assert second.server_address[1] != app.port
    finally:
        second.server_close()


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def test_methods_other_than_get_and_post_give_405(app, method):
    assert send(app, method, "/api/inbox").status == 405
    assert send(app, method, "/").status == 405


def test_get_on_the_inbox_endpoint_and_post_on_the_others_give_405(app):
    assert send(app, "GET", "/api/inbox").status == 405
    assert send(app, "POST", "/api/overview", body=b"{}").status == 405
    assert send(app, "POST", "/", body=b"{}").status == 405


def test_the_token_is_different_on_every_run_and_an_old_token_is_refused(app):
    other = start(app.os_dir)
    try:
        assert other.token != app.token
        old = SimpleNamespace(port=other.server_address[1], token=app.token)
        assert send(old, "GET", "/api/overview").status == 403
    finally:
        stop(other)


def tree_state(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_after_every_endpoint_has_been_used_only_the_inbox_has_changed(app):
    os_before = tree_state(app.os_dir)
    repo_files_before = sorted(p.name for p in app.repo.iterdir())
    index_before = index_state(app.repo)

    api_get(app, "/api/overview")
    for label in ("Repo", "Plain"):
        api_get(app, "/api/project?id=" + project_id(app, label))
    send(app, "GET", "/")
    add_idea(app, "one change")
    add_idea(app, "bad", headers={"X-OS-Token": None})

    os_after = tree_state(app.os_dir)
    changed = {name for name in os_after if os_after[name] != os_before.get(name)}
    assert changed == {"INBOX.md"}
    assert set(os_after) == set(os_before)
    assert sorted(p.name for p in app.repo.iterdir()) == repo_files_before
    assert index_state(app.repo) == index_before


def run_parallel_adds(app, count):
    results = []

    def one(number):
        results.append(add_idea(app, f"idea {number}").status)

    threads = [threading.Thread(target=one, args=(n,)) for n in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_twenty_simultaneous_adds_give_twenty_whole_lines_as_a_smoke_test(app):
    app.inbox.write_bytes(b"# Inbox\n\n- start")

    results = run_parallel_adds(app, 20)

    lines = app.inbox.read_text(encoding="utf-8").split("\n")
    assert results == [201] * 20
    assert sorted(line for line in lines if line.startswith("- idea")) == sorted(f"- idea {n}" for n in range(20))
    assert lines[:3] == ["# Inbox", "", "- start"]
    assert "" not in lines[3:-1]


def test_a_forced_race_between_the_newline_check_and_the_write_still_gives_whole_lines(app, monkeypatch):
    app.inbox.write_bytes(b"# Inbox\n\n- start")
    real_write = os.write

    def slow_write(descriptor, data):
        time.sleep(0.02)
        return real_write(descriptor, data)

    monkeypatch.setattr(serve_dashboard.os, "write", slow_write)

    results = run_parallel_adds(app, 20)

    lines = app.inbox.read_text(encoding="utf-8").split("\n")
    assert results == [201] * 20
    assert "" not in lines[3:-1]
    assert len([line for line in lines if line.startswith("- idea")]) == 20


def test_the_served_files_have_no_innerhtml_no_comments_and_no_outside_addresses():
    files = {name: (WEB / name).read_text(encoding="utf-8") for name in ("index.html", "app.css", "app.js", "graph.js")}

    assert "innerHTML" not in files["app.js"]
    assert "outerHTML" not in files["app.js"]
    assert "insertAdjacentHTML" not in files["app.js"]
    assert "eval(" not in files["app.js"]
    assert "document.write" not in files["app.js"]
    for name, text in files.items():
        assert "<!--" not in text, name
        assert "/*" not in text, name
        assert "//" not in text, name
        assert "http:" not in text and "https:" not in text, name
        assert "<script" not in text.lower() or 'src="/app.js"' in text, name
    assert "style=" not in files["index.html"]


def test_an_unexpected_fault_gives_a_plain_json_error_not_a_traceback(app, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(serve_dashboard, "build_overview", broken)

    response = send(app, "GET", "/api/overview")

    assert response.status == 500
    assert "boom" not in response.body.decode()
    assert "Traceback" not in response.body.decode()
    assert "error" in as_json(response)
