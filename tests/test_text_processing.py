"""
Tests for BaseTextProcessor static methods:
  - parse_text_into_paragraphs
  - split_text_into_pages

These are pure functions with no I/O or network dependencies.
"""


from src.processors.base_text_processor import BaseTextProcessor


# ---------------------------------------------------------------------------
# parse_text_into_paragraphs
# ---------------------------------------------------------------------------

class TestParseTextIntoParagraphs:

    def test_empty_string_returns_empty_list(self):
        assert BaseTextProcessor.parse_text_into_paragraphs("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert BaseTextProcessor.parse_text_into_paragraphs("   \n  \t  ") == []

    def test_single_paragraph_no_newlines(self):
        result = BaseTextProcessor.parse_text_into_paragraphs("Hello World")
        assert result == ["Hello World"]

    def test_double_newline_splits_into_paragraphs(self):
        result = BaseTextProcessor.parse_text_into_paragraphs("Hello\n\nWorld")
        assert result == ["Hello", "World"]

    def test_multiple_double_newlines(self):
        result = BaseTextProcessor.parse_text_into_paragraphs("A\n\nB\n\nC")
        assert result == ["A", "B", "C"]

    def test_strips_leading_trailing_whitespace_from_each_paragraph(self):
        result = BaseTextProcessor.parse_text_into_paragraphs("  Hello  \n\n  World  ")
        assert result == ["Hello", "World"]

    def test_empty_paragraphs_filtered_out(self):
        # Three newlines → blank paragraph in the middle is filtered
        result = BaseTextProcessor.parse_text_into_paragraphs("Hello\n\n\n\nWorld")
        assert result == ["Hello", "World"]

    def test_single_newlines_within_text_kept_as_one_paragraph(self):
        # No double newlines → `split('\n\n')` produces one non-empty part;
        # the single-newline fallback branch is NOT reached.
        result = BaseTextProcessor.parse_text_into_paragraphs("Line1\nLine2\nLine3")
        assert len(result) == 1
        assert "Line1" in result[0]
        assert "Line2" in result[0]

    def test_content_with_only_single_newlines_returned_as_one_paragraph(self):
        result = BaseTextProcessor.parse_text_into_paragraphs("  Line1  \n  Line2  ")
        assert len(result) == 1
        assert "Line1" in result[0]

    def test_single_newline_empty_lines_filtered(self):
        result = BaseTextProcessor.parse_text_into_paragraphs("Line1\n\nLine2")
        # double newline takes priority → paragraph split
        assert result == ["Line1", "Line2"]

    def test_cjk_content_preserved(self):
        result = BaseTextProcessor.parse_text_into_paragraphs("日本語\n\n中文")
        assert result == ["日本語", "中文"]


# ---------------------------------------------------------------------------
# split_text_into_pages
# ---------------------------------------------------------------------------

class TestSplitTextIntoPages:

    def test_empty_list_returns_single_empty_string(self):
        result = BaseTextProcessor.split_text_into_pages([])
        assert result == [""]

    def test_single_small_paragraph_stays_on_one_page(self):
        result = BaseTextProcessor.split_text_into_pages(["Hello World"])
        assert result == ["Hello World"]

    def test_multiple_small_paragraphs_fit_on_one_page(self):
        paragraphs = ["Short"] * 10
        result = BaseTextProcessor.split_text_into_pages(paragraphs, target_page_size=2000)
        assert len(result) == 1

    def test_single_oversized_paragraph_stays_on_one_page(self):
        # A paragraph larger than the target must not be discarded or split
        big = "x" * 5000
        result = BaseTextProcessor.split_text_into_pages([big], target_page_size=2000)
        assert len(result) == 1
        assert result[0] == big

    def test_two_paragraphs_that_exceed_target_split_across_pages(self):
        # Each 1100 chars; after first: current_size = 1102
        # Second: 1102 + 1100 = 2202 > 2000 → new page
        p1, p2 = "a" * 1100, "b" * 1100
        result = BaseTextProcessor.split_text_into_pages([p1, p2], target_page_size=2000)
        assert len(result) == 2
        assert result[0] == p1
        assert result[1] == p2

    def test_two_paragraphs_that_fit_stay_on_one_page(self):
        # Each 990 chars; after first: current_size = 992
        # Second: 992 + 990 = 1982 ≤ 2000 → same page
        p1, p2 = "a" * 990, "b" * 990
        result = BaseTextProcessor.split_text_into_pages([p1, p2], target_page_size=2000)
        assert len(result) == 1
        assert result[0] == f"{p1}\n\np2".replace("p2", p2)

    def test_paragraphs_joined_with_double_newline(self):
        paragraphs = ["First", "Second", "Third"]
        result = BaseTextProcessor.split_text_into_pages(paragraphs, target_page_size=2000)
        assert len(result) == 1
        assert result[0] == "First\n\nSecond\n\nThird"

    def test_separator_cost_counted_in_page_size(self):
        # A paragraph of exactly target_page_size - 1 fits alone; the +2 separator
        # should push a 2nd paragraph of size 1 to a new page when: (size-1+2)+1 > size
        # i.e. (1999+2)+1 = 2002 > 2000 → new page
        target = 2000
        p1 = "a" * (target - 1)   # 1999 chars → current_size = 1999 + 2 = 2001
        p2 = "b"                   # 2001 + 1 = 2002 > 2000 → new page
        result = BaseTextProcessor.split_text_into_pages([p1, p2], target_page_size=target)
        assert len(result) == 2

    def test_three_pages(self):
        # Use a small page size to force three pages with simple content
        paragraphs = ["aaa"] * 3
        # Each "aaa" is 3 chars. After first: current_size = 5 (3+2).
        # Second: 5+3=8 > 5 and current_page not empty → new page. After: current_size=5.
        # Third: same → new page.
        result = BaseTextProcessor.split_text_into_pages(paragraphs, target_page_size=5)
        assert len(result) == 3

    def test_custom_target_page_size(self):
        paragraphs = ["word"] * 5  # each 4 chars
        result = BaseTextProcessor.split_text_into_pages(paragraphs, target_page_size=10)
        # First: current_size=6 (4+2). Second: 6+4=10 ≤ 10 → same page, current_size=12.
        # Third: 12+4=16 > 10 → new page. Etc.
        assert len(result) >= 2
