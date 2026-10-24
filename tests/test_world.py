import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world
from test_build_dashboard import make_os_dir

HOSTILE = "<script>alert(1)</script>"


def make_skill(root, name, description="A skill.", body="", extra=""):
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n", encoding="utf-8")
    return folder


def make_agent(home, name, description="An agent.", body=""):
    folder = Path(home) / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8")


def make_routine(home, name, prompt):
    folder = Path(home) / "scheduled-tasks" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Runs {name}\n---\n{prompt}\n", encoding="utf-8")


@pytest.fixture
def lab(tmp_path):
    home = tmp_path / "home"
    (home / "skills").mkdir(parents=True)
    work = tmp_path / "work"
    atlas = work / "Atlas"
    ops = work / "Ops"
    for folder in (atlas, ops):
        folder.mkdir(parents=True)
    os_dir = make_os_dir(
        tmp_path,
        [("Atlas", atlas), ("Ops", ops)],
        [("SEO", atlas / "reports" / "seo" / "YYYY-MM-DD.md"), ("Elsewhere", tmp_path / "other" / "YYYY-MM-DD.md")],
        inbox="# Inbox\n\n- first idea\n- second idea\n",
    )
    return type("Lab", (), {"home": home, "os_dir": os_dir, "atlas": atlas, "ops": ops, "tmp": tmp_path})


def by_id(data):
    return {node["id"]: node for node in data["nodes"]}


def edge_pairs(data, kind=None):
    return {(edge["a"], edge["b"]) for edge in data["edges"] if kind is None or edge["kind"] == kind}


def test_the_world_has_a_node_for_each_kind_and_every_link_joins_two_real_nodes(lab):
    make_skill(lab.home / "skills", "alpha")
    make_agent(lab.home, "scout")
    make_routine(lab.home, "weekly-check", f"Work in {lab.atlas}. Save to {lab.atlas}/reports/seo/2026-10-01.md")

    data = world.build_world(lab.home, lab.os_dir)

    kinds = {node["type"] for node in data["nodes"]}
    ids = [node["id"] for node in data["nodes"]]
    assert {"project", "report", "routine", "skill", "agent", "idea", "hub"} <= kinds
    assert len(ids) == len(set(ids))
    assert all(edge["a"] in ids and edge["b"] in ids and edge["a"] != edge["b"] for edge in data["edges"])
    assert data["cut"] == {"nodes": 0, "edges": 0}
    assert sum(1 for node in data["nodes"] if node["type"] == "idea") == 2


def test_a_project_skill_and_a_global_skill_with_the_same_name_stay_distinct(lab):
    make_skill(lab.home / "skills", "deploy")
    make_skill(lab.atlas / ".claude" / "skills", "deploy")

    nodes = by_id(world.build_world(lab.home, lab.os_dir))

    assert "skill:global:deploy" in nodes
    assert "skill:project:Atlas:deploy" in nodes


def test_a_project_skill_hangs_off_its_project_and_global_skills_off_a_group_hub(lab):
    make_skill(lab.home / "skills", "alpha")
    make_skill(lab.atlas / ".claude" / "skills", "local-one")

    data = world.build_world(lab.home, lab.os_dir)

    members = edge_pairs(data, "member")
    project_id = next(node["id"] for node in data["nodes"] if node["type"] == "project" and node["name"] == "Atlas")
    assert (project_id, "skill:project:Atlas:local-one") in members
    assert ("hub:skills-global:global", "skill:global:alpha") in members


def test_plugin_skills_and_agents_come_from_the_installed_plugins_list(lab):
    install = lab.tmp / "plug" / "ecc" / "1.0"
    make_skill(install / "skills", "plan")
    make_agent(install, "security-reviewer")
    listing = {"version": 2, "plugins": {"ecc@market": [{"installPath": str(install)}]}}
    (lab.home / "plugins").mkdir()
    (lab.home / "plugins" / "installed_plugins.json").write_text(json.dumps(listing), encoding="utf-8")
    stray = lab.tmp / "plug" / "ecc" / "0.9"
    make_skill(stray / "skills", "old-copy")

    nodes = by_id(world.build_world(lab.home, lab.os_dir))

    assert "skill:plugin:ecc:plan" in nodes
    assert "agent:plugin:ecc:security-reviewer" in nodes
    assert not any("old-copy" in node_id for node_id in nodes)


@pytest.mark.parametrize(
    "front, expected",
    [
        ('---\nname: a\ndescription: "Quoted: yes"\n---\n', "Quoted: yes"),
        ("---\nname: a\ndescription: 'Single quoted'\n---\n", "Single quoted"),
        ("---\nname: a\ndescription: >\n  Folded line one\n  and line two\nother: x\n---\n", "Folded line one and line two"),
        ("---\nname: a\ndescription: |\n  Literal one\n  literal two\n---\n", "Literal one literal two"),
        ("---\nname: a\n---\nbody only\n", ""),
        ("---\nname: a\nhooks:\n  description: nested should be ignored\n---\n", ""),
        ("no frontmatter at all", ""),
        ("", ""),
    ],
)
def test_frontmatter_is_read_leniently(front, expected):
    assert world.clean(world.parse_frontmatter(front).get("description", ""), 300) == expected


def test_binary_junk_and_a_huge_file_give_a_node_and_never_an_error(lab):
    folder = lab.home / "skills" / "junk"
    folder.mkdir()
    (folder / "SKILL.md").write_bytes(b"\x00\xff\xfe---\n" + os.urandom(5000))
    huge = lab.home / "skills" / "huge"
    huge.mkdir()
    (huge / "SKILL.md").write_text("---\nname: huge\ndescription: " + "x" * 400_000 + "\n---\n", encoding="utf-8")

    nodes = by_id(world.build_world(lab.home, lab.os_dir))

    assert "skill:global:junk" in nodes
    assert len(nodes["skill:global:huge"]["desc"]) <= 300


def test_a_hostile_description_is_cleaned_and_capped(lab):
    make_skill(lab.home / "skills", "evil", description=HOSTILE + "\x07\x1b[31m red" + "a" * 10_000 + "\u200b\u202e")

    desc = by_id(world.build_world(lab.home, lab.os_dir))["skill:global:evil"]["desc"]

    assert len(desc) <= 300
    assert "\x07" not in desc and "\x1b" not in desc and "\u200b" not in desc and "\u202e" not in desc
    assert desc.startswith(HOSTILE)


def test_a_name_that_is_not_safe_for_a_prompt_is_marked_unsafe(lab):
    make_skill(lab.home / "skills", "good-name")
    make_skill(lab.home / "skills", "Bad Name; rm")

    nodes = by_id(world.build_world(lab.home, lab.os_dir))

    assert nodes["skill:global:good-name"]["safe"] is True
    assert nodes["skill:global:Bad Name; rm"]["safe"] is False


def mention_links(data):
    return {(a, b) for a, b in edge_pairs(data, "mentions")}


def test_a_bare_word_does_not_link_but_a_backticked_name_does(lab):
    make_skill(lab.home / "skills", "next")
    make_skill(lab.home / "skills", "source", body="We go next. Then `next` runs.")
    make_skill(lab.home / "skills", "quiet", body="We go next and carry on.")

    links = mention_links(world.build_world(lab.home, lab.os_dir))

    assert ("skill:global:source", "skill:global:next") in links
    assert not any(a == "skill:global:quiet" for a, _ in links)


@pytest.mark.parametrize(
    "body, linked",
    [
        ("Run /target now.", True),
        ("Use the skill target here.", True),
        ("Use the agent target here.", True),
        ("Load skill: `ecc:target`.", True),
        ("See D:/stuff/skills/target/SKILL.md for it.", True),
        ("Read the path skills/target/notes.", False),
        ("A word like target alone.", False),
        ("and/or target-extra is different", False),
        ("https://example.com/target", False),
    ],
)
def test_only_written_forms_of_a_name_make_a_link(lab, body, linked):
    make_skill(lab.home / "skills", "target")
    make_skill(lab.home / "skills", "source", body=body)

    links = mention_links(world.build_world(lab.home, lab.os_dir))

    assert (("skill:global:source", "skill:global:target") in links) is linked


def test_a_name_with_a_longer_name_around_it_links_to_the_right_one(lab):
    make_skill(lab.home / "skills", "ponytail")
    make_skill(lab.home / "skills", "ponytail-review")
    make_skill(lab.home / "skills", "source", body="Use `ponytail-review` first.")

    links = mention_links(world.build_world(lab.home, lab.os_dir))

    assert ("skill:global:source", "skill:global:ponytail-review") in links
    assert ("skill:global:source", "skill:global:ponytail") not in links


def test_the_word_agent_prefers_an_agent_and_a_skill_prefers_a_skill(lab):
    make_skill(lab.home / "skills", "reviewer")
    make_agent(lab.home, "reviewer")
    make_skill(lab.home / "skills", "uses-agent", body="Ask the agent reviewer.")
    make_skill(lab.home / "skills", "uses-skill", body="Run the skill reviewer.")

    links = mention_links(world.build_world(lab.home, lab.os_dir))

    assert ("skill:global:uses-agent", "agent:global:reviewer") in links
    assert ("skill:global:uses-skill", "skill:global:reviewer") in links


def test_a_skill_that_mentions_itself_makes_no_link(lab):
    make_skill(lab.home / "skills", "loop", body="`loop` calls `loop`.")

    assert not mention_links(world.build_world(lab.home, lab.os_dir))


def test_no_node_ever_has_more_than_six_text_links_however_popular_it_is(lab):
    make_skill(lab.home / "skills", "hub")
    for number in range(15):
        make_skill(lab.home / "skills", f"source-{number}", body="Uses `hub`.")

    data = world.build_world(lab.home, lab.os_dir)

    degree = {}
    for a, b in mention_links(data):
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    assert max(degree.values()) <= 6
    assert degree["skill:global:hub"] == 6


def test_a_project_links_to_skills_its_claude_md_names_and_a_routine_to_what_it_works_on(lab):
    make_skill(lab.home / "skills", "commit-local")
    (lab.atlas / "CLAUDE.md").write_text("Finish with `commit-local`.", encoding="utf-8")
    make_routine(lab.home, "weekly-check", f"Work in {lab.atlas}. Use skills/commit-local/SKILL.md. Save to {lab.atlas}/reports/seo/2026-10-01.md")

    data = world.build_world(lab.home, lab.os_dir)

    project = next(node["id"] for node in data["nodes"] if node["type"] == "project" and node["name"] == "Atlas")
    assert (project, "skill:global:commit-local") in mention_links(data)
    assert ("routine:scheduled:weekly-check", project) in edge_pairs(data, "works in")
    assert ("routine:scheduled:weekly-check", "skill:global:commit-local") in mention_links(data)
    assert ("routine:scheduled:weekly-check", "report:index:SEO") in edge_pairs(data, "writes")


def test_a_report_inside_a_project_folder_hangs_off_that_project_and_one_outside_does_not(lab):
    data = world.build_world(lab.home, lab.os_dir)

    holds = edge_pairs(data, "holds")
    project = next(node["id"] for node in data["nodes"] if node["type"] == "project" and node["name"] == "Atlas")
    assert (project, "report:index:SEO") in holds
    assert not any(b == "report:index:Elsewhere" for _, b in holds)


def test_inbox_ideas_hang_off_the_inbox_hub(lab):
    data = world.build_world(lab.home, lab.os_dir)

    ideas = edge_pairs(data, "idea")
    assert ("hub:inbox:inbox", "idea:inbox:0") in ideas and ("hub:inbox:inbox", "idea:inbox:1") in ideas


def test_when_there_are_too_many_nodes_the_least_important_are_cut_and_the_world_says_so(lab, monkeypatch):
    for number in range(30):
        make_skill(lab.home / "skills", f"skill-{number}")
    make_agent(lab.home, "scout")
    monkeypatch.setattr(world, "MAX_NODES", 15)

    data = world.cap_world(world.build_world(lab.home, lab.os_dir))

    kinds = [node["type"] for node in data["nodes"]]
    assert len(data["nodes"]) == 15
    assert data["cut"]["nodes"] > 0
    assert kinds.count("project") == 2
    assert all(edge["a"] in by_id(data) and edge["b"] in by_id(data) for edge in data["edges"])


def write_transcript(folder, name, lines):
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return path


def skill_line(skill):
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": skill}}]}}, separators=(",", ":"))


def agent_line(kind):
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Agent", "input": {"subagent_type": kind, "prompt": "x"}}]}}, separators=(",", ":"))


def command_line(command):
    return f'{{"type":"user","message":"<command-message>x</command-message><command-name>/{command}</command-name>"}}'


def run_scan(scan, wait=20):
    assert scan.start(force=True) == "started"
    scan.thread.join(timeout=wait)
    return scan.snapshot()


@pytest.fixture
def scan_lab(lab):
    make_skill(lab.home / "skills", "ponytail-review")
    make_skill(lab.home / "skills", "no-ai-slop")
    make_agent(lab.home, "reviewer")
    data = world.build_world(lab.home, lab.os_dir)
    skill_keys, agent_keys = world.usage_keys(data)
    transcripts = lab.tmp / "transcripts"
    scan = world.UsageScan(transcripts, skill_keys, agent_keys, min_interval=0)
    return type("ScanLab", (), {"scan": scan, "transcripts": transcripts, "world": data, "lab": lab})


def test_a_planted_secret_in_a_transcript_never_reaches_the_world(scan_lab):
    secret = "SECRET-TOKEN-12345"
    write_transcript(scan_lab.transcripts, "p/session.jsonl", [
        skill_line("ponytail-review"),
        skill_line(secret),
        agent_line(secret),
        command_line(secret),
        json.dumps({"type": "user", "message": {"content": f"my password is hunter2 and the key is {secret}"}}),
        skill_line("ponytail-review"),
    ])

    snapshot = run_scan(scan_lab.scan)
    merged = world.with_usage(scan_lab.world, snapshot)
    everything = json.dumps([snapshot, merged])

    node_ids = {node["id"] for node in scan_lab.world["nodes"]}
    assert snapshot["counts"]["skill:global:ponytail-review"][0] == 2
    assert set(snapshot["counts"]) <= node_ids
    assert secret.lower() not in everything.lower()
    assert "hunter2" not in everything.lower()
    assert "password" not in everything.lower()


def test_only_names_that_match_a_node_are_counted_and_the_rest_are_dropped(scan_lab):
    write_transcript(scan_lab.transcripts, "p/a.jsonl", [
        skill_line("ponytail-review"), skill_line("no-ai-slop"), skill_line("not-a-skill"),
        agent_line("reviewer"), agent_line("general-purpose"),
        command_line("no-ai-slop"), command_line("made-up"),
    ])

    counts = run_scan(scan_lab.scan)["counts"]

    assert counts == {
        "skill:global:ponytail-review": [1, counts["skill:global:ponytail-review"][1]],
        "skill:global:no-ai-slop": [2, counts["skill:global:no-ai-slop"][1]],
        "agent:global:reviewer": [1, counts["agent:global:reviewer"][1]],
    }


def test_a_prefixed_plugin_name_counts_for_the_plugin_skill(lab):
    install = lab.tmp / "plug" / "ecc" / "1.0"
    make_skill(install / "skills", "plan")
    (lab.home / "plugins").mkdir()
    listing = {"plugins": {"ecc@market": [{"installPath": str(install)}]}}
    (lab.home / "plugins" / "installed_plugins.json").write_text(json.dumps(listing), encoding="utf-8")
    data = world.build_world(lab.home, lab.os_dir)
    skill_keys, agent_keys = world.usage_keys(data)
    transcripts = lab.tmp / "t"
    write_transcript(transcripts, "a.jsonl", [skill_line("ecc:plan"), skill_line("plan")])

    counts = run_scan(world.UsageScan(transcripts, skill_keys, agent_keys, min_interval=0))["counts"]

    assert counts["skill:plugin:ecc:plan"][0] == 2


def test_a_corrupt_line_an_empty_file_and_a_half_written_last_line_are_fine(scan_lab):
    write_transcript(scan_lab.transcripts, "p/a.jsonl", [skill_line("ponytail-review"), "{not json at all", '{"name":"Skill","input":{"skill":"no-ai'])
    (scan_lab.transcripts / "p" / "empty.jsonl").write_bytes(b"")

    snapshot = run_scan(scan_lab.scan)

    assert snapshot["state"] == "done"
    assert snapshot["counts"]["skill:global:ponytail-review"][0] == 1
    assert "skill:global:no-ai-slop" not in snapshot["counts"]


def test_progress_reaches_the_total_and_files_older_than_nothing_are_still_listed(scan_lab):
    write_transcript(scan_lab.transcripts, "p/a.jsonl", [skill_line("no-ai-slop")])
    write_transcript(scan_lab.transcripts, "p/b.jsonl", [skill_line("no-ai-slop")])

    snapshot = run_scan(scan_lab.scan)

    assert snapshot["state"] == "done"
    assert snapshot["files_done"] == snapshot["files_total"] == 2


def test_the_scan_records_the_last_day_a_name_was_seen(scan_lab):
    path = write_transcript(scan_lab.transcripts, "p/a.jsonl", [skill_line("no-ai-slop")])
    stamp = time.time() - 5 * 86400
    os.utime(path, (stamp, stamp))

    counts = run_scan(scan_lab.scan)["counts"]

    assert counts["skill:global:no-ai-slop"][1] == time.strftime("%Y-%m-%d", time.localtime(stamp))


def test_a_stop_request_ends_the_scan_and_says_so(scan_lab):
    for number in range(40):
        write_transcript(scan_lab.transcripts, f"p/{number}.jsonl", [skill_line("no-ai-slop")] * 200)
    scan_lab.scan.stop_event.set()
    scan_lab.scan.start(force=True)
    scan_lab.scan.stop_event.set()
    scan_lab.scan.stop()

    snapshot = scan_lab.scan.snapshot()

    assert snapshot["state"] in ("stopped", "done")
    assert not scan_lab.scan.thread.is_alive()


def test_a_scan_that_hits_its_time_cap_stops_and_says_the_counts_are_partial(scan_lab):
    write_transcript(scan_lab.transcripts, "p/a.jsonl", [skill_line("no-ai-slop")])
    scan_lab.scan.time_cap = -1

    snapshot = run_scan(scan_lab.scan)

    assert snapshot["state"] == "stopped"
    assert "partial" in snapshot["note"]


def test_a_second_start_while_one_is_running_is_refused_and_a_quick_restart_is_too_soon(scan_lab):
    write_transcript(scan_lab.transcripts, "p/a.jsonl", [skill_line("no-ai-slop")])
    scan_lab.scan.min_interval = 30

    assert scan_lab.scan.start() == "started"
    scan_lab.scan.thread.join(timeout=10)

    assert scan_lab.scan.start() == "too soon"
    scan_lab.scan.thread = type("Alive", (), {"is_alive": staticmethod(lambda: True)})()
    assert scan_lab.scan.start(force=True) == "running"


def tree(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in Path(folder).rglob("*") if p.is_file()}


def test_building_the_world_and_scanning_write_nothing_anywhere(scan_lab):
    write_transcript(scan_lab.transcripts, "p/a.jsonl", [skill_line("no-ai-slop")])
    before = tree(scan_lab.lab.tmp)

    world.build_world(scan_lab.lab.home, scan_lab.lab.os_dir)
    run_scan(scan_lab.scan)

    assert tree(scan_lab.lab.tmp) == before


def test_use_counts_are_merged_onto_a_copy_and_unused_nodes_get_zero(scan_lab):
    write_transcript(scan_lab.transcripts, "p/a.jsonl", [skill_line("no-ai-slop")] * 3)
    snapshot = run_scan(scan_lab.scan)

    merged = world.with_usage(scan_lab.world, snapshot)

    by = by_id(merged)
    assert by["skill:global:no-ai-slop"]["use"] == 3
    assert by["skill:global:ponytail-review"]["use"] == 0
    assert "use" not in scan_lab.world["nodes"][0]


def test_the_cap_keeps_a_used_skill_over_an_unused_one_of_the_same_kind(lab, monkeypatch):
    for number in range(20):
        make_skill(lab.home / "skills", f"skill-{number}")
    base = world.build_world(lab.home, lab.os_dir)
    used = world.with_usage(base, {"counts": {"skill:global:skill-19": [5, "2026-10-20"]}})
    monkeypatch.setattr(world, "MAX_NODES", len(base["nodes"]) - 5)

    data = world.cap_world(used)

    ids = {node["id"] for node in data["nodes"]}
    assert "skill:global:skill-19" in ids
    assert data["cut"]["nodes"] == 5


def test_a_rescan_keeps_the_old_counts_until_the_new_scan_is_complete(scan_lab, monkeypatch):
    import threading

    write_transcript(scan_lab.transcripts, "p/a.jsonl", [skill_line("no-ai-slop")] * 2)
    first = run_scan(scan_lab.scan)
    assert first["counts"]["skill:global:no-ai-slop"][0] == 2
    gate = threading.Event()
    real = scan_lab.scan.scan_file

    def held(*args, **kwargs):
        gate.wait(timeout=10)
        return real(*args, **kwargs)

    monkeypatch.setattr(scan_lab.scan, "scan_file", held)

    assert scan_lab.scan.start(force=True) == "started"
    during = scan_lab.scan.snapshot()
    gate.set()
    scan_lab.scan.thread.join(timeout=10)

    assert during["state"] == "scanning"
    assert during["counts"]["skill:global:no-ai-slop"][0] == 2
    assert scan_lab.scan.snapshot()["counts"]["skill:global:no-ai-slop"][0] == 2


def test_the_cap_keeps_a_used_skill_ahead_of_unused_agents_and_unused_skills(lab, monkeypatch):
    for number in range(10):
        make_skill(lab.home / "skills", f"skill-{number}")
        make_agent(lab.home, f"scout-{number}")
    base = world.build_world(lab.home, lab.os_dir)
    used = world.with_usage(base, {"counts": {"skill:global:skill-3": [4, "2026-10-20"]}})
    important = [node for node in base["nodes"] if node["type"] in ("project", "routine", "report", "hub", "idea")]
    monkeypatch.setattr(world, "MAX_NODES", len(important) + 1)

    data = world.cap_world(used)

    kinds = [node["type"] for node in data["nodes"] if node["type"] in ("skill", "agent")]
    assert kinds == ["skill"]
    assert any(node["id"] == "skill:global:skill-3" for node in data["nodes"])


def test_a_hostile_two_hundred_kilobyte_skill_file_is_handled_quickly(lab):
    make_skill(lab.home / "skills", "target")
    folder = lab.home / "skills" / "evil"
    folder.mkdir()
    hostile = ("`" * 50 + " skill " + "/" * 50 + " agent " + "a" * 40) * 1500
    (folder / "SKILL.md").write_text("---\nname: evil\ndescription: x\n---\n" + hostile[:200_000], encoding="utf-8")
    started = time.monotonic()

    data = world.build_world(lab.home, lab.os_dir)

    assert time.monotonic() - started < 5
    assert "skill:global:evil" in by_id(data)


def test_a_plugin_name_with_direction_marks_is_cleaned_in_the_scope_and_the_id(lab):
    install = lab.tmp / "plug" / "x" / "1"
    make_skill(install / "skills", "plan")
    (lab.home / "plugins").mkdir()
    listing = {"plugins": {"ec‮c@market": [{"installPath": str(install)}]}}
    (lab.home / "plugins" / "installed_plugins.json").write_text(json.dumps(listing), encoding="utf-8")

    data = world.build_world(lab.home, lab.os_dir)

    plugin_nodes = [node for node in data["nodes"] if node["scope"].startswith("plugin:")]
    assert plugin_nodes
    assert all("‮" not in node["scope"] and "‮" not in node["id"] for node in plugin_nodes)
