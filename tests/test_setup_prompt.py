import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_dashboard

PROMPT = Path(__file__).resolve().parent.parent / "docs" / "SETUP_PROMPT.md"


def prompt_text():
    return re.search(r"```text\n(.*?)\n```", PROMPT.read_text(encoding="utf-8"), re.S).group(1)


def layout_from_the_prompt():
    return prompt_text().split("CLAUDE.md must follow this layout exactly.")[1].split("INBOX.md must be exactly:")[0]


def test_the_layout_in_the_setup_prompt_is_one_the_page_can_read():
    layout = layout_from_the_prompt()
    layout = layout.replace("<name I gave>", "Atlas").replace("<full path>", "C:/work/Atlas")
    layout = layout.replace("<name>", "Weekly").replace("<when>", "Mondays")
    layout = layout.replace("<full path, with YYYY-MM-DD in place of the date if it has one>", "C:/work/reports/YYYY-MM-DD.md")

    projects = build_dashboard.parse_projects(layout)
    reports = build_dashboard.parse_reports(layout)

    assert projects == [("Atlas", Path("C:/work/Atlas"))]
    assert reports == [("Weekly", Path("C:/work/reports"), False, "*.md")]


def test_the_prompt_in_the_readme_is_the_same_as_the_one_in_the_docs():
    readme = (PROMPT.parent.parent / "README.md").read_text(encoding="utf-8")

    assert prompt_text() in readme


def test_the_command_in_the_setup_prompt_names_a_real_option():
    text = PROMPT.read_text(encoding="utf-8")

    assert 'python serve_dashboard.py --root "<this folder>"' in text
    assert "--root" in (Path(__file__).resolve().parent.parent / "serve_dashboard.py").read_text(encoding="utf-8")


def test_the_inbox_text_in_the_setup_prompt_is_a_file_the_page_can_read(tmp_path):
    inbox = prompt_text().split("INBOX.md must be exactly:")[1].split("When both files are written")[0].strip()
    (tmp_path / "INBOX.md").write_text(inbox + "\n- a first idea\n", encoding="utf-8")

    ideas, problem = build_dashboard.read_inbox(tmp_path / "INBOX.md")

    assert problem is None and ideas == ["a first idea"]
