"""Errors raised by the SDK-style client; names follow claude-agent-sdk."""

from eigenaugen.src.errors import EigenaugenError


class ClaudeSDKError(EigenaugenError):
    """Base class for client errors."""


class CLINotFoundError(ClaudeSDKError):
    """The claude executable is not on PATH."""


class ProcessError(ClaudeSDKError):
    """The CLI exited non-zero."""

    def __init__(self, exit_code: int, stderr: str) -> None:
        self.exit_code = exit_code
        self.stderr = stderr.strip()
        detail = self.stderr or "no error output"
        super().__init__(f"claude exited with code {exit_code}: {detail}")


class CLIJSONDecodeError(ClaudeSDKError):
    """A line on the CLI's stdout was not JSON."""


class MessageParseError(ClaudeSDKError):
    """A JSON message lacked fields its type requires."""
