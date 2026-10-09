import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from eigenaugen.src.agent_sdk import client
from eigenaugen.src.agent_sdk.errors import (
    CLIJSONDecodeError,
    CLINotFoundError,
    MessageParseError,
    ProcessError,
)
from eigenaugen.src.agent_sdk.message_parser import parse_message
from eigenaugen.src.agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    McpStdioServerConfig,
    ResultMessage,
    SystemMessage,
    SystemPromptPreset,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)


def flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_build_command_maps_options(tmp_path):
    options = ClaudeAgentOptions(
        system_prompt=SystemPromptPreset(append="extra"),
        tools=["Read", "Grep"],
        allowed_tools=["Read", "mcp__gh"],
        model="opus",
        effort="xhigh",
        permission_mode="dontAsk",
        add_dirs=[tmp_path],
        mcp_servers={"gh": McpStdioServerConfig("py", ["-m", "x"], {"A": "1"})},
        agents={"scout": AgentDefinition("d", "p", ["Read"], "haiku")},
        extra_args={"restricted": None, "permission-prompts": "none"},
    )
    cmd = client.build_command(options)
    assert cmd[:2] == ["claude", "--print"]
    assert flag_value(cmd, "--input-format") == "stream-json"
    assert flag_value(cmd, "--output-format") == "stream-json"
    assert flag_value(cmd, "--append-system-prompt") == "extra"
    assert flag_value(cmd, "--tools") == "Read,Grep"
    assert flag_value(cmd, "--allowedTools") == "Read,mcp__gh"
    assert flag_value(cmd, "--model") == "opus"
    assert flag_value(cmd, "--effort") == "xhigh"
    assert flag_value(cmd, "--add-dir") == str(tmp_path)
    assert json.loads(flag_value(cmd, "--mcp-config")) == {
        "mcpServers": {
            "gh": {
                "type": "stdio",
                "command": "py",
                "args": ["-m", "x"],
                "env": {"A": "1"},
            }
        }
    }
    assert json.loads(flag_value(cmd, "--agents")) == {
        "scout": {
            "description": "d",
            "prompt": "p",
            "tools": ["Read"],
            "model": "haiku",
        }
    }
    assert "--restricted" in cmd
    assert flag_value(cmd, "--permission-prompts") == "none"
    assert "--system-prompt" not in cmd


def test_build_command_empty_tools_disables_all():
    cmd = client.build_command(ClaudeAgentOptions(tools=[], system_prompt="sys"))
    assert flag_value(cmd, "--tools") == ""
    assert flag_value(cmd, "--system-prompt") == "sys"


def test_build_env_overrides_and_removes(monkeypatch):
    monkeypatch.setenv("KEEP", "1")
    monkeypatch.setenv("DROP", "1")
    env = client.build_env({"DROP": None, "NEW": "2"})
    assert env["KEEP"] == "1" and env["NEW"] == "2" and "DROP" not in env


def test_parse_messages():
    assistant = parse_message(
        {
            "type": "assistant",
            "parent_tool_use_id": "t0",
            "message": {
                "model": "m",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"a": 1}},
                    {"type": "server_tool_use", "id": "x"},
                ],
            },
        }
    )
    assert assistant == AssistantMessage(
        [TextBlock("hi"), ToolUseBlock("t1", "Read", {"a": 1})], "m", "t0"
    )
    user = parse_message({"type": "user", "message": {"content": "q"}})
    assert user == UserMessage("q")
    system = parse_message({"type": "system", "subtype": "init", "k": 1})
    assert system == SystemMessage(
        "init", {"type": "system", "subtype": "init", "k": 1}
    )
    result = parse_message(
        {"type": "result", "subtype": "success", "is_error": False, "result": "r"}
    )
    assert isinstance(result, ResultMessage) and result.result == "r"
    assert (result.model_usage, result.subagent_stats) == ({}, {})
    usage = parse_message(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "modelUsage": {"m": {"costUSD": 0.5}},
            "subagent_stats": {"spawned": 2},
        }
    )
    assert usage.model_usage == {"m": {"costUSD": 0.5}}
    assert usage.subagent_stats == {"spawned": 2}
    assert parse_message({"type": "rate_limit_event"}) is None


def test_parse_message_reports_missing_fields():
    with pytest.raises(MessageParseError):
        parse_message({"type": "result"})


FAKE_CLI = """\
#!{python}
import json, os, sys
received = json.loads(sys.stdin.readline())
print(json.dumps({{"type": "system", "subtype": "init"}}))
print("")
print(json.dumps({{"type": "rate_limit_event"}}))
if os.environ.get("FAKE_GARBAGE"):
    print("not json")
print(json.dumps({{
    "type": "result", "subtype": "success", "is_error": False,
    "result": received["message"]["content"] + "|" + os.environ.get("FAKE_VAR", "unset"),
}}))
sys.stderr.write("boom")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


@pytest.fixture
def fake_cli(tmp_path) -> Path:
    path = tmp_path / "fake-claude"
    path.write_text(FAKE_CLI.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run_fake(fake_cli: Path, env: dict) -> list:
    options = ClaudeAgentOptions(cli_path=str(fake_cli), env=env)
    return list(client.query(prompt="ping", options=options))


def test_query_yields_parsed_messages(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_VAR", "inherited")
    messages = run_fake(fake_cli, {})
    assert [type(m) for m in messages] == [SystemMessage, ResultMessage]
    assert messages[-1].result == "ping|inherited"


def test_query_env_none_unsets_variable(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_VAR", "inherited")
    assert run_fake(fake_cli, {"FAKE_VAR": None})[-1].result == "ping|unset"


def test_query_raises_process_error_with_stderr(fake_cli):
    with pytest.raises(ProcessError, match="code 3: boom"):
        run_fake(fake_cli, {"FAKE_EXIT": "3"})


def test_query_rejects_non_json(fake_cli):
    with pytest.raises(CLIJSONDecodeError):
        run_fake(fake_cli, {"FAKE_GARBAGE": "1"})


def test_query_missing_cli():
    options = ClaudeAgentOptions(cli_path="/nonexistent/claude")
    with pytest.raises(CLINotFoundError):
        list(client.query(prompt="x", options=options))


def test_query_stops_cli_when_caller_stops_early(tmp_path):
    path = tmp_path / "slow-claude"
    path.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json, sys, time
            sys.stdin.readline()
            print(json.dumps({{"type": "system", "subtype": "init"}}), flush=True)
            time.sleep(60)
            """
        )
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    stream = client.query(prompt="x", options=ClaudeAgentOptions(cli_path=str(path)))
    assert isinstance(next(stream), SystemMessage)
    stream.close()  # must terminate the CLI instead of waiting 60 s
