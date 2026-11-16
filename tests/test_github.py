import json

import pytest

from eigenaugen.src import github
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import PullRequest, PullTarget

BASE = "a" * 40
HEAD = "b" * 40
URL = "https://github.com/octo/repo/pull/7"


def test_from_url_parses_parts():
    pr = PullRequest.from_url(URL, BASE, HEAD)
    assert (pr.host, pr.owner, pr.repo, pr.number) == ("github.com", "octo", "repo", 7)
    assert pr.url == URL
    assert pr.repo_spec == "github.com/octo/repo"
    assert pr.api_root == "repos/octo/repo"


def test_from_url_accepts_enterprise_host_and_sha256():
    pr = PullRequest.from_url("https://ghe.corp/o/r/pull/1/", "c" * 64, HEAD)
    assert pr.host == "ghe.corp"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r/pull/1",
        "https://github.com/o/r/issues/1",
        "https://github.com/o/r/pull/x",
        "https://github.com/o/r/pull/1?x=y",
        "https://github.com/o/r/pull/1\n",
    ],
)
def test_from_url_rejects_non_pr_urls(url):
    with pytest.raises(EigenaugenError):
        PullRequest.from_url(url, BASE, HEAD)


@pytest.mark.parametrize("sha", ["abc", "g" * 40, "A" * 40, BASE + "\n"])
def test_from_url_rejects_bad_shas(sha):
    with pytest.raises(EigenaugenError):
        PullRequest.from_url(URL, sha, HEAD)


def test_resolve_pins_commits(monkeypatch):
    data = {
        "url": URL,
        "baseRefOid": BASE,
        "headRefOid": HEAD,
        "changedFiles": 2,
        "additions": 3,
        "deletions": 4,
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "feat",
        "isCrossRepository": False,
    }
    calls = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        return json.dumps(data)

    monkeypatch.setattr(github.proc, "run", fake_run)
    resolved = github.resolve(PullTarget("7", "octo/repo"))
    assert resolved.pr.head_sha == HEAD
    assert (resolved.changed_files, resolved.additions, resolved.deletions) == (2, 3, 4)
    assert (resolved.base_ref, resolved.head_ref, resolved.cross_repo) == (
        "main",
        "feat",
        False,
    )
    assert calls[0][:6] == ["gh", "pr", "view", "7", "--repo", "octo/repo"]
    assert "isCrossRepository" in calls[0][-1]


def test_key_and_same_repo():
    pr = PullRequest.from_url(URL, BASE, HEAD)
    assert pr.key == "octo/repo#7"
    assert pr.same_repo(PullRequest.from_url(URL.replace("/7", "/8"), BASE, HEAD))
    other = PullRequest.from_url("https://github.com/octo/other/pull/7", BASE, HEAD)
    assert not pr.same_repo(other)


def test_compare_returns_merge_base(monkeypatch):
    calls = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        return json.dumps({"merge_base": "c" * 40, "behind_by": 3})

    monkeypatch.setattr(github.proc, "run", fake_run)
    comparison = github.compare(PullRequest.from_url(URL, BASE, HEAD))
    assert comparison == github.Comparison("c" * 40, 3)
    assert calls[0][-1] == f"repos/octo/repo/compare/{BASE}...{HEAD}?per_page=1"
    assert "--jq" in calls[0]


def test_compare_rejects_missing_merge_base(monkeypatch):
    monkeypatch.setattr(github.proc, "run", lambda argv, **kw: '{"merge_base": null}')
    with pytest.raises(EigenaugenError, match="no merge base"):
        github.compare(PullRequest.from_url(URL, BASE, HEAD))


def test_graphql_types_variables(monkeypatch):
    calls = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        return json.dumps({"data": {"repository": {}}})

    monkeypatch.setattr(github.proc, "run", fake_run)
    data = github.graphql("ghe.corp", "query { x }", owner="o", number=7)
    assert data == {"repository": {}}
    argv = calls[0]
    assert argv[:5] == ["gh", "api", "graphql", "--hostname", "ghe.corp"]
    assert argv[5:] == ["-f", "query=query { x }", "-f", "owner=o", "-F", "number=7"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("7", PullTarget("7")),
        (URL + "/changes", PullTarget(URL)),
        ("octo/other#12", PullTarget("12", "octo/other")),
        ("ghe.corp/octo/other#12", PullTarget("12", "ghe.corp/octo/other")),
    ],
)
def test_parse_ref_accepts(text, expected):
    assert github.parse_ref(text) == expected


@pytest.mark.parametrize(
    "text", ["", "0", "octo/other", "octo#1", "octo/other#0", "#3"]
)
def test_parse_ref_rejects(text):
    with pytest.raises(EigenaugenError, match="OWNER/REPO#NUMBER"):
        github.parse_ref(text)


def test_resolve_reports_missing_field(monkeypatch):
    monkeypatch.setattr(github.proc, "run", lambda argv, **kw: json.dumps({"url": URL}))
    with pytest.raises(EigenaugenError, match="lacks field"):
        github.resolve(PullTarget("7"))


def test_resolve_reports_invalid_json(monkeypatch):
    monkeypatch.setattr(github.proc, "run", lambda argv, **kw: "<html>")
    with pytest.raises(EigenaugenError, match="invalid JSON"):
        github.resolve(PullTarget("7"))


def test_post_review_pins_commit(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["input"] = argv, kw["input_text"]
        return json.dumps({"html_url": "https://github.com/octo/repo/pull/7#r1"})

    monkeypatch.setattr(github.proc, "run", fake_run)
    url = github.post_review(PullRequest.from_url(URL, BASE, HEAD), "body")
    assert url.endswith("#r1")
    assert json.loads(seen["input"]) == {
        "commit_id": HEAD,
        "event": "COMMENT",
        "body": "body",
    }
    assert seen["argv"][-1] == "repos/octo/repo/pulls/7/reviews"


@pytest.mark.parametrize(
    "args,expected",
    [
        (("123",), PullTarget("123")),
        ((URL,), PullTarget(URL)),
        ((URL + "/",), PullTarget(URL)),
        ((URL + "/changes",), PullTarget(URL)),
        ((URL + "/changes/",), PullTarget(URL)),
        ((URL + "/files",), PullTarget(URL)),
        ((URL + "/changes#diff-abc123",), PullTarget(URL)),
        ((URL + "?w=1",), PullTarget(URL)),
        (
            ("https://ghe.corp/o/r/pull/3/changes",),
            PullTarget("https://ghe.corp/o/r/pull/3"),
        ),
        (("octo/repo", "7"), PullTarget("7", "octo/repo")),
        (("ghe.corp/octo/repo", "7"), PullTarget("7", "ghe.corp/octo/repo")),
    ],
)
def test_parse_target_accepts(args, expected):
    assert github.parse_target(*args) == expected


@pytest.mark.parametrize(
    "args",
    [
        ("0",),
        ("-1",),
        ("12a",),
        ("octo/repo",),
        ("octo", "7"),
        ("octo/repo", "x"),
        ("123", "456"),
        (URL, "7"),
        (URL + "/commits",),
        (URL + "/changesx",),
        ("http://github.com/octo/repo/pull/7",),
        ("https://github.com/octo/repo/issues/7",),
        (URL + "\n",),
    ],
)
def test_parse_target_rejects(args):
    with pytest.raises(EigenaugenError, match="expected NUMBER"):
        github.parse_target(*args)
