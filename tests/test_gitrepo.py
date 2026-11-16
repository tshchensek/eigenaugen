"""GitRepo against real local repositories; no network."""

import subprocess
from pathlib import Path

import pytest

from eigenaugen.src import gitrepo
from eigenaugen.src.diffplan import SKIM_ATTRIBUTES
from eigenaugen.src.diffs import primary_path, split_sections
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.gitrepo import GitRepo, validate_repo_path

IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
# User settings that would break parsing if they leaked into the diff.
HOSTILE_CONFIG = """\
[color]
    diff = always
    ui = always
[diff]
    noprefix = true
    mnemonicPrefix = true
[core]
    quotePath = true
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def origin(tmp_path, monkeypatch) -> tuple[Path, str, str]:
    """A repository with a base and a head commit."""
    for key, value in IDENTITY.items():
        monkeypatch.setenv(key, value)
    config = tmp_path / "gitconfig"
    config.write_text(HOSTILE_CONFIG)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    repo = tmp_path / "origin"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    (repo / "app.py").write_text("x = 1\n")
    (repo / "gen").mkdir()
    (repo / "gen" / "api.py").write_text("a = 1\n")
    (repo / ".gitattributes").write_text("gen/** linguist-generated\n")
    base = commit_all(repo, "base")
    (repo / "app.py").write_text("x = 2\n")
    (repo / "gen" / "api.py").write_text("a = 2\n")
    (repo / "ä b.txt").write_text("hi\n")
    # The head commit tries to unmark its generated file.
    (repo / ".gitattributes").write_text("")
    head = commit_all(repo, "head")
    return repo, base, head


def test_diff_ignores_user_git_config(origin):
    repo, base, head = origin
    diff = GitRepo(repo).diff(base, head)
    assert "\x1b[" not in diff
    paths = [primary_path(s) for s in split_sections(diff)]
    assert paths == [".gitattributes", "app.py", "gen/api.py", "ä b.txt"]
    assert "--- a/app.py\n+++ b/app.py\n" in diff


def test_file_reads_pinned_commits(origin):
    repo, base, head = origin
    assert GitRepo(repo).file(base, "app.py") == "x = 1\n"
    assert GitRepo(repo).file(head, "app.py") == "x = 2\n"
    with pytest.raises(EigenaugenError, match="does not exist"):
        GitRepo(repo).file(base, "nope.py")
    with pytest.raises(EigenaugenError, match="invalid repository path"):
        GitRepo(repo).file(base, "../x")


def test_attributes_come_from_the_given_commit(origin):
    repo, base, head = origin
    paths = ["gen/api.py", "app.py"]
    at_base = GitRepo(repo).attributes(base, paths, SKIM_ATTRIBUTES)
    assert at_base["gen/api.py"]["linguist-generated"] == "set"
    assert at_base["app.py"]["linguist-generated"] == "unspecified"
    at_head = GitRepo(repo).attributes(head, paths, SKIM_ATTRIBUTES)
    assert at_head["gen/api.py"]["linguist-generated"] == "unspecified"
    assert GitRepo(repo).attributes(base, [], SKIM_ATTRIBUTES) == {}


def test_fetch_and_checkout_by_sha(origin, tmp_path):
    repo, base, head = origin
    clone = tmp_path / "clone"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-checkout",
            "--depth=1",
            f"file://{repo}",
            str(clone),
        ],
        check=True,
    )
    local = GitRepo(clone)
    local.fetch([head, base, head])
    local.checkout(head)
    assert (clone / "app.py").read_text() == "x = 2\n"
    assert local.file(base, "app.py") == "x = 1\n"


def test_clone_uses_gh_with_shallow_blobless_flags(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        gitrepo.proc, "run", lambda argv, **kw: calls.append(argv) or ""
    )
    repo = GitRepo.clone("github.com/o/r", tmp_path / "c")
    assert repo.path == tmp_path / "c"
    assert calls[0][:5] == [
        "gh",
        "repo",
        "clone",
        "github.com/o/r",
        str(tmp_path / "c"),
    ]
    assert calls[0][5:] == ["--", *gitrepo.CLONE_FLAGS]


@pytest.mark.parametrize("path", ["src/app.py", "a b/c.txt", ".github/x.yml"])
def test_validate_repo_path_accepts(path):
    assert validate_repo_path(path) == path


@pytest.mark.parametrize(
    "path", ["", "/etc/passwd", "a//b", "../x", "a/../../user", "a/./b", "dir/"]
)
def test_validate_repo_path_rejects(path):
    with pytest.raises(EigenaugenError):
        validate_repo_path(path)
