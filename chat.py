"""Lets the console talk to Claude Code, read only.

The server starts one Claude Code process per message. This module builds that command from fixed parts,
decides which folders the process may look at (never the page), and later turns its output into a few safe
events. Nothing here writes a file, and nothing from the page ever becomes a flag, a path or a command.
"""

import collections
import ctypes
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import unicodedata
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

import world

MODEL = "sonnet"
BUDGET_USD = "1"
TOOLS = "Read,Glob,Grep"
SECRET_PATTERNS = [
    "**/.env*", "**/*.pem", "**/*.key", "**/*.pfx", "**/*.p12", "**/*.kdbx", "**/id_*", "**/.npmrc", "**/.netrc",
    "**/*credentials*", "**/*secret*", "**/*token*", "**/*password*", "**/.pgpass", "**/.htpasswd", "**/*.ppk",
    "**/*.jks", "**/rclone.conf", "**/.git/config",
]
DENIED_TOOLS = ("Read", "Grep", "Glob")
SYSTEM_TEXT = (
    "You are answering inside the AI agent operations console, a read only view of the owner's work folders. "
    "You can only read, search and list files. You cannot change anything, and you must never say or imply "
    "that you changed something. Reply in plain British English with short sentences. "
    "Use no em dashes and no exclamation marks. Say what is true, say why, and stop. "
    "If a question needs a change, say what the owner should do, and do not do it."
)
PATH_OK = re.compile(r"^[A-Za-z]:[\\/][A-Za-z0-9 _.'\-\\/]{1,200}$")
REMOVED_VARIABLES = {
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "CLAUDE_AGENT_SDK_VERSION",
}
REMOVED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_")


def denied_patterns():
    return [f"{tool}({pattern})" for tool in DENIED_TOOLS for pattern in SECRET_PATTERNS]


def build_command(executable, session_id, resume, add_dirs):
    """The full command line as a list. The prompt is not part of it: it goes in on standard input."""
    uuid.UUID(str(session_id))
    command = [
        str(executable), "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--safe-mode", "--restricted",
        "--tools", TOOLS,
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--strict-mcp-config",
        "--model", MODEL,
        "--max-budget-usd", BUDGET_USD,
        "--disallowed-tools", *denied_patterns(),
        "--append-system-prompt", SYSTEM_TEXT,
        "--resume" if resume else "--session-id", str(session_id),
    ]
    for folder in add_dirs:
        command += ["--add-dir", str(folder)]
    return command


def clean_environment(environment):
    """A copy of the environment that cannot bill an API key and does not look like a nested Claude session."""
    return {
        name: value for name, value in environment.items()
        if name not in REMOVED_VARIABLES and not name.startswith(REMOVED_PREFIXES)
    }


@dataclass
class Context:
    cwd: Path
    add_dirs: list = field(default_factory=list)
    label: str = ""
    prefix: str = ""
    refused: str = ""


def parts_of(path):
    return [part.lower() for part in Path(path).parts]


def is_within(inner, outer):
    inner_parts, outer_parts = parts_of(inner), parts_of(outer)
    return inner_parts[: len(outer_parts)] == outer_parts


def has_odd_characters(text):
    return any(unicodedata.category(character) in world.HIDDEN_CATEGORIES for character in str(text))


def allowed_roots(home, projects):
    """(folder, may be used as it is). A folder is only offered when it sits inside one of these."""
    home = Path(home)
    roots = [
        (home / "skills", False), (home / "plugins", False), (home / "scheduled-tasks", False),
        (home / "agents", True), (home.parent / ".agents" / "skills", False),
    ]
    return roots + [(Path(project), False) for project in projects]


def private_folder(resolved, home):
    """True when the folder is, holds or sits inside the private Claude data (login, settings, transcripts)."""
    for home_real in claude_homes(home):
        sensitive = [
            home_real, home_real / "projects", home_real / ".credentials.json",
            home_real / "settings.json", home_real / "settings.local.json", home_real.parent / ".claude.json",
        ]
        if any(is_within(item, resolved) for item in sensitive) or is_within(resolved, home_real / "projects"):
            return True
    return False


def claude_homes(home):
    """Every Claude folder that holds private data: the one given, the real one for this user, and the configured one.

    Claude Code reads its own folder whatever --home says, so the real one is always protected.
    """
    found = [Path(home), Path.home() / ".claude"]
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        found.append(Path(os.environ["CLAUDE_CONFIG_DIR"]))
    resolved = []
    for folder in found:
        try:
            real = folder.resolve()
        except (OSError, RuntimeError):
            continue
        if real not in resolved:
            resolved.append(real)
    return resolved


def extra_folder(path, home, projects):
    """The resolved folder to add, or (None, why not). Refuses the Claude folder, projects and anything wide."""
    if not path or has_odd_characters(path):
        return None, "That node has no safe extra folder to share."
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None, "That node has no extra folder to share, so Claude can only see the home base."
    if not resolved.is_dir():
        return None, "That node has no extra folder to share, because its path is not a folder."
    if private_folder(resolved, home):
        return None, "That node's folder holds private Claude data, so no extra folder is shared."
    for root, allow_equal in allowed_roots(home, projects):
        try:
            root_real = root.resolve()
        except (OSError, RuntimeError):
            continue
        if is_within(resolved, root_real) and (allow_equal or parts_of(resolved) != parts_of(root_real)):
            return str(resolved), ""
    return None, "That node's folder is outside the places Claude may look, so no extra folder is shared."


def resolved_or_none(path):
    try:
        return Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def project_for(path, projects):
    target = resolved_or_none(path)
    if target is None:
        return None
    for project in projects:
        real = resolved_or_none(project)
        if real is not None and is_within(target, real):
            return Path(project)
    return None


def intro(node, kind):
    """A first-message sentence built only from a validated name or path, never from descriptions or other file text."""
    name = str(node.get("name", ""))
    path = str(node.get("path", ""))
    if kind == "project":
        return f"The owner clicked a project in the console. Its working folder is {path}." if PATH_OK.match(path) else "The owner clicked a project in the console."
    if kind == "report":
        return f"The owner clicked a report in the console. It lives in {path}." if PATH_OK.match(path) else "The owner clicked a report in the console."
    if kind in ("skill", "agent", "routine"):
        if world.SAFE_NAME.fullmatch(name):
            return f"The owner clicked the {kind} named {name} in the console."
        return f"The owner clicked a {kind} in the console."
    return "The owner opened a general chat in the console."


def describe(label_name, cwd):
    where = str(cwd)
    return f"{label_name} · {where} · read only · {MODEL.capitalize()} · files Claude reads are sent to Anthropic"


def resolve_context(node, index_dir, home, projects):
    """Decide the working folder, extra folders and first-message sentence for a node (or None for a general chat)."""
    index_dir = Path(index_dir)
    if not node or not isinstance(node, dict):
        return Context(index_dir, [], describe("General chat", index_dir), intro({}, "general"))
    kind = str(node.get("type", ""))
    path = node.get("path", "")
    label_name = world.clean(node.get("label") or node.get("name") or kind, 60)
    cwd, add_dirs, refused = index_dir, [], ""
    if kind == "project":
        match = None
        target = resolved_or_none(path) if path and not has_odd_characters(path) else None
        for project in projects:
            if target is not None and resolved_or_none(project) == target:
                match = Path(project)
        if match is not None:
            cwd = match
        else:
            kind = "general"
    elif kind == "report":
        owner = project_for(path, projects) if path and not has_odd_characters(path) else None
        if owner is not None:
            cwd = owner
    elif kind in ("skill", "routine"):
        folder, refused = extra_folder(path, home, projects)
        if folder:
            add_dirs.append(folder)
    elif kind == "agent":
        parent = str(Path(path).parent) if str(path).lower().endswith(".md") else str(path)
        folder, refused = extra_folder(parent, home, projects)
        if folder:
            add_dirs.append(folder)
    else:
        kind = "general"
    if cwd != index_dir and private_folder(resolved_or_none(cwd) or cwd, home):
        cwd, kind, refused = index_dir, "general", "That node's folder holds private Claude data, so the chat uses the home base."
    return Context(cwd, add_dirs, describe(label_name, cwd), intro(node, kind), refused)


MESSAGE_CAP = 4000
LIMITS = {
    "total_seconds": 300, "idle_seconds": 90, "heartbeat_seconds": 10, "min_gap_seconds": 1.0,
    "session_cap_usd": 5.0, "killed_run_usd": 1.0, "text_cap": 200_000, "line_cap": 1_048_576,
    "stderr_keep": 4096, "chats_kept": 20, "version_check_seconds": 30, "version_cache_seconds": 60,
    "kill_seconds": 15, "exit_wait_seconds": 10,
}
KNOWN_TOOLS = {"Read", "Glob", "Grep"}
FIXED_ERRORS = {
    "signin": "Claude Code says you are not signed in. Open the Claude app, sign in, then try again.",
    "billing": "Claude reports a usage or billing limit. Try again later.",
    "budget": "This message reached its spending limit, so it stopped.",
    "timeout": "Claude took too long, so it was stopped. Ask a narrower question.",
    "silent": "Claude went quiet, so it was stopped. Send the message again.",
    "missing": "Claude Code was not found on this computer. Install it and sign in, then reload this page.",
    "version": "Claude Code was updated. Run the chat gate check first.",
    "generic": "Claude Code stopped with an error. Send the message again.",
    "empty": "Claude returned nothing. Try again.",
    "cut": f"The answer was cut at {LIMITS['text_cap']:,} characters.",
}


class ChatError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def validate_message(value):
    """One message from the page: 1 to 4,000 characters, newline and tab allowed, no other control or invisible characters."""
    problem = ChatError(400, f"Write a message of 1 to {MESSAGE_CAP:,} characters, with no control or invisible characters.")
    if not isinstance(value, str):
        raise problem
    text = value.strip()
    if not 1 <= len(text) <= MESSAGE_CAP:
        raise problem
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise problem
    for character in text:
        if character not in "\n\t" and unicodedata.category(character) in world.HIDDEN_CATEGORIES:
            raise problem
    return text


def clean_stream_text(value):
    """Answer text with control and invisible characters removed, newline and tab kept."""
    return "".join(
        character for character in str(value)
        if character in "\n\t" or unicodedata.category(character) not in world.HIDDEN_CATEGORIES
    )


def find_executable(environment=None):
    environment = os.environ if environment is None else environment
    wanted = environment.get("CONSOLE_CLAUDE") or shutil.which("claude")
    if wanted and Path(wanted).is_file() and Path(wanted).suffix.lower() == ".exe":
        return str(Path(wanted))
    raise ChatError(503, FIXED_ERRORS["missing"])


def read_version(executable):
    try:
        result = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, timeout=LIMITS["version_check_seconds"],
            env=clean_environment(os.environ), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    found = re.search(r"\d+\.\d+\.\d+", result.stdout or "")
    return found.group(0) if found else ""


def tested_versions(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000  # Windows ends every process in the job when its last handle closes
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9  # the information class SetInformationJobObject takes for that limit


class JobObject:
    """A Windows job that kills everything still inside it when closed, so no Claude process can outlive the server."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            return
        info = Extended()
        info.Basic.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if kernel.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = handle
        else:
            kernel.CloseHandle(handle)

    def assign(self, process):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            self.close()

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def kill_tree(process):
    """Ends the process and everything it started. Safe to call twice."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=LIMITS["kill_seconds"])
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        process.kill()
    except OSError:
        pass


@dataclass
class ChatState:
    session_id: str
    node_id: str
    cwd: Path
    add_dirs: list
    label: str
    refused: str = ""
    messages: int = 0
    cost: float = 0.0


def classify_failure(tail):
    lowered = tail.lower()
    if any(word in lowered for word in ("login", "log in", "sign in", "signed in", "authenticat", "oauth", "unauthorized")):
        return FIXED_ERRORS["signin"]
    if any(word in lowered for word in ("credit", "billing", "usage limit", "rate limit", "quota")):
        return FIXED_ERRORS["billing"]
    return FIXED_ERRORS["generic"]


class ChatManager:
    """Runs one read only Claude Code message at a time and sends only a few safe events to a send function."""

    def __init__(self, index_dir, home, projects, launcher=None, executable=None, version=None, tested=None, limits=None):
        self.index_dir, self.home, self.projects = Path(index_dir), Path(home), projects
        self.launcher, self.executable = launcher, executable
        self.version_provider, self.tested_provider = version, tested
        self.limits = {**LIMITS, **(limits or {})}
        self.guard = threading.Lock()
        self.stop_event = threading.Event()
        self.process = None
        self.running = False
        self.chats = collections.OrderedDict()
        self.session_cost = 0.0
        self.last_start = 0.0
        self.current_label = ""
        self.version_cache = ("", 0.0)

    def current_version(self):
        value, stamp = self.version_cache
        if value and time.monotonic() - stamp < self.limits["version_cache_seconds"]:
            return value
        provider = self.version_provider
        found = provider() if provider else read_version(self.executable or find_executable())
        self.version_cache = (found, time.monotonic())
        return found

    def status(self):
        problem, version = "", ""
        try:
            version = self.current_version()
            tested = self.tested_provider() if self.tested_provider else set()
            if not version or version not in tested:
                problem = FIXED_ERRORS["version"]
        except ChatError as error:
            problem = error.message
        return {
            "ready": not problem, "problem": problem, "version": version, "running": self.running,
            "label": self.current_label, "session_cost": round(self.session_cost, 4),
            "model": MODEL.capitalize(), "budget_usd": BUDGET_USD, "message_chars": MESSAGE_CAP,
        }

    def stop(self):
        process = self.process
        if not self.running:
            return False
        self.stop_event.set()
        if process is not None:
            kill_tree(process)
        return True

    def shutdown(self):
        self.stop()
        deadline = time.monotonic() + self.limits["exit_wait_seconds"]
        while self.running and time.monotonic() < deadline:
            time.sleep(0.05)

    def chat_for(self, node, chat_id, context):
        state = self.chats.get(chat_id) if chat_id else None
        node_id = str(node.get("id")) if isinstance(node, dict) and node.get("id") else ""
        if state is not None and state.node_id == node_id and state.cwd == context.cwd:
            self.chats.move_to_end(chat_id)
            return chat_id, state, True
        new_id = secrets.token_hex(8)
        state = ChatState(str(uuid.uuid4()), node_id, context.cwd, context.add_dirs, context.label, context.refused)
        self.chats[new_id] = state
        while len(self.chats) > self.limits["chats_kept"]:
            self.chats.popitem(last=False)
        return new_id, state, False

    def run_message(self, text, node, chat_id, send):
        """Answer one message, sending events. Raises ChatError before anything is sent if it cannot start."""
        text = validate_message(text)
        if not self.guard.acquire(blocking=False):
            raise ChatError(409, "Claude is already answering. Stop it first.")
        try:
            status = self.status()
            if not status["ready"]:
                raise ChatError(409, status["problem"])
            if self.session_cost + self.limits["killed_run_usd"] > self.limits["session_cap_usd"]:
                raise ChatError(429, "The spending total for this session has been reached. Restart the console to chat again.")
            if time.monotonic() - self.last_start < self.limits["min_gap_seconds"]:
                raise ChatError(429, "Wait a moment before sending another message.")
            if private_folder(resolved_or_none(self.index_dir) or Path(self.index_dir), self.home):
                raise ChatError(409, "The index folder holds private Claude data, so the chat will not start. Restart the console with --root pointing at another folder.")
            context = resolve_context(node, self.index_dir, self.home, self.projects())
            new_id, state, resumed = self.chat_for(node, chat_id, context)
            executable = self.executable or find_executable()
            command = build_command(executable, state.session_id, resumed and state.messages > 0, state.add_dirs)
            if self.launcher:
                command = list(self.launcher) + command[1:]
            prompt = text if state.messages > 0 else context.prefix + "\n\nQuestion:\n" + text
            self.last_start = time.monotonic()
            self.stop_event.clear()
            self.running = True
            self.current_label = state.label
        except Exception:
            self.guard.release()
            raise
        try:
            self.stream(command, prompt, state, new_id, send)
        finally:
            if self.process is not None:
                kill_tree(self.process)
            if state.messages == 0:
                self.chats.pop(new_id, None)
            self.running = False
            self.process = None
            self.guard.release()

    def stream(self, command, prompt, state, chat_id, send):
        limits = self.limits
        job = JobObject()
        try:
            process = subprocess.Popen(
                command, cwd=str(state.cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=clean_environment(os.environ),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            job.close()
            send({"t": "error", "message": FIXED_ERRORS["missing"]})
            return
        self.process = process
        job.assign(process)
        lines = queue.Queue()
        stderr_tail = []

        def feed():
            try:
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
            except OSError:
                pass

        def drain():
            kept = 0
            try:
                for chunk in iter(lambda: process.stderr.read(4096), b""):
                    if kept < limits["stderr_keep"]:
                        stderr_tail.append(chunk[: limits["stderr_keep"] - kept])
                        kept += len(chunk)
            except (OSError, ValueError):
                pass

        def read():
            try:
                while True:
                    line = process.stdout.readline(limits["line_cap"] + 1)
                    if not line:
                        break
                    if len(line) > limits["line_cap"] and not line.endswith(b"\n"):
                        while True:
                            rest = process.stdout.readline(limits["line_cap"])
                            if not rest or rest.endswith(b"\n"):
                                break
                        continue
                    lines.put(line)
            except (OSError, ValueError):
                pass
            lines.put(None)

        for target in (feed, drain, read):
            threading.Thread(target=target, daemon=True).start()
        try:
            send({"t": "start", "chat": chat_id, "label": state.label, "refused": state.refused})
        except OSError:
            self.finish(process, job, True)
            return
        started = last_output = last_beat = time.monotonic()
        spoke, tooled, text_total, result, cut, reason = False, False, 0, None, False, ""
        while True:
            now = time.monotonic()
            if self.stop_event.is_set():
                reason = "stopped"
                break
            if now - started > limits["total_seconds"]:
                reason = "timeout"
                break
            if now - last_output > limits["idle_seconds"]:
                reason = "silent"
                break
            if now - last_beat >= limits["heartbeat_seconds"]:
                last_beat = now
                try:
                    send({"t": "ping"})
                except OSError:
                    reason = "gone"
                    break
            try:
                raw = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if raw is None:
                break
            last_output = time.monotonic()
            try:
                event = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            try:
                kind = event.get("type")
                if kind == "stream_event":
                    delta = (event.get("event") or {}).get("delta") or {}
                    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                        piece = clean_stream_text(delta["text"])
                        if piece and not cut:
                            room = limits["text_cap"] - text_total
                            if len(piece) > room:
                                piece, cut = piece[:room], True
                            text_total += len(piece)
                            spoke = True
                            send({"t": "text", "text": piece})
                elif kind == "assistant":
                    for block in (event.get("message") or {}).get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in KNOWN_TOOLS:
                            data = block.get("input") if isinstance(block.get("input"), dict) else {}
                            target = data.get("file_path") or data.get("path") or data.get("pattern") or ""
                            tooled = True
                            send({"t": "tool", "name": block["name"], "target": world.clean(target, 120)})
                elif kind == "result":
                    result = event
                    break
            except OSError:
                reason = "gone"
                break
            except (AttributeError, TypeError, KeyError):
                continue
        if not reason and result is None and self.stop_event.is_set():
            reason = "stopped"
        self.finish(process, job, reason != "" or result is not None)
        message_cost = 0.0
        if result is not None:
            try:
                message_cost = float(result.get("total_cost_usd") or 0)
            except (TypeError, ValueError):
                message_cost = 0.0
        elif reason in ("stopped", "timeout", "silent", "gone") or spoke or tooled:
            message_cost = float(limits["killed_run_usd"])
        state.cost += message_cost
        self.session_cost += message_cost
        if reason == "gone":
            return
        try:
            if reason == "stopped":
                send({"t": "stopped"})
            elif reason in ("timeout", "silent"):
                send({"t": "error", "message": FIXED_ERRORS[reason]})
            elif result is None:
                tail = b"".join(stderr_tail).decode("utf-8", errors="replace")
                send({"t": "error", "message": classify_failure(tail)})
            else:
                subtype = str(result.get("subtype", ""))
                if result.get("is_error") or subtype.startswith("error"):
                    send({"t": "error", "message": FIXED_ERRORS["budget"] if "budget" in subtype else FIXED_ERRORS["generic"]})
                else:
                    if not spoke and isinstance(result.get("result"), str) and result["result"].strip():
                        send({"t": "text", "text": clean_stream_text(result["result"])[: limits["text_cap"]]})
                        spoke = True
                    if not spoke:
                        send({"t": "error", "message": FIXED_ERRORS["empty"]})
                    else:
                        state.messages += 1
                        if cut:
                            send({"t": "error", "message": FIXED_ERRORS["cut"]})
                        send({
                            "t": "done", "cost": round(message_cost, 4), "chat_cost": round(state.cost, 4),
                            "session_cost": round(self.session_cost, 4), "turns": int(result.get("num_turns") or 0),
                        })
        except OSError:
            pass

    def finish(self, process, job, kill):
        """Make sure the process and anything it started are gone, then close the job."""
        if kill:
            kill_tree(process)
        try:
            process.wait(timeout=self.limits["exit_wait_seconds"])
        except subprocess.TimeoutExpired:
            kill_tree(process)
        job.close()
