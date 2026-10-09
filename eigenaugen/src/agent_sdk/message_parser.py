"""Turn stream-json dictionaries from the CLI into typed messages."""

from enum import StrEnum
from typing import Any

from eigenaugen.src.agent_sdk.errors import MessageParseError
from eigenaugen.src.agent_sdk.types import (
    AssistantMessage,
    ContentBlock,
    Message,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


class MessageType(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    RESULT = "result"


class BlockType(StrEnum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"


def _parse_block(data: dict[str, Any]) -> ContentBlock | None:
    match data.get("type"):
        case BlockType.TEXT:
            return TextBlock(data["text"])
        case BlockType.THINKING:
            return ThinkingBlock(data["thinking"])
        case BlockType.TOOL_USE:
            return ToolUseBlock(data["id"], data["name"], data["input"])
        case BlockType.TOOL_RESULT:
            return ToolResultBlock(
                data["tool_use_id"], data.get("content"), data.get("is_error")
            )
    return None


def _parse_content(content: Any) -> str | list[ContentBlock]:
    if isinstance(content, str):
        return content
    blocks = (_parse_block(item) for item in content)
    return [block for block in blocks if block is not None]


def parse_message(data: dict[str, Any]) -> Message | None:
    """Parse one stream-json message; None for types the client ignores
    (rate limit events, stream events, ...)."""
    try:
        match data.get("type"):
            case MessageType.USER:
                return UserMessage(
                    _parse_content(data["message"]["content"]),
                    data.get("parent_tool_use_id"),
                )
            case MessageType.ASSISTANT:
                message = data["message"]
                content = _parse_content(message["content"])
                return AssistantMessage(
                    content if isinstance(content, list) else [TextBlock(content)],
                    message.get("model", ""),
                    data.get("parent_tool_use_id"),
                )
            case MessageType.SYSTEM:
                return SystemMessage(data.get("subtype", ""), data)
            case MessageType.RESULT:
                return ResultMessage(
                    subtype=data["subtype"],
                    is_error=data["is_error"],
                    num_turns=data.get("num_turns", 0),
                    duration_ms=data.get("duration_ms", 0),
                    session_id=data.get("session_id", ""),
                    result=data.get("result"),
                    total_cost_usd=data.get("total_cost_usd"),
                    permission_denials=data.get("permission_denials") or [],
                    model_usage=data.get("modelUsage") or {},
                    subagent_stats=data.get("subagent_stats") or {},
                )
    except (KeyError, TypeError) as exc:
        raise MessageParseError(
            f"malformed {data.get('type')} message: missing {exc}"
        ) from exc
    return None
