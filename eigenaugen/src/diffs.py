"""Split a unified git diff into per-file sections and read their headers.

Format: https://git-scm.com/docs/diff-format
"""

import re

SECTION_HEADER = "diff --git "
HUNK_HEADER = "@@"
SIDE_A = "a/"
SIDE_B = "b/"
SIDE_SEPARATOR = " "
# git quotes paths with special characters: "a/t\tx".
QUOTE = '"'
OLD_PATH_PREFIX = "--- a/"
NEW_PATH_PREFIX = "+++ b/"
TARGET_PATH_PREFIXES = ("rename to ", "copy to ")
# Extended header lines that name a path.
PATH_LINE_PREFIXES = (
    OLD_PATH_PREFIX,
    NEW_PATH_PREFIX,
    "rename from ",
    "copy from ",
    *TARGET_PATH_PREFIXES,
)
# git ends `---`/`+++` names that contain a space with a tab, for patch(1).
NAME_TERMINATOR = "\t"
# Header lines git writes instead of hunks for binary files.
BINARY_MARKERS = ("Binary files ", "GIT binary patch")
ADDED = "+"
DELETED = "-"
CONTEXT = " "
# `@@ -OLD[,COUNT] +NEW[,COUNT] @@ ...`
HUNK_RE = re.compile(
    r"@@ -(?P<old>[0-9]+)(?:,[0-9]+)? \+(?P<new>[0-9]+)(?:,[0-9]+)? @@.*"
)


def split_sections(diff: str) -> list[str]:
    """Split DIFF into sections, one per file, each starting with its
    `diff --git` line."""
    sections: list[str] = []
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith(SECTION_HEADER) and current:
            sections.append("".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("".join(current))
    return sections


def _header_path(header: str) -> str | None:
    """Path from `diff --git a/P b/P` when both sides are equal.

    Works for paths with spaces, which make the line ambiguous in general.
    """
    sides = header.removeprefix(SECTION_HEADER).rstrip("\n")
    fixed = len(SIDE_A) + len(SIDE_SEPARATOR) + len(SIDE_B)
    if (len(sides) - fixed) % 2:
        return None
    path = sides[len(SIDE_A) : len(SIDE_A) + (len(sides) - fixed) // 2]
    if sides == f"{SIDE_A}{path}{SIDE_SEPARATOR}{SIDE_B}{path}":
        return path
    return None


def _header_lines(section: str) -> tuple[str, list[str]]:
    """The `diff --git` line and the extended header lines before the first
    hunk."""
    header, *rest = section.splitlines()
    lines = []
    for line in rest:
        if line.startswith(HUNK_HEADER):
            break
        lines.append(line)
    return header, lines


def _path_after(line: str, prefix: str) -> str:
    return line.removeprefix(prefix).removesuffix(NAME_TERMINATOR)


def section_paths(section: str) -> set[str]:
    """Every path a section names: old, new, rename, and copy paths."""
    header, lines = _header_lines(section)
    paths = set()
    if (path := _header_path(header)) is not None:
        paths.add(path)
    for line in lines:
        for prefix in PATH_LINE_PREFIXES:
            if line.startswith(prefix):
                paths.add(_path_after(line, prefix))
    return paths


def primary_path(section: str) -> str:
    """The path a section is known by: the new path, or the old path of a
    deleted file."""
    header, lines = _header_lines(section)
    new = old = target = None
    for line in lines:
        if line.startswith(NEW_PATH_PREFIX):
            new = _path_after(line, NEW_PATH_PREFIX)
        elif line.startswith(OLD_PATH_PREFIX):
            old = _path_after(line, OLD_PATH_PREFIX)
        elif line.startswith(TARGET_PATH_PREFIXES):
            target = line.split(SIDE_SEPARATOR, 2)[2]
    if path := new or target or _header_path(header) or old:
        return path
    # Quoted or unusual names: the right-hand side of the header.
    sides = header.removeprefix(SECTION_HEADER)
    for separator in (SIDE_SEPARATOR + QUOTE + SIDE_B, SIDE_SEPARATOR + SIDE_B):
        _, found, right = sides.rpartition(separator)
        if found:
            return QUOTE + SIDE_B + right if QUOTE in separator else right
    return sides


def is_binary(section: str) -> bool:
    _, lines = _header_lines(section)
    return any(line.startswith(BINARY_MARKERS) for line in lines)


def line_counts(section: str) -> tuple[int, int]:
    """Added and deleted lines in a section's hunks."""
    added = deleted = 0
    in_hunks = False
    for line in section.splitlines():
        if line.startswith(HUNK_HEADER):
            in_hunks = True
        elif in_hunks and line.startswith(ADDED):
            added += 1
        elif in_hunks and line.startswith(DELETED):
            deleted += 1
    return added, deleted


class LinePosition:
    """Tracks the old and new line numbers while walking a section, so a
    page that starts mid-file can say where it continues."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.old: int | None = None
        self.new: int | None = None

    def advance(self, line: str) -> None:
        """Account for LINE, which has just been emitted."""
        if match := HUNK_RE.fullmatch(line.rstrip("\n")):
            self.old, self.new = int(match["old"]), int(match["new"])
        elif self.old is None or self.new is None:
            return
        elif line.startswith(CONTEXT):
            self.old += 1
            self.new += 1
        elif line.startswith(DELETED):
            self.old += 1
        elif line.startswith(ADDED):
            self.new += 1

    def note(self, mid_line: bool) -> str:
        """Where the next emitted text continues."""
        if mid_line:
            return f"continues a long line of `{self.path}`"
        if self.old is None:
            return f"continues the header of `{self.path}`"
        return f"continues `{self.path}`; next line is old {self.old}, new {self.new}"
