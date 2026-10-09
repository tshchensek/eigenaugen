"""Entry point: python -P -m eigenaugen.src.mcp_server --pr-url URL ...

Started by the claude CLI as a stdio MCP server. stdout carries protocol
messages only; diagnostics go to stderr.
"""

import argparse
import sys

from eigenaugen.src.constants import VERSION
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import PullRequest
from eigenaugen.src.mcp_server.github_tools import GitHubTools
from eigenaugen.src.mcp_server.protocol import Server
from eigenaugen.src.proc import ENCODING

SERVER_NAME = "eigenaugen-github"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m eigenaugen.src.mcp_server",
        description="Read-only MCP server for one pinned GitHub pull request.",
    )
    parser.add_argument("--pr-url", required=True, help="pull request URL")
    parser.add_argument("--base-sha", required=True, help="pinned base commit")
    parser.add_argument("--head-sha", required=True, help="pinned head commit")
    args = parser.parse_args(argv)
    try:
        pr = PullRequest.from_url(args.pr_url, args.base_sha, args.head_sha)
    except EigenaugenError as exc:
        parser.error(str(exc))
    sys.stdin.reconfigure(encoding=ENCODING)
    sys.stdout.reconfigure(encoding=ENCODING)
    Server(SERVER_NAME, VERSION, GitHubTools(pr).tools()).serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
