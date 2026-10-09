import pytest

from eigenaugen.src import prompt_loader, reviewer
from eigenaugen.src.agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
)
from eigenaugen.src.constants import MCP_SERVER_NAME, Destination
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import PullRequest
from eigenaugen.src.mcp_server.github_tools import ToolName
from eigenaugen.src.workspace import Workspace

PR = PullRequest.from_url("https://github.com/o/r/pull/1", "a" * 40, "b" * 40)


@pytest.fixture
def ws(tmp_path) -> Workspace:
    return Workspace(tmp_path)


def test_system_prompt_names_every_tool_agent_and_contract():
    text = prompt_loader.system_prompt()
    for name in [*ToolName, *prompt_loader.agents()]:
        assert f"`{name}`" in text
    assert reviewer.FAILURE_PREFIX in text
    assert f"\n{reviewer.REPORT_HEADING}\n" in text


def test_subagent_models_are_left_to_the_reviewer():
    agents = prompt_loader.agents()
    assert set(agents) == {"standards-scout", "code-tracer"}
    for agent in agents.values():
        assert agent.model is None and agent.effort is None
        assert agent.tools and set(agent.tools) <= set(reviewer.BUILTIN_TOOLS)
        assert agent.prompt and agent.description
    text = prompt_loader.system_prompt()
    assert "Pass `model` and `effort` on every `Agent` call" in text


def test_build_prompt_fills_every_field(ws):
    text = reviewer.build_prompt(PR, ws, Destination.GITHUB, "cli")
    assert "$" not in text
    for value in (PR.url, PR.base_sha, PR.head_sha, str(ws.checkout), "github", "cli"):
        assert value in text


def test_build_options_sandbox(ws):
    options = reviewer.build_options(PR, ws, "opus", "xhigh")
    assert (options.model, options.effort) == ("opus", "xhigh")
    assert options.cwd == ws.session and options.add_dirs == [ws.checkout]
    assert "Bash" not in options.tools and "WebFetch" not in options.tools
    assert "restricted" in options.extra_args
    # Sessions are kept so the report can offer `claude --resume`.
    assert "no-session-persistence" not in options.extra_args
    assert options.env["ANTHROPIC_API_KEY"] is None
    server = options.mcp_servers[MCP_SERVER_NAME]
    assert "-P" in server.args and PR.head_sha in server.args


def test_describe_tool_use_strips_workspace_root():
    block = ToolUseBlock("1", "Read", {"file_path": "/tmp/ws/src/a.py", "limit": 5})
    assert reviewer.describe_tool_use(block, "/tmp/ws/") == "Read src/a.py"
    mcp = ToolUseBlock("2", f"mcp__{MCP_SERVER_NAME}__get_diff", {"path": "a.py"})
    assert reviewer.describe_tool_use(mcp, "/x/") == "get_diff a.py"
    agent = ToolUseBlock(
        "3",
        "Agent",
        {
            "subagent_type": "code-tracer",
            "model": "sonnet",
            "effort": "high",
            "description": "Trace callers",
            "prompt": "long",
        },
    )
    assert (
        reviewer.describe_tool_use(agent, "/x/")
        == "Agent code-tracer sonnet high Trace callers"
    )


MAIN = "claude-opus-5-5"
CHEAP = "claude-haiku-5-5"


def init(status: str) -> SystemMessage:
    return SystemMessage(
        "init",
        {"model": MAIN, "mcp_servers": [{"name": MCP_SERVER_NAME, "status": status}]},
    )


def result(
    text: str, is_error: bool = False, turns: int = 3, cost: float = 1.5
) -> ResultMessage:
    return ResultMessage(
        "success",
        is_error,
        turns,
        10,
        "sess-1",
        result=text,
        total_cost_usd=cost,
        model_usage={MAIN: {"costUSD": cost - 0.25}, CHEAP: {"costUSD": 0.25}},
        subagent_stats={"spawned": 1, "by_type": {"standards-scout": 1}},
    )


def run_review(monkeypatch, ws, messages):
    monkeypatch.setattr(reviewer, "query", lambda **_kw: iter(messages))
    progress = []
    outcome = reviewer.review(
        PR,
        ws,
        model="opus",
        effort="xhigh",
        destination=Destination.STDOUT,
        caller_entrypoint="unset",
        on_progress=progress.append,
    )
    return outcome, progress


def test_review_returns_sanitized_report_and_progress(monkeypatch, ws):
    tool = ToolUseBlock("1", "Grep", {"pattern": "foo"})
    messages = [
        init("connected"),
        AssistantMessage([tool], "m"),
        AssistantMessage([tool], "m", parent_tool_use_id="p"),
        result("## Verdict\x1b[2J ok"),
    ]
    outcome, progress = run_review(monkeypatch, ws, messages)
    assert outcome.report == "## Verdict[2J ok"
    assert progress == ["Grep foo", "  Grep foo"]
    assert outcome.stats == reviewer.RunStats(
        session_id="sess-1",
        model=MAIN,
        cost_usd=1.5,
        turns=3,
        model_costs={MAIN: 1.25, CHEAP: 0.25},
        subagents={"standards-scout": 1},
    )


def test_review_across_background_subagent_turns(monkeypatch, ws):
    """Running totals come from the last result; turns add up; the report is
    the last result in report format."""
    messages = [
        init("connected"),
        result("## Verdict\nfull report", turns=40, cost=1.0),
        init("connected"),
        result("The subagent finished; the report stands.", turns=2, cost=1.2),
    ]
    outcome, _ = run_review(monkeypatch, ws, messages)
    assert outcome.report == "## Verdict\nfull report"
    assert (outcome.stats.turns, outcome.stats.cost_usd) == (42, 1.2)


def test_review_falls_back_to_last_text(monkeypatch, ws):
    messages = [init("connected"), result("first"), result("no heading"), result(" ")]
    outcome, _ = run_review(monkeypatch, ws, messages)
    assert outcome.report == "no heading"


@pytest.mark.parametrize(
    "messages,match",
    [
        ([init("failed"), result("x")], "not connected"),
        ([init("connected")], "without a result"),
        ([init("connected"), result("boom", is_error=True)], "boom"),
        ([init("connected"), result("  ")], "empty"),
        ([init("connected"), result("REVIEW FAILED: no diff")], "no diff"),
    ],
)
def test_review_failures(monkeypatch, ws, messages, match):
    with pytest.raises(EigenaugenError, match=match):
        run_review(monkeypatch, ws, messages)


def test_check_login_requires_claude_ai(monkeypatch):
    monkeypatch.setattr(
        reviewer.proc,
        "run",
        lambda argv, **kw: '{"loggedIn": true, "authMethod": "apiKey"}',
    )
    with pytest.raises(EigenaugenError, match="claude auth login"):
        reviewer.check_login()
