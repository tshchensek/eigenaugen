"""prepare against local repositories standing in for GitHub; no network."""

import subprocess
from pathlib import Path

import pytest

from eigenaugen.src import prepare
from eigenaugen.src.context import ReviewContext, Role
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import Comparison, PullRequest, PullTarget, Resolved
from eigenaugen.src.gitrepo import GitRepo
from eigenaugen.src.mcp_server.github_tools import GitHubTools, _file_pages
from eigenaugen.src.stacks import Relation, Stack, StackMember, StackSource
from eigenaugen.src.workspace import Workspace

IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "c")
    return git(repo, "rev-parse", "HEAD")


def new_origin(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "--quiet", "--initial-branch=main")
    git(path, "config", "uploadpack.allowAnySHA1InWant", "true")
    return path


def resolved(url: str, base: str, head: str, base_ref: str, head_ref: str) -> Resolved:
    return Resolved(
        PullRequest.from_url(url, base, head), 1, 1, 0, "OPEN", base_ref, head_ref
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    """o/r: #1 (main <- b1) with #2 stacked on it; x/y: #5. GitHub is faked."""
    for key, value in IDENTITY.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    origin = new_origin(tmp_path / "o-r")
    base = commit(
        origin, {"app.py": "x = 1\n", ".gitattributes": "gen/** linguist-generated\n"}
    )
    head1 = commit(origin, {"app.py": "x = 2\n", "gen/api.py": "a = 1\n"})
    head2 = commit(origin, {"app.py": "x = 3\n"})
    other = new_origin(tmp_path / "x-y")
    other_base = commit(other, {"client.py": "call(1)\n"})
    other_head = commit(other, {"client.py": "call(2)\n"})
    origins = {"github.com/o/r": origin, "github.com/x/y": other}

    def fake_clone(spec, dest):
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-checkout",
                "--depth=1",
                f"file://{origins[spec]}",
                str(dest),
            ],
            check=True,
        )
        return GitRepo(dest)

    merge_bases = {"o/r#1": base, "o/r#2": head1, "x/y#5": other_base}
    monkeypatch.setattr(prepare.GitRepo, "clone", staticmethod(fake_clone))
    monkeypatch.setattr(
        prepare.github, "compare", lambda pr: Comparison(merge_bases[pr.key], 0)
    )
    pr1 = resolved("https://github.com/o/r/pull/1", base, head1, "main", "b1")
    pr2 = resolved("https://github.com/o/r/pull/2", head1, head2, "b1", "b2")
    pr5 = resolved("https://github.com/x/y/pull/5", other_base, other_head, "main", "f")
    stack = Stack(
        StackSource.BRANCHES,
        pr1.pr.key,
        None,
        (StackMember(pr1, 1, Relation.THIS), StackMember(pr2, 2, Relation.ABOVE)),
    )
    monkeypatch.setattr(
        prepare.stacks,
        "discover",
        lambda pull: stack if pull.pr.key == "o/r#1" else None,
    )
    by_ref = {
        ("5", "x/y"): pr5,
        ("1", "github.com/o/r"): pr1,
        ("2", "github.com/o/r"): pr2,
    }
    monkeypatch.setattr(
        prepare.github, "resolve", lambda ref: by_ref[(ref.ref, ref.repo)]
    )
    ws = Workspace(tmp_path / "ws")
    for directory in (ws.root, ws.session, ws.data, ws.repos):
        directory.mkdir()
    return ws, pr1


def test_prepare_clones_diffs_and_writes_context(world):
    ws, pr1 = world
    progress = []
    request = prepare.Request(related=[PullTarget("5", "x/y")])
    prepared = prepare.prepare(pr1, ws, request, progress.append)
    context = ReviewContext.load(prepared.context_file)
    assert list(context.pulls) == ["o/r#1", "o/r#2", "x/y#5"]
    assert [p.role for p in context.pulls.values()] == [
        Role.REVIEWED,
        Role.STACK,
        Role.RELATED,
    ]
    assert context.stacks[0].entries[1].relation == "above"
    # The agent's checkout is the reviewed head.
    assert (ws.checkout / "app.py").read_text() == "x = 2\n"
    reviewed = context.reviewed_pull
    assert "+x = 2" in reviewed.diff() and "gen/api.py" in reviewed.diff()
    assert reviewed.skim == {"gen/api.py": "generated"}
    assert "+x = 3" in context.pulls["o/r#2"].diff()
    assert Path(context.pulls["x/y#5"].repo_dir) == ws.repo_clone(
        "github.com", "x", "y"
    )
    assert [f.path for f in prepared.plan.files] == ["app.py", "gen/api.py"]
    assert any("branch chain, 2 PRs; it is at position 1" in line for line in progress)
    # The MCP tools read every pinned commit from the clones.
    _file_pages.cache_clear()
    tools = GitHubTools(context)
    assert tools.get_file({"pr": "x/y#5", "path": "client.py"}).endswith("call(2)\n")
    assert tools.get_file({"pr": "o/r#2", "path": "app.py"}).endswith("x = 3\n")
    merge_base = tools.get_file({"path": "app.py", "side": "merge-base"})
    assert merge_base.endswith("x = 1\n")


def test_explicit_stack_keeps_the_reviewed_pins(world):
    _, pr1 = world
    request = prepare.Request(stack=[PullTarget("1"), PullTarget("2")], discover=False)
    found, pulls = prepare.gather(pr1, request, lambda line: None)
    assert found[0].source == StackSource.EXPLICIT
    assert pulls["o/r#1"][0] is pr1
    assert [role for _, role in pulls.values()] == [Role.REVIEWED, Role.STACK]


def test_related_must_not_be_the_reviewed_pr(world):
    _, pr1 = world
    request = prepare.Request(related=[PullTarget("1")], discover=False)
    with pytest.raises(EigenaugenError, match="names the reviewed PR"):
        prepare.gather(pr1, request, lambda line: None)
