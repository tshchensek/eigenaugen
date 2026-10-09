"""Run one agent session through the claude CLI, like claude-agent-sdk.

The SDK drives the CLI as a subprocess: options become flags, the prompt
goes in as a stream-json user message on stdin, and every stdout line is a
stream-json message. This client does the same, synchronously, so it can
use the claude.ai login the CLI already holds.
CLI flags: `claude --help`; https://code.claude.com/docs/en/cli-reference
"""

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping

from eigenaugen.src.agent_sdk.errors import (
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
)
from eigenaugen.src.agent_sdk.message_parser import MessageType, parse_message
from eigenaugen.src.agent_sdk.types import (
    ClaudeAgentOptions,
    Message,
    SystemPromptPreset,
)
from eigenaugen.src.proc import DECODE_ERRORS, ENCODING

STREAM_JSON = "stream-json"
USER_ROLE = "user"
LIST_SEPARATOR = ","
# Seconds the CLI gets to exit after SIGTERM before it is killed.
TERMINATE_TIMEOUT_S = 5


def build_command(options: ClaudeAgentOptions) -> list[str]:
    """Translate OPTIONS into a claude argv (no prompt; that goes on stdin)."""
    cmd = [
        options.cli_path,
        "--print",
        "--input-format",
        STREAM_JSON,
        "--output-format",
        STREAM_JSON,
        "--verbose",
    ]
    prompt = options.system_prompt
    if isinstance(prompt, str):
        cmd += ["--system-prompt", prompt]
    elif isinstance(prompt, SystemPromptPreset) and prompt.append:
        cmd += ["--append-system-prompt", prompt.append]
    if options.tools is not None:
        cmd += ["--tools", LIST_SEPARATOR.join(options.tools)]
    if options.allowed_tools:
        cmd += ["--allowedTools", LIST_SEPARATOR.join(options.allowed_tools)]
    if options.disallowed_tools:
        cmd += [
            "--disallowedTools",
            LIST_SEPARATOR.join(options.disallowed_tools),
        ]
    if options.model:
        cmd += ["--model", options.model]
    if options.effort:
        cmd += ["--effort", options.effort]
    if options.permission_mode:
        cmd += ["--permission-mode", options.permission_mode]
    for directory in options.add_dirs:
        cmd += ["--add-dir", str(directory)]
    if options.mcp_servers:
        servers = {n: s.to_cli() for n, s in options.mcp_servers.items()}
        cmd += ["--mcp-config", json.dumps({"mcpServers": servers})]
    if options.agents:
        agents = {n: a.to_cli() for n, a in options.agents.items()}
        cmd += ["--agents", json.dumps(agents)]
    for flag, value in options.extra_args.items():
        cmd.append(f"--{flag}")
        if value is not None:
            cmd.append(value)
    return cmd


def build_env(overrides: Mapping[str, str | None]) -> dict[str, str]:
    """Inherited environment with OVERRIDES applied; None removes a key."""
    env = dict(os.environ)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def user_message(prompt: str) -> str:
    """One stream-json input line carrying PROMPT as the user turn."""
    return json.dumps(
        {
            "type": MessageType.USER,
            "message": {"role": USER_ROLE, "content": prompt},
            "parent_tool_use_id": None,
            "session_id": "",
        }
    )


def _decode(line: str) -> dict:
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise CLIJSONDecodeError(f"non-JSON line from claude: {line[:200]!r}") from exc


def _send(child: subprocess.Popen, prompt: str) -> None:
    try:
        child.stdin.write(user_message(prompt) + "\n")
        child.stdin.close()
    except BrokenPipeError:
        # The CLI already exited; its exit code and stderr say why.
        pass


def _stop(child: subprocess.Popen) -> None:
    """Make sure the CLI is gone, e.g. when the caller stops iterating."""
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=TERMINATE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def query(*, prompt: str, options: ClaudeAgentOptions) -> Iterator[Message]:
    """Run one session and yield its messages as they arrive.

    The last message of a completed session is a ResultMessage. Raises
    ProcessError if the CLI exits non-zero.
    """
    if shutil.which(options.cli_path) is None:
        raise CLINotFoundError(f"{options.cli_path} not found on PATH")
    cmd = build_command(options)
    # stderr goes to a file: a second pipe could fill up and deadlock the
    # CLI while this side blocks reading stdout.
    with tempfile.TemporaryFile(
        "w+", encoding=ENCODING, errors=DECODE_ERRORS
    ) as stderr:
        with subprocess.Popen(
            cmd,
            cwd=options.cwd,
            env=build_env(options.env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding=ENCODING,
            errors=DECODE_ERRORS,
        ) as child:
            try:
                _send(child, prompt)
                for line in child.stdout:
                    if not line.strip():
                        continue
                    message = parse_message(_decode(line))
                    if message is not None:
                        yield message
                returncode = child.wait()
            finally:
                _stop(child)
        if returncode != 0:
            stderr.seek(0)
            raise ProcessError(returncode, stderr.read())
