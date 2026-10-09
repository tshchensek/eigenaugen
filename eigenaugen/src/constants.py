"""Constants shared across eigenaugen modules."""

from enum import IntEnum, StrEnum
from pathlib import Path

VERSION = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent
# Repository root: the directory that must be on sys.path.
PROJECT_ROOT = PACKAGE_DIR.parent.parent
PROMPTS_DIR = PACKAGE_DIR / "prompts"

GH_BIN = "gh"
GIT_BIN = "git"
CLAUDE_BIN = "claude"

# Alias that always resolves to the latest stable Opus model.
DEFAULT_MODEL = "opus"


class Effort(StrEnum):
    """Values accepted by `claude --effort`."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


DEFAULT_EFFORT = Effort.XHIGH


class Destination(StrEnum):
    """Where the report goes; decides whether markdown links render."""

    STDOUT = "stdout"
    GITHUB = "github"


# Set by Claude Code clients (cli, claude-vscode, claude-desktop, ...).
ENTRYPOINT_ENV = "CLAUDE_CODE_ENTRYPOINT"
ENTRYPOINT_UNSET = "unset"

# Key of the MCP server in the agent's config; its tools are named
# mcp__<MCP_SERVER_NAME>__<tool>.
MCP_SERVER_NAME = "github"

SHORT_SHA_LEN = 12


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    # 128 + SIGINT, the shell convention for Ctrl-C.
    INTERRUPTED = 130
