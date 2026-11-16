import pytest

from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.text import (
    number_ranges,
    page_header,
    page_of,
    sanitize,
    shorten,
    split_pages,
)


def test_sanitize_strips_escape_sequences_keeps_layout():
    assert sanitize("a\x1b[31mred\x1b[0m\r\tb\nc\x9b") == "a[31mred[0m\tb\nc"


def test_shorten():
    assert shorten("abcdef", 10) == "abcdef"
    assert shorten("abcdef", 5) == "ab..."


def test_split_pages_breaks_at_line_ends():
    assert split_pages("aa\nbb\ncc\n", 6) == ["aa\nbb\n", "cc\n"]


def test_split_pages_cuts_lines_longer_than_a_page():
    pages = split_pages("x" * 10 + "\ny\n", 4)
    assert pages == ["xxxx", "xxxx", "xx\n", "y\n"]
    assert all(len(p) <= 4 for p in pages)


def test_split_pages_is_lossless():
    text = "".join(f"line {i}\n" * (i % 5) for i in range(50))
    assert "".join(split_pages(text, 17)) == text
    assert split_pages("", 10) == []


def test_page_of_names_next_page():
    pages = ["a\n", "b\n"]
    assert page_of(pages, 1, "doc") == "[doc, page 1 of 2; next page 2]\na\n"
    assert page_of(pages, 2, "doc") == "[doc, page 2 of 2]\nb\n"
    assert page_of([], 1, "doc") == "[doc] (empty)"


@pytest.mark.parametrize("number", [0, 3])
def test_page_of_rejects_missing_pages(number):
    with pytest.raises(EigenaugenError, match="pages are 1 to 2"):
        page_of(["a", "b"], number, "doc")


def test_page_header_with_note():
    assert (
        page_header("d", 2, 2, "continues `a`") == "[d, page 2 of 2; continues `a`]\n"
    )


@pytest.mark.parametrize(
    "numbers,text",
    [([], ""), ([4], "4"), ([1, 2, 3, 5], "1-3, 5"), ([7, 6, 6, 1], "1, 6-7")],
)
def test_number_ranges(numbers, text):
    assert number_ranges(numbers) == text
