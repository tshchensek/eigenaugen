"""Which pages of the reviewed PR's diff were read, and which were not.

The MCP server records every page of the reviewed diff it serves, to the
main agent or a subagent alike; the CLI checks the record against the diff
plan after the agent finishes. The record lives in a file, so it survives
the MCP server restarting when the session is resumed.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from eigenaugen.src.diffplan import DiffPlan
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.text import number_ranges

TMP_SUFFIX = ".tmp"
CHUNK_PAGES_KEY = "chunk_pages"
PATH_PAGES_KEY = "path_pages"
PATH_TOTALS_KEY = "path_totals"


@dataclass
class Coverage:
    # (chunk, page) pairs served by get_diff with `chunk`.
    chunk_pages: set[tuple[int, int]] = field(default_factory=set)
    # Path -> pages served by get_diff with `path`.
    path_pages: dict[str, set[int]] = field(default_factory=dict)
    # Path -> number of pages of that path's diff.
    path_totals: dict[str, int] = field(default_factory=dict)

    def record_chunk(self, chunk: int, page: int) -> bool:
        """Note a served chunk page; True if it is new."""
        if (chunk, page) in self.chunk_pages:
            return False
        self.chunk_pages.add((chunk, page))
        return True

    def record_path(self, path: str, page: int, total: int) -> bool:
        """Note a served page of one path's diff; True if it is new."""
        pages = self.path_pages.setdefault(path, set())
        self.path_totals[path] = total
        if page in pages:
            return False
        pages.add(page)
        return True

    def save(self, path: Path) -> None:
        """Write atomically: a reader never sees half a file."""
        data = {
            CHUNK_PAGES_KEY: sorted(self.chunk_pages),
            PATH_PAGES_KEY: {p: sorted(n) for p, n in self.path_pages.items()},
            PATH_TOTALS_KEY: self.path_totals,
        }
        tmp = path.with_name(path.name + TMP_SUFFIX)
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "Coverage":
        """The record at PATH; empty if nothing was served yet."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(
                chunk_pages={(c, p) for c, p in data[CHUNK_PAGES_KEY]},
                path_pages={p: set(n) for p, n in data[PATH_PAGES_KEY].items()},
                path_totals=dict(data[PATH_TOTALS_KEY]),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise EigenaugenError(f"{path}: unreadable coverage record: {exc}") from exc


def _files_read_by_path(plan: DiffPlan, coverage: Coverage) -> set[int]:
    """Files whose whole diff was read through `path` requests."""
    read: set[int] = set()
    for path, pages in coverage.path_pages.items():
        total = coverage.path_totals.get(path, 0)
        if total and pages >= set(range(1, total + 1)):
            read.update(plan.files_touching(path))
    return read


def unread(plan: DiffPlan, coverage: Coverage) -> dict[int, list[int]]:
    """Review chunk -> its pages nobody read.

    A page counts as read when it was served, or when every file on it was
    read in full through `path` requests. Skim chunks are not required.
    """
    by_path = _files_read_by_path(plan, coverage)
    missing: dict[int, list[int]] = {}
    for chunk in plan.review_chunks:
        for number, page in enumerate(chunk.pages, 1):
            if (chunk.number, number) in coverage.chunk_pages:
                continue
            if all(index in by_path for index in page.files):
                continue
            missing.setdefault(chunk.number, []).append(number)
    return missing


def describe(missing: dict[int, list[int]]) -> str:
    """E.g. `chunk 4 pages 1-3; chunk 9 page 2`."""
    parts = []
    for chunk, pages in sorted(missing.items()):
        noun = "page" if len(pages) == 1 else "pages"
        parts.append(f"chunk {chunk} {noun} {number_ranges(pages)}")
    return "; ".join(parts)
