"""Find the stack a pull request belongs to.

Sources, in order:
1. GitHub stacked pull requests: the `stack` field of a GraphQL
   PullRequest, ordered by `position` (1 = closest to the base branch).
   https://docs.github.com/en/pull-requests/get-started/about-stacked-prs
2. Branch chain, as Graphite, git-town, spr, or hand-made stacks build
   it: each PR's base branch is the head branch of the PR below it.
3. Explicit: the PRs given with --stack, bottom first.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from eigenaugen.src import github, log
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import PullState, Resolved

# Upper bound on PRs taken into one stack; also bounds discovery queries.
MAX_STACK_SIZE = 50
# Open PRs fetched per branch lookup; more means an ambiguous chain.
MAX_BRANCH_MATCHES = 20
BOTTOM_POSITION = 1

# Same names as github.RESOLVE_FIELDS, so Resolved.from_json reads nodes.
PR_FIELDS = " ".join(github.RESOLVE_FIELDS)
GITHUB_STACK_QUERY = f"""
query($owner: String!, $name: String!, $number: Int!) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      stack {{
        number
        entries(first: {MAX_STACK_SIZE}) {{
          totalCount
          nodes {{ position pullRequest {{ {PR_FIELDS} }} }}
        }}
      }}
    }}
  }}
}}
"""
# Open PRs whose head branch is BRANCH: candidates for the PR below.
BELOW_QUERY = f"""
query($owner: String!, $name: String!, $branch: String!) {{
  repository(owner: $owner, name: $name) {{
    defaultBranchRef {{ name }}
    pullRequests(headRefName: $branch, states: [OPEN], first: {MAX_BRANCH_MATCHES}) {{
      nodes {{ {PR_FIELDS} }}
    }}
  }}
}}
"""
# Open PRs whose base branch is BRANCH: the PRs stacked on top.
ABOVE_QUERY = f"""
query($owner: String!, $name: String!, $branch: String!) {{
  repository(owner: $owner, name: $name) {{
    pullRequests(baseRefName: $branch, states: [OPEN], first: {MAX_BRANCH_MATCHES}) {{
      nodes {{ {PR_FIELDS} }}
    }}
  }}
}}
"""


class StackSource(StrEnum):
    GITHUB = "github"
    BRANCHES = "branches"
    EXPLICIT = "explicit"


class Relation(StrEnum):
    """Position relative to the stack's anchor PR."""

    BELOW = "below"
    THIS = "this"
    ABOVE = "above"


@dataclass(frozen=True)
class StackMember:
    pull: Resolved
    # 1 = bottom. In a branch-chain tree, siblings share a position.
    position: int
    relation: Relation


@dataclass(frozen=True)
class Stack:
    source: StackSource
    # Key of the PR the stack was looked up for.
    anchor: str
    # GitHub's stack number; None for other sources.
    number: int | None
    members: tuple[StackMember, ...]

    def describe(self) -> str:
        """E.g. `github stack #577, 3 PRs` or `branch chain, 2 PRs`."""
        size = f"{len(self.members)} PRs"
        match self.source:
            case StackSource.GITHUB:
                return f"GitHub stack #{self.number}, {size}"
            case StackSource.BRANCHES:
                return f"branch chain, {size}"
            case _:
                return f"--stack, {size}"


def _relation(position: int, anchor_position: int) -> Relation:
    if position < anchor_position:
        return Relation.BELOW
    if position > anchor_position:
        return Relation.ABOVE
    return Relation.THIS


def _repo_vars(anchor: Resolved) -> dict[str, str]:
    return {"owner": anchor.pr.owner, "name": anchor.pr.repo}


def _github_stack(anchor: Resolved) -> Stack | None:
    data = github.graphql(
        anchor.pr.host,
        GITHUB_STACK_QUERY,
        **_repo_vars(anchor),
        number=anchor.pr.number,
    )
    stack = ((data.get("repository") or {}).get("pullRequest") or {}).get("stack")
    if not stack:
        return None
    entries = stack["entries"]
    if entries["totalCount"] > len(entries["nodes"]):
        log.warn(
            f"{anchor.pr.key}: GitHub stack #{stack['number']} has "
            f"{entries['totalCount']} PRs; using the first {len(entries['nodes'])}"
        )
    found = sorted(
        (node["position"], Resolved.from_json(node["pullRequest"]))
        for node in entries["nodes"]
        if node.get("pullRequest")
    )
    positions = {pull.pr.key: position for position, pull in found}
    anchor_position = positions.get(anchor.pr.key)
    if anchor_position is None:
        return None
    # Merged and closed PRs no longer change anything; keep the anchor.
    members = tuple(
        StackMember(pull, position, _relation(position, anchor_position))
        for position, pull in found
        if pull.state == PullState.OPEN or pull.pr.key == anchor.pr.key
    )
    return Stack(StackSource.GITHUB, anchor.pr.key, stack["number"], members)


def _same_repo_heads(nodes: list[dict]) -> list[Resolved]:
    """Only a branch of the base repository can be another PR's base."""
    return [Resolved.from_json(n) for n in nodes if not n["isCrossRepository"]]


def _below(anchor: Resolved, seen: set[str]) -> list[Resolved]:
    """PRs below ANCHOR, bottom first."""
    below: list[Resolved] = []
    current = anchor
    while len(seen) < MAX_STACK_SIZE:
        data = github.graphql(
            anchor.pr.host, BELOW_QUERY, **_repo_vars(anchor), branch=current.base_ref
        )
        repository = data.get("repository") or {}
        default = (repository.get("defaultBranchRef") or {}).get("name")
        if current.base_ref == default:
            break
        nodes = (repository.get("pullRequests") or {}).get("nodes") or []
        parents = _same_repo_heads(nodes)
        # None: the bottom. Several: no single chain to follow.
        if len(parents) != 1 or parents[0].pr.key in seen:
            break
        current = parents[0]
        seen.add(current.pr.key)
        below.insert(0, current)
    return below


def _above(anchor: Resolved, seen: set[str]) -> list[tuple[Resolved, int]]:
    """PRs stacked on ANCHOR, directly or transitively, with their distance."""
    above: list[tuple[Resolved, int]] = []
    frontier = [(anchor, 0)]
    while frontier and len(seen) < MAX_STACK_SIZE:
        node, depth = frontier.pop(0)
        if node.cross_repo:
            continue
        data = github.graphql(
            anchor.pr.host, ABOVE_QUERY, **_repo_vars(anchor), branch=node.head_ref
        )
        nodes = ((data.get("repository") or {}).get("pullRequests") or {}).get(
            "nodes"
        ) or []
        for child in (Resolved.from_json(n) for n in nodes):
            if child.pr.key in seen or len(seen) >= MAX_STACK_SIZE:
                continue
            seen.add(child.pr.key)
            above.append((child, depth + 1))
            frontier.append((child, depth + 1))
    return above


def _branch_stack(anchor: Resolved) -> Stack:
    seen = {anchor.pr.key}
    below = _below(anchor, seen)
    above = _above(anchor, seen)
    anchor_position = len(below) + BOTTOM_POSITION
    members = [
        StackMember(pull, position, Relation.BELOW)
        for position, pull in enumerate(below, BOTTOM_POSITION)
    ]
    members.append(StackMember(anchor, anchor_position, Relation.THIS))
    members += [
        StackMember(pull, anchor_position + depth, Relation.ABOVE)
        for pull, depth in above
    ]
    return Stack(StackSource.BRANCHES, anchor.pr.key, None, tuple(members))


def discover(anchor: Resolved) -> Stack | None:
    """ANCHOR's stack, or None when it stands alone."""
    try:
        stack = _github_stack(anchor)
    except EigenaugenError as exc:
        # Older GitHub Enterprise servers lack the field.
        log.warn(f"{anchor.pr.key}: no GitHub stack data ({exc}); trying branches")
        stack = None
    if stack is None or len(stack.members) < 2:
        stack = _branch_stack(anchor)
    return stack if len(stack.members) > 1 else None


def explicit(anchor: Resolved, pulls: Sequence[Resolved]) -> Stack:
    """The stack given with --stack: PULLS bottom first, ANCHOR among them."""
    keys = [pull.pr.key for pull in pulls]
    if anchor.pr.key not in keys:
        raise EigenaugenError(
            f"--stack must list the reviewed PR {anchor.pr.key} too, in stack order"
        )
    if len(set(keys)) != len(keys):
        raise EigenaugenError("--stack lists a PR twice")
    for pull in pulls:
        if not pull.pr.same_repo(anchor.pr):
            raise EigenaugenError(
                f"--stack PR {pull.pr.key} is not in {anchor.pr.repo_spec}; "
                "name PRs of other repositories with --related"
            )
    anchor_position = keys.index(anchor.pr.key) + BOTTOM_POSITION
    members = tuple(
        StackMember(pull, position, _relation(position, anchor_position))
        for position, pull in enumerate(pulls, BOTTOM_POSITION)
    )
    return Stack(StackSource.EXPLICIT, anchor.pr.key, None, members)
