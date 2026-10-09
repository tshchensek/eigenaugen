"""Footer appended to every report: what was reviewed, and what it took."""

from eigenaugen.src.constants import CLAUDE_BIN, SHORT_SHA_LEN
from eigenaugen.src.github import PullRequest
from eigenaugen.src.reviewer import RunStats

SECONDS_PER_MINUTE = 60
NO_SUBAGENTS = "none"
RESUME_LABEL = "To continue this conversation:"
SEPARATOR = "\n\n---\n"


def _usd(amount: float) -> str:
    return f"${amount:.2f}"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def footer(
    pr: PullRequest,
    effort: str,
    stats: RunStats,
    elapsed_s: float,
    resume: bool = True,
) -> str:
    """E.g.:

    Reviewed commit `a9d9d84ee3e8` with eigenaugen, effort `xhigh`
    - Run: claude-opus-5-5, $2.30 (API list price), 4.2 min, 69 turns
    - Models: claude-opus-5-5 $2.21, claude-haiku-5-5 $0.09
    - Subagents: 2 (code-tracer x1, standards-scout x1)
    - To continue this conversation: claude --resume 0b1c...

    RESUME False drops the last line, e.g. for a review posted to GitHub,
    where readers cannot reach the local session.
    """
    by_cost = sorted(stats.model_costs.items(), key=lambda item: item[1], reverse=True)
    models = ", ".join(f"{name} {_usd(cost)}" for name, cost in by_cost)
    spawned = sum(stats.subagents.values())
    if spawned:
        kinds = ", ".join(f"{kind} x{n}" for kind, n in sorted(stats.subagents.items()))
        subagents = f"{spawned} ({kinds})"
    else:
        subagents = NO_SUBAGENTS
    sha = pr.head_sha[:SHORT_SHA_LEN]
    minutes = elapsed_s / SECONDS_PER_MINUTE
    run = (
        f"{stats.model}, {_usd(stats.cost_usd)} (API list price), "
        f"{minutes:.1f} min, {_plural(stats.turns, 'turn')}"
    )
    lines = [
        f"Reviewed commit `{sha}` with eigenaugen, effort `{effort}`",
        f"- Run: {run}",
        f"- Models: {models or stats.model}",
        f"- Subagents: {subagents}",
    ]
    if resume and stats.session_id:
        lines.append(f"- {RESUME_LABEL} {CLAUDE_BIN} --resume {stats.session_id}")
    return "\n".join(lines)


def compose(
    report: str,
    pr: PullRequest,
    effort: str,
    stats: RunStats,
    elapsed_s: float,
    resume: bool = True,
) -> str:
    tail = footer(pr, effort, stats, elapsed_s, resume)
    return f"{report.rstrip()}{SEPARATOR}{tail}\n"
