"""Local clones for reading pinned commits: checkout, diffs, files,
attributes. No GitHub API limits apply: diffs and files of any size.

git reference: https://git-scm.com/docs
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from eigenaugen.src import proc
from eigenaugen.src.constants import GH_BIN, GIT_BIN
from eigenaugen.src.errors import EigenaugenError

# Shallow, blob-less clone without a checkout: commits and trees of one
# commit up front; file contents are fetched lazily, in batches, by
# checkout and diff.
CLONE_FLAGS = ("--depth=1", "--filter=blob:none", "--no-checkout", "--quiet")
FETCH_FLAGS = ("--quiet", "--depth=1")
REMOTE = "origin"
GIT_ENV_OVERRIDES = {
    # Reviews read source; skip downloading Git LFS objects.
    "GIT_LFS_SKIP_SMUDGE": "1",
    # Fail instead of hanging on a credential prompt.
    "GIT_TERMINAL_PROMPT": "0",
}
# Settings from the user's git config that would change what git prints:
# octal-escaped non-ASCII paths, attributes from a global attributes file.
CONFIG_OVERRIDES = (
    "-c",
    "core.quotePath=false",
    "-c",
    f"core.attributesFile={os.devnull}",
)
# A diff in the format diffs.py parses, whatever the user's git config says
# about colors, prefixes, external diff tools, or textconv filters.
DIFF_FLAGS = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-relative",
    "--find-renames",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)
NUL = "\0"
# `git check-attr -z` prints PATH NUL ATTRIBUTE NUL VALUE NUL per pair.
CHECK_ATTR_FIELDS = 3
PATH_SEPARATOR = "/"
FORBIDDEN_PATH_SEGMENTS = ("", ".", "..")


def validate_repo_path(path: str) -> str:
    """Return PATH if it is a plain repository-relative file path.

    Rejects absolute paths, empty segments, and `.`/`..` segments, so a
    path names a file inside the commit and nothing else.
    """
    segments = path.split(PATH_SEPARATOR)
    if any(seg in FORBIDDEN_PATH_SEGMENTS for seg in segments):
        raise EigenaugenError(f"invalid repository path: {path!r}")
    return path


def _env() -> dict[str, str]:
    return {**os.environ, **GIT_ENV_OVERRIDES}


@dataclass(frozen=True)
class GitRepo:
    path: Path

    def _git(self, *args: str, input_text: str | None = None) -> str:
        argv = [GIT_BIN, "-C", str(self.path), *CONFIG_OVERRIDES, *args]
        return proc.run(argv, env=_env(), input_text=input_text)

    @classmethod
    def clone(cls, repo_spec: str, dest: Path) -> "GitRepo":
        """A fresh clone of REPO_SPEC ([HOST/]OWNER/REPO) in DEST, so the
        user's own clones are never touched."""
        proc.run(
            [GH_BIN, "repo", "clone", repo_spec, str(dest), "--", *CLONE_FLAGS],
            env=_env(),
        )
        return cls(dest)

    def fetch(self, shas: Iterable[str]) -> None:
        """Fetch commits by SHA. Works for PRs from forks too: GitHub keeps
        their commits reachable from the base repository."""
        self._git("fetch", *FETCH_FLAGS, REMOTE, *dict.fromkeys(shas))

    def checkout(self, sha: str) -> None:
        self._git("checkout", "--quiet", "--detach", sha)

    def diff(self, old: str, new: str) -> str:
        """Unified diff from commit OLD to commit NEW."""
        return self._git("diff", *DIFF_FLAGS, old, new)

    def file(self, sha: str, path: str) -> str:
        """Contents of PATH at commit SHA."""
        return self._git("cat-file", "blob", f"{sha}:{validate_repo_path(path)}")

    def attributes(
        self, sha: str, paths: Iterable[str], names: Iterable[str]
    ) -> dict[str, dict[str, str]]:
        """Path -> {attribute: value} for NAMES, read from the
        .gitattributes files of commit SHA."""
        listed = list(dict.fromkeys(paths))
        if not listed:
            return {}
        out = self._git(
            "check-attr",
            "-z",
            "--stdin",
            "--source",
            sha,
            *names,
            input_text=NUL.join(listed) + NUL,
        )
        fields = out.split(NUL)
        result: dict[str, dict[str, str]] = {}
        for start in range(0, len(fields) - CHECK_ATTR_FIELDS + 1, CHECK_ATTR_FIELDS):
            path, name, value = fields[start : start + CHECK_ATTR_FIELDS]
            result.setdefault(path, {})[name] = value
        return result
