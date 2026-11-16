import pytest

from eigenaugen.src import diffplan, prompt_loader, reviewer
from eigenaugen.src.agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
)
from eigenaugen.src.constants import MCP_SERVER_NAME, Destination
from eigenaugen.src.context import (
    ContextPull,
    ReviewContext,
    Role,
    StackEntry,
    StackInfo,
)
from eigenaugen.src.coverage import Coverage
from eigenaugen.src.diffplan import DiffPlan
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.mcp_server.github_tools import ToolName
from eigenaugen.src.prepare import Prepared
from eigenaugen.src.workspace import Workspace

KEY = "o/r#1"
URL = "https://github.com/o/r/pull/1"
MCP_TOOLS = {f"mcp__{MCP_SERVER_NAME}__{name}" for name in ToolName}


def section(path: str, lines: int) -> str:
    body = "".join(f"+line {i:04}\n" for i in range(lines))
    return f"diff --git a/{path} b/{path}\n@@ -0,0 +1,{lines} @@\n{body}"


@pytest.fixture
def ws(tmp_path) -> Workspace:
    workspace = Workspace(tmp_path)
    workspace.data.mkdir()
    return workspace


def make_prepared(ws: Workspace, diff: str, others: int = 0) -> Prepared:
    def pull(number: int, role: Role) -> ContextPull:
        diff_file = ws.data / f"{number}.diff"
        diff_file.write_text(diff)
        return ContextPull(
            key=f"o/r#{number}",
            url=f"https://github.com/o/r/pull/{number}",
            base_sha="a" * 40,
            head_sha="b" * 40,
            merge_base_sha="c" * 40,
            behind_by=number - 1,
            role=role,
            state="OPEN",
            changed_files=1,
            additions=1,
            deletions=0,
            repo_dir=str(ws.checkout),
            diff_file=str(diff_file),
        )

    pulls = {KEY: pull(1, Role.REVIEWED)}
    for number in range(2, others + 2):
        pulls[f"o/r#{number}"] = pull(number, Role.STACK)
    stacks = ()
    if others:
        entries = tuple(
            StackEntry(key, position, "this" if key == KEY else "above")
            for position, key in enumerate(pulls, 1)
        )
        stacks = (StackInfo(KEY, "github", 7, "GitHub stack #7, 2 PRs", entries),)
    context = ReviewContext(KEY, pulls, stacks, str(ws.data / "coverage.json"))
    context_file = ws.data / "context.json"
    context.save(context_file)
    return Prepared(context, context_file, DiffPlan.build(diff, {}), ())


@pytest.fixture
def prepared(ws) -> Prepared:
    return make_prepared(ws, section("a.py", 2))


def test_system_prompt_names_every_tool_agent_and_contract():
    text = prompt_loader.system_prompt()
    for name in [*ToolName, *prompt_loader.agents()]:
        assert f"`{name}`" in text
    assert reviewer.FAILURE_PREFIX in text
    assert f"\n{reviewer.REPORT_HEADING}\n" in text


def test_review_axes_are_numbered_from_one():
    axes = prompt_loader.review_axes()
    assert axes
    assert [axis.number for axis in axes] == list(range(1, len(axes) + 1))


def test_subagent_models_are_left_to_the_reviewer():
    agents = prompt_loader.agents()
    assert set(agents) == {
        "standards-scout",
        "code-tracer",
        "diff-reviewer",
        "cross-checker",
    }
    for agent in agents.values():
        assert agent.model is None and agent.effort is None
        assert agent.tools and set(agent.tools) <= {*reviewer.BUILTIN_TOOLS, *MCP_TOOLS}
        assert "Agent" not in agent.tools
        assert agent.prompt and agent.description
    text = prompt_loader.system_prompt()
    assert "Pass `model` and `effort` on every `Agent` call" in text


def test_diff_reviewer_embeds_the_shared_rules():
    prompt = prompt_loader.agents()["diff-reviewer"].prompt
    assert "$" not in prompt
    for axis in prompt_loader.review_axes():
        assert f"**{axis.label}**" in prompt
    assert prompt_loader.section("Evidence rules") in prompt


def test_section_ignores_headings_in_code_blocks():
    report_format = prompt_loader.section("Report format")
    assert "## Verdict" in report_format and "## Coverage" in report_format
    with pytest.raises(EigenaugenError, match="no section"):
        prompt_loader.section("Nope")


def test_build_prompt_fills_every_field(ws, prepared):
    text = reviewer.build_prompt(prepared, ws, Destination.GITHUB, "cli")
    assert "$" not in text
    pull = prepared.context.reviewed_pull
    for value in (
        pull.url,
        pull.base_sha,
        pull.merge_base_sha,
        pull.head_sha,
        str(ws.checkout),
        "`github`",
        "`cli`",
        "`direct`",
        "o/r#1 diff: 1 files",
        reviewer.NO_CONTEXT,
    ):
        assert value in text


def test_context_table_lists_stack_positions(ws):
    prepared = make_prepared(ws, section("a.py", 1), others=1)
    table = reviewer.context_table(prepared.context)
    assert (
        "| `o/r#2` | https://github.com/o/r/pull/2 | stack | GitHub stack #7, 2 PRs"
        in table
    )
    assert "of o/r#1: position 2, above | 1 | 1 files, +1 -0 |" in table


def test_build_options_sandbox(ws, prepared):
    options = reviewer.build_options(prepared.context_file, ws, "opus", "xhigh")
    assert (options.model, options.effort) == ("opus", "xhigh")
    assert options.cwd == ws.session and options.add_dirs == [ws.checkout]
    assert "Bash" not in options.tools and "WebFetch" not in options.tools
    assert "restricted" in options.extra_args
    # Sessions are kept so the report can offer `claude --resume`.
    assert "no-session-persistence" not in options.extra_args
    assert options.env["ANTHROPIC_API_KEY"] is None
    assert options.resume is None
    server = options.mcp_servers[MCP_SERVER_NAME]
    assert "-P" in server.args
    assert server.args[-2:] == ["--context", str(prepared.context_file)]
    # The data directory with diffs and coverage is not open to file tools.
    assert ws.data not in options.add_dirs


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
    diff = ToolUseBlock(
        "4", f"mcp__{MCP_SERVER_NAME}__get_diff", {"pr": "o/r#2", "chunk": 3, "page": 2}
    )
    assert reviewer.describe_tool_use(diff, "/x/") == "get_diff o/r#2 chunk 3 page 2"


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


def read_all(prepared: Prepared) -> None:
    """What an agent's get_diff calls leave behind: every page served."""
    record = Coverage()
    for chunk in prepared.plan.chunks:
        for number in range(1, len(chunk.pages) + 1):
            record.record_chunk(chunk.number, number)
    record.save(prepared.context_file.parent / "coverage.json")


def run_review(monkeypatch, ws, messages, prepared=None):
    prepared = prepared or make_prepared(ws, section("a.py", 2))
    read_all(prepared)
    monkeypatch.setattr(reviewer, "query", lambda **_kw: iter(messages))
    progress = []
    outcome = reviewer.review(
        prepared,
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
    assert (outcome.unread, outcome.rounds) == ({}, 1)
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


class FakeCLI:
    """Each run answers with a report; READS[n] says which pages the n-th
    run reads (None: all of them)."""

    def __init__(self, prepared: Prepared, reads: list):
        self.prepared = prepared
        self.reads = reads
        self.runs = []

    def __call__(self, *, prompt, options):
        number = len(self.runs)
        self.runs.append((prompt, options.resume))
        record = Coverage.load(self.prepared.context_file.parent / "coverage.json")
        pages = self.reads[number]
        if pages is None:
            pages = [
                (c.number, n)
                for c in self.prepared.plan.chunks
                for n in range(1, len(c.pages) + 1)
            ]
        for chunk, page in pages:
            record.record_chunk(chunk, page)
        record.save(self.prepared.context_file.parent / "coverage.json")
        text = f"## Verdict\nreport {number + 1}"
        return iter([init("connected"), result(text, turns=2, cost=1.0 + number)])


def big_prepared(ws, monkeypatch) -> Prepared:
    monkeypatch.setattr(diffplan, "PAGE_CHARS", 120)
    prepared = make_prepared(ws, "".join(section(f"f{i}.py", 6) for i in range(6)))
    assert prepared.plan.review_pages == 6
    return prepared


def review_with(monkeypatch, ws, fake, prepared=None):
    monkeypatch.setattr(reviewer, "query", fake)
    progress = []
    outcome = reviewer.review(
        prepared or fake.prepared,
        ws,
        model="opus",
        effort="xhigh",
        destination=Destination.STDOUT,
        caller_entrypoint="unset",
        on_progress=progress.append,
    )
    return outcome, progress


def test_review_resumes_until_every_page_is_read(monkeypatch, ws):
    prepared = big_prepared(ws, monkeypatch)
    fake = FakeCLI(prepared, reads=[[(1, 1), (1, 2), (1, 3)], [(1, 4)], None])
    outcome, progress = review_with(monkeypatch, ws, fake)
    assert (outcome.unread, outcome.rounds) == ({}, 3)
    assert outcome.report == "## Verdict\nreport 3"
    assert [resume for _, resume in fake.runs] == [None, "sess-1", "sess-1"]
    assert "chunk 1 page 4; chunk 2 pages 1-2" in fake.runs[1][0]
    assert any("resuming the session" in line for line in progress)
    assert outcome.stats.turns == 6


def test_review_gives_up_after_max_continuations(monkeypatch, ws):
    prepared = big_prepared(ws, monkeypatch)
    fake = FakeCLI(prepared, reads=[[(1, 1)]] + [[]] * reviewer.MAX_CONTINUATIONS)
    outcome, _ = review_with(monkeypatch, ws, fake)
    assert outcome.rounds == 1 + reviewer.MAX_CONTINUATIONS
    assert outcome.unread == {1: [2, 3, 4], 2: [1, 2]}


def test_failed_run_is_not_resumed(monkeypatch, ws):
    prepared = big_prepared(ws, monkeypatch)
    runs = []

    def failing(**kw):
        runs.append(kw)
        return iter([init("connected"), result("REVIEW FAILED: no diff")])

    with pytest.raises(EigenaugenError, match="no diff"):
        review_with(monkeypatch, ws, failing, prepared)
    assert len(runs) == 1


def test_check_login_requires_claude_ai(monkeypatch):
    monkeypatch.setattr(
        reviewer.proc,
        "run",
        lambda argv, **kw: '{"loggedIn": true, "authMethod": "apiKey"}',
    )
    with pytest.raises(EigenaugenError, match="claude auth login"):
        reviewer.check_login()
