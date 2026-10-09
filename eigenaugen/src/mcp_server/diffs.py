"""Split a unified git diff into per-file sections.

Format: https://git-scm.com/docs/diff-format
"""

from eigenaugen.src.errors import EigenaugenError

SECTION_HEADER = "diff --git "
HUNK_HEADER = "@@"
SIDE_A = "a/"
SIDE_B = "b/"
SIDE_SEPARATOR = " "
# Extended header lines that name a path.
PATH_LINE_PREFIXES = (
    "--- a/",
    "+++ b/",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
)
# Changed paths listed in a "no such file" error.
MAX_LISTED_PATHS = 50


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


def section_paths(section: str) -> set[str]:
    """Every path a section names: old, new, rename, and copy paths."""
    header, *rest = section.splitlines()
    paths = set()
    if (path := _header_path(header)) is not None:
        paths.add(path)
    for line in rest:
        if line.startswith(HUNK_HEADER):
            break
        for prefix in PATH_LINE_PREFIXES:
            if line.startswith(prefix):
                paths.add(line.removeprefix(prefix))
    return paths


def file_diff(diff: str, path: str) -> str:
    """The section(s) of DIFF that touch PATH."""
    sections = split_sections(diff)
    matches = [s for s in sections if path in section_paths(s)]
    if matches:
        return "".join(matches)
    changed = sorted(set().union(*(section_paths(s) for s in sections)))
    listed = ", ".join(changed[:MAX_LISTED_PATHS])
    more = " ..." if len(changed) > MAX_LISTED_PATHS else ""
    raise EigenaugenError(f"no changes to {path!r}; changed paths: {listed}{more}")
