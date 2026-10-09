import pytest

from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.text import page, sanitize, shorten


def test_sanitize_strips_escape_sequences_keeps_layout():
    assert sanitize("a\x1b[31mred\x1b[0m\r\tb\nc\x9b") == "a[31mred[0m\tb\nc"


def test_shorten():
    assert shorten("abcdef", 10) == "abcdef"
    assert shorten("abcdef", 5) == "ab..."


TEXT = "".join(f"l{i}\n" for i in range(5))


def test_page_whole_text_has_no_header():
    assert page(TEXT, 0, 10) == TEXT


def test_page_first_page_points_to_next():
    assert page(TEXT, 0, 2) == "[lines 1-2 of 5; next offset 2]\nl0\nl1\n"


def test_page_last_page_has_no_next():
    assert page(TEXT, 4, 2) == "[lines 5-5 of 5]\nl4\n"


def test_page_empty():
    assert page("", 0, 10) == "(empty)"


@pytest.mark.parametrize("offset,limit", [(5, 1), (-1, 1), (0, 0)])
def test_page_rejects_bad_ranges(offset, limit):
    with pytest.raises(EigenaugenError):
        page(TEXT, offset, limit)
