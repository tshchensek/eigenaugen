"""Configure and run the review agent; return its report."""

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass

from eigenaugen.src import proc, prompt_loader
from eigenaugen.src.agent_sdk.client import build_env, query
from eigenaugen.src.agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    McpStdioServerConfig,
    ResultMessage,
    SystemMessage,
    SystemPromptPreset,
    ToolUseBlock,
)
from eigenaugen.src.constants import (
    CLAUDE_BIN,
    MCP_SERVER_NAME,
    PROJECT_ROOT,
    Destination,
)
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import PullRequest
from eigenaugen.src.text import sanitize
from eigenaugen.src.workspace import Workspace

# Built-in tools that exist in the session: read-only file access,
# subagents, and documentation search. No shell, no edits, no WebFetch.
BUILTIN_TOOLS = ("Read", "Grep", "Glob", "Agent", "WebSearch")
# Permission rule naming a whole MCP server allows all of its tools.
MCP_ALLOW_RULE = f"mcp__{MCP_SERVER_NAME}"
# Anything not allowed above is denied instead of prompting.
PERMISSION_MODE = "dontAsk"
SANDBOX_ARGS = {
    # Drops code-running tools and WebFetch, ignores user/project/local
    # settings files, confines file tools to the working directories.
    "restricted": None,
    # Only the MCP servers passed here; none from the user's config.
    "strict-mcp-config": None,
    "disable-slash-commands": None,
    "permission-prompts": "none",
}
ENV_OVERRIDES: dict[str, str | None] = {
    # Bill the claude.ai login, never an API key or token.
    "ANTHROPIC_API_KEY": None,
    "ANTHROPIC_AUTH_TOKEN": None,
    # These would override --effort and the subagents' model and effort.
    "CLAUDE_CODE_EFFORT_LEVEL": None,
    "CLAUDE_CODE_SUBAGENT_MODEL": None,
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": None,
}
MCP_SERVER_MODULE = "eigenaugen.src.mcp_server"
# -P keeps the current directory off sys.path.
PYTHON_SAFE_PATH = "-P"
PYTHONPATH_ENV = "PYTHONPATH"

CLAUDE_AI_AUTH_METHOD = "claude.ai"
INIT_SUBTYPE = "init"
MCP_CONNECTED = "connected"
# Contract with the system prompt: a report starts with REPORT_HEADING; when
# inputs cannot be fetched the agent answers with FAILURE_PREFIX instead.
REPORT_HEADING = "## Verdict"
FAILURE_PREFIX = "REVIEW FAILED:"
# Keys in ResultMessage.model_usage / subagent_stats.
COST_KEY = "costUSD"
SUBAGENTS_BY_TYPE_KEY = "by_type"

# Tool input keys worth showing in progress lines, in display order.
PROGRESS_KEYS = (
    "subagent_type",
    "model",
    "effort",
    "description",
    "file_path",
    "path",
    "pattern",
    "query",
    "offset",
)
MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
NESTED_INDENT = "  "

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class RunStats:
    """What a review consumed, and the session to resume, as reported by the
    CLI."""

    session_id: str
    model: str
    # API list price, summed over all models; a claude.ai plan is not billed
    # per run.
    cost_usd: float
    turns: int
    # Model ID -> list-price cost: main agent, subagents, and the CLI's own
    # helper calls (WebSearch runs on haiku).
    model_costs: dict[str, float]
    # Subagent type -> times spawned.
    subagents: dict[str, int]


@dataclass(frozen=True)
class Review:
    report: str
    stats: RunStats


def check_login() -> None:
    """Fail early unless the CLI is logged in with a claude.ai account."""
    out = proc.run(
        [CLAUDE_BIN, "auth", "status", "--json"], env=build_env(ENV_OVERRIDES)
    )
    try:
        status = json.loads(out)
    except json.JSONDecodeError as exc:
        raise EigenaugenError(f"claude auth status: invalid JSON: {exc}") from exc
    if not status.get("loggedIn") or status.get("authMethod") != CLAUDE_AI_AUTH_METHOD:
        raise EigenaugenError(
            "claude is not logged in with a claude.ai account -- run: claude auth login"
        )


def build_options(
    pr: PullRequest, ws: Workspace, model: str, effort: str
) -> ClaudeAgentOptions:
    server = McpStdioServerConfig(
        command=sys.executable,
        args=[
            PYTHON_SAFE_PATH,
            "-m",
            MCP_SERVER_MODULE,
            "--pr-url",
            pr.url,
            "--base-sha",
            pr.base_sha,
            "--head-sha",
            pr.head_sha,
        ],
        env={PYTHONPATH_ENV: str(PROJECT_ROOT)},
    )
    return ClaudeAgentOptions(
        system_prompt=SystemPromptPreset(append=prompt_loader.system_prompt()),
        tools=list(BUILTIN_TOOLS),
        allowed_tools=[*BUILTIN_TOOLS, MCP_ALLOW_RULE],
        mcp_servers={MCP_SERVER_NAME: server},
        agents=prompt_loader.agents(),
        permission_mode=PERMISSION_MODE,
        model=model,
        effort=effort,
        cwd=ws.session,
        add_dirs=[ws.checkout],
        env=dict(ENV_OVERRIDES),
        extra_args=dict(SANDBOX_ARGS),
        cli_path=CLAUDE_BIN,
    )


def describe_tool_use(block: ToolUseBlock, root_prefix: str) -> str:
    """One progress line, e.g. `Read src/app/main.py`."""
    name = block.name.removeprefix(MCP_TOOL_PREFIX)
    values = [
        str(block.input[key]).replace(root_prefix, "")
        for key in PROGRESS_KEYS
        if key in block.input
    ]
    return " ".join([name, *values])


def _check_mcp(message: SystemMessage) -> None:
    servers = {
        s.get("name"): s.get("status") for s in message.data.get("mcp_servers", [])
    }
    status = servers.get(MCP_SERVER_NAME)
    if status != MCP_CONNECTED:
        raise EigenaugenError(f"MCP server {MCP_SERVER_NAME!r} not connected: {status}")


def _report(results: list[ResultMessage]) -> str:
    """The report among the session's results.

    Background subagents can split a session into several turns, each with
    its own result: take the last one in report format, else the last
    non-empty one.
    """
    if not results:
        raise EigenaugenError("agent ended without a result")
    final = results[-1]
    final_text = (final.result or "").strip()
    if final.is_error:
        raise EigenaugenError(f"review failed ({final.subtype}): {final_text}")
    if final_text.startswith(FAILURE_PREFIX):
        raise EigenaugenError(final_text)
    texts = [text for r in results if (text := (r.result or "").strip())]
    reports = [text for text in texts if text.startswith(REPORT_HEADING)]
    if reports:
        return reports[-1]
    if texts:
        return texts[-1]
    raise EigenaugenError("agent returned an empty report")


def _stats(model: str, results: list[ResultMessage]) -> RunStats:
    """Cost, model usage, and subagent counts are running totals, so the
    last result has them; turns are per result and add up."""
    final = results[-1]
    return RunStats(
        session_id=final.session_id,
        model=model,
        cost_usd=final.total_cost_usd or 0.0,
        turns=sum(r.num_turns for r in results),
        model_costs={
            name: usage.get(COST_KEY, 0.0) for name, usage in final.model_usage.items()
        },
        subagents=dict(final.subagent_stats.get(SUBAGENTS_BY_TYPE_KEY, {})),
    )


def build_prompt(
    pr: PullRequest, ws: Workspace, destination: Destination, caller_entrypoint: str
) -> str:
    return prompt_loader.user_prompt(
        pr_url=pr.url,
        base_sha=pr.base_sha,
        head_sha=pr.head_sha,
        checkout=str(ws.checkout),
        destination=destination,
        caller_entrypoint=caller_entrypoint,
    )


def review(
    pr: PullRequest,
    ws: Workspace,
    *,
    model: str,
    effort: str,
    destination: Destination,
    caller_entrypoint: str,
    on_progress: ProgressCallback,
) -> Review:
    """Run the agent to completion; return its sanitized report and stats."""
    prompt = build_prompt(pr, ws, destination, caller_entrypoint)
    options = build_options(pr, ws, model, effort)
    root_prefix = f"{ws.root}/"
    main_model = model
    results: list[ResultMessage] = []
    for message in query(prompt=prompt, options=options):
        match message:
            case SystemMessage(subtype=subtype) if subtype == INIT_SUBTYPE:
                _check_mcp(message)
                # The resolved model ID, e.g. claude-opus-5-5 for "opus".
                main_model = message.data.get("model") or main_model
            case AssistantMessage(content=content, parent_tool_use_id=parent):
                indent = NESTED_INDENT if parent else ""
                for block in content:
                    if isinstance(block, ToolUseBlock):
                        on_progress(indent + describe_tool_use(block, root_prefix))
            case ResultMessage():
                results.append(message)
    report = sanitize(_report(results))
    return Review(report, _stats(main_model, results))
