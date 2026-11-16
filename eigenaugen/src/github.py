"""GitHub access through the gh CLI, pinned to exact commits.

gh reference: https://cli.github.com/manual/
REST reference: https://docs.github.com/en/rest
"""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from eigenaugen.src import proc
from eigenaugen.src.constants import GH_BIN
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
# A PR named in --stack or --related: [HOST/]OWNER/REPO#NUMBER.
REPO_PR_RE = re.compile(
    r"(?P<repo>(?:[^/\s#]+/)?[^/\s#]+/[^/\s#]+)#(?P<number>[1-9][0-9]*)"
)
REF_USAGE = "expected NUMBER, a pull request URL, or OWNER/REPO#NUMBER"
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
    "state",
    "baseRefName",
    "headRefName",
    "isCrossRepository",
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


class HttpMethod(StrEnum):
    POST = "POST"


class PullState(StrEnum):
    # https://docs.github.com/en/graphql/reference/enums#pullrequeststate
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MERGED = "MERGED"


# https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request
REVIEW_EVENT_COMMENT = "COMMENT"
STDIN_PATH = "-"
# Compare responses list commits; one is enough, the merge base comes anyway.
# https://docs.github.com/en/rest/commits/commits#compare-two-commits
COMPARE_QUERY = "?per_page=1"
COMPARE_JQ = "{merge_base: .merge_base_commit.sha, behind_by: .behind_by}"
GRAPHQL_ENDPOINT = "graphql"


def pr_url(host: str, owner: str, repo: str, number: int | str) -> str:
    return f"https://{host}/{owner}/{repo}/pull/{number}"


@dataclass(frozen=True)
class PullTarget:
    """What to look up with `gh pr view`: a number or URL, and the repo
    (None: the current directory's repo)."""

    ref: str
    repo: str | None = None


def parse_ref(text: str) -> PullTarget:
    """Interpret a PR named in --stack or --related.

    * NUMBER: a PR of the reviewed PR's repository (repo None)
    * URL: a PR URL, as accepted for TARGET
    * [HOST/]OWNER/REPO#NUMBER
    """
    if PR_NUMBER_RE.fullmatch(text):
        return PullTarget(text)
    if match := PASTED_PR_URL_RE.fullmatch(text):
        return PullTarget(pr_url(*match.group("host", "owner", "repo", "number")))
    if match := REPO_PR_RE.fullmatch(text):
        return PullTarget(match["number"], match["repo"])
    raise EigenaugenError(f"{REF_USAGE}; got {text!r}")


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

    @property
    def key(self) -> str:
        """How tools and reports name the PR, e.g. `octo/repo#7`."""
        return f"{self.owner}/{self.repo}#{self.number}"

    def same_repo(self, other: "PullRequest") -> bool:
        return self.repo_spec == other.repo_spec


@dataclass(frozen=True)
class Resolved:
    """A pinned PR plus the figures and refs known when it was looked up."""

    pr: PullRequest
    changed_files: int
    additions: int
    deletions: int
    state: str = PullState.OPEN
    base_ref: str = ""
    head_ref: str = ""
    # Head branch in a fork: no PR in the base repository can stack on it.
    cross_repo: bool = False

    @classmethod
    def from_json(cls, data: dict) -> "Resolved":
        """From `gh pr view --json RESOLVE_FIELDS` output or a GraphQL
        PullRequest node with the same fields."""
        try:
            pr = PullRequest.from_url(
                data["url"], data["baseRefOid"], data["headRefOid"]
            )
            return cls(
                pr,
                data["changedFiles"],
                data["additions"],
                data["deletions"],
                data["state"],
                data["baseRefName"],
                data["headRefName"],
                data["isCrossRepository"],
            )
        except KeyError as exc:
            raise EigenaugenError(f"pull request data lacks field {exc}") from exc


@dataclass(frozen=True)
class Comparison:
    """Where a PR branched off its base."""

    merge_base_sha: str
    # Base commits the head does not contain yet.
    behind_by: int


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
    return Resolved.from_json(_view(target.ref, target.repo, RESOLVE_FIELDS))


def compare(pr: PullRequest) -> Comparison:
    """Merge base of the pinned commits: the diff starts there, as GitHub's
    three-dot compare and "Files changed" do."""
    endpoint = f"{pr.api_root}/compare/{pr.base_sha}...{pr.head_sha}{COMPARE_QUERY}"
    data = _parse_json(_api(pr, endpoint, "--jq", COMPARE_JQ), "gh api compare")
    sha = data.get("merge_base")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise EigenaugenError(f"{pr.key}: compare returned no merge base: {data}")
    return Comparison(sha, int(data.get("behind_by") or 0))


def graphql(host: str, query: str, **variables: str | int) -> dict:
    """Run a GraphQL QUERY on HOST; returns its `data`.

    gh sends `-F` values typed (integers stay integers) and fails when the
    response holds errors.
    https://cli.github.com/manual/gh_api
    """
    argv = [GH_BIN, "api", GRAPHQL_ENDPOINT, "--hostname", host, "-f", f"query={query}"]
    for name, value in variables.items():
        flag = "-F" if isinstance(value, int) else "-f"
        argv += [flag, f"{name}={value}"]
    data = _parse_json(proc.run(argv), "gh api graphql")
    return data.get("data") or {}


def metadata(pr: PullRequest) -> dict:
    """Live PR metadata plus the commits this review is pinned to."""
    data = _view(pr.url, None, METADATA_FIELDS)
    data[PINNED_KEY] = {"baseRefOid": pr.base_sha, "headRefOid": pr.head_sha}
    return data


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
