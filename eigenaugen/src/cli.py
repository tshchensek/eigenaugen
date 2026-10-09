"""Command line: `eigenaugen review ...`."""

import argparse
import os
import time
from enum import StrEnum

from eigenaugen.src import github, log, proc, report, reviewer
from eigenaugen.src.constants import (
    CLAUDE_BIN,
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    ENTRYPOINT_ENV,
    ENTRYPOINT_UNSET,
    GH_BIN,
    GIT_BIN,
    SHORT_SHA_LEN,
    Destination,
    Effort,
    ExitCode,
)
from eigenaugen.src.errors import CommandError, EigenaugenError
from eigenaugen.src.github import PullTarget
from eigenaugen.src.workspace import workspace

PROG = "eigenaugen"
REVIEW_EXAMPLES = """\
examples:
  eigenaugen review 123                                    # inside a clone of the repo
  eigenaugen review https://github.com/OWNER/REPO/pull/123
  eigenaugen review https://github.com/OWNER/REPO/pull/123/changes
  eigenaugen review OWNER/REPO 123
  eigenaugen review 123 --post                             # also post it to the PR
"""


class Command(StrEnum):
    REVIEW = "review"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG, description="Pull request review with Claude Code."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    review = commands.add_parser(
        Command.REVIEW,
        help="review a GitHub pull request",
        description=(
            "Review a GitHub pull request along fixed axes (concurrency, races, "
            "edge cases, hygiene, DRY, injection/auth, error propagation, docs, "
            "magic values, scalability). The report goes to stdout, progress "
            "to stderr."
        ),
        epilog=REVIEW_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    review.add_argument(
        "target",
        metavar="TARGET",
        help="PR number (inside a clone), PR URL, or OWNER/REPO",
    )
    review.add_argument(
        "number", nargs="?", metavar="NUMBER", help="PR number, after OWNER/REPO"
    )
    review.add_argument(
        "--post",
        action="store_true",
        help="also post the report as a COMMENT review on the reviewed commit",
    )
    review.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model alias or ID (default: {DEFAULT_MODEL})",
    )
    review.add_argument(
        "--effort",
        default=DEFAULT_EFFORT,
        choices=list(Effort),
        help=f"reasoning effort (default: {DEFAULT_EFFORT})",
    )
    args = parser.parse_args(argv)
    try:
        args.pull = github.parse_target(args.target, args.number)
    except EigenaugenError as exc:
        review.error(str(exc))
    return args


def caller_entrypoint() -> str:
    """CLAUDE_CODE_ENTRYPOINT of whoever launched this program. The agent
    cannot read it itself: the CLI sets its own value (sdk-cli)."""
    return os.environ.get(ENTRYPOINT_ENV) or ENTRYPOINT_UNSET


def require_repo_context(target: PullTarget) -> None:
    """A bare PR number needs a clone to name the repository."""
    if target.repo is not None or not github.PR_NUMBER_RE.fullmatch(target.ref):
        return
    try:
        proc.run([GIT_BIN, "rev-parse", "--is-inside-work-tree"])
    except CommandError:
        raise EigenaugenError(
            "a bare PR number works only inside a clone -- pass a PR URL or "
            "OWNER/REPO NUMBER instead"
        ) from None


def review(args: argparse.Namespace) -> ExitCode:
    started = time.monotonic()
    proc.require(GH_BIN, GIT_BIN, CLAUDE_BIN)
    require_repo_context(args.pull)
    reviewer.check_login()
    resolved = github.resolve(args.pull)
    pr = resolved.pr
    log.info(
        f"{pr.url} at {pr.head_sha[:SHORT_SHA_LEN]}: {resolved.changed_files} "
        f"files, +{resolved.additions} -{resolved.deletions}"
    )
    if resolved.changed_files == 0:
        log.warn("no changed files; nothing to review")
        return ExitCode.OK
    destination = Destination.GITHUB if args.post else Destination.STDOUT
    with workspace() as ws:
        log.info("checking out the head commit")
        github.checkout(pr, ws.checkout)
        log.info(f"reviewing with {args.model}, effort {args.effort}; takes minutes")
        outcome = reviewer.review(
            pr,
            ws,
            model=args.model,
            effort=args.effort,
            destination=destination,
            caller_entrypoint=caller_entrypoint(),
            on_progress=log.info,
        )
    elapsed_s = time.monotonic() - started
    parts = (outcome.report, pr, args.effort, outcome.stats, elapsed_s)
    # Print first: if posting fails, the report is not lost.
    print(report.compose(*parts), end="", flush=True)
    if args.post:
        # PR readers cannot resume a session on this machine.
        posted = github.post_review(pr, report.compose(*parts, resume=False))
        log.info(f"posted review: {posted}")
    return ExitCode.OK


COMMANDS = {Command.REVIEW: review}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except EigenaugenError as exc:
        log.error(str(exc))
        return ExitCode.ERROR
    except KeyboardInterrupt:
        log.error("interrupted")
        return ExitCode.INTERRUPTED
