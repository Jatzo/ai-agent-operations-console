import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat

EXE = "C:/tools/claude.exe"
SESSION = str(uuid.uuid4())


def command(**changes):
    options = {"executable": EXE, "session_id": SESSION, "resume": False, "add_dirs": []}
    options.update(changes)
    return chat.build_command(**options)


def after(flag, built):
    return built[built.index(flag) + 1]


def test_the_executable_comes_first_and_the_command_is_a_list_never_a_shell_string():
    built = command()

    assert built[0] == EXE
    assert isinstance(built, list) and all(isinstance(part, str) for part in built)
    assert built[1] == "-p"


@pytest.mark.parametrize(
    "flag",
    [
        "--safe-mode", "--restricted", "--strict-mcp-config", "--verbose", "--include-partial-messages",
        "--output-format", "--tools", "--permission-prompts", "--permission-mode", "--model", "--max-budget-usd",
        "--disallowed-tools", "--append-system-prompt",
    ],
)
def test_every_safety_flag_is_present(flag):
    assert flag in command()


def test_the_flags_have_the_values_the_plan_names():
    built = command()

    assert after("--output-format", built) == "stream-json"
    assert after("--tools", built) == "Read,Glob,Grep"
    assert after("--permission-prompts", built) == "none"
    assert after("--permission-mode", built) == "dontAsk"
    assert after("--model", built) == "sonnet"
    assert after("--max-budget-usd", built) == "1"


def test_safe_mode_and_restricted_are_both_on_and_the_flags_that_widen_access_never_appear():
    built = command()

    for forbidden in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--bare", "--allowed-tools", "--allowedTools", "--mcp-config", "--chrome", "--agents", "--plugin-dir", "--plugin-url", "--remote-control", "--cloud", "--ide", "--worktree", "--settings"):
        assert forbidden not in built
    assert built.count("--safe-mode") == 1 and built.count("--restricted") == 1


def test_only_the_three_read_only_tools_are_offered():
    assert after("--tools", command()) == "Read,Glob,Grep"
    assert not {"Edit", "Write", "Bash", "PowerShell", "WebFetch", "Skill", "Agent", "Task", "NotebookEdit"} & set(after("--tools", command()).split(","))


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob"])
@pytest.mark.parametrize(
    "pattern",
    [
        "**/.env*", "**/*.pem", "**/*.key", "**/*.pfx", "**/*.p12", "**/*.kdbx", "**/id_*", "**/.npmrc", "**/.netrc",
        "**/*credentials*", "**/*secret*", "**/*token*",
    ],
)
def test_each_secret_pattern_is_denied_for_each_file_tool(tool, pattern):
    built = command()
    start = built.index("--disallowed-tools") + 1
    denied = []
    for part in built[start:]:
        if part.startswith("--"):
            break
        denied.append(part)

    assert f"{tool}({pattern})" in denied


def test_the_deny_list_stops_at_the_next_flag_so_it_cannot_swallow_other_arguments():
    built = command(add_dirs=["C:/work/skill"])
    start = built.index("--disallowed-tools") + 1
    end = start
    while end < len(built) and not built[end].startswith("--"):
        end += 1

    assert all("(" in part and part.endswith(")") for part in built[start:end])
    assert built[end] == "--append-system-prompt"


def test_the_first_message_names_the_session_and_follow_ups_resume_it():
    first = command(resume=False)
    later = command(resume=True)

    assert after("--session-id", first) == SESSION and "--resume" not in first
    assert after("--resume", later) == SESSION and "--session-id" not in later


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "../../etc", SESSION + "; calc", "12345"])
def test_a_session_id_that_is_not_a_uuid_is_refused(bad):
    with pytest.raises(ValueError):
        command(session_id=bad)


def test_added_folders_follow_their_flag_and_none_appear_when_there_are_none():
    assert "--add-dir" not in command()
    built = command(add_dirs=["C:/work/one"])
    assert built[-2:] == ["--add-dir", "C:/work/one"]


def test_the_fixed_instruction_is_read_only_plain_and_free_of_dashes_and_exclamations():
    text = after("--append-system_prompt".replace("_", "-"), command())

    assert "read" in text.lower() and "cannot change" in text.lower()
    assert "British English" in text
    assert "\u2014" not in text and "!" not in text


def test_the_environment_loses_api_keys_and_parent_session_markers_but_keeps_the_login_and_path():
    environment = {
        "PATH": "C:/bin", "USERPROFILE": "C:/Users/someone", "APPDATA": "C:/Users/someone/AppData/Roaming",
        "ANTHROPIC_API_KEY": "sk-secret", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "http://evil",
        "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "x", "CLAUDE_CODE_SSE_PORT": "1",
        "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_AGENT_SDK_VERSION": "1", "CLAUDE_CODE_GIT_BASH_PATH": "C:/Git/bash.exe",
    }

    cleaned = chat.clean_environment(environment)

    assert cleaned["PATH"] == "C:/bin" and cleaned["USERPROFILE"] == "C:/Users/someone"
    assert cleaned["CLAUDE_CODE_GIT_BASH_PATH"] == "C:/Git/bash.exe"
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_AGENT_SDK_VERSION"):
        assert name not in cleaned
    assert "sk-secret" not in repr(cleaned)


def test_the_environment_function_does_not_change_the_dictionary_it_was_given():
    environment = {"ANTHROPIC_API_KEY": "sk-secret", "PATH": "x"}

    chat.clean_environment(environment)

    assert "ANTHROPIC_API_KEY" in environment
