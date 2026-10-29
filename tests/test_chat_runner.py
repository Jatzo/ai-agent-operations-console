import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat

FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")


def alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def wait_until(check, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def lab(tmp_path, monkeypatch):
    index_dir = tmp_path / "os"
    index_dir.mkdir()
    home = tmp_path / "home" / ".claude"
    home.mkdir(parents=True)
    monkeypatch.setenv("FAKE_PIDFILE", str(tmp_path / "pid.txt"))
    monkeypatch.setenv("FAKE_ARGSFILE", str(tmp_path / "args.json"))
    monkeypatch.setenv("FAKE_CHILDFILE", str(tmp_path / "child.txt"))
    return type("Lab", (), {"tmp": tmp_path, "index_dir": index_dir, "home": home})


def manager(lab, **limits):
    base = {"min_gap_seconds": 0, "heartbeat_seconds": 60}
    base.update(limits)
    return chat.ChatManager(
        lab.index_dir, lab.home, lambda: [], launcher=[sys.executable, "-I", FAKE], executable="C:/fake/claude.exe",
        version=lambda: "1.2.3", tested=lambda: {"1.2.3"}, limits=base,
    )


def ask(mgr, text="Hello", node=None, chat_id=None):
    events = []
    mgr.run_message(text, node, chat_id, events.append)
    return events


def kinds(events):
    return [event["t"] for event in events]


def read_args(lab):
    return json.loads((lab.tmp / "args.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "x" * 4001, "a\x00b", "a\x1bb", "a\u202eb", "a\u200bb", "a\u2028b", "a\ud800b", None, 5, ["x"]],
    ids=["empty", "blank", "too-long", "nul", "escape", "bidi", "zero-width", "line-sep", "lone-surrogate", "none", "number", "list"],
)
def test_bad_messages_are_refused_before_anything_starts(lab, bad):
    mgr = manager(lab)

    with pytest.raises(chat.ChatError) as caught:
        ask(mgr, bad)

    assert caught.value.status == 400
    assert not (lab.tmp / "pid.txt").exists()


def test_accented_text_a_4000_character_message_and_many_newlines_are_accepted(lab):
    mgr = manager(lab)

    assert chat.validate_message("caf\u00e9 na\u00efve \u00fcber") == "caf\u00e9 na\u00efve \u00fcber"
    assert len(chat.validate_message("e" * 4000)) == 4000
    assert chat.validate_message("line\n" * 500).count("\n") >= 499
    assert chat.validate_message("a\tb") == "a\tb"
    assert mgr


def test_a_normal_message_streams_start_text_and_done_with_cost(lab):
    events = ask(manager(lab))

    assert kinds(events) == ["start", "text", "text", "done"]
    assert "".join(e["text"] for e in events if e["t"] == "text") == "Hello there."
    assert events[0]["chat"] and "read only" in events[0]["label"].lower()
    assert events[-1]["cost"] == pytest.approx(0.0123) and events[-1]["session_cost"] == pytest.approx(0.0123)


def test_the_prompt_goes_in_on_standard_input_never_on_the_command_line(lab):
    ask(manager(lab), "What is in this folder?")
    args = read_args(lab)

    assert "What is in this folder?" in args["prompt"]
    assert not any("What is in this folder" in part for part in args["argv"])
    assert "--safe-mode" in args["argv"] and "--restricted" in args["argv"]


def test_the_process_runs_in_the_home_base_and_the_environment_is_cleaned(lab, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live-planted")
    monkeypatch.setenv("CLAUDECODE", "1")
    ask(manager(lab))
    args = read_args(lab)

    assert Path(args["cwd"]).resolve() == lab.index_dir.resolve()
    assert "ANTHROPIC_API_KEY" not in args["env"] and "CLAUDECODE" not in args["env"]


def test_only_known_read_tools_are_reported_with_a_cleaned_short_target(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "tool")
    events = ask(manager(lab))
    tools = [e for e in events if e["t"] == "tool"]

    assert [t["name"] for t in tools] == ["Read"]
    assert tools[0]["target"] == "D:/Atlas/README.md"
    assert "rm -rf" not in json.dumps(events)


def test_unknown_events_garbage_lines_and_init_data_never_reach_the_page(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "unknown")
    events = ask(manager(lab))

    assert kinds(events) == ["start", "text", "done"]
    assert "SHOULD-NOT-LEAK" not in json.dumps(events)


def test_control_and_invisible_characters_are_stripped_from_the_answer(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "control")
    text = "".join(e["text"] for e in ask(manager(lab)) if e["t"] == "text")

    assert text == "ab[31mcde\nf\tg"


def test_when_no_partial_text_arrives_the_final_result_is_used(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "final_only")
    events = ask(manager(lab))

    assert [e["text"] for e in events if e["t"] == "text"] == ["Only the final text."]
    assert events[-1]["t"] == "done"


def test_an_empty_answer_is_an_error_not_a_done(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "empty")
    events = ask(manager(lab))

    assert events[-1] == {"t": "error", "message": chat.FIXED_ERRORS["empty"]}
    assert "done" not in kinds(events)


def test_a_sign_in_failure_gives_the_fixed_message_and_never_the_raw_stderr(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "error")
    events = ask(manager(lab))

    assert events[-1] == {"t": "error", "message": chat.FIXED_ERRORS["signin"]}
    assert "PLANTED" not in json.dumps(events)


def test_an_unknown_failure_gives_the_generic_message_and_hides_stderr(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "stderr_secret")
    events = ask(manager(lab))

    assert events[-1] == {"t": "error", "message": chat.FIXED_ERRORS["generic"]}
    assert "SECRET-FROM-STDERR" not in json.dumps(events) and "PLANTED" not in json.dumps(events)


def test_a_run_that_did_work_and_then_ended_without_a_result_still_counts_towards_the_cap(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "crash_after_work")
    mgr = manager(lab)
    events = ask(mgr)

    assert events[-1] == {"t": "error", "message": chat.FIXED_ERRORS["generic"]}
    assert mgr.session_cost == pytest.approx(chat.LIMITS["killed_run_usd"])


def test_the_chat_will_not_start_when_the_index_folder_is_the_private_claude_folder(lab):
    mgr = chat.ChatManager(
        lab.home, lab.home, lambda: [], launcher=[sys.executable, "-I", FAKE], executable="C:/fake/claude.exe",
        version=lambda: "1.2.3", tested=lambda: {"1.2.3"}, limits={"min_gap_seconds": 0, "heartbeat_seconds": 60},
    )

    with pytest.raises(chat.ChatError) as caught:
        ask(mgr)

    assert caught.value.status == 409 and "private Claude data" in caught.value.message
    assert not (lab.tmp / "pid.txt").exists()


def test_a_budget_stop_gives_the_fixed_message_and_the_cost_still_counts(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "budget")
    mgr = manager(lab)
    events = ask(mgr)

    assert events[-1] == {"t": "error", "message": chat.FIXED_ERRORS["budget"]}
    assert mgr.session_cost == pytest.approx(1.01)


def test_a_silent_process_is_killed_after_the_idle_limit_and_counts_as_a_dollar(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "silent")
    mgr = manager(lab, idle_seconds=1, total_seconds=20)
    events = ask(mgr)
    pid = int((lab.tmp / "pid.txt").read_text())

    assert events[-1] == {"t": "error", "message": chat.FIXED_ERRORS["silent"]}
    assert wait_until(lambda: not alive(pid))
    assert mgr.session_cost == pytest.approx(1.0)


def test_a_process_that_talks_but_never_finishes_is_killed_at_the_total_limit(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "slow")
    mgr = manager(lab, total_seconds=1)
    events = ask(mgr)
    pid = int((lab.tmp / "pid.txt").read_text())

    assert events[-1] == {"t": "error", "message": chat.FIXED_ERRORS["timeout"]}
    assert wait_until(lambda: not alive(pid))


def test_stop_kills_the_process_by_pid_and_the_page_gets_stopped(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    mgr = manager(lab)
    events = []
    worker = threading.Thread(target=lambda: mgr.run_message("Hi", None, None, events.append))
    worker.start()
    assert wait_until(lambda: any(e["t"] == "text" for e in list(events)))
    pid = int((lab.tmp / "pid.txt").read_text())

    assert mgr.stop() is True
    worker.join(15)

    assert not worker.is_alive()
    assert events[-1] == {"t": "stopped"}
    assert wait_until(lambda: not alive(pid))
    assert mgr.running is False and mgr.session_cost == pytest.approx(1.0)


def test_stop_with_nothing_running_does_nothing(lab):
    assert manager(lab).stop() is False


def test_a_second_message_while_one_runs_is_refused_with_409_and_starts_nothing(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    mgr = manager(lab)
    events = []
    worker = threading.Thread(target=lambda: mgr.run_message("Hi", None, None, events.append))
    worker.start()
    assert wait_until(lambda: any(e["t"] == "text" for e in list(events)))
    first_pid = (lab.tmp / "pid.txt").read_text()

    with pytest.raises(chat.ChatError) as caught:
        mgr.run_message("Again", None, None, lambda event: None)

    assert caught.value.status == 409
    assert (lab.tmp / "pid.txt").read_text() == first_pid
    mgr.stop()
    worker.join(15)


def test_a_message_sent_too_soon_after_the_last_is_refused(lab):
    mgr = manager(lab, min_gap_seconds=30)
    ask(mgr)

    with pytest.raises(chat.ChatError) as caught:
        ask(mgr)

    assert caught.value.status == 429


def test_the_five_dollar_session_total_stops_further_messages(lab):
    mgr = manager(lab)
    mgr.session_cost = 5.0

    with pytest.raises(chat.ChatError) as caught:
        ask(mgr)

    assert caught.value.status == 429
    assert not (lab.tmp / "pid.txt").exists()


def test_the_cost_adds_up_across_messages(lab):
    mgr = manager(lab)
    first = ask(mgr)
    second = ask(mgr, "More", chat_id=first[0]["chat"])

    assert second[-1]["chat_cost"] == pytest.approx(0.0246)
    assert second[-1]["session_cost"] == pytest.approx(0.0246)


def test_a_follow_up_in_the_same_chat_resumes_the_session_without_repeating_the_intro(lab):
    mgr = manager(lab)
    first = ask(mgr, "One")
    first_args = read_args(lab)
    ask(mgr, "Two", chat_id=first[0]["chat"])
    second_args = read_args(lab)

    assert "--session-id" in first_args["argv"] and "--resume" not in first_args["argv"]
    assert "--resume" in second_args["argv"] and "--session-id" not in second_args["argv"]
    assert "Question:" in first_args["prompt"] and second_args["prompt"] == "Two"
    assert first_args["argv"][first_args["argv"].index("--session-id") + 1] == second_args["argv"][second_args["argv"].index("--resume") + 1]


def test_a_chat_id_from_another_node_starts_a_new_session(lab):
    mgr = manager(lab)
    first = ask(mgr, "One", node=None)
    other = {"id": "skill::alpha", "type": "hub", "name": "alpha", "label": "alpha", "path": ""}
    second = ask(mgr, "Two", node=other, chat_id=first[0]["chat"])

    assert second[0]["chat"] != first[0]["chat"]
    assert "--session-id" in read_args(lab)["argv"]


def test_an_invented_chat_id_never_resumes_anything(lab):
    ask(manager(lab), "One", chat_id="deadbeefdeadbeef")

    assert "--resume" not in read_args(lab)["argv"]


def test_a_dropped_connection_kills_the_process(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    mgr = manager(lab)

    def send(event):
        if event["t"] == "text":
            raise BrokenPipeError()

    mgr.run_message("Hi", None, None, send)
    pid = int((lab.tmp / "pid.txt").read_text())

    assert wait_until(lambda: not alive(pid))
    assert mgr.running is False


def test_a_dropped_connection_is_noticed_on_a_heartbeat_even_when_nothing_is_being_said(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "silent")
    mgr = manager(lab, heartbeat_seconds=0.3)

    def send(event):
        if event["t"] == "ping":
            raise BrokenPipeError()

    started = time.monotonic()
    mgr.run_message("Hi", None, None, send)
    pid = int((lab.tmp / "pid.txt").read_text())

    assert time.monotonic() - started < 15
    assert wait_until(lambda: not alive(pid))
    assert mgr.running is False


def test_a_dropped_connection_still_counts_as_a_dollar_so_it_cannot_dodge_the_session_total(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "silent")
    mgr = manager(lab, heartbeat_seconds=0.3)

    def send(event):
        if event["t"] == "ping":
            raise BrokenPipeError()

    mgr.run_message("Hi", None, None, send)

    assert mgr.session_cost == pytest.approx(1.0)


def test_malformed_events_are_skipped_and_the_answer_still_arrives(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "malformed")
    mgr = manager(lab)
    events = ask(mgr)

    assert "".join(e["text"] for e in events if e["t"] == "text") == "Survived."
    assert events[-1]["t"] == "done" and mgr.running is False


def test_a_message_that_could_push_the_session_past_five_dollars_is_refused(lab):
    mgr = manager(lab)
    mgr.session_cost = 4.5

    with pytest.raises(chat.ChatError) as caught:
        ask(mgr)

    assert caught.value.status == 429


def test_only_an_exe_is_accepted_as_claude_so_no_shell_script_can_be_run(tmp_path):
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off", encoding="utf-8")
    real = tmp_path / "claude.exe"
    real.write_bytes(b"x")

    with pytest.raises(chat.ChatError):
        chat.find_executable({"CONSOLE_CLAUDE": str(shim)})
    assert chat.find_executable({"CONSOLE_CLAUDE": str(real)}) == str(real)


def test_a_chat_whose_first_message_failed_starts_a_fresh_session_next_time(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "error")
    mgr = manager(lab)
    first = ask(mgr, "One")
    first_args = read_args(lab)
    monkeypatch.setenv("FAKE_SCENARIO", "text")
    ask(mgr, "Two", chat_id=first[0]["chat"])
    second_args = read_args(lab)

    assert "--session-id" in second_args["argv"] and "--resume" not in second_args["argv"]
    assert second_args["argv"][second_args["argv"].index("--session-id") + 1] != first_args["argv"][first_args["argv"].index("--session-id") + 1]


def test_a_process_that_started_its_own_child_loses_the_child_too(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "child")
    mgr = manager(lab)
    events = []
    worker = threading.Thread(target=lambda: mgr.run_message("Hi", None, None, events.append))
    worker.start()
    assert wait_until(lambda: any(e["t"] == "text" for e in list(events)))
    child = int((lab.tmp / "child.txt").read_text())
    assert alive(child)

    mgr.stop()
    worker.join(15)

    assert wait_until(lambda: not alive(child))


def test_shutdown_stops_a_running_message(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    mgr = manager(lab)
    events = []
    worker = threading.Thread(target=lambda: mgr.run_message("Hi", None, None, events.append))
    worker.start()
    assert wait_until(lambda: any(e["t"] == "text" for e in list(events)))
    pid = int((lab.tmp / "pid.txt").read_text())

    mgr.shutdown()
    worker.join(15)

    assert not worker.is_alive() and wait_until(lambda: not alive(pid))


def test_heartbeats_are_sent_while_waiting(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "slow")
    events = ask(manager(lab, heartbeat_seconds=0.5))

    assert kinds(events).count("ping") >= 2


def test_an_answer_over_the_cap_is_cut_and_says_so(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "huge")
    events = ask(manager(lab, text_cap=50_000))
    total = sum(len(e["text"]) for e in events if e["t"] == "text")

    assert total == 50_000
    assert {"t": "error", "message": chat.FIXED_ERRORS["cut"]} in events


def test_one_enormous_line_is_discarded_and_the_next_lines_still_work(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "hugeline")
    events = ask(manager(lab, line_cap=100_000))

    assert "".join(e["text"] for e in events if e["t"] == "text") == "After the long line."
    assert events[-1]["t"] == "done"


def test_chat_is_refused_when_the_version_has_not_been_tested(lab):
    mgr = chat.ChatManager(
        lab.index_dir, lab.home, lambda: [], launcher=[sys.executable, "-I", FAKE], executable="C:/fake/claude.exe",
        version=lambda: "9.9.9", tested=lambda: {"1.2.3"}, limits={"min_gap_seconds": 0},
    )

    with pytest.raises(chat.ChatError) as caught:
        ask(mgr)

    assert caught.value.status == 409 and "updated" in caught.value.message
    assert not (lab.tmp / "pid.txt").exists()
    assert mgr.status()["ready"] is False


def test_chat_is_refused_when_nothing_has_been_tested_yet(lab):
    mgr = chat.ChatManager(
        lab.index_dir, lab.home, lambda: [], launcher=[sys.executable, "-I", FAKE], executable="C:/fake/claude.exe",
        version=lambda: "1.2.3", tested=lambda: set(), limits={"min_gap_seconds": 0},
    )

    with pytest.raises(chat.ChatError):
        ask(mgr)


def test_the_tested_versions_file_is_read_with_comments_and_blank_lines_ignored(tmp_path):
    path = tmp_path / "tested.txt"
    path.write_text("# note\n\n2.1.292\n 2.1.293 \n", encoding="utf-8")

    assert chat.tested_versions(path) == {"2.1.292", "2.1.293"}
    assert chat.tested_versions(tmp_path / "missing.txt") == set()


def test_a_missing_claude_gives_the_fixed_message(lab):
    mgr = chat.ChatManager(
        lab.index_dir, lab.home, lambda: [], launcher=[str(lab.tmp / "nope.exe")], executable="C:/fake/claude.exe",
        version=lambda: "1.2.3", tested=lambda: {"1.2.3"}, limits={"min_gap_seconds": 0},
    )

    events = ask(mgr)

    assert events == [{"t": "error", "message": chat.FIXED_ERRORS["missing"]}]
    assert mgr.running is False


def test_the_manager_never_writes_a_file(lab):
    before = {p for p in lab.tmp.rglob("*") if p.is_file() and p.name not in ("pid.txt", "args.json", "child.txt")}
    ask(manager(lab))
    after = {p for p in lab.tmp.rglob("*") if p.is_file() and p.name not in ("pid.txt", "args.json", "child.txt")}

    assert before == after
