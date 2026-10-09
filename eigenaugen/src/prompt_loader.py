"""Load the reviewer's prompt files from eigenaugen/src/prompts/."""

from pathlib import Path
from string import Template

from eigenaugen.src.agent_sdk.types import AgentDefinition
from eigenaugen.src.constants import PROMPTS_DIR
from eigenaugen.src.errors import EigenaugenError

SYSTEM_PROMPT_PATH = PROMPTS_DIR / "system.md"
USER_PROMPT_PATH = PROMPTS_DIR / "user.md"
AGENTS_DIR = PROMPTS_DIR / "agents"
AGENT_GLOB = "*.md"
FRONTMATTER_DELIMITER = "---"
KEY_SEPARATOR = ":"
LIST_SEPARATOR = ","
REQUIRED_KEYS = ("name", "description")


def system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text()


def user_prompt(**fields: str) -> str:
    """Fill user.md; raises KeyError if a placeholder has no value."""
    return Template(USER_PROMPT_PATH.read_text()).substitute(fields)


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
    """Subagent definitions from prompts/agents/*.md, keyed by name."""
    definitions = {}
    for path in sorted(AGENTS_DIR.glob(AGENT_GLOB)):
        meta, body = _split_frontmatter(path)
        tools = meta.get("tools")
        definitions[meta["name"]] = AgentDefinition(
            description=meta["description"],
            prompt=body,
            tools=[t.strip() for t in tools.split(LIST_SEPARATOR)] if tools else None,
            model=meta.get("model"),
            effort=meta.get("effort"),
        )
    return definitions
