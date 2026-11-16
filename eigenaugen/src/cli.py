"""Command line: `eigenaugen review ...`."""

import argparse
import os
import time
from enum import StrEnum

from eigenaugen.src import (
    coverage,
    github,
    log,
    prepare,
    proc,
    prompt_loader,
    report,
    reviewer,
)
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
REVIEW_INTRO = "Review a GitHub pull request along these axes:"
REVIEW_OUTPUTS = "The report goes to stdout, progress to stderr."
HELP_INDENT = "  "
REVIEW_CONTEXT = (
    "Its stack (GitHub stacked PRs, or a chain of base and head branches) is\n"
    "found and cross-checked, as are PRs named with --related."
)
REVIEW_EXAMPLES = """\
examples:
  eigenaugen review 123                                    # inside a clone of the repo
  eigenaugen review https://github.com/OWNER/REPO/pull/123
  eigenaugen review https://github.com/OWNER/REPO/pull/123/changes
  eigenaugen review OWNER/REPO 123
  eigenaugen review 123 --post                             # also post it to the PR
  eigenaugen review 122 --stack 120,121,122,123            # stack given, bottom first
  eigenaugen review 122 --related OTHER/REPO#45            # cross-check another repo's PR
"""
REF_LIST_SEPARATOR = ","
PR_REF_HELP = "PR: NUMBER (same repository), URL, or OWNER/REPO#NUMBER"


class Command(StrEnum):
    REVIEW = "review"


PROG_DESCRIPTION = "Pull request review with Claude Code."
# `eigenaugen -h` lists only commands; argparse keeps their options per command.
COMMAND_HELP_HINT = (
    f"Run '{PROG} COMMAND -h' for a command's options, e.g. {PROG} {Command.REVIEW} -h"
)


def review_description() -> str:
    """`review -h` text; the axes come from the system prompt."""
    axes = [
        f"{HELP_INDENT}{axis.number}. {axis.label}"
        for axis in prompt_loader.review_axes()
    ]
    return "\n".join([REVIEW_INTRO, *axes, "", REVIEW_CONTEXT, REVIEW_OUTPUTS])


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG, description=PROG_DESCRIPTION, epilog=COMMAND_HELP_HINT
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    review = commands.add_parser(
        Command.REVIEW,
        help="review a GitHub pull request",
        description=review_description(),
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
    review.add_argument(
        "--stack",
        metavar="PR,PR,...",
        help=(
            "the PRs of TARGET's stack, bottom first, TARGET included; "
            f"replaces discovery. {PR_REF_HELP}"
        ),
    )
    review.add_argument(
        "--related",
        metavar="PR",
        action="append",
        default=[],
        help=(
            "a related PR, e.g. in another repository, cross-checked like a "
            f"stack member; its stack too. Repeatable. {PR_REF_HELP}"
        ),
    )
    review.add_argument(
        "--no-stack",
        action="store_true",
        help="do not discover stacks (TARGET's or related PRs')",
    )
    args = parser.parse_args(argv)
    try:
        args.pull = github.parse_target(args.target, args.number)
        args.stack_refs = (
            None
            if args.stack is None
            else [
                github.parse_ref(ref.strip())
                for ref in args.stack.split(REF_LIST_SEPARATOR)
            ]
        )
        args.related_refs = [github.parse_ref(ref) for ref in args.related]
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
    request = prepare.Request(
        stack=args.stack_refs,
        related=args.related_refs,
        discover=not args.no_stack,
    )
    with workspace() as ws:
        log.info("gathering the PRs, clones, and diffs the review reads")
        prepared = prepare.prepare(resolved, ws, request, on_progress=log.info)
        plan = prepared.plan
        log.info(f"{plan.summary(pr.key)}; review mode {plan.mode}")
        log.info(f"reviewing with {args.model}, effort {args.effort}; takes minutes")
        outcome = reviewer.review(
            prepared,
            ws,
            model=args.model,
            effort=args.effort,
            destination=destination,
            caller_entrypoint=caller_entrypoint(),
            on_progress=log.info,
        )
    elapsed_s = time.monotonic() - started
    scope = [report.diff_line(plan, outcome.unread)]
    if context := report.context_line(prepared.context):
        scope.append(context)
    parts = (outcome.report, pr, args.effort, outcome.stats, elapsed_s, scope)
    # Print first: if posting fails, the report is not lost.
    print(report.compose(*parts), end="", flush=True)
    if outcome.unread:
        # Never post a review that did not read the whole diff.
        log.error(
            f"the review left diff pages unread after {outcome.rounds} runs: "
            f"{coverage.describe(outcome.unread)}"
            + ("; not posted" if args.post else "")
        )
        return ExitCode.ERROR
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
