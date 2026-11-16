"""Split a pull request's diff into pages and chunks a reviewer can read.

* Page: one tool result, at most PAGE_CHARS characters.
* Chunk: up to CHUNK_PAGES consecutive pages, the share of one subagent.

Files a reviewer only skims (lockfiles, vendored, generated, minified,
binary) get chunks of their own, numbered after the review chunks. A plan
is a pure function of the diff and the skim classification, so the MCP
server and the completeness check in the CLI derive the same chunks.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from eigenaugen.src.diffs import (
    LinePosition,
    is_binary,
    line_counts,
    primary_path,
    section_paths,
    split_sections,
)
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.text import line_pieces, number_ranges, split_pages

# About 10k tokens: well below the CLI's cap on one MCP tool result, even
# for token-dense text.
PAGE_CHARS = 40_000
# About 40k tokens of diff per subagent, leaving room for the files it reads.
CHUNK_PAGES = 4
# Up to this many review pages (about 50k tokens) the main reviewer reads
# the diff itself; beyond, subagents review it chunk by chunk.
DIRECT_REVIEW_PAGES = 5
# Rough ratio for code and diffs; only used to display sizes.
CHARS_PER_TOKEN = 4
THOUSAND = 1_000
MILLION = 1_000_000
# Directory depth used to summarize skimmed files, e.g. `deps/v8`.
SUMMARY_DEPTH = 2
ROOT_DIRECTORY = "."
PATH_SEPARATOR = "/"
NO_SKIM = "-"
# Changed paths listed in an unknown-path error.
MAX_LISTED_PATHS = 50

# gitattributes GitHub Linguist reads; the repository can set them either way.
# https://github.com/github-linguist/linguist/blob/main/docs/overrides.md
ATTR_GENERATED = "linguist-generated"
ATTR_VENDORED = "linguist-vendored"
SKIM_ATTRIBUTES = (ATTR_GENERATED, ATTR_VENDORED)
# `git check-attr` output values: https://git-scm.com/docs/git-check-attr
ATTR_TRUE = ("set", "true")
ATTR_FALSE = ("unset", "false")

# Never skimmed: the review must read migrations and schema dumps even when
# a tool generated them.
ALWAYS_REVIEW_RE = re.compile(
    r"(?:.*/)?(?:migrations?|migrate)/.*"
    r"|(?:.*/)?(?:schema\.rb|structure\.sql|schema\.prisma)"
    r"|.*\.sql"
)
LOCKFILE_NAMES = frozenset(
    {
        "Cargo.lock",
        "Gemfile.lock",
        "Package.resolved",
        "Pipfile.lock",
        "Podfile.lock",
        "bun.lock",
        "bun.lockb",
        "composer.lock",
        "flake.lock",
        "go.sum",
        "gradle.lockfile",
        "mix.lock",
        "package-lock.json",
        "packages.lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pubspec.lock",
        "uv.lock",
        "yarn.lock",
    }
)
MINIFIED_RE = re.compile(r".*\.min\.(?:js|mjs|css)|.*\.(?:js|mjs|css)\.map")
# Third-party code directories, after Linguist's vendor.yml:
# https://github.com/github-linguist/linguist/blob/main/lib/linguist/vendor.yml
VENDORED_RE = re.compile(
    r"(?:.*/)?(?:vendor|vendors|third[_-]?party|node_modules|bower_components"
    r"|Pods|Carthage)/.*"
    r"|deps/.*"
)
# Generated code, after Linguist's generated.rb:
# https://github.com/github-linguist/linguist/blob/main/lib/linguist/generated.rb
GENERATED_RE = re.compile(
    r".*\.pb\.(?:go|cc|h)|.*_pb2(?:_grpc)?\.pyi?|(?:.*/)?zz_generated[^/]*\.go"
    r"|.*\.generated\.[^/]+|(?:.*/)?__snapshots__/.*|.*\.snap"
)


class SkimReason(StrEnum):
    LOCKFILE = "lockfile"
    VENDORED = "vendored"
    GENERATED = "generated"
    MINIFIED = "minified"
    BINARY = "binary"


class Mode(StrEnum):
    """How the main reviewer covers the diff; system.md says what each means."""

    DIRECT = "direct"
    DELEGATE = "delegate"


def _flag(value: str | None) -> bool | None:
    if value in ATTR_TRUE:
        return True
    if value in ATTR_FALSE:
        return False
    return None


def skim_reason(
    path: str, section: str, attributes: Mapping[str, str]
) -> SkimReason | None:
    """Why the file at PATH is only skimmed, or None to review it.

    ATTRIBUTES are its gitattributes; they win over the path patterns.
    """
    if ALWAYS_REVIEW_RE.fullmatch(path):
        return None
    generated = _flag(attributes.get(ATTR_GENERATED))
    vendored = _flag(attributes.get(ATTR_VENDORED))
    if generated:
        return SkimReason.GENERATED
    if vendored:
        return SkimReason.VENDORED
    if is_binary(section):
        return SkimReason.BINARY
    if generated is False or vendored is False:
        return None
    if path.rpartition(PATH_SEPARATOR)[2] in LOCKFILE_NAMES:
        return SkimReason.LOCKFILE
    if MINIFIED_RE.fullmatch(path):
        return SkimReason.MINIFIED
    if VENDORED_RE.fullmatch(path):
        return SkimReason.VENDORED
    if GENERATED_RE.fullmatch(path):
        return SkimReason.GENERATED
    return None


def classify(
    diff: str, attributes: Mapping[str, Mapping[str, str]]
) -> dict[str, SkimReason]:
    """Primary path -> skim reason, for the skimmed files of DIFF.

    ATTRIBUTES maps paths to their gitattributes (see SKIM_ATTRIBUTES).
    """
    skim = {}
    for section in split_sections(diff):
        path = primary_path(section)
        if reason := skim_reason(path, section, attributes.get(path, {})):
            skim[path] = reason
    return skim


@dataclass(frozen=True)
class FileEntry:
    index: int
    path: str
    # Every path the section names (old, new, rename, copy).
    paths: frozenset[str]
    additions: int
    deletions: int
    chars: int
    skim: SkimReason | None


@dataclass(frozen=True)
class Page:
    text: str
    # Where the page resumes a file the previous page cut; empty otherwise.
    note: str
    # Indexes of the files with text on this page.
    files: tuple[int, ...]


@dataclass(frozen=True)
class Chunk:
    number: int
    skim: bool
    pages: tuple[Page, ...]

    @property
    def files(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys(i for page in self.pages for i in page.files))

    @property
    def kind(self) -> str:
        return "skim" if self.skim else "review"


class _PageBuilder:
    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.pages: list[Page] = []
        self._pieces: list[str] = []
        self._size = 0
        self._files: list[int] = []
        self._note = ""

    def fits(self, size: int) -> bool:
        return not self._pieces or self._size + size <= self.budget

    def add(self, index: int, piece: str) -> None:
        self._pieces.append(piece)
        self._size += len(piece)
        if not self._files or self._files[-1] != index:
            self._files.append(index)

    def flush(self, next_note: str = "") -> None:
        if self._pieces:
            text = "".join(self._pieces)
            self.pages.append(Page(text, self._note, tuple(self._files)))
        self._pieces, self._size, self._files = [], 0, []
        self._note = next_note


def paginate(items: Sequence[tuple[int, str, str]], budget: int) -> list[Page]:
    """Pack ITEMS (file index, path, section) into pages of at most BUDGET
    characters. A file that fits on one page is never split; a longer one
    continues on the next pages, each noting where it resumes."""
    builder = _PageBuilder(budget)
    for index, path, section in items:
        if len(section) <= budget and not builder.fits(len(section)):
            builder.flush()
        position = LinePosition(path)
        for line in section.splitlines(keepends=True):
            for number, piece in enumerate(line_pieces(line, budget)):
                if not builder.fits(len(piece)):
                    builder.flush(position.note(mid_line=number > 0))
                builder.add(index, piece)
            position.advance(line)
    builder.flush()
    return builder.pages


def _chunks(pages: list[Page], skim: bool, first: int) -> list[Chunk]:
    return [
        Chunk(first + n, skim, tuple(pages[start : start + CHUNK_PAGES]))
        for n, start in enumerate(range(0, len(pages), CHUNK_PAGES))
    ]


def _approx_tokens(chars: int) -> str:
    tokens = chars // CHARS_PER_TOKEN
    if tokens >= MILLION:
        return f"~{tokens / MILLION:.1f}M tokens"
    if tokens >= THOUSAND:
        return f"~{tokens // THOUSAND}k tokens"
    return f"~{tokens} tokens"


def _lines(additions: int, deletions: int) -> str:
    return f"+{additions} -{deletions}"


def _directory(path: str) -> str:
    parts = path.split(PATH_SEPARATOR)[:-1][:SUMMARY_DEPTH]
    return PATH_SEPARATOR.join(parts) or ROOT_DIRECTORY


@dataclass(frozen=True)
class DiffPlan:
    files: tuple[FileEntry, ...]
    sections: tuple[str, ...]
    chunks: tuple[Chunk, ...]

    @classmethod
    def build(cls, diff: str, skim: Mapping[str, str]) -> "DiffPlan":
        """Plan DIFF; SKIM maps primary paths to skim reasons."""
        sections = split_sections(diff)
        files = []
        for index, section in enumerate(sections):
            path = primary_path(section)
            additions, deletions = line_counts(section)
            reason = skim.get(path)
            files.append(
                FileEntry(
                    index=index,
                    path=path,
                    paths=frozenset(section_paths(section) | {path}),
                    additions=additions,
                    deletions=deletions,
                    chars=len(section),
                    skim=SkimReason(reason) if reason else None,
                )
            )
        review = [(f.index, f.path, sections[f.index]) for f in files if not f.skim]
        skimmed = [(f.index, f.path, sections[f.index]) for f in files if f.skim]
        review_chunks = _chunks(paginate(review, PAGE_CHARS), skim=False, first=1)
        skim_chunks = _chunks(
            paginate(skimmed, PAGE_CHARS), skim=True, first=len(review_chunks) + 1
        )
        return cls(tuple(files), tuple(sections), (*review_chunks, *skim_chunks))

    # -- Figures ----------------------------------------------------------
    @property
    def review_chunks(self) -> list[Chunk]:
        return [c for c in self.chunks if not c.skim]

    @property
    def skim_chunks(self) -> list[Chunk]:
        return [c for c in self.chunks if c.skim]

    @property
    def review_pages(self) -> int:
        return sum(len(c.pages) for c in self.review_chunks)

    @property
    def skim_files(self) -> list[FileEntry]:
        return [f for f in self.files if f.skim]

    @property
    def mode(self) -> Mode:
        if self.review_pages <= DIRECT_REVIEW_PAGES:
            return Mode.DIRECT
        return Mode.DELEGATE

    @property
    def chars(self) -> int:
        return sum(f.chars for f in self.files)

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)

    # -- Lookup -----------------------------------------------------------
    def chunk(self, number: int) -> Chunk:
        if not 1 <= number <= len(self.chunks):
            raise EigenaugenError(
                f"no chunk {number}; chunks are 1 to {len(self.chunks)}"
            )
        return self.chunks[number - 1]

    def files_touching(self, path: str) -> list[int]:
        return [f.index for f in self.files if path in f.paths]

    def path_pages(self, path: str) -> list[Page]:
        """Pages of the section(s) that touch PATH."""
        indexes = self.files_touching(path)
        if not indexes:
            changed = sorted(set().union(*(f.paths for f in self.files)))
            listed = ", ".join(changed[:MAX_LISTED_PATHS])
            more = " ..." if len(changed) > MAX_LISTED_PATHS else ""
            raise EigenaugenError(
                f"no changes to {path!r}; changed paths: {listed}{more}"
            )
        items = [(i, self.files[i].path, self.sections[i]) for i in indexes]
        return paginate(items, PAGE_CHARS)

    # -- Rendering --------------------------------------------------------
    def summary(self, key: str) -> str:
        """One line, e.g. `o/r#1 diff: 3 files, +10 -2, 812 characters
        (~203 tokens)`."""
        return (
            f"{key} diff: {len(self.files)} files, "
            f"{_lines(self.additions, self.deletions)}, {self.chars} characters "
            f"({_approx_tokens(self.chars)})"
        )

    def _chunk_range(self, chunks: list[Chunk]) -> str:
        if not chunks:
            return "none"
        files = len({i for c in chunks for i in c.files})
        pages = sum(len(c.pages) for c in chunks)
        span = number_ranges(c.number for c in chunks)
        return f"{span} ({files} files, {pages} pages)"

    def index(self, key: str) -> str:
        """Overview: sizes, mode, review chunks, skimmed files by directory."""
        mode = (
            f"Mode: {self.mode} ({self.review_pages} review pages; "
            f"direct review up to {DIRECT_REVIEW_PAGES})"
        )
        sizes = f"Pages hold at most {PAGE_CHARS} characters, chunks at most {CHUNK_PAGES} pages."
        lines = [
            self.summary(key),
            mode,
            sizes,
            f"Review chunks: {self._chunk_range(self.review_chunks)}",
            f"Skim chunks: {self._chunk_range(self.skim_chunks)}",
        ]
        if self.review_chunks:
            lines += [
                "",
                "| Chunk | Pages | Files | Lines | First file | Last file |",
                "|-------|-------|-------|-------|------------|-----------|",
            ]
            for chunk in self.review_chunks:
                entries = [self.files[i] for i in chunk.files]
                added = sum(e.additions for e in entries)
                deleted = sum(e.deletions for e in entries)
                lines.append(
                    f"| {chunk.number} | {len(chunk.pages)} | {len(entries)} | "
                    f"{_lines(added, deleted)} | "
                    f"`{entries[0].path}` | `{entries[-1].path}` |"
                )
        if self.skim_files:
            groups: Counter[tuple[str, str]] = Counter()
            group_added: Counter[tuple[str, str]] = Counter()
            group_deleted: Counter[tuple[str, str]] = Counter()
            for entry in self.skim_files:
                group = (str(entry.skim), _directory(entry.path))
                groups[group] += 1
                group_added[group] += entry.additions
                group_deleted[group] += entry.deletions
            lines += [
                "",
                "Skimmed files:",
                "| Reason | Directory | Files | Lines |",
                "|--------|-----------|-------|-------|",
            ]
            for group in sorted(groups):
                lines.append(
                    f"| {group[0]} | `{group[1]}` | {groups[group]} | "
                    f"{_lines(group_added[group], group_deleted[group])} |"
                )
        return "\n".join(lines) + "\n"

    def chunk_index(self, key: str, number: int) -> str:
        """The files of one chunk and the pages each appears on."""
        chunk = self.chunk(number)
        pages_of: dict[int, list[int]] = {}
        for page_number, page in enumerate(chunk.pages, 1):
            for index in page.files:
                pages_of.setdefault(index, []).append(page_number)
        title = (
            f"{key} diff chunk {number} of {len(self.chunks)} ({chunk.kind}): "
            f"{len(chunk.pages)} pages, {len(pages_of)} files"
        )
        lines = [
            title,
            "",
            "| File | Lines | Skim | Pages |",
            "|------|-------|------|-------|",
        ]
        for index, pages in pages_of.items():
            entry = self.files[index]
            lines.append(
                f"| `{entry.path}` | {_lines(entry.additions, entry.deletions)} | "
                f"{entry.skim or NO_SKIM} | {number_ranges(pages)} |"
            )
        return "\n".join(lines) + "\n"

    def index_pages(self, key: str, chunk: int | None) -> list[str]:
        text = self.index(key) if chunk is None else self.chunk_index(key, chunk)
        return split_pages(text, PAGE_CHARS)
