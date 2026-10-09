"""GitHub access through the gh CLI, pinned to exact commits.

gh reference: https://cli.github.com/manual/
REST reference: https://docs.github.com/en/rest
"""

import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import quote

from eigenaugen.src import proc
from eigenaugen.src.constants import GH_BIN, GIT_BIN
from eigenaugen.src.errors import EigenaugenError

# All patterns are matched with fullmatch: `$` would also accept a trailing
# newline.
PR_URL_PARTS = (
    r"https://(?P<host>[^/\s?#]+)/(?P<owner>[^/\s?#]+)/(?P<repo>[^/\s?#]+)"
    r"/pull/(?P<number>[0-9]+)"
)
# Canonical PR URL, as gh reports it.
PR_URL_RE = re.compile(PR_URL_PARTS + "/?")
# PR tabs a pasted browser URL may end in; `files` is the older name of
# `changes`.
PR_TABS = ("changes", "files")
# A PR URL as copied from a browser: optional tab, query, or fragment.
PASTED_PR_URL_RE = re.compile(
    PR_URL_PARTS + rf"(?:/(?:{'|'.join(PR_TABS)}))?/?(?:[?#]\S*)?"
)
# [HOST/]OWNER/REPO, the form `gh --repo` takes.
REPO_RE = re.compile(r"(?:[^/\s]+/)?[^/\s]+/[^/\s]+")
PR_NUMBER_RE = re.compile(r"[1-9][0-9]*")
TARGET_USAGE = "expected NUMBER, a pull request URL, or OWNER/REPO NUMBER"
# Full SHA-1 (40 hex) or SHA-256 (64 hex) object name.
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

# `gh pr view --json` fields; see `gh pr view --help`.
RESOLVE_FIELDS = (
    "url",
    "baseRefOid",
    "headRefOid",
    "changedFiles",
    "additions",
    "deletions",
)
METADATA_FIELDS = (
    "number",
    "url",
    "title",
    "body",
    "author",
    "state",
    "isDraft",
    "labels",
    "baseRefName",
    "baseRefOid",
    "headRefName",
    "headRefOid",
    "additions",
    "deletions",
    "changedFiles",
    "files",
)
PINNED_KEY = "pinned"


class MediaType(StrEnum):
    # https://docs.github.com/en/rest/using-the-rest-api/media-types
    DIFF = "application/vnd.github.diff"
    RAW = "application/vnd.github.raw+json"


class HttpMethod(StrEnum):
    POST = "POST"


# https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request
REVIEW_EVENT_COMMENT = "COMMENT"
STDIN_PATH = "-"

# Shallow, blob-less clone: one commit's trees up front; file contents are
# fetched lazily by checkout.
CLONE_FLAGS = ("--depth=1", "--filter=blob:none", "--no-checkout", "--quiet")
FETCH_FLAGS = ("--quiet", "--depth=1")
REMOTE = "origin"
GIT_ENV_OVERRIDES = {
    # Reviews read source; skip downloading Git LFS objects.
    "GIT_LFS_SKIP_SMUDGE": "1",
    # Fail instead of hanging on a credential prompt.
    "GIT_TERMINAL_PROMPT": "0",
}
PATH_SEPARATOR = "/"
FORBIDDEN_PATH_SEGMENTS = ("", ".", "..")


def pr_url(host: str, owner: str, repo: str, number: int | str) -> str:
    return f"https://{host}/{owner}/{repo}/pull/{number}"


@dataclass(frozen=True)
class PullTarget:
    """What to look up with `gh pr view`: a number or URL, and the repo
    (None: the current directory's repo)."""

    ref: str
    repo: str | None = None


def parse_target(target: str, number: str | None = None) -> PullTarget:
    """Interpret the `review` arguments.

    * NUMBER: a PR of the current directory's repository
    * URL: a PR URL, optionally ending in /changes or /files, a query, or a
      fragment, as copied from a browser
    * OWNER/REPO NUMBER (also HOST/OWNER/REPO NUMBER)
    """
    if number is None:
        if PR_NUMBER_RE.fullmatch(target):
            return PullTarget(target)
        if match := PASTED_PR_URL_RE.fullmatch(target):
            return PullTarget(pr_url(*match.group("host", "owner", "repo", "number")))
    elif REPO_RE.fullmatch(target) and PR_NUMBER_RE.fullmatch(number):
        return PullTarget(number, target)
    given = " ".join(arg for arg in (target, number) if arg is not None)
    raise EigenaugenError(f"{TARGET_USAGE}; got {given!r}")


@dataclass(frozen=True)
class PullRequest:
    """A pull request pinned to the base and head commits under review."""

    host: str
    owner: str
    repo: str
    number: int
    base_sha: str
    head_sha: str

    @classmethod
    def from_url(cls, url: str, base_sha: str, head_sha: str) -> "PullRequest":
        match = PR_URL_RE.fullmatch(url)
        if match is None:
            raise EigenaugenError(f"not a pull request URL: {url!r}")
        for sha in (base_sha, head_sha):
            if not SHA_RE.fullmatch(sha):
                raise EigenaugenError(f"not a full commit SHA: {sha!r}")
        return cls(
            host=match["host"],
            owner=match["owner"],
            repo=match["repo"],
            number=int(match["number"]),
            base_sha=base_sha,
            head_sha=head_sha,
        )

    @property
    def url(self) -> str:
        return pr_url(self.host, self.owner, self.repo, self.number)

    @property
    def repo_spec(self) -> str:
        """[HOST/]OWNER/REPO as gh expects it."""
        return f"{self.host}/{self.owner}/{self.repo}"

    @property
    def api_root(self) -> str:
        return f"repos/{self.owner}/{self.repo}"


@dataclass(frozen=True)
class Resolved:
    """A pinned PR plus the size figures shown before the review starts."""

    pr: PullRequest
    changed_files: int
    additions: int
    deletions: int


def _parse_json(text: str, source: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise EigenaugenError(f"{source} returned invalid JSON: {exc}") from exc


def _view(ref: str, repo: str | None, fields: tuple[str, ...]) -> dict:
    argv = [GH_BIN, "pr", "view", ref]
    if repo:
        argv += ["--repo", repo]
    argv += ["--json", ",".join(fields)]
    return _parse_json(proc.run(argv), "gh pr view")


def _api(pr: PullRequest, endpoint: str, *flags: str, **kwargs: Any) -> str:
    return proc.run([GH_BIN, "api", "--hostname", pr.host, *flags, endpoint], **kwargs)


def resolve(target: PullTarget) -> Resolved:
    """Look up the PR and pin it to its current base and head commits."""
    data = _view(target.ref, target.repo, RESOLVE_FIELDS)
    try:
        pr = PullRequest.from_url(data["url"], data["baseRefOid"], data["headRefOid"])
        return Resolved(pr, data["changedFiles"], data["additions"], data["deletions"])
    except KeyError as exc:
        raise EigenaugenError(f"gh pr view output lacks field {exc}") from exc


def metadata(pr: PullRequest) -> dict:
    """Live PR metadata plus the commits this review is pinned to."""
    data = _view(pr.url, None, METADATA_FIELDS)
    data[PINNED_KEY] = {"baseRefOid": pr.base_sha, "headRefOid": pr.head_sha}
    return data


def diff(pr: PullRequest) -> str:
    """Unified diff of the PR between its pinned commits.

    A three-dot compare diffs head against merge-base(base, head), the range
    GitHub shows under "Files changed", and later pushes cannot change it.
    https://docs.github.com/en/rest/commits/commits#compare-two-commits
    """
    endpoint = f"{pr.api_root}/compare/{pr.base_sha}...{pr.head_sha}"
    return _api(pr, endpoint, "--header", f"Accept: {MediaType.DIFF}")


def validate_repo_path(path: str) -> str:
    """Return PATH if it is a plain repository-relative file path.

    Rejects absolute paths, empty segments, and `.`/`..` segments so a path
    cannot climb out of the contents endpoint into other API routes.
    """
    segments = path.split(PATH_SEPARATOR)
    if any(seg in FORBIDDEN_PATH_SEGMENTS for seg in segments):
        raise EigenaugenError(f"invalid repository path: {path!r}")
    return path


def base_file(pr: PullRequest, path: str) -> str:
    """Contents of PATH at the pinned base commit.

    https://docs.github.com/en/rest/repos/contents#get-repository-content
    """
    quoted = quote(validate_repo_path(path), safe=PATH_SEPARATOR)
    endpoint = f"{pr.api_root}/contents/{quoted}?ref={pr.base_sha}"
    return _api(pr, endpoint, "--header", f"Accept: {MediaType.RAW}")


def checkout(pr: PullRequest, dest: Path) -> None:
    """Check out the pinned head commit into DEST (a new directory).

    Uses a fresh clone so the user's own clones are never touched. Fetching
    by SHA also works for PRs from forks: GitHub keeps their commits
    reachable from the base repository.
    """
    env = {**os.environ, **GIT_ENV_OVERRIDES}
    proc.run(
        [GH_BIN, "repo", "clone", pr.repo_spec, str(dest), "--", *CLONE_FLAGS],
        env=env,
    )
    git = [GIT_BIN, "-C", str(dest)]
    proc.run([*git, "fetch", *FETCH_FLAGS, REMOTE, pr.head_sha], env=env)
    proc.run([*git, "checkout", "--quiet", "--detach", pr.head_sha], env=env)


def post_review(pr: PullRequest, body: str) -> str:
    """Post BODY as a COMMENT review on the reviewed head commit.

    Returns the review's URL. Pinning commit_id keeps the review attached to
    the code it describes even if the author pushed in the meantime.
    """
    payload = json.dumps(
        {"commit_id": pr.head_sha, "event": REVIEW_EVENT_COMMENT, "body": body}
    )
    out = _api(
        pr,
        f"{pr.api_root}/pulls/{pr.number}/reviews",
        "--method",
        HttpMethod.POST,
        "--input",
        STDIN_PATH,
        input_text=payload,
    )
    return _parse_json(out, "gh api").get("html_url", pr.url)
