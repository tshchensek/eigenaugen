"""Minimal MCP server over stdio: newline-delimited JSON-RPC 2.0.

Implements the subset a client needs to list and call tools:
initialize, notifications/initialized, ping, tools/list, tools/call.
Spec: https://modelcontextprotocol.io/specification/2025-06-18
Messages are handled one at a time, in arrival order; responses carry the
request id, so a client may still pipeline requests.
"""

import json
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, TextIO

from eigenaugen.src.errors import EigenaugenError

JSONRPC_VERSION = "2.0"
# Newest first; an unknown requested version gets the newest.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
CONTENT_TYPE_TEXT = "text"


class Method(StrEnum):
    INITIALIZE = "initialize"
    PING = "ping"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"


class ErrorCode(IntEnum):
    # https://www.jsonrpc.org/specification#error_object
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602


class InvalidParams(Exception):
    """A request's params are unusable; answered with INVALID_PARAMS."""


@dataclass(frozen=True)
class Tool:
    """A tool: JSON Schema for its arguments and a handler returning text.

    Handlers raise EigenaugenError for failures the model should see; the
    result is then flagged isError instead of failing the request.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _response(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}


def _error(msg_id: Any, code: ErrorCode, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": msg_id,
        "error": {"code": int(code), "message": message},
    }


def _text_result(text: str, is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": CONTENT_TYPE_TEXT, "text": text}],
        "isError": is_error,
    }


class Server:
    def __init__(self, name: str, version: str, tools: list[Tool]) -> None:
        self.name = name
        self.version = version
        self.tools = {tool.name: tool for tool in tools}
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            Method.INITIALIZE: self._initialize,
            Method.PING: lambda _params: {},
            Method.TOOLS_LIST: self._list_tools,
            Method.TOOLS_CALL: self._call_tool,
        }

    def serve(self, reader: TextIO, writer: TextIO) -> None:
        """Answer requests from READER on WRITER until READER closes."""
        for line in reader:
            if not line.strip():
                continue
            response = self.handle(line)
            if response is not None:
                writer.write(json.dumps(response) + "\n")
                writer.flush()

    def handle(self, line: str) -> dict[str, Any] | None:
        """Process one message; None for notifications and responses."""
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            return _error(None, ErrorCode.PARSE_ERROR, str(exc))
        if not isinstance(message, dict):
            return _error(None, ErrorCode.INVALID_REQUEST, "expected an object")
        if "id" not in message:
            return None  # notification: never answered
        msg_id = message["id"]
        method = message.get("method")
        if not isinstance(method, str):
            return _error(msg_id, ErrorCode.INVALID_REQUEST, "missing method")
        handler = self.handlers.get(method)
        if handler is None:
            return _error(
                msg_id, ErrorCode.METHOD_NOT_FOUND, f"unknown method: {method}"
            )
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(msg_id, ErrorCode.INVALID_PARAMS, "params must be an object")
        try:
            return _response(msg_id, handler(params))
        except InvalidParams as exc:
            return _error(msg_id, ErrorCode.INVALID_PARAMS, str(exc))

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if requested in SUPPORTED_PROTOCOL_VERSIONS:
            version = requested
        else:
            version = SUPPORTED_PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def _list_tools(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": [tool.spec() for tool in self.tools.values()]}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(params.get("name"))
        if tool is None:
            raise InvalidParams(f"unknown tool: {params.get('name')}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise InvalidParams("arguments must be an object")
        try:
            return _text_result(tool.handler(arguments), is_error=False)
        except EigenaugenError as exc:
            return _text_result(str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001 -- keep serving other calls
            traceback.print_exc(file=sys.stderr)
            return _text_result(
                f"internal error: {type(exc).__name__}: {exc}", is_error=True
            )
