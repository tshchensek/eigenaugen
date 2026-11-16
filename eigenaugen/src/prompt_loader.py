"""Load the reviewer's prompt files from eigenaugen/src/prompts/."""

import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

from eigenaugen.src.agent_sdk.types import AgentDefinition
from eigenaugen.src.constants import PROMPTS_DIR
from eigenaugen.src.errors import EigenaugenError

SYSTEM_PROMPT_PATH = PROMPTS_DIR / "system.md"
USER_PROMPT_PATH = PROMPTS_DIR / "user.md"
CONTINUE_PROMPT_PATH = PROMPTS_DIR / "continue.md"
AGENTS_DIR = PROMPTS_DIR / "agents"
AGENT_GLOB = "*.md"
FRONTMATTER_DELIMITER = "---"
KEY_SEPARATOR = ":"
LIST_SEPARATOR = ","
REQUIRED_KEYS = ("name", "description")
# Review axis headings in system.md, e.g. "### 11. **Schema compatibility**".
AXIS_HEADING_RE = re.compile(r"### (?P<number>[0-9]+)\. \*\*(?P<label>[^*]+)\*\*")
SECTION_PREFIX = "## "
CODE_FENCE = "```"
# Subagent prompts embed these system.md sections, so each rule has one
# definition: placeholder in agents/*.md -> system.md heading.
SHARED_SECTIONS = {
    "review_axes": "Review axes",
    "severity_and_verdict": "Severity and verdict",
    "evidence_rules": "Evidence rules",
}


@dataclass(frozen=True)
class ReviewAxis:
    number: int
    # Also the `Axis` value in the report.
    label: str


def system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text()


def review_axes() -> list[ReviewAxis]:
    """Review axes in system.md order; system.md is their only definition."""
    return [
        ReviewAxis(int(m["number"]), m["label"])
        for line in system_prompt().splitlines()
        if (m := AXIS_HEADING_RE.fullmatch(line))
    ]


def section(heading: str) -> str:
    """The body of system.md's `## HEADING` section, up to the next `## `
    heading outside a code block."""
    lines = system_prompt().splitlines()
    title = SECTION_PREFIX + heading
    if title not in lines:
        raise EigenaugenError(f"{SYSTEM_PROMPT_PATH}: no section {title!r}")
    start = lines.index(title) + 1
    end = len(lines)
    fenced = False
    for number in range(start, len(lines)):
        line = lines[number]
        if line.startswith(CODE_FENCE):
            fenced = not fenced
        elif not fenced and line.startswith(SECTION_PREFIX):
            end = number
            break
    return "\n".join(lines[start:end]).strip()


def user_prompt(**fields: str) -> str:
    """Fill user.md; raises KeyError if a placeholder has no value."""
    return Template(USER_PROMPT_PATH.read_text()).substitute(fields)


def continue_prompt(**fields: str) -> str:
    """Fill continue.md, the message that resumes an incomplete review."""
    return Template(CONTINUE_PROMPT_PATH.read_text()).substitute(fields)


def _split_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    """Parse `---`-delimited `key: value` lines (the subset of YAML that
    Claude Code agent files use here) and return them with the body."""
    lines = path.read_text().splitlines()
    try:
        if lines[0] != FRONTMATTER_DELIMITER:
            raise ValueError
        end = lines.index(FRONTMATTER_DELIMITER, 1)
    except (IndexError, ValueError):
        raise EigenaugenError(f"{path}: missing --- frontmatter") from None
    meta = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(KEY_SEPARATOR)
        if not sep:
            raise EigenaugenError(f"{path}: bad frontmatter line {line!r}")
        meta[key.strip()] = value.strip()
    missing = [key for key in REQUIRED_KEYS if key not in meta]
    if missing:
        raise EigenaugenError(f"{path}: frontmatter lacks {', '.join(missing)}")
    return meta, "\n".join(lines[end + 1 :]).strip()


def agents() -> dict[str, AgentDefinition]:
    """Subagent definitions from prompts/agents/*.md, keyed by name.

    `$name` placeholders in a body become the SHARED_SECTIONS of
    system.md; a literal dollar sign is written `$$`.
    """
    shared = {name: section(heading) for name, heading in SHARED_SECTIONS.items()}
    definitions = {}
    for path in sorted(AGENTS_DIR.glob(AGENT_GLOB)):
        meta, body = _split_frontmatter(path)
        tools = meta.get("tools")
        try:
            prompt = Template(body).substitute(shared)
        except (KeyError, ValueError) as exc:
            raise EigenaugenError(f"{path}: bad placeholder: {exc}") from exc
        definitions[meta["name"]] = AgentDefinition(
            description=meta["description"],
            prompt=prompt,
            tools=[t.strip() for t in tools.split(LIST_SEPARATOR)] if tools else None,
            model=meta.get("model"),
            effort=meta.get("effort"),
        )
    return definitions
