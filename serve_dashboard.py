"""Serves the AI agent operations console on this computer only, with a drill-down per project and an add-to-inbox box.

It reads folders and git exactly as build_dashboard.py does. The one thing it writes is a single line
appended to INBOX.md, when the page asks. Start it with start.cmd, or run demo.py for a made-up world.
"""

import argparse
import hmac
import http.server
import json
import os
import re
import secrets
import socket
import socketserver
import sys
import threading
import time
import traceback
import unicodedata
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import build_dashboard as overview
import chat
import world

CODE_DIR = Path(__file__).resolve().parent
HOME = Path.home() / ".claude"
WEB_DIR = CODE_DIR / "web"
PORTS = range(8765, 8776)
REQUEST_TIMEOUT_SECONDS = 5
MAX_BODY_BYTES = 4096
MAX_CHAT_BODY_BYTES = 24 * 1024
CHAT_ID = re.compile(r"[0-9a-f]{16}", re.ASCII)
TESTED_VERSION_FILE = "tested_claude_version.txt"
MAX_IDEA_CHARACTERS = 300
MAX_INBOX_BYTES = 100 * 1024
OVERVIEW_CACHE_SECONDS = 10
WORLD_CACHE_SECONDS = 60
REFRESH_MINIMUM_SECONDS = 2
# Each API path, the methods it takes, and for each the handler's name and the largest body it reads (None for a GET).
# Every handler takes one argument: the parsed JSON body for a POST, the parsed query string for a GET.
ENDPOINTS = {
    "/api/overview": {"GET": ("get_overview", None)},
    "/api/world": {"GET": ("get_world", None)},
    "/api/project": {"GET": ("get_project", None)},
    "/api/scan": {"GET": ("get_scan", None), "POST": ("start_scan", MAX_BODY_BYTES)},
    "/api/chat": {"GET": ("get_chat", None), "POST": ("stream_chat", MAX_CHAT_BODY_BYTES)},
    "/api/chat/stop": {"POST": ("stop_chat", MAX_BODY_BYTES)},
    "/api/inbox": {"POST": ("add_idea", MAX_BODY_BYTES)},
}
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


class Refusal(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def build_overview(index_dir):
    now = time.time()
    text, problem = overview.read_index(index_dir)
    problems = [problem] if problem else []
    projects, reports = [], []
    if text is not None:
        try:
            for label, path in overview.parse_projects(text):
                card = overview.read_project(label, path, now)
                card["id"] = world.project_id(label, path)
                projects.append(card)
        except overview.CouldNotRead as error:
            problems.append(str(error))
        try:
            for label, location, fixed, pattern in overview.parse_reports(text):
                reports.append(overview.read_report(label, location, fixed, now, pattern or "*"))
        except overview.CouldNotRead as error:
            problems.append(str(error))
    ideas, inbox_error = overview.read_inbox(Path(index_dir) / "INBOX.md")
    return {
        "generated": datetime.fromtimestamp(now).strftime("%H:%M:%S"),
        "window_days": overview.WINDOW_DAYS,
        "recent_hours": overview.RECENT_HOURS,
        "idea_chars": MAX_IDEA_CHARACTERS,
        "problems": problems,
        "projects": projects,
        "reports": reports,
        "inbox": {"ideas": ideas or [], "error": inbox_error},
    }


def project_detail(index_dir, wanted_id):
    """Drill-down for one project, found by its id in a fresh read of the index. None if there is no match."""
    text, _ = overview.read_index(index_dir)
    if text is None:
        return None
    try:
        projects = overview.parse_projects(text)
    except overview.CouldNotRead:
        return None
    match = next(((label, path) for label, path in projects if world.project_id(label, path) == wanted_id), None)
    if match is None:
        return None
    label, folder = match
    now = time.time()
    detail = {"label": label, "path": str(folder)}
    try:
        detail["kind"] = overview.folder_kind(folder)
        if detail["kind"] == "git":
            detail.update(overview.git_detail(folder))
        else:
            files = overview.changed_since_window(folder, now)
            detail["total"] = len(files)
            detail["files"] = [
                {"path": name, "date": datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")}
                for stamp, name in files[:overview.LINES_SHOWN]
            ]
    except overview.CouldNotRead as error:
        detail.update(kind="error", error=str(error))
    return detail


def clean_idea(value):
    """The idea as one safe line, or a Refusal saying what to fix."""
    message = (
        f"Write the idea as one line of 1 to {MAX_IDEA_CHARACTERS} characters. Invisible characters, "
        "such as those in pasted text or joined emoji, are not allowed."
    )
    if not isinstance(value, str):
        raise Refusal(400, message)
    text = value.strip()
    if not 1 <= len(text) <= MAX_IDEA_CHARACTERS:
        raise Refusal(400, message)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise Refusal(400, message)
    if any(unicodedata.category(character) in overview.HIDDEN_CATEGORIES for character in text):
        raise Refusal(400, message)
    return text


def append_idea(index_dir, text, lock):
    """Appends '- text' as one write. The file must already exist, so its heading is never lost."""
    path = Path(index_dir) / "INBOX.md"
    with lock:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
        except FileNotFoundError:
            raise Refusal(404, "INBOX.md is missing, so nothing was added. Restore it, then try again.")
        try:
            size = os.fstat(descriptor).st_size
            tail = b""
            if size:
                with open(path, "rb") as reader:
                    reader.seek(max(size - 4096, 0))
                    tail = reader.read()
            # Match the file's own line ending, taken from its last line break, so a CRLF file is never mixed.
            last_break = tail.rfind(b"\n")
            ending = b"\r\n" if last_break > 0 and tail[last_break - 1:last_break] == b"\r" else b"\n"
            lead = ending if tail and not tail.endswith(b"\n") else b""
            payload = lead + b"- " + text.encode("utf-8") + ending
            if size + len(payload) > MAX_INBOX_BYTES:
                raise Refusal(413, f"The inbox file is full ({MAX_INBOX_BYTES // 1024} KB). Tidy INBOX.md, then try again.")
            if os.write(descriptor, payload) != len(payload):
                raise Refusal(500, "The idea could not be written in full. Check INBOX.md, then try again.")
        finally:
            os.close(descriptor)


class Handler(http.server.BaseHTTPRequestHandler):
    timeout = REQUEST_TIMEOUT_SECONDS
    server_version = "AIAgentConsole"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.send_json(code, {"error": "That request could not be read. Try Refresh."})

    def send_bytes(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, data, extra=None):
        self.send_bytes(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8", extra)

    def allowed_hosts(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def handle_request(self):
        try:
            self.route()
        except Refusal as refusal:
            extra = {"Allow": "GET, POST"} if refusal.status == 405 else None
            self.send_json(refusal.status, {"error": refusal.message}, extra)
        except (TimeoutError, ConnectionError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "Something went wrong reading that. Try Refresh."})

    def route(self):
        host = (self.headers.get("Host") or "").lower()
        if host not in self.allowed_hosts():
            raise Refusal(403, "That request did not come from this computer's own address. Open the page from the address the server printed.")
        path = urlsplit(self.path).path
        if self.command not in ("GET", "POST"):
            raise Refusal(405, "That method is not allowed here.")
        if path == "/favicon.ico" and self.command == "GET":
            self.send_bytes(204, b"", "image/x-icon")
        elif path in STATIC_FILES:
            if self.command != "GET":
                raise Refusal(405, "That method is not allowed here.")
            self.serve_static(path)
        elif path in ENDPOINTS:
            self.check_token()
            self.serve_api(path)
        else:
            raise Refusal(404, "There is nothing at that address.")

    def check_token(self):
        given = self.headers.get("X-Console-Token") or ""
        if not hmac.compare_digest(given.encode("utf-8"), self.server.token.encode("utf-8")):
            raise Refusal(403, "The server restarted, or this page is out of date. Reload the page.")

    def serve_static(self, path):
        name, content_type = STATIC_FILES[path]
        body = (WEB_DIR / name).read_bytes()
        if name == "index.html":
            body = body.replace(b"{{token}}", self.server.token.encode("ascii"))
        self.send_bytes(200, body, content_type)

    def serve_api(self, path):
        methods = ENDPOINTS[path]
        if self.command not in methods:
            raise Refusal(405, "That method is not allowed here.")
        handler_name, body_limit = methods[self.command]
        if self.command == "POST":
            argument = self.read_post_json(body_limit)
        else:
            argument = parse_qs(urlsplit(self.path).query)
        getattr(self, handler_name)(argument)

    def get_chat(self, query):
        self.send_json(200, self.server.chat.status())

    def get_overview(self, query):
        self.send_json(200, self.server.overview(fresh="fresh" in query))

    def get_world(self, query):
        self.send_json(200, self.server.world_view(fresh="fresh" in query))

    def get_scan(self, query):
        self.send_json(200, self.server.scan_state())

    def get_project(self, query):
        ids = query.get("id", [])
        if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12}", ids[0], re.ASCII):
            raise Refusal(400, "That is not a project id. Refresh the page and try again.")
        detail = project_detail(self.server.index_dir, ids[0])
        if detail is None:
            raise Refusal(404, "The index changed. Refresh the page.")
        self.send_json(200, detail)

    def stop_chat(self, data):
        self.send_json(200, {"stopped": self.server.chat.stop()})

    def read_post_json(self, limit=MAX_BODY_BYTES):
        allowed = {f"http://{host}" for host in self.allowed_hosts()}
        origin = self.headers.get("Origin")
        if origin is not None and origin not in allowed:
            raise Refusal(403, "That request came from another site, so it was refused.")
        media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise Refusal(415, "Send the request as JSON.")
        length_text = self.headers.get("Content-Length")
        if self.headers.get("Transfer-Encoding") is not None or length_text is None:
            raise Refusal(411, "The request needs a Content-Length header and no chunking.")
        if not (length_text.isascii() and length_text.isdigit()):
            raise Refusal(400, "The Content-Length header is not a number.")
        if len(length_text) > 7:
            raise Refusal(413, "That request is too large.")
        length = int(length_text)
        if length > limit:
            raise Refusal(413, "That request is too large.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise Refusal(400, "The request body was shorter than it said.")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refusal(400, "Send the request as UTF-8 JSON.")
        if not isinstance(data, dict):
            raise Refusal(400, "Send the request as a JSON object.")
        return data

    def add_idea(self, data):
        text = clean_idea(data.get("text"))
        append_idea(self.server.index_dir, text, self.server.inbox_lock)
        self.server.forget_overview()
        self.send_json(201, {"ok": True})

    def chat_node(self, node_id):
        """The node the page clicked, taken from the cached world by id. The page never supplies a path."""
        if node_id is None:
            return None
        if not isinstance(node_id, str) or len(node_id) > 300:
            raise Refusal(400, "That is not a node id. Reload the page and try again.")
        for node in self.server.ensure_world()["nodes"]:
            if node["id"] == node_id:
                return node
        raise Refusal(404, "That node is not in the world any more. Rebuild the world and try again.")

    def stream_chat(self, data):
        node = self.chat_node(data.get("node"))
        chat_id = data.get("chat")
        if chat_id is not None and not (isinstance(chat_id, str) and CHAT_ID.fullmatch(chat_id)):
            raise Refusal(400, "That is not a chat id. Start a new chat.")
        started = []

        def send(event):
            if not started:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Connection", "close")
                for name, value in SECURITY_HEADERS.items():
                    self.send_header(name, value)
                self.end_headers()
                started.append(True)
            self.wfile.write(json.dumps(event).encode("utf-8") + b"\n")
            self.wfile.flush()

        self.close_connection = True
        try:
            self.server.chat.run_message(data.get("message"), node, chat_id, send)
        except chat.ChatError as error:
            if not started:
                raise Refusal(error.status, error.message)

    def start_scan(self, data):
        self.server.ensure_world()
        outcome = self.server.scan.start()
        if outcome == "too soon":
            raise Refusal(429, f"A scan finished a moment ago. Wait {world.SCAN_MIN_INTERVAL_SECONDS} seconds, then try again.")
        note = "A scan is already running." if outcome == "running" else ""
        self.send_json(202 if outcome == "started" else 200, {"state": self.server.scan_state()["state"], "note": note})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = handle_request


class DashboardServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    def overview(self, fresh=False):
        """The overview, shared between requests so a stream of them cannot start a stream of git calls."""
        with self.overview_lock:
            age = time.monotonic() - self.overview_at if self.overview_cache is not None else float("inf")
            if age >= (REFRESH_MINIMUM_SECONDS if fresh else OVERVIEW_CACHE_SECONDS):
                self.overview_cache = build_overview(self.index_dir)
                self.overview_at = time.monotonic()
            return self.overview_cache

    def forget_overview(self):
        with self.overview_lock:
            self.overview_cache = None
        with self.world_lock:
            self.world_cache = None

    def ensure_world(self, fresh=False):
        """The cached graph, rebuilt when stale. Cached so a stream of requests cannot start a stream of file reads."""
        with self.world_lock:
            age = time.monotonic() - self.world_at if self.world_cache is not None else float("inf")
            if age >= (REFRESH_MINIMUM_SECONDS if fresh else WORLD_CACHE_SECONDS):
                self.world_cache = world.build_world(self.home, self.index_dir)
                self.world_at = time.monotonic()
                self.scan.set_keys(*world.usage_keys(self.world_cache))
            return self.world_cache

    def world_view(self, fresh=False):
        """The world with use counts merged in and the cap applied."""
        base = self.ensure_world(fresh)
        merged = world.cap_world(world.with_usage(base, self.scan.snapshot()))
        merged["scan"] = self.scan_state()
        return merged

    def scan_state(self):
        state = self.scan.snapshot()
        state.pop("counts", None)
        return state

    def server_close(self):
        manager = getattr(self, "chat", None)
        if manager is not None:
            manager.shutdown()
        scan = getattr(self, "scan", None)
        if scan is not None:
            scan.stop()
        super().server_close()


def indexed_projects(index_dir):
    """The project folders listed in the index, read fresh each time."""
    text, _ = overview.read_index(index_dir)
    if text is None:
        return []
    try:
        return [path for _, path in overview.parse_projects(text)]
    except overview.CouldNotRead:
        return []


def make_server(index_dir=CODE_DIR, ports=PORTS, home=HOME, chat_manager=None, gate_file=None):
    """Binds to 127.0.0.1 on the first free port in ports. Raises OSError if none is free."""
    last_error = OSError("No port was tried.")
    for port in ports:
        try:
            server = DashboardServer(("127.0.0.1", port), Handler)
        except OSError as error:
            last_error = error
            continue
        server.index_dir = Path(index_dir)
        server.token = secrets.token_urlsafe(32)
        server.inbox_lock = threading.Lock()
        server.overview_lock = threading.Lock()
        server.overview_cache = None
        server.overview_at = 0.0
        server.home = Path(home)
        server.world_lock = threading.Lock()
        server.world_cache = None
        server.world_at = 0.0
        server.scan = world.UsageScan(Path(home) / "projects", {}, {})
        server.chat = chat_manager or chat.ChatManager(
            index_dir, home, lambda: indexed_projects(index_dir),
            tested=lambda: chat.tested_versions(gate_file or CODE_DIR / TESTED_VERSION_FILE),
        )
        return server
    raise last_error


def serve(server):
    """Builds the first view, opens the browser and runs until the window is closed."""
    address = f"http://127.0.0.1:{server.server_address[1]}/"
    server.world_view()
    server.scan.start()
    print(f"AI agent operations console running at {address}")
    print("Close this window to stop it.")
    threading.Timer(0.5, webbrowser.open, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serves the AI agent operations console on this computer only.")
    parser.add_argument("--root", type=Path, default=CODE_DIR, help="folder holding the index CLAUDE.md and INBOX.md")
    parser.add_argument("--home", type=Path, default=HOME, help="the Claude folder to read skills, agents and history from")
    arguments = parser.parse_args(argv)
    if not (arguments.root / "CLAUDE.md").is_file():
        print(
            f"There is no CLAUDE.md index in {arguments.root}. Point --root at the folder that holds it "
            "(README.md shows the format), or run demo.py to see a made-up world.",
            file=sys.stderr,
        )
        return 2
    if chat.private_folder(arguments.root.resolve(), arguments.home):
        print(
            f"{arguments.root} holds private Claude data (login, settings or transcripts), so the console will not use it as the index folder. "
            "Make a separate folder for CLAUDE.md and INBOX.md, then point --root at it.",
            file=sys.stderr,
        )
        return 2
    try:
        server = make_server(arguments.root, PORTS, arguments.home)
    except OSError as error:
        print(f"Could not start: no free port from {PORTS[0]} to {PORTS[-1]} ({error}).", file=sys.stderr)
        return 1
    serve(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
