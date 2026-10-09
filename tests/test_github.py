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


@pytest.mark.parametrize("path", ["src/app.py", "a b/c.txt", ".github/x.yml"])
def test_validate_repo_path_accepts(path):
    assert github.validate_repo_path(path) == path


@pytest.mark.parametrize(
    "path", ["", "/etc/passwd", "a//b", "../x", "a/../../user", "a/./b", "dir/"]
)
def test_validate_repo_path_rejects(path):
    with pytest.raises(EigenaugenError):
        github.validate_repo_path(path)


def test_base_file_quotes_path(monkeypatch):
    calls = []
    monkeypatch.setattr(github.proc, "run", lambda argv, **kw: calls.append(argv) or "")
    github.base_file(PullRequest.from_url(URL, BASE, HEAD), "a b/c?.py")
    assert calls[0][-1] == f"repos/octo/repo/contents/a%20b/c%3F.py?ref={BASE}"


def test_resolve_pins_commits(monkeypatch):
    data = {
        "url": URL,
        "baseRefOid": BASE,
        "headRefOid": HEAD,
        "changedFiles": 2,
        "additions": 3,
        "deletions": 4,
    }
    calls = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        return json.dumps(data)

    monkeypatch.setattr(github.proc, "run", fake_run)
    resolved = github.resolve(PullTarget("7", "octo/repo"))
    assert resolved.pr.head_sha == HEAD
    assert (resolved.changed_files, resolved.additions, resolved.deletions) == (2, 3, 4)
    assert calls[0][:6] == ["gh", "pr", "view", "7", "--repo", "octo/repo"]


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
