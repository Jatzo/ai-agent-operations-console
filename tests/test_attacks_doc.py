import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOC = HERE.parent / "docs" / "ATTACKS.md"
MUTATIONS = HERE.parent / "tools" / "mutations.json"


def table_rows():
    rows = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and not line.startswith("|---") and not line.startswith("| What is tried"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def test_names_of_tests_all_exist_as_whole_names_in_the_test_files():
    defined = set()
    for path in HERE.glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_\w+)\(", path.read_text(encoding="utf-8"), re.M))
    named = [name for row in table_rows() for name in re.findall(r"`(test_\w+)`", row[2])]

    assert len(named) >= 20
    assert [name for name in named if name not in defined] == []


def test_the_breakages_named_in_the_table_all_exist_in_the_mutation_list():
    known = {item["name"] for item in json.loads(MUTATIONS.read_text(encoding="utf-8"))}
    named = [name for row in table_rows() for name in re.findall(r"`([^`]+)`", row[3])]

    assert len(named) >= 20
    assert [name for name in named if name not in known] == []


def test_each_breakage_in_a_row_is_noticed_by_a_file_that_holds_one_of_that_rows_tests():
    where = {}
    for path in HERE.glob("test_*.py"):
        for name in re.findall(r"^def (test_\w+)\(", path.read_text(encoding="utf-8"), re.M):
            where.setdefault(name, set()).add(path.name)
    selectors = {item["name"]: item["tests"] for item in json.loads(MUTATIONS.read_text(encoding="utf-8"))}
    mismatches = []
    for row in table_rows():
        tests = re.findall(r"`(test_\w+)`", row[2])
        for breakage in re.findall(r"`([^`]+)`", row[3]):
            files = {Path(selector.split("::")[0]).name for selector in selectors[breakage]}
            named = {selector.split("::")[1] for selector in selectors[breakage] if "::" in selector}
            if not any(where[name] & files or name in named for name in tests):
                mismatches.append(breakage)

    assert mismatches == []


def test_every_row_has_a_test_and_says_plainly_when_no_breakage_covers_it():
    for row in table_rows():
        assert len(row) == 4, row
        assert re.search(r"`test_\w+`", row[2]), row
        assert "`" in row[3] or row[3] == "none yet", row
