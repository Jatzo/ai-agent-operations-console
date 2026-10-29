import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
WRITEUP = (ROOT / "docs" / "WRITEUP.md").read_text(encoding="utf-8")
MUTATIONS = len(json.loads((ROOT / "tools" / "mutations.json").read_text(encoding="utf-8")))


def test_the_badge_points_at_a_workflow_that_exists():
    workflow = re.search(r"actions/workflows/([\w.-]+\.yml)/badge\.svg", README)

    assert workflow and (ROOT / ".github" / "workflows" / workflow.group(1)).is_file()


def test_the_readme_has_the_diagram_and_links_to_the_write_up_and_the_attack_table_that_exist():
    assert "```mermaid" in README
    for link in ("docs/WRITEUP.md", "docs/ATTACKS.md"):
        assert f"]({link})" in README and (ROOT / link).is_file()


def test_the_write_up_link_comes_before_the_quick_start():
    assert README.index("](docs/WRITEUP.md)") < README.index("## Try it in one command")


def test_the_mutation_count_in_the_readme_and_the_write_up_matches_the_mutation_list():
    readme = re.search(r"(\d+) protections, each broken on purpose", README)
    takes = re.search(r"takes (\d+) changes", WRITEUP)
    caught = re.search(r"Every one of the (\d+) was caught", WRITEUP)

    assert readme and takes and caught
    assert int(readme.group(1)) == int(takes.group(1)) == int(caught.group(1)) == MUTATIONS


def test_the_workflow_installs_the_same_pytest_version_as_the_dev_requirements():
    pinned = re.search(r"^pytest==([\d.]+)", (ROOT / "requirements-dev.txt").read_text(encoding="utf-8"), re.M)
    workflow = re.search(r"pip install pytest==([\d.]+)", (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))

    assert pinned and workflow and pinned.group(1) == workflow.group(1)


def test_over_450_tests_is_still_true():
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--ignore=tests/browser_check.py", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    count = re.search(r"(\d+) tests collected", done.stdout)

    assert "Over 450 tests" in README and count and int(count.group(1)) > 450
