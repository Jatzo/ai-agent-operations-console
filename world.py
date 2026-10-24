"""Builds the console's map: nodes for projects, reports, routines, skills, agents and ideas, and the links between them.

Everything read here is untrusted text. Names and descriptions are cleaned and capped before they leave this
module, and nothing is written to disk. The history scan keeps only counts for names that match a node.
"""

import hashlib
import json
import mmap
import os
import re
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import build_dashboard as overview

MAX_NODES = 600
MAX_EDGES = 3000
MAX_IDEAS = 40
TEXT_LINKS_PER_NODE = 6
READ_CAP_BYTES = 200_000
LABEL_CAP = 80
DESCRIPTION_CAP = 300
SAFE_NAME = re.compile(r"[a-z0-9:_-]{1,64}")
PRIORITY = ["project", "routine", "report", "hub", "idea", "agent", "skill:global", "skill:project", "skill:plugin"]


def project_id(label, path):
    return hashlib.sha256(f"{label}\0{path}".encode("utf-8")).hexdigest()[:12]


def clean(value, limit):
    """One safe line: control characters become spaces, invisible formatting characters are dropped, and it is cut to limit."""
    out = []
    for character in str(value):
        category = unicodedata.category(character)
        if category in ("Cc", "Zl", "Zp"):
            out.append(" ")
        elif category != "Cf" and category != "Cs":
            out.append(character)
    text = re.sub(r"\s+", " ", "".join(out)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def read_text(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(READ_CAP_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def parse_frontmatter(text):
    """Top-level 'key: value' pairs from a leading --- block. Folded and literal blocks are joined; nested keys are skipped."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    found = {}
    index = 1
    while index < len(lines) and lines[index].strip() != "---":
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", lines[index])
        index += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if re.fullmatch(r"[>|][+-]?", value):
            block = []
            while index < len(lines) and lines[index].strip() != "---" and (lines[index][:1] in (" ", "\t") or not lines[index].strip()):
                block.append(lines[index].strip())
                index += 1
            found[key] = " ".join(part for part in block if part)
        else:
            found[key] = value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value
    return found


def make_node(node_type, scope, name, label=None, description="", path="", **extra):
    scope = clean(scope, LABEL_CAP)
    node = {
        "id": f"{node_type}:{scope}:{clean(name, LABEL_CAP)}",
        "type": node_type,
        "scope": scope,
        "name": clean(name, LABEL_CAP),
        "label": clean(label if label is not None else name, LABEL_CAP),
        "desc": clean(description, DESCRIPTION_CAP),
        "path": clean(path, 260),
        "safe": bool(SAFE_NAME.fullmatch(str(name))),
    }
    node.update(extra)
    return node


def skill_entries(root):
    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name.lower())
    except OSError:
        return
    for entry in entries:
        if (entry / "SKILL.md").is_file():
            yield entry


def installed_plugins(home):
    """(plugin name, install path) for each active plugin install, from installed_plugins.json."""
    try:
        data = json.loads(read_text(Path(home) / "plugins" / "installed_plugins.json"))
        plugins = data.get("plugins", {})
    except (ValueError, AttributeError):
        return []
    found = []
    for key, installs in plugins.items():
        for install in installs if isinstance(installs, list) else []:
            location = install.get("installPath") if isinstance(install, dict) else None
            if location:
                found.append((str(key).split("@")[0], Path(location)))
    return found


def collect_skills(home, projects):
    skills, seen = [], set()

    def add(directory, scope, owner=None):
        real = os.path.normcase(str(directory.resolve()))
        if real in seen:
            return
        seen.add(real)
        text = read_text(directory / "SKILL.md")
        meta = parse_frontmatter(text)
        node = make_node(
            "skill", scope, directory.name, description=meta.get("description", ""),
            path=str(directory), text=text, owner=owner,
        )
        if node["id"] not in {existing["id"] for existing in skills}:
            skills.append(node)

    for entry in skill_entries(Path(home) / "skills"):
        add(entry, "global")
    for label, folder in projects:
        for entry in skill_entries(Path(folder) / ".claude" / "skills"):
            add(entry, f"project:{label}", owner=project_id(label, folder))
    for plugin, location in installed_plugins(home):
        for entry in skill_entries(location / "skills"):
            add(entry, f"plugin:{plugin}")
    return skills


def collect_agents(home):
    agents, seen = [], set()
    roots = [("global", Path(home) / "agents")] + [(f"plugin:{name}", location / "agents") for name, location in installed_plugins(home)]
    for scope, root in roots:
        try:
            files = sorted(root.glob("*.md"), key=lambda file: file.name.lower())
        except OSError:
            continue
        for file in files:
            text = read_text(file)
            node = make_node("agent", scope, file.stem, description=parse_frontmatter(text).get("description", ""), path=str(file), text=text)
            if node["id"] not in seen:
                seen.add(node["id"])
                agents.append(node)
    return agents


def collect_routines(home):
    routines = []
    try:
        folders = sorted((Path(home) / "scheduled-tasks").iterdir(), key=lambda entry: entry.name.lower())
    except OSError:
        return routines
    for folder in folders:
        text = read_text(folder / "SKILL.md")
        if text:
            meta = parse_frontmatter(text)
            routines.append(make_node("routine", "scheduled", folder.name, description=meta.get("description", ""), path=str(folder), text=text))
    return routines


def norm_path(value):
    return str(value).replace("\\", "/").lower().rstrip("/")


def inside(child, parent):
    child, parent = norm_path(child), norm_path(parent)
    return child == parent or child.startswith(parent + "/")


def mention_patterns(names):
    """Regexes that find a skill or agent name only when it is written as one: backticked, /name, or after 'skill' or 'agent'."""
    alternation = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    prefix = r"(?:[\w-]+:)?"
    return [
        ("name", re.compile(r"`/?" + prefix + "(" + alternation + r")`", re.IGNORECASE)),
        ("name", re.compile(r"(?<![\w/:.\\-])/" + prefix + "(" + alternation + r")(?![\w/\\-])", re.IGNORECASE)),
        ("keyword", re.compile(r"\b(skill|agent)s?\s*:?\s*[`'\"]?" + prefix + "(" + alternation + r")(?![\w-])", re.IGNORECASE)),
        ("name", re.compile(r"skills[\\/]+(" + alternation + r")[\\/]+SKILL\.md", re.IGNORECASE)),
    ]


def find_mentions(text, patterns):
    """{(name lower, preferred type or None): count} for every written mention in text."""
    found = {}
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            if kind == "keyword":
                preferred, name = match.group(1).lower(), match.group(2).lower()
            else:
                preferred, name = None, match.group(1).lower()
            key = (name, preferred)
            found[key] = found.get(key, 0) + 1
    return found


def build_world(home, os_dir, now=None):
    """The world as plain data: {'nodes', 'edges', 'cut', 'generated'}. Reads only."""
    home, os_dir = Path(home), Path(os_dir)
    index_text, _ = overview_index(os_dir)
    project_rows = report_rows = []
    problems = []
    if index_text is not None:
        try:
            project_rows = overview.parse_projects(index_text)
        except overview.CouldNotRead as error:
            problems.append(str(error))
        try:
            report_rows = overview.parse_reports(index_text)
        except overview.CouldNotRead as error:
            problems.append(str(error))

    nodes = []
    for label, folder in project_rows:
        claude = read_text(Path(folder) / "CLAUDE.md")
        nodes.append(make_node("project", "index", label, description=f"Work folder {folder}", path=str(folder), text=claude, key=project_id(label, folder)))
    for label, location, fixed, pattern in report_rows:
        nodes.append(make_node("report", "index", label, description="Report location", path=str(location)))
    routines = collect_routines(home)
    nodes += routines
    skills = collect_skills(home, project_rows)
    agents = collect_agents(home)
    ideas, _ = overview.read_inbox(os_dir / "INBOX.md")
    hub_inbox = make_node("hub", "inbox", "inbox", label="Inbox", description="Ideas waiting in INBOX.md", path=str(os_dir / "INBOX.md"))
    nodes.append(hub_inbox)
    hubs = {}
    for scope in sorted({node["scope"] for node in skills if not node["scope"].startswith("project:")}):
        hubs[scope] = make_node("hub", f"skills-{scope}", scope, label="Skills" if scope == "global" else scope.replace("plugin:", "") + " skills", description="Group of skills")
    hubs["agents"] = make_node("hub", "agents", "agents", label="Agents", description="Group of agents")
    nodes += list(hubs.values())
    nodes += agents
    nodes += skills
    for number, idea in enumerate((ideas or [])[:MAX_IDEAS]):
        nodes.append(make_node("idea", "inbox", str(number), label=idea, description=idea))

    texts = {node["id"]: node.pop("text", "") for node in nodes}
    by_id = {node["id"]: node for node in nodes}
    edges = {}

    def link(a, b, kind, weight=1):
        if a in by_id and b in by_id and a != b:
            edges.setdefault((a, b, kind), weight)

    projects = [node for node in nodes if node["type"] == "project"]
    reports = [node for node in nodes if node["type"] == "report"]
    for report in reports:
        for project in projects:
            if inside(report["path"], project["path"]):
                link(project["id"], report["id"], "holds")
    for routine in routines:
        text = norm_path(texts[routine["id"]])
        for project in projects:
            if norm_path(project["path"]) in text:
                link(routine["id"], project["id"], "works in")
        for report in reports:
            if norm_path(report["path"]) in text:
                link(routine["id"], report["id"], "writes")
    project_by_key = {project["key"]: project["id"] for project in projects}
    for skill in skills:
        group = hubs[skill["scope"]]["id"] if skill["scope"] in hubs else project_by_key.get(skill["owner"])
        link(group, skill["id"], "member")
    for agent in agents:
        link(hubs["agents"]["id"], agent["id"], "member")
    for node in nodes:
        if node["type"] == "idea":
            link(hub_inbox["id"], node["id"], "idea")

    mentionable = {}
    for group in (skills, agents):
        for node in group:
            mentionable.setdefault(node["name"].lower(), []).append(node)
    candidates = []
    if mentionable:
        patterns = mention_patterns(list(mentionable))
        for source in projects + routines + skills + agents:
            for (name, preferred), count in find_mentions(texts[source["id"]], patterns).items():
                targets = mentionable.get(name, [])
                wanted = [node for node in targets if preferred is None or node["type"] == preferred] or targets
                if wanted and wanted[0]["id"] != source["id"]:
                    candidates.append((-count, source["id"], wanted[0]["id"], count))
    degree = {}
    for _, a, b, count in sorted(candidates):
        if degree.get(a, 0) < TEXT_LINKS_PER_NODE and degree.get(b, 0) < TEXT_LINKS_PER_NODE and (b, a, "mentions") not in edges:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
            link(a, b, "mentions", count)

    for node in nodes:
        node.pop("owner", None)
    edge_list = [{"a": a, "b": b, "kind": kind, "w": weight} for (a, b, kind), weight in edges.items()]
    return {
        "generated": datetime.fromtimestamp(now or time.time()).strftime("%H:%M:%S"),
        "nodes": nodes, "edges": edge_list,
        "cut": {"nodes": 0, "edges": 0}, "problems": problems,
    }


def cap_world(data):
    """The world cut down to MAX_NODES and MAX_EDGES, keeping the important and the used. 'cut' says how much went."""
    rank = {name: number for number, name in enumerate(PRIORITY)}

    def importance(node):
        if node["type"] in ("skill", "agent") and node.get("use"):
            return (rank["routine"] + 0.5, -node["use"])
        tag = node["type"] if node["type"] != "skill" else "skill:" + node["scope"].split(":")[0]
        return (rank.get(tag, len(rank)), 0)

    nodes = data["nodes"]
    cut_nodes = max(len(nodes) - MAX_NODES, 0)
    if cut_nodes:
        nodes = sorted(nodes, key=importance)[:MAX_NODES]
    kept = {node["id"] for node in nodes}
    edges = [edge for edge in data["edges"] if edge["a"] in kept and edge["b"] in kept]
    cut_edges = max(len(edges) - MAX_EDGES, 0)
    return {**data, "nodes": nodes, "edges": edges[:MAX_EDGES], "cut": {"nodes": cut_nodes, "edges": cut_edges}}


def overview_index(os_dir):
    try:
        return (Path(os_dir) / "CLAUDE.md").read_text(encoding="utf-8"), None
    except OSError as error:
        return None, str(error)


def usage_keys(world):
    """Lookup tables from a name as written in a transcript to a node id, for skills and for agents."""
    skill_keys, agent_keys = {}, {}
    order = {"global": 0, "project": 1, "plugin": 2}
    for node in sorted(world["nodes"], key=lambda n: order.get(n["scope"].split(":")[0], 3)):
        if node["type"] == "skill":
            skill_keys.setdefault(node["name"].lower(), node["id"])
            if node["scope"].startswith("plugin:"):
                skill_keys.setdefault(f"{node['scope'][7:]}:{node['name']}".lower(), node["id"])
        elif node["type"] == "agent":
            agent_keys.setdefault(node["name"].lower(), node["id"])
            if node["scope"].startswith("plugin:"):
                agent_keys.setdefault(f"{node['scope'][7:]}:{node['name']}".lower(), node["id"])
    return skill_keys, agent_keys


SKILL_CALL = re.compile(rb'"name":"Skill","input":\{[^{}]{0,200}?"skill":"([^"]{1,80})"')
TYPED_COMMAND = re.compile(rb"<command-name>/?([^<]{1,80})</command-name>")
AGENT_CALL = re.compile(rb'"subagent_type":"([^"]{1,80})"')


class UsageScan:
    """Counts how often each known skill and agent appears in the Claude transcripts.

    It memory-maps each file and runs three byte patterns, so a half-written last line is harmless.
    A captured name is looked up in the node tables and dropped if it is not there, so nothing
    from a transcript but a count for a known node is ever kept. Results stay in memory.
    """

    def __init__(self, transcripts, skill_keys, agent_keys, time_cap=300, byte_cap=8 * 2**30, min_interval=30):
        self.transcripts = Path(transcripts)
        self.skill_keys, self.agent_keys = skill_keys, agent_keys
        self.time_cap, self.byte_cap, self.min_interval = time_cap, byte_cap, min_interval
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.finished_at = None
        self.counts = {}
        self.progress = {"state": "idle", "files_done": 0, "files_total": 0, "note": ""}

    def set_keys(self, skill_keys, agent_keys):
        self.skill_keys, self.agent_keys = skill_keys, agent_keys

    def start(self, force=False):
        """'started', 'running' if a scan is in progress, or 'too soon' inside the minimum interval."""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return "running"
            if not force and self.finished_at is not None and time.monotonic() - self.finished_at < self.min_interval:
                return "too soon"
            self.stop_event.clear()
            self.progress = {"state": "scanning", "files_done": 0, "files_total": 0, "note": ""}
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            return "started"

    def stop(self):
        self.stop_event.set()
        thread = self.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)

    def snapshot(self):
        with self.lock:
            return {**self.progress, "counts": {key: list(value) for key, value in self.counts.items()}}

    def note(self, **changes):
        with self.lock:
            self.progress.update(changes)

    def run(self):
        files = []
        for root, _, names in os.walk(self.transcripts):
            for name in names:
                if name.endswith(".jsonl"):
                    path = os.path.join(root, name)
                    try:
                        files.append((path, os.path.getsize(path), os.path.getmtime(path)))
                    except OSError:
                        continue
        self.note(files_total=len(files))
        started, scanned_bytes, state, message = time.monotonic(), 0, "done", ""
        counts = {}
        for number, (path, size, modified) in enumerate(files, 1):
            if self.stop_event.is_set():
                state, message = "stopped", "Stopped before it finished."
                break
            if time.monotonic() - started > self.time_cap or scanned_bytes > self.byte_cap:
                state, message = "stopped", "Stopped at the time or size limit, so counts are partial."
                break
            self.scan_file(path, size, modified, counts)
            scanned_bytes += size
            self.note(files_done=number)
        with self.lock:
            self.counts = counts
            self.progress.update(state=state, note=message)
            self.finished_at = time.monotonic()

    def scan_file(self, path, size, modified, counts):
        if size == 0:
            return
        day = datetime.fromtimestamp(modified).strftime("%Y-%m-%d")
        try:
            with open(path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
                seen = 0
                for pattern, keys in ((SKILL_CALL, self.skill_keys), (TYPED_COMMAND, self.skill_keys), (AGENT_CALL, self.agent_keys)):
                    for match in pattern.finditer(view):
                        self.count(match.group(1), keys, day, counts)
                        seen += 1
                        if seen % 1024 == 0 and self.stop_event.is_set():
                            return
        except (OSError, ValueError):
            return

    def count(self, raw, keys, day, counts):
        name = raw.decode("utf-8", errors="ignore").strip().lower()
        node_id = keys.get(name) or keys.get(name.rsplit(":", 1)[-1])
        if node_id is None:
            return
        entry = counts.setdefault(node_id, [0, ""])
        entry[0] += 1
        entry[1] = max(entry[1], day)


def with_usage(world, snapshot):
    """A copy of the world's nodes with use counts and last-seen day from a scan snapshot."""
    counts = snapshot["counts"]
    nodes = []
    for node in world["nodes"]:
        used = counts.get(node["id"])
        nodes.append({**node, "use": used[0] if used else 0, "last": used[1] if used else ""})
    return {**world, "nodes": nodes}
