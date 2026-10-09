import pytest

from eigenaugen.src import cli
from eigenaugen.src.constants import ExitCode
from eigenaugen.src.errors import CommandError, EigenaugenError
from eigenaugen.src.github import PullRequest, PullTarget, Resolved

PR = PullRequest.from_url("https://github.com/o/r/pull/1", "a" * 40, "b" * 40)


def test_review_defaults_are_opus_xhigh():
    args = cli.parse_args(["review", "123"])
    assert args.command == "review"
    assert (args.model, args.effort, args.post) == ("opus", "xhigh", False)
    assert args.pull == PullTarget("123")


@pytest.mark.parametrize(
    "argv,target",
    [
        (
            ["review", "https://github.com/o/r/pull/9/changes"],
            PullTarget("https://github.com/o/r/pull/9"),
        ),
        (["review", "o/r", "9"], PullTarget("9", "o/r")),
        (["review", "o/r", "9", "--post"], PullTarget("9", "o/r")),
    ],
)
def test_review_targets(argv, target):
    assert cli.parse_args(argv).pull == target


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["review"],
        ["review", "o/r"],
        ["review", "123", "456"],
        ["review", "nope"],
        ["review", "123", "--effort", "huge"],
        ["frobnicate", "1"],
    ],
)
def test_bad_arguments_exit_with_usage(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value,expected", [("cli", "cli"), ("", "unset"), (None, "unset")]
)
def test_caller_entrypoint(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", value)
    assert cli.caller_entrypoint() == expected


def not_a_repo(argv, **_kw):
    raise CommandError(argv, 128, "fatal: not a git repository")


def test_bare_number_needs_a_clone(monkeypatch):
    monkeypatch.setattr(cli.proc, "run", not_a_repo)
    with pytest.raises(EigenaugenError, match="only inside a clone"):
        cli.require_repo_context(PullTarget("123"))


@pytest.mark.parametrize("target", [PullTarget(PR.url), PullTarget("1", "o/r")])
def test_url_and_repo_targets_work_anywhere(monkeypatch, target):
    monkeypatch.setattr(cli.proc, "run", not_a_repo)
    cli.require_repo_context(target)


@pytest.fixture
def no_preflight(monkeypatch):
    monkeypatch.setattr(cli.proc, "require", lambda *cmds: None)
    monkeypatch.setattr(cli, "require_repo_context", lambda target: None)
    monkeypatch.setattr(cli.reviewer, "check_login", lambda: None)


def test_empty_pr_skips_review(monkeypatch, no_preflight):
    monkeypatch.setattr(cli.github, "resolve", lambda target: Resolved(PR, 0, 0, 0))
    monkeypatch.setattr(cli.github, "checkout", pytest.fail)
    assert cli.main(["review", "1"]) == ExitCode.OK


def test_errors_exit_nonzero_without_traceback(monkeypatch, no_preflight, capsys):
    def boom(target):
        raise EigenaugenError("gh failed")

    monkeypatch.setattr(cli.github, "resolve", boom)
    assert cli.main(["review", "1"]) == ExitCode.ERROR
    assert "gh failed" in capsys.readouterr().err
