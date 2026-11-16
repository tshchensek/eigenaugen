import pytest

from eigenaugen.src import coverage, diffplan
from eigenaugen.src.coverage import Coverage
from eigenaugen.src.diffplan import DiffPlan
from eigenaugen.src.errors import EigenaugenError


def section(path: str, lines: int) -> str:
    body = "".join(f"+line {i:04}\n" for i in range(lines))
    return f"diff --git a/{path} b/{path}\n@@ -0,0 +1,{lines} @@\n{body}"


@pytest.fixture
def plan(monkeypatch) -> DiffPlan:
    """Two review chunks of CHUNK_PAGES pages each, plus a skim chunk."""
    monkeypatch.setattr(diffplan, "PAGE_CHARS", 120)
    monkeypatch.setattr(diffplan, "CHUNK_PAGES", 2)
    diff = "".join(section(f"f{i}.py", 6) for i in range(4)) + section("yarn.lock", 6)
    result = DiffPlan.build(diff, {"yarn.lock": "lockfile"})
    assert [len(c.pages) for c in result.review_chunks] == [2, 2]
    assert len(result.skim_chunks) == 1
    return result


def test_nothing_read(plan):
    assert coverage.unread(plan, Coverage()) == {1: [1, 2], 2: [1, 2]}


def test_chunk_pages_count_and_skim_is_optional(plan):
    cov = Coverage()
    for chunk, page in [(1, 1), (1, 2), (2, 1), (2, 2)]:
        assert cov.record_chunk(chunk, page)
    assert not cov.record_chunk(1, 1)
    assert coverage.unread(plan, cov) == {}


def test_path_reads_count_when_complete(plan):
    cov = Coverage()
    cov.record_chunk(1, 1)
    cov.record_chunk(1, 2)
    page_files = {plan.files[i].path for i in plan.chunk(2).pages[0].files}
    for path in page_files:
        total = len(plan.path_pages(path))
        for number in range(1, total + 1):
            cov.record_path(path, number, total)
    assert coverage.unread(plan, cov) == {2: [2]}
    # A path read only in part covers nothing.
    partial = Coverage(path_pages={"f2.py": {1}}, path_totals={"f2.py": 2})
    assert coverage.unread(plan, partial) == {1: [1, 2], 2: [1, 2]}


def test_save_and_load_round_trip(tmp_path):
    cov = Coverage()
    cov.record_chunk(3, 1)
    cov.record_path("a b.py", 2, 4)
    path = tmp_path / "coverage.json"
    cov.save(path)
    assert Coverage.load(path) == cov
    assert Coverage.load(tmp_path / "missing.json") == Coverage()
    path.write_text("{")
    with pytest.raises(EigenaugenError, match="unreadable coverage record"):
        Coverage.load(path)


def test_describe():
    assert (
        coverage.describe({9: [2], 4: [1, 2, 3]}) == "chunk 4 pages 1-3; chunk 9 page 2"
    )
