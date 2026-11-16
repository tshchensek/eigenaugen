"""What one review covers: the reviewed PR and its context PRs, pinned.

The CLI writes it as JSON into the workspace's data directory; the MCP
server reads it. Context PRs are the reviewed PR's stack, related PRs
named with --related, and their stacks.
"""

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import PullRequest
from eigenaugen.src.proc import DECODE_ERRORS, ENCODING

JSON_INDENT = 1


class Role(StrEnum):
    REVIEWED = "reviewed"
    # In the reviewed PR's stack.
    STACK = "stack"
    # Named with --related.
    RELATED = "related"
    # In a related PR's stack.
    RELATED_STACK = "related-stack"


# When a PR is found in several roles, the first one listed here wins.
ROLE_PRIORITY = (Role.REVIEWED, Role.STACK, Role.RELATED, Role.RELATED_STACK)


@dataclass(frozen=True)
class ContextPull:
    key: str
    url: str
    base_sha: str
    head_sha: str
    merge_base_sha: str
    # Base commits the head does not contain.
    behind_by: int
    role: Role
    state: str
    changed_files: int
    additions: int
    deletions: int
    # Local clone holding all pinned commits of this PR.
    repo_dir: str
    # The PR's diff, merge base to head.
    diff_file: str
    # Primary path -> diffplan.SkimReason, for skimmed files.
    skim: dict[str, str] = field(default_factory=dict)

    @property
    def pr(self) -> PullRequest:
        return PullRequest.from_url(self.url, self.base_sha, self.head_sha)

    def diff(self) -> str:
        return Path(self.diff_file).read_text(encoding=ENCODING, errors=DECODE_ERRORS)


@dataclass(frozen=True)
class StackEntry:
    key: str
    position: int
    # stacks.Relation to the stack's anchor.
    relation: str


@dataclass(frozen=True)
class StackInfo:
    # Key of the PR the stack was found for.
    anchor: str
    # stacks.StackSource
    source: str
    number: int | None
    description: str
    entries: tuple[StackEntry, ...]


@dataclass(frozen=True)
class ReviewContext:
    reviewed: str
    # Key -> PR, the reviewed PR first.
    pulls: dict[str, ContextPull]
    stacks: tuple[StackInfo, ...]
    # Where the MCP server records which diff pages it served.
    coverage_file: str

    @property
    def reviewed_pull(self) -> ContextPull:
        return self.pulls[self.reviewed]

    def pull(self, key: str | None) -> ContextPull:
        """The PR named KEY; the reviewed PR when KEY is None."""
        if key is None:
            return self.reviewed_pull
        if key not in self.pulls:
            known = ", ".join(self.pulls)
            raise EigenaugenError(f"unknown PR {key!r}; known PRs: {known}")
        return self.pulls[key]

    def stack_of(self, key: str) -> StackInfo | None:
        return next((s for s in self.stacks if s.anchor == key), None)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=JSON_INDENT), encoding=ENCODING)

    @classmethod
    def load(cls, path: Path) -> "ReviewContext":
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding=ENCODING))
            return cls(
                reviewed=data["reviewed"],
                pulls={
                    key: ContextPull(**{**pull, "role": Role(pull["role"])})
                    for key, pull in data["pulls"].items()
                },
                stacks=tuple(
                    StackInfo(
                        **{
                            **stack,
                            "entries": tuple(
                                StackEntry(**entry) for entry in stack["entries"]
                            ),
                        }
                    )
                    for stack in data["stacks"]
                ),
                coverage_file=data["coverage_file"],
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise EigenaugenError(f"{path}: unreadable review context: {exc}") from exc
