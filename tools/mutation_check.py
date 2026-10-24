"""Breaks each protection on purpose and checks that a test notices.

  python tools\\mutation_check.py              run every mutation
  python tools\\mutation_check.py "stop"       run only mutations whose name contains the word

Each entry in tools\\mutations.json names a file, one exact piece of text in it, what to put there instead (or a
list of such edits), and the tests that must then fail. The work is done on a temporary copy, never on this folder. A mutation counts as
caught only when a test fails for a real reason: a crash on import, a syntax error or a collection error does
not count. The baseline (no change) must pass first, or every result would be meaningless.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SKIP = shutil.ignore_patterns(".git", ".demo-world", "__pycache__", ".pytest_cache", "docs", "dashboard.html")
BAD_SIGNS = ("SyntaxError", "ImportError", "ModuleNotFoundError", "collection error", "errors during collection")


def run_tests(folder, tests, timeout=420):
    command = [sys.executable, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider", "--no-header", "-x"]
    try:
        done = subprocess.run(command, cwd=folder, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    return done, (done.stdout + done.stderr)


def main():
    words = sys.argv[1:]
    mutations = json.loads((HERE / "tools" / "mutations.json").read_text(encoding="utf-8"))
    chosen = [item for item in mutations if not words or any(word in item["name"] for word in words)]
    scratch = Path(tempfile.mkdtemp(prefix="mut"))
    results = []
    try:
        baseline = scratch / "baseline"
        shutil.copytree(HERE, baseline, ignore=SKIP)
        files = sorted({test for item in chosen for test in item["tests"]})
        done, text = run_tests(baseline, files)
        if done is None or done.returncode != 0:
            print("The baseline does not pass, so no mutation result can be trusted:\n" + text[-1500:])
            return 2
        print(f"Baseline passes ({len(files)} test selectors).\n")
        for number, item in enumerate(chosen):
            work = scratch / f"m{number}"
            shutil.copytree(HERE, work, ignore=SKIP)
            edits = item.get("edits") or [[item["file"], item["old"], item["new"]]]
            bad = next((name for name, old, _ in edits if (work / name).read_text(encoding="utf-8").count(old) != 1), None)
            if bad:
                results.append((item["name"], "BAD ANCHOR", bad))
                print(f"BAD ANCHOR  {item['name']} (the text to replace does not match exactly once in {bad})")
                continue
            for name, old, new in edits:
                target = work / name
                target.write_text(target.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
            done, text = run_tests(work, item["tests"])
            if done is None:
                verdict, detail = "NOT CAUGHT", "timed out"
            elif done.returncode == 1 and "FAILED" in text and not any(sign in text for sign in BAD_SIGNS):
                verdict = "CAUGHT"
                detail = next((line.split(" - ")[0].replace("FAILED ", "") for line in text.splitlines() if line.startswith("FAILED")), "")
            else:
                verdict, detail = "NOT CAUGHT", text.strip().splitlines()[-1] if text.strip() else ""
            results.append((item["name"], verdict, detail))
            print(f"{verdict:10}  {item['name']}\n            {detail}", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    caught = sum(1 for _, verdict, _ in results if verdict == "CAUGHT")
    print(f"\n{caught} of {len(results)} mutations caught.")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
