"""Text helpers: terminal-safe output and character-budget paging."""

import re
from collections.abc import Iterable, Sequence

from eigenaugen.src.errors import EigenaugenError

# C0 controls except tab and newline, DEL, and C1 controls. Removing them
# keeps PR-controlled text from emitting terminal escape sequences or
# rewriting lines with carriage returns.
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
ELLIPSIS = "..."
EMPTY_TEXT = "(empty)"
RANGE_SEPARATOR = ", "


def sanitize(text: str) -> str:
    """Strip control characters that a terminal would interpret."""
    return CONTROL_CHARS_RE.sub("", text)


def shorten(text: str, limit: int) -> str:
    """Cut TEXT to at most LIMIT characters, marking the cut."""
    if len(text) <= limit:
        return text
    return text[: max(limit - len(ELLIPSIS), 0)] + ELLIPSIS


def line_pieces(line: str, budget: int) -> list[str]:
    """LINE cut into pieces of at most BUDGET characters."""
    return [line[start : start + budget] for start in range(0, len(line), budget)]


def split_pages(text: str, budget: int) -> list[str]:
    """Cut TEXT into pages of at most BUDGET characters at line ends.

    A line longer than BUDGET spans several pages, so no page exceeds it.
    """
    pages: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        for piece in line_pieces(line, budget):
            if current and size + len(piece) > budget:
                pages.append("".join(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece)
    if current:
        pages.append("".join(current))
    return pages


def check_page(number: int, total: int, label: str) -> None:
    if not 1 <= number <= total:
        raise EigenaugenError(
            f"{label} has no page {number}; pages are 1 to {max(total, 1)}"
        )


def page_header(label: str, number: int, total: int, note: str = "") -> str:
    """E.g. `[o/r#1 diff, chunk 2, page 1 of 3; next page 2; NOTE]`."""
    parts = [f"{label}, page {number} of {total}"]
    if number < total:
        parts.append(f"next page {number + 1}")
    if note:
        parts.append(note)
    return f"[{'; '.join(parts)}]\n"


def page_of(pages: Sequence[str], number: int, label: str) -> str:
    """Page NUMBER (1-based) of PAGES under a header naming LABEL."""
    if not pages:
        return f"[{label}] {EMPTY_TEXT}"
    check_page(number, len(pages), label)
    return page_header(label, number, len(pages)) + pages[number - 1]


def number_ranges(numbers: Iterable[int]) -> str:
    """E.g. [1, 2, 3, 5] -> `1-3, 5`."""
    ranges: list[str] = []
    ordered = sorted(set(numbers))
    start = prev = None
    for number in [*ordered, None]:
        if prev is not None and number == prev + 1:
            prev = number
            continue
        if start is not None:
            ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = number
    return RANGE_SEPARATOR.join(ranges)
