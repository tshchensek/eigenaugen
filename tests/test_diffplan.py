import pytest

from eigenaugen.src import diffplan
from eigenaugen.src.diffplan import DiffPlan, Mode, SkimReason, classify, paginate
from eigenaugen.src.errors import EigenaugenError


def section(path: str, lines: int, width: int = 10) -> str:
    body = "".join(f"+{str(i).rjust(width, 'x')}\n" for i in range(lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,0 +1,{lines} @@\n{body}"
    )


def diff_of(*sections: str) -> str:
    return "".join(sections)


@pytest.mark.parametrize(
    "path,reason",
    [
        ("yarn.lock", SkimReason.LOCKFILE),
        ("web/package-lock.json", SkimReason.LOCKFILE),
        ("vendor/github.com/x/y.go", SkimReason.VENDORED),
        ("lib/third_party/z.c", SkimReason.VENDORED),
        ("deps/v8/src/api.cc", SkimReason.VENDORED),
        ("static/app.min.js", SkimReason.MINIFIED),
        ("api/v1/types.pb.go", SkimReason.GENERATED),
        ("pkg/zz_generated.deepcopy.go", SkimReason.GENERATED),
        ("src/__snapshots__/a.test.js.snap", SkimReason.GENERATED),
        ("src/app.py", None),
        ("src/deps/app.py", None),
        # Migrations and schema dumps are always reviewed.
        ("vendor/db/migrations/001_init.py", None),
        ("db/structure.sql", None),
    ],
)
def test_skim_reason_by_path(path, reason):
    assert diffplan.skim_reason(path, section(path, 1), {}) == reason


def test_skim_reason_binary():
    text = "diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ\n"
    assert diffplan.skim_reason("i.png", text, {}) == SkimReason.BINARY


@pytest.mark.parametrize(
    "path,attributes,reason",
    [
        ("src/api.py", {"linguist-generated": "set"}, SkimReason.GENERATED),
        ("src/api.py", {"linguist-generated": "true"}, SkimReason.GENERATED),
        ("lib/x.js", {"linguist-vendored": "true"}, SkimReason.VENDORED),
        # The repository says these are its own code.
        ("vendor/ours.go", {"linguist-vendored": "false"}, None),
        ("yarn.lock", {"linguist-generated": "unset"}, None),
        ("yarn.lock", {"linguist-generated": "unspecified"}, SkimReason.LOCKFILE),
    ],
)
def test_skim_reason_attributes_win(path, attributes, reason):
    assert diffplan.skim_reason(path, section(path, 1), attributes) == reason


def test_classify_maps_primary_paths():
    diff = diff_of(section("a.py", 1), section("yarn.lock", 1))
    assert classify(diff, {}) == {"yarn.lock": SkimReason.LOCKFILE}


def test_paginate_keeps_small_files_whole_and_respects_budget():
    a, b, c = section("a.py", 3), section("b.py", 3), section("c.py", 3)
    budget = len(a) + len(b) + 5
    pages = paginate([(0, "a.py", a), (1, "b.py", b), (2, "c.py", c)], budget)
    assert [p.files for p in pages] == [(0, 1), (2,)]
    assert [p.text for p in pages] == [a + b, c]
    assert all(p.note == "" for p in pages)


def test_paginate_splits_large_file_with_line_numbers():
    big = section("big.py", 30)
    pages = paginate([(0, "big.py", big)], 100)
    assert len(pages) > 1
    assert all(len(p.text) <= 100 for p in pages)
    assert "".join(p.text for p in pages) == big
    assert pages[0].note == ""
    # The first page holds the header, the hunk header, and n added lines.
    added_on_first = pages[0].text.count("\n+") - 1
    assert pages[1].note == (
        f"continues `big.py`; next line is old 1, new {1 + added_on_first}"
    )


def test_paginate_cuts_overlong_lines():
    text = "diff --git a/m.js b/m.js\n@@ -1 +1 @@\n+" + "x" * 250 + "\n"
    pages = paginate([(0, "m.js", text)], 100)
    assert all(len(p.text) <= 100 for p in pages)
    assert "".join(p.text for p in pages) == text
    assert "long line" in pages[-1].note


def small_plan(monkeypatch, files: int, lines: int, skim: dict | None = None):
    monkeypatch.setattr(diffplan, "PAGE_CHARS", 200)
    diff = diff_of(*(section(f"f{i}.py", lines) for i in range(files)))
    return diff, DiffPlan.build(diff, skim or {})


def test_build_chunks_review_before_skim(monkeypatch):
    diff = diff_of(section("a.py", 1), section("yarn.lock", 1), section("b.py", 1))
    plan = DiffPlan.build(diff, {"yarn.lock": SkimReason.LOCKFILE})
    assert [c.skim for c in plan.chunks] == [False, True]
    assert [plan.files[i].path for i in plan.chunks[0].files] == ["a.py", "b.py"]
    assert plan.mode == Mode.DIRECT
    assert len(plan.skim_files) == 1
    assert (plan.additions, plan.deletions, plan.chars) == (3, 0, len(diff))


def test_build_groups_pages_into_chunks(monkeypatch):
    diff, plan = small_plan(monkeypatch, files=40, lines=5)
    pages = [p for c in plan.chunks for p in c.pages]
    assert "".join(p.text for p in pages) == diff
    assert all(len(c.pages) <= diffplan.CHUNK_PAGES for c in plan.chunks)
    assert len(plan.review_chunks) > 1
    assert plan.mode == Mode.DELEGATE
    assert plan.review_pages == len(pages)


def test_path_pages_and_lookup(monkeypatch):
    _, plan = small_plan(monkeypatch, files=3, lines=2)
    assert "".join(p.text for p in plan.path_pages("f1.py")).startswith(
        "diff --git a/f1.py"
    )
    assert plan.files_touching("f2.py") == [2]
    with pytest.raises(
        EigenaugenError, match="no changes to 'nope.py'; changed paths: f0.py"
    ):
        plan.path_pages("nope.py")
    with pytest.raises(EigenaugenError, match="no chunk 9"):
        plan.chunk(9)


def test_index_lists_chunks_and_skimmed_directories():
    diff = diff_of(
        section("src/a.py", 2), section("vendor/lib/x.go", 3), section("yarn.lock", 1)
    )
    plan = DiffPlan.build(diff, classify(diff, {}))
    text = plan.index("o/r#1")
    assert text.startswith("o/r#1 diff: 3 files, +6 -0, ")
    assert "Mode: direct" in text
    assert "| 1 | 1 | 1 | +2 -0 | `src/a.py` | `src/a.py` |" in text
    assert "| vendored | `vendor/lib` | 1 | +3 -0 |" in text
    assert "| lockfile | `.` | 1 | +1 -0 |" in text
    chunk = plan.chunk_index("o/r#1", 2)
    assert "chunk 2 of 2 (skim)" in chunk
    assert "| `yarn.lock` | +1 -0 | lockfile | 1 |" in chunk
