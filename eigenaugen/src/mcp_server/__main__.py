"""Entry point: python -P -m eigenaugen.src.mcp_server --context FILE

Started by the claude CLI as a stdio MCP server. stdout carries protocol
messages only; diagnostics go to stderr.
"""

import argparse
import sys
from pathlib import Path

from eigenaugen.src.constants import VERSION
from eigenaugen.src.context import ReviewContext
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.mcp_server.github_tools import GitHubTools
from eigenaugen.src.mcp_server.protocol import Server
from eigenaugen.src.proc import ENCODING

SERVER_NAME = "eigenaugen-github"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m eigenaugen.src.mcp_server",
        description="Read-only MCP server for the pinned PRs of one review.",
    )
    parser.add_argument(
        "--context", required=True, type=Path, help="review context JSON file"
    )
    args = parser.parse_args(argv)
    try:
        tools = GitHubTools(ReviewContext.load(args.context))
    except EigenaugenError as exc:
        parser.error(str(exc))
    sys.stdin.reconfigure(encoding=ENCODING)
    sys.stdout.reconfigure(encoding=ENCODING)
    Server(SERVER_NAME, VERSION, tools.tools()).serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
