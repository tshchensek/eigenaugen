from eigenaugen.src import report
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
