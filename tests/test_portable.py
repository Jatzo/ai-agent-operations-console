import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat
import serve_dashboard
from test_build_dashboard import make_os_dir


def test_the_real_claude_folder_is_private_even_when_home_points_somewhere_else(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert chat.private_folder(Path.home() / ".claude", elsewhere) is True
    assert chat.private_folder(Path.home() / ".claude" / "projects" / "x", elsewhere) is True
    assert chat.private_folder(Path.home(), elsewhere) is True


def test_the_configured_claude_folder_is_private_too(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    (configured / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(configured))

    assert chat.private_folder(configured / "projects", tmp_path / "home") is True
    assert chat.private_folder(configured, tmp_path / "home") is True


def test_an_ordinary_folder_is_not_private(tmp_path):
    ordinary = tmp_path / "work" / "project"
    ordinary.mkdir(parents=True)

    assert chat.private_folder(ordinary, tmp_path / "home") is False


def test_an_index_entry_for_the_real_claude_folder_never_becomes_a_working_folder(tmp_path):
    home = tmp_path / "other-home"
    home.mkdir()
    os_dir = tmp_path / "os"
    os_dir.mkdir()
    real = Path.home() / ".claude"
    if not real.exists():
        return

    context = chat.resolve_context(
        {"id": "project:index:x", "type": "project", "name": "x", "label": "x", "path": str(real)},
        os_dir=os_dir, home=home, projects=[real],
    )

    assert context.cwd == os_dir


def test_the_server_reads_the_gate_file_from_the_code_folder_not_from_the_index_folder(tmp_path, monkeypatch):
    code = tmp_path / "code"
    code.mkdir()
    (code / serve_dashboard.TESTED_VERSION_FILE).write_text("9.9.9\n", encoding="utf-8")
    root = make_os_dir(tmp_path, [("Atlas", tmp_path)])
    (root / serve_dashboard.TESTED_VERSION_FILE).write_text("1.1.1\n", encoding="utf-8")
    monkeypatch.setattr(serve_dashboard, "CODE_DIR", code)

    server = serve_dashboard.make_server(root, ports=[0], home=tmp_path / "home")
    try:
        assert server.chat.tested_provider() == {"9.9.9"}
    finally:
        server.server_close()


def test_the_gate_script_writes_the_file_the_server_reads():
    gate_source = (Path(__file__).resolve().parent / "chat_gate.py").read_text(encoding="utf-8")

    assert serve_dashboard.TESTED_VERSION_FILE in gate_source
    assert "OS_DIR = Path(__file__).resolve().parent.parent" in gate_source
    assert serve_dashboard.CODE_DIR == Path(serve_dashboard.__file__).resolve().parent


def test_starting_without_an_index_says_what_to_do_and_starts_nothing(tmp_path, capsys):
    code = serve_dashboard.main(["--root", str(tmp_path)])

    assert code == 2
    message = capsys.readouterr().err
    assert "no CLAUDE.md index" in message and "demo.py" in message
