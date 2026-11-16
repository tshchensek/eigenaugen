from pathlib import Path

import pytest

from eigenaugen.src import cli, prompt_loader
from eigenaugen.src.constants import ExitCode
from eigenaugen.src.context import ReviewContext
from eigenaugen.src.diffplan import DiffPlan
from eigenaugen.src.errors import CommandError, EigenaugenError
from eigenaugen.src.github import PullRequest, PullTarget, Resolved
from eigenaugen.src.prepare import Prepared
from eigenaugen.src.reviewer import Review, RunStats

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


def test_top_level_help_points_to_command_help(capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(["-h"])
    # argparse wraps the epilog to the terminal width; compare unwrapped.
    assert cli.COMMAND_HELP_HINT in " ".join(capsys.readouterr().out.split())


def test_review_help_lists_every_axis(capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(["review", "-h"])
    out = capsys.readouterr().out
    for axis in prompt_loader.review_axes():
        assert f"{axis.number}. {axis.label}\n" in out


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
    monkeypatch.setattr(cli.prepare, "prepare", pytest.fail)
    assert cli.main(["review", "1"]) == ExitCode.OK


def test_context_options():
    args = cli.parse_args(
        ["review", "2", "--stack", "1, 2,o/r#3", "--related", "x/y#5", "--no-stack"]
    )
    assert args.stack_refs == [PullTarget("1"), PullTarget("2"), PullTarget("3", "o/r")]
    assert args.related_refs == [PullTarget("5", "x/y")]
    assert args.no_stack
    defaults = cli.parse_args(["review", "2"])
    assert (defaults.stack_refs, defaults.related_refs, defaults.no_stack) == (
        None,
        [],
        False,
    )


@pytest.mark.parametrize(
    "argv",
    [["review", "2", "--stack", "1,,2"], ["review", "2", "--related", "x/y"]],
)
def test_bad_context_refs_exit_with_usage(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    assert "OWNER/REPO#NUMBER" in capsys.readouterr().err


def fake_review(monkeypatch, unread):
    plan = DiffPlan.build("diff --git a/a b/a\n@@ -0,0 +1 @@\n+x\n", {})
    context = ReviewContext(PR.key, {}, (), "c")
    monkeypatch.setattr(cli.github, "resolve", lambda target: Resolved(PR, 1, 1, 0))
    monkeypatch.setattr(
        cli.prepare, "prepare", lambda *a, **kw: Prepared(context, Path("c"), plan, ())
    )
    monkeypatch.setattr(cli.report, "context_line", lambda context: None)
    stats = RunStats("s", "m", 0.0, 1, {}, {})
    monkeypatch.setattr(
        cli.reviewer,
        "review",
        lambda *a, **kw: Review("## Verdict\nok", stats, unread, 2),
    )


def test_unread_diff_fails_and_is_not_posted(monkeypatch, no_preflight, capsys):
    fake_review(monkeypatch, {1: [1]})
    monkeypatch.setattr(cli.github, "post_review", pytest.fail)
    assert cli.main(["review", "1", "--post"]) == ExitCode.ERROR
    captured = capsys.readouterr()
    assert "UNREAD: chunk 1 page 1" in captured.out
    assert "unread after 2 runs: chunk 1 page 1; not posted" in captured.err


def test_complete_review_is_posted(monkeypatch, no_preflight, capsys):
    fake_review(monkeypatch, {})
    posted = []
    monkeypatch.setattr(
        cli.github, "post_review", lambda pr, body: posted.append(body) or "url"
    )
    assert cli.main(["review", "1", "--post"]) == ExitCode.OK
    assert "- Diff: 1 file, +1 -0; 1 review page in 1 chunk; all read" in posted[0]
    assert "--resume" not in posted[0]
    assert "--resume s" in capsys.readouterr().out


def test_errors_exit_nonzero_without_traceback(monkeypatch, no_preflight, capsys):
    def boom(target):
        raise EigenaugenError("gh failed")

    monkeypatch.setattr(cli.github, "resolve", boom)
    assert cli.main(["review", "1"]) == ExitCode.ERROR
    assert "gh failed" in capsys.readouterr().err
