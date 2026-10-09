"""Options and message types modeled on the Claude Agent SDK for Python.

Class and field names follow claude-agent-sdk so code reads the same:
https://docs.claude.com/en/docs/agent-sdk/python
Only the subset eigenaugen needs is implemented, synchronously.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CLI_PATH = "claude"
MCP_TYPE_STDIO = "stdio"


@dataclass(frozen=True)
class AgentDefinition:
    """A subagent; serialized into `claude --agents`.

    Fields: https://code.claude.com/docs/en/sub-agents
    """

    description: str
    prompt: str
    tools: list[str] | None = None
    model: str | None = None
    effort: str | None = None

    def to_cli(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class McpStdioServerConfig:
    """An MCP server the CLI starts as a subprocess and talks to over stdio."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def to_cli(self) -> dict[str, Any]:
        return {"type": MCP_TYPE_STDIO, **asdict(self)}


@dataclass(frozen=True)
class SystemPromptPreset:
    """The default Claude Code system prompt, with optional text appended."""

    append: str | None = None


@dataclass
class ClaudeAgentOptions:
    """Session options, translated to CLI flags by client.build_command."""

    system_prompt: str | SystemPromptPreset | None = None
    # Built-in tools that exist in the session; None keeps the default set.
    tools: list[str] | None = None
    # Permission rules that run without prompting.
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    mcp_servers: dict[str, McpStdioServerConfig] = field(default_factory=dict)
    agents: dict[str, AgentDefinition] = field(default_factory=dict)
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    cwd: Path | None = None
    add_dirs: list[Path] = field(default_factory=list)
    # Merged over the inherited environment. Extension to the SDK: a None
    # value removes the inherited variable.
    env: dict[str, str | None] = field(default_factory=dict)
    # Other CLI flags: {"flag": "value"} or {"flag": None} for switches.
    extra_args: dict[str, str | None] = field(default_factory=dict)
    cli_path: str = DEFAULT_CLI_PATH


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ThinkingBlock:
    thinking: str


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None


ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True)
class UserMessage:
    content: str | list[ContentBlock]
    parent_tool_use_id: str | None = None


@dataclass(frozen=True)
class AssistantMessage:
    content: list[ContentBlock]
    model: str
    parent_tool_use_id: str | None = None


@dataclass(frozen=True)
class SystemMessage:
    subtype: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ResultMessage:
    """End of one agent turn. A session can emit several (for example after
    background subagents finish): num_turns and duration_ms cover this turn
    only, while total_cost_usd, model_usage, and subagent_stats are running
    totals for the session."""

    subtype: str
    is_error: bool
    num_turns: int
    duration_ms: int
    session_id: str
    result: str | None = None
    total_cost_usd: float | None = None
    permission_denials: list[dict[str, Any]] = field(default_factory=list)
    # Model ID -> usage: tokens, costUSD, costBasis, ...
    model_usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    # spawned, completed, failed, by_type (subagent type -> count), ...
    subagent_stats: dict[str, Any] = field(default_factory=dict)


Message = UserMessage | AssistantMessage | SystemMessage | ResultMessage
