"""A stand-in for Claude Code in tests. It reads the prompt, then replays one scenario named by FAKE_SCENARIO."""

import json
import os
import sys
import time

scenario = os.environ.get("FAKE_SCENARIO", "text")
pidfile = os.environ.get("FAKE_PIDFILE")
argsfile = os.environ.get("FAKE_ARGSFILE")
if pidfile:
    with open(pidfile, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
prompt = sys.stdin.buffer.read().decode("utf-8", errors="replace")
if argsfile:
    with open(argsfile, "w", encoding="utf-8") as handle:
        json.dump({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(), "env": sorted(os.environ)}, handle)


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def delta(text):
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})


def result(**changes):
    body = {"type": "result", "subtype": "success", "is_error": False, "result": "", "total_cost_usd": 0.0123, "num_turns": 1}
    body.update(changes)
    emit(body)


emit({"type": "system", "subtype": "init", "session_id": "x", "tools": ["Read"], "secret": "SHOULD-NOT-LEAK"})
if scenario == "text":
    delta("Hello ")
    delta("there.")
    result(result="Hello there.")
elif scenario == "tool":
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "D:/Atlas/README.md"}}]}})
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "rm -rf /"}}]}})
    delta("Done reading.")
    result(result="Done reading.")
elif scenario == "final_only":
    result(result="Only the final text.")
elif scenario == "empty":
    result(result="")
elif scenario == "unknown":
    emit({"type": "rate_limit_event", "info": {"token": "SHOULD-NOT-LEAK"}})
    emit({"type": "stream_event", "event": {"type": "content_block_start"}})
    sys.stdout.write("not json at all\n")
    sys.stdout.write("[1, 2, 3]\n")
    sys.stdout.flush()
    delta("Still fine.")
    result(result="Still fine.")
elif scenario == "malformed":
    emit({"type": "stream_event", "event": "x"})
    emit({"type": "stream_event", "event": {"delta": "x"}})
    emit({"type": "assistant", "message": "x"})
    emit({"type": "assistant", "message": {"content": [5, {"type": "tool_use", "name": "Read", "input": "x"}]}})
    delta("Survived.")
    result(result="Survived.")
elif scenario == "control":
    delta("a\x00b\x1b[31mc\u202ed\u200be\nf\tg")
    result(result="x")
elif scenario == "error":
    sys.stderr.write("Error: not logged in. Please run /login. token=sk-ant-PLANTED\n")
    sys.exit(1)
elif scenario == "stderr_secret":
    sys.stderr.write("SECRET-FROM-STDERR sk-ant-PLANTED\n")
    sys.exit(2)
elif scenario == "crash_after_work":
    delta("Partial answer.")
    sys.exit(3)
elif scenario == "budget":
    delta("Part ")
    result(subtype="error_max_budget_usd", is_error=True, total_cost_usd=1.01)
elif scenario == "hang":
    delta("Starting.")
    time.sleep(600)
elif scenario == "silent":
    time.sleep(600)
elif scenario == "slow":
    for _ in range(40):
        delta("tick ")
        time.sleep(0.25)
    result(result="done")
elif scenario == "huge":
    for _ in range(300):
        delta("x" * 1000)
    result(result="x")
elif scenario == "hugeline":
    sys.stdout.write("y" * 3_000_000 + "\n")
    sys.stdout.flush()
    delta("After the long line.")
    result(result="After the long line.")
elif scenario == "child":
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    childfile = os.environ.get("FAKE_CHILDFILE")
    if childfile:
        with open(childfile, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    delta("Spawned.")
    time.sleep(600)
