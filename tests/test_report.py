from eigenaugen.src import diffplan, report
from eigenaugen.src.context import ContextPull, ReviewContext, Role, StackInfo
from eigenaugen.src.diffplan import DiffPlan
from eigenaugen.src.github import PullRequest
from eigenaugen.src.reviewer import RunStats

PR = PullRequest.from_url("https://github.com/o/r/pull/1", "a" * 40, "b" * 40)
SESSION = "0b1c2d3e-0000-4000-8000-000000000000"
STATS = RunStats(
    session_id=SESSION,
    model="claude-opus-5-5",
    cost_usd=2.3,
    turns=69,
    model_costs={"claude-haiku-5-5": 0.09, "claude-opus-5-5": 2.21},
    subagents={"standards-scout": 1, "code-tracer": 2},
)


def test_compose_appends_run_footer():
    body = report.compose("## Verdict\nok\n\n", PR, "xhigh", STATS, 252)
    assert body == (
        "## Verdict\nok\n\n---\n"
        "Reviewed commit `bbbbbbbbbbbb` with eigenaugen, effort `xhigh`\n"
        "- Run: claude-opus-5-5, $2.30 (API list price), 4.2 min, 69 turns\n"
        "- Models: claude-opus-5-5 $2.21, claude-haiku-5-5 $0.09\n"
        "- Subagents: 3 (code-tracer x2, standards-scout x1)\n"
        f"- To continue this conversation: claude --resume {SESSION}\n"
    )


def test_posted_report_has_no_resume_line():
    body = report.compose("## Verdict\nok", PR, "xhigh", STATS, 252, resume=False)
    assert "--resume" not in body
    assert body.endswith("- Subagents: 3 (code-tracer x2, standards-scout x1)\n")


def test_footer_without_subagents_or_usage():
    stats = RunStats("", "claude-opus-5-5", 0.4, 1, {}, {})
    text = report.footer(PR, "high", stats, 30)
    assert "- Run: claude-opus-5-5, $0.40 (API list price), 0.5 min, 1 turn\n" in text
    assert "- Models: claude-opus-5-5\n" in text
    # No session ID, no resume line.
    assert text.endswith("- Subagents: none")


def test_scope_lines_follow_the_title():
    scope = ["Diff: 1 file, +1 -0; 1 review page in 1 chunk; all read"]
    text = report.footer(PR, "high", STATS, 30, scope)
    lines = text.splitlines()
    assert lines[1] == f"- {scope[0]}"
    assert lines[2].startswith("- Run: ")


def plan_of(monkeypatch, files: int, skim: dict | None = None) -> DiffPlan:
    monkeypatch.setattr(diffplan, "PAGE_CHARS", 60)
    diff = "".join(
        f"diff --git a/f{i} b/f{i}\n@@ -0,0 +1 @@\n+{'x' * 20}\n" for i in range(files)
    )
    return DiffPlan.build(diff, skim or {})


def test_diff_line(monkeypatch):
    plan = plan_of(monkeypatch, 3, {"f2": "lockfile"})
    assert report.diff_line(plan, {}) == (
        "Diff: 3 files, +3 -0; 2 review pages in 1 chunk; 1 file skimmed; all read"
    )
    assert report.diff_line(plan, {1: [2]}).endswith("; UNREAD: chunk 1 page 2")


def test_context_line():
    def pull(key, role):
        return ContextPull(key, "u", "a", "b", "c", 0, role, "OPEN", 1, 1, 0, "r", "d")

    alone = ReviewContext("o/r#1", {"o/r#1": pull("o/r#1", Role.REVIEWED)}, (), "c")
    assert report.context_line(alone) is None
    stack = StackInfo("o/r#1", "github", 7, "GitHub stack #7, 2 PRs", ())
    pulls = {
        "o/r#1": pull("o/r#1", Role.REVIEWED),
        "o/r#2": pull("o/r#2", Role.STACK),
        "x/y#5": pull("x/y#5", Role.RELATED),
    }
    context = ReviewContext("o/r#1", pulls, (stack,), "c")
    assert report.context_line(context) == (
        "Context: GitHub stack #7, 2 PRs of o/r#1; related x/y#5"
    )
