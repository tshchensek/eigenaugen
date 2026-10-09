"""Text helpers: terminal-safe output and line paging."""

import re

from eigenaugen.src.errors import EigenaugenError

# C0 controls except tab and newline, DEL, and C1 controls. Removing them
# keeps PR-controlled text from emitting terminal escape sequences or
# rewriting lines with carriage returns.
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
ELLIPSIS = "..."
EMPTY_TEXT = "(empty)"


def sanitize(text: str) -> str:
    """Strip control characters that a terminal would interpret."""
    return CONTROL_CHARS_RE.sub("", text)


def shorten(text: str, limit: int) -> str:
    """Cut TEXT to at most LIMIT characters, marking the cut."""
    if len(text) <= limit:
        return text
    return text[: max(limit - len(ELLIPSIS), 0)] + ELLIPSIS


def page(text: str, offset: int, limit: int) -> str:
    """Return LIMIT lines of TEXT starting at line OFFSET (0-based).

    A partial page starts with a header naming the range and, if more lines
    remain, the offset of the next page.
    """
    if offset < 0 or limit < 1:
        raise EigenaugenError("offset must be >= 0 and limit >= 1")
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if total == 0:
        return EMPTY_TEXT
    if offset >= total:
        raise EigenaugenError(f"offset {offset} is past the end ({total} lines)")
    end = min(offset + limit, total)
    if offset == 0 and end == total:
        return text
    more = f"; next offset {end}" if end < total else ""
    header = f"[lines {offset + 1}-{end} of {total}{more}]\n"
    return header + "".join(lines[offset:end])
