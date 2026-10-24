"""A stand-in for Claude Code that the demo uses, so the demo costs nothing and needs no account.

It reads the question from standard input and prints the same kind of stream the real command prints, with
a canned answer. It never touches the network and never reads a file.
"""

import json
import sys
import time

question = sys.stdin.buffer.read().decode("utf-8", errors="replace").lower()


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


if "unfinished" in question or "uncommitted" in question:
    answer = (
        "Demo reply. Two projects have uncommitted files, and one has a draft that was never committed. "
        "Start with the one that has the most files, then check the draft. "
        "This is a canned answer: no real Claude is called in the demo."
    )
else:
    answer = (
        "Demo reply. This panel normally answers with Claude Code, read only, from the folder you clicked. "
        "Here the answer is canned, so nothing was sent anywhere and nothing was spent. "
        "Run serve_dashboard.py with your own folders to use the real thing."
    )

emit({"type": "system", "subtype": "init", "tools": ["Read", "Glob", "Grep"]})
emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "demo: no file is read"}}]}})
for word in answer.split(" "):
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": word + " "}}})
    time.sleep(0.03)
emit({"type": "result", "subtype": "success", "is_error": False, "result": answer, "total_cost_usd": 0, "num_turns": 1})
