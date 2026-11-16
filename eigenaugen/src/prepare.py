"""Everything a review reads, gathered before the agent starts: the
reviewed PR's stack, related PRs and their stacks, merge bases, clones,
diffs, and the review context file the MCP server serves them from."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from eigenaugen.src import github, stacks
from eigenaugen.src.context import (
    ContextPull,
    ReviewContext,
    Role,
    StackEntry,
    StackInfo,
)
from eigenaugen.src.diffplan import SKIM_ATTRIBUTES, DiffPlan, classify
from eigenaugen.src.diffs import primary_path, split_sections
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import PullRequest, PullTarget, Resolved
from eigenaugen.src.gitrepo import GitRepo
from eigenaugen.src.proc import ENCODING
from eigenaugen.src.stacks import Stack
from eigenaugen.src.workspace import Workspace

CONTEXT_FILENAME = "context.json"
COVERAGE_FILENAME = "coverage.json"
DIFFS_DIRNAME = "diffs"
DIFF_SUFFIX = ".diff"

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class Request:
    """Context options from the command line."""

    # --stack, bottom first; None: discover the stack (unless discover off).
    stack: Sequence[PullTarget] | None = None
    # --related
    related: Sequence[PullTarget] = field(default_factory=tuple)
    # False with --no-stack.
    discover: bool = True


@dataclass(frozen=True)
class Prepared:
    context: ReviewContext
    context_file: Path
    # The reviewed PR's diff plan.
    plan: DiffPlan
    stacks: tuple[Stack, ...]


def _resolve_ref(ref: PullTarget, reviewed: PullRequest) -> Resolved:
    """A bare number names a PR of the reviewed PR's repository."""
    if ref.repo is None and github.PR_NUMBER_RE.fullmatch(ref.ref):
        ref = PullTarget(ref.ref, reviewed.repo_spec)
    return github.resolve(ref)


def _stack_members(stack: Stack) -> list[Resolved]:
    return [member.pull for member in stack.members]


def gather(
    reviewed: Resolved, request: Request, on_progress: ProgressCallback
) -> tuple[tuple[Stack, ...], dict[str, tuple[Resolved, Role]]]:
    """The stacks found and every PR the review reads, keyed, with roles."""
    found: list[Stack] = []
    if request.stack is not None:
        pulls = [_resolve_ref(ref, reviewed.pr) for ref in request.stack]
        # The reviewed PR keeps the commits it was pinned to.
        pulls = [reviewed if p.pr.key == reviewed.pr.key else p for p in pulls]
        found.append(stacks.explicit(reviewed, pulls))
    elif request.discover:
        if stack := stacks.discover(reviewed):
            found.append(stack)
    related: list[Resolved] = []
    for ref in request.related:
        pull = _resolve_ref(ref, reviewed.pr)
        if pull.pr.key == reviewed.pr.key:
            raise EigenaugenError(f"--related names the reviewed PR {pull.pr.key}")
        related.append(pull)
    related_stacks = []
    if request.discover:
        related_stacks = [s for pull in related if (s := stacks.discover(pull))]
    pulls_by_key: dict[str, tuple[Resolved, Role]] = {}

    def add(pulls: list[Resolved], role: Role) -> None:
        # Added in ROLE_PRIORITY order: a PR keeps its first role and pins.
        for pull in pulls:
            pulls_by_key.setdefault(pull.pr.key, (pull, role))

    add([reviewed], Role.REVIEWED)
    for stack in found:
        add(_stack_members(stack), Role.STACK)
    add(related, Role.RELATED)
    for stack in related_stacks:
        add(_stack_members(stack), Role.RELATED_STACK)
    all_stacks = (*found, *related_stacks)
    for stack in all_stacks:
        this = next(m for m in stack.members if m.pull.pr.key == stack.anchor)
        on_progress(
            f"{stack.anchor}: {stack.describe()}; it is at position {this.position}"
        )
    return all_stacks, pulls_by_key


def _stack_info(stack: Stack) -> StackInfo:
    return StackInfo(
        anchor=stack.anchor,
        source=stack.source,
        number=stack.number,
        description=stack.describe(),
        entries=tuple(
            StackEntry(m.pull.pr.key, m.position, m.relation) for m in stack.members
        ),
    )


def _clones(
    reviewed: PullRequest, pulls: list[Resolved], ws: Workspace
) -> dict[str, GitRepo]:
    """One clone per repository, holding every pinned commit of its PRs.

    The reviewed repository's clone is the agent's checkout of the head.
    """
    by_repo: dict[str, list[PullRequest]] = {}
    for pull in pulls:
        by_repo.setdefault(pull.pr.repo_spec, []).append(pull.pr)
    repos = {}
    for spec, prs in by_repo.items():
        first = prs[0]
        dest = (
            ws.checkout
            if spec == reviewed.repo_spec
            else ws.repo_clone(first.host, first.owner, first.repo)
        )
        repos[spec] = GitRepo.clone(spec, dest)
    return repos


def _skim(repo: GitRepo, diff: str, merge_base: str) -> dict[str, str]:
    """Skimmed files of DIFF. Attributes come from the merge base, so a PR
    cannot mark its own files as generated or vendored."""
    paths = [primary_path(section) for section in split_sections(diff)]
    attributes = repo.attributes(merge_base, paths, SKIM_ATTRIBUTES)
    return {path: str(reason) for path, reason in classify(diff, attributes).items()}


def prepare(
    reviewed: Resolved,
    ws: Workspace,
    request: Request,
    on_progress: ProgressCallback,
) -> Prepared:
    """Gather the review's PRs into WS and write the review context."""
    found, pulls_by_key = gather(reviewed, request, on_progress)
    pulls = [pull for pull, _ in pulls_by_key.values()]
    comparisons = {pull.pr.key: github.compare(pull.pr) for pull in pulls}
    on_progress(f"cloning {len({p.pr.repo_spec for p in pulls})} repositories")
    repos = _clones(reviewed.pr, pulls, ws)
    for spec, repo in repos.items():
        repo.fetch(
            sha
            for pull in pulls
            if pull.pr.repo_spec == spec
            for sha in (
                pull.pr.head_sha,
                comparisons[pull.pr.key].merge_base_sha,
                pull.pr.base_sha,
            )
        )
    repos[reviewed.pr.repo_spec].checkout(reviewed.pr.head_sha)
    diffs_dir = ws.data / DIFFS_DIRNAME
    diffs_dir.mkdir()
    context_pulls = {}
    reviewed_diff = reviewed_skim = None
    for number, (key, (pull, role)) in enumerate(pulls_by_key.items()):
        repo = repos[pull.pr.repo_spec]
        comparison = comparisons[key]
        on_progress(f"computing the diff of {key}")
        diff = repo.diff(comparison.merge_base_sha, pull.pr.head_sha)
        diff_file = diffs_dir / f"{number}{DIFF_SUFFIX}"
        diff_file.write_text(diff, encoding=ENCODING)
        skim = _skim(repo, diff, comparison.merge_base_sha)
        if role == Role.REVIEWED:
            reviewed_diff, reviewed_skim = diff, skim
        context_pulls[key] = ContextPull(
            key=key,
            url=pull.pr.url,
            base_sha=pull.pr.base_sha,
            head_sha=pull.pr.head_sha,
            merge_base_sha=comparison.merge_base_sha,
            behind_by=comparison.behind_by,
            role=role,
            state=pull.state,
            changed_files=pull.changed_files,
            additions=pull.additions,
            deletions=pull.deletions,
            repo_dir=str(repo.path),
            diff_file=str(diff_file),
            skim=skim,
        )
    context = ReviewContext(
        reviewed=reviewed.pr.key,
        pulls=context_pulls,
        stacks=tuple(_stack_info(stack) for stack in found),
        coverage_file=str(ws.data / COVERAGE_FILENAME),
    )
    context_file = ws.data / CONTEXT_FILENAME
    context.save(context_file)
    plan = DiffPlan.build(reviewed_diff or "", reviewed_skim or {})
    return Prepared(context, context_file, plan, found)
