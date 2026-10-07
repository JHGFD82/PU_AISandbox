"""Tests for src/processors/pdf_processor.py: reading the text of a PDF, page by page.

Covers cleaning extracted text, working out its layout, recognising numbered
content (detect_numbered_content), and what each page's request carries from
the page before (generate_process_text). No API calls are made.
"""

from unittest.mock import MagicMock, patch

import pytest
from pdfminer.layout import LTChar, LTFigure, LTPage, LTTextBox, LTTextContainer, LTTextLine

from src.processors.pdf_processor import (
    PDFProcessor,
    detect_numbered_content,
    generate_process_text,
)


# ---------------------------------------------------------------------------
# detect_numbered_content
# ---------------------------------------------------------------------------


def _mock_lt(cls, text: str):
    """Return a MagicMock whose isinstance check passes as *cls*."""
    obj = MagicMock()
    obj.__class__ = cls
    obj.get_text.return_value = text
    return obj


class TestDetectNumberedContent:

    def test_decimal_list_item(self):
        assert detect_numbered_content("1. Some text") is True

    def test_fullwidth_space_after_number(self):
        assert detect_numbered_content("1\u3000Some item") is True

    def test_number_space_text(self):
        assert detect_numbered_content("2 Some text") is True

    def test_bracket_reference(self):
        assert detect_numbered_content("See [1] for details") is True

    def test_paren_reference(self):
        assert detect_numbered_content("(1) First point") is True

    def test_cjk_paren_reference(self):
        assert detect_numbered_content("\uff081\uff09\u7b2c\u4e00\u70b9") is True

    def test_cjk_closing_paren(self):
        assert detect_numbered_content("1\uff09\u7b2c\u4e00\u70b9") is True

    def test_circled_number(self):
        assert detect_numbered_content("\u2460\u6700\u521d") is True

    def test_chinese_numeral_list(self):
        assert detect_numbered_content("\u4e00\u3001\u7b2c\u4e00\u7ae0") is True

    def test_standalone_number_on_own_line(self):
        assert detect_numbered_content("Some text\n42\nMore text") is True

    def test_plain_prose_returns_false(self):
        assert detect_numbered_content("The quick brown fox") is False

    def test_empty_string_returns_false(self):
        assert detect_numbered_content("") is False

    def test_cjk_text_no_numbers_returns_false(self):
        assert detect_numbered_content("\u6771\u4eac\u5927\u5b66\u306e\u7814\u7a76") is False


# ---------------------------------------------------------------------------
# generate_process_text
# ---------------------------------------------------------------------------


class TestGenerateProcessText:

    def test_output_always_starts_with_current_page_header(self):
        out = generate_process_text("", "page content", "")
        assert out.startswith("--Current Page:")

    def test_abstract_used_as_context_when_provided(self):
        out = generate_process_text("abstract here", "page text", "previous page content")
        assert "abstract here" in out
        assert "--Context:" in out

    def test_no_abstract_uses_tail_of_previous_page(self):
        # previous_page of 20 chars with 0.65 context -> tail from char 13 onward
        prev = "0123456789ABCDEFGHIJ"
        out = generate_process_text("", "page text", prev)
        assert "EFGHIJ" in out
        assert "--Context:" in out

    def test_no_context_at_all_produces_no_context_section(self):
        out = generate_process_text("", "page text", "")
        assert "--Context:" not in out

    def test_previous_translated_with_numbered_page_appends_hint(self):
        prev_translated = "Line one\n1. First item\n2. Second item"
        out = generate_process_text("", "1. Numbered content", "", previous_translated=prev_translated)
        assert "Previous numbering ended with" in out

    def test_previous_translated_without_numbered_page_no_hint(self):
        prev_translated = "Line one\n1. First item"
        out = generate_process_text("", "Plain prose text", "", previous_translated=prev_translated)
        assert "Previous numbering ended with" not in out

    def test_previous_translated_all_plain_lines_no_hint(self):
        prev_translated = "plain\nno numbers here\njust text"
        out = generate_process_text("", "1. Numbered content", "", previous_translated=prev_translated)
        assert "Previous numbering ended with" not in out

    def test_page_text_appears_in_output(self):
        out = generate_process_text("", "unique_page_content_xyz", "")
        assert "unique_page_content_xyz" in out


# ---------------------------------------------------------------------------
# PDFProcessor._clean_text
# ---------------------------------------------------------------------------


class TestPDFProcessorCleanText:

    @pytest.fixture()
    def p(self):
        return PDFProcessor()

    def test_empty_string_returns_empty(self, p):
        assert p._clean_text("") == ""

    def test_removes_null_characters(self, p):
        assert p._clean_text("hel\x00lo") == "hello"

    def test_removes_bom(self, p):
        assert p._clean_text("\ufeffhello") == "hello"

    def test_removes_cid_references(self, p):
        # After CID removal the space-collapser merges the surrounding spaces to one
        assert p._clean_text("text (cid:123) more") == "text more"

    def test_collapses_multiple_spaces(self, p):
        assert p._clean_text("a   b\t\tc") == "a b c"

    def test_preserves_newlines(self, p):
        result = p._clean_text("line one\nline two")
        assert "\n" in result

    def test_preserves_cjk_text(self, p):
        assert p._clean_text("\u65e5\u672c\u8a9e\u30c6\u30ad\u30b9\u30c8") == "\u65e5\u672c\u8a9e\u30c6\u30ad\u30b9\u30c8"

    def test_strips_leading_trailing_whitespace(self, p):
        assert p._clean_text("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# PDFProcessor.parse_layout
# ---------------------------------------------------------------------------


class TestPDFProcessorParseLayout:

    @pytest.fixture()
    def p(self):
        return PDFProcessor()

    def _layout(self, children):
        layout = MagicMock(spec=LTPage)
        layout.__iter__ = MagicMock(return_value=iter(children))
        return layout

    def test_empty_layout_returns_empty_string(self, p):
        assert p.parse_layout(self._layout([])) == ""

    def test_lt_text_line_text_included(self, p):
        line = _mock_lt(LTTextLine, "Hello world\n")
        out = p.parse_layout(self._layout([line]))
        assert "Hello world" in out

    def test_lt_text_container_text_included(self, p):
        container = _mock_lt(LTTextContainer, "Container text\n")
        out = p.parse_layout(self._layout([container]))
        assert "Container text" in out

    def test_lt_char_text_included(self, p):
        char = _mock_lt(LTChar, "A")
        out = p.parse_layout(self._layout([char]))
        assert "A" in out

    def test_lt_text_box_text_extracted_via_get_text(self, p):
        # LTTextBox is a subclass of LTTextContainer so it is handled by the
        # LTTextContainer branch (get_text), not by child expansion.
        box = MagicMock()
        box.__class__ = LTTextBox
        box.get_text.return_value = "Box text\n"
        out = p.parse_layout(self._layout([box]))
        assert "Box text" in out

    def test_lt_figure_children_are_expanded(self, p):
        inner = _mock_lt(LTTextLine, "Figure text\n")
        fig = MagicMock()
        fig.__class__ = LTFigure
        fig.__iter__ = MagicMock(return_value=iter([inner]))
        out = p.parse_layout(self._layout([fig]))
        assert "Figure text" in out

    def test_empty_text_elements_excluded(self, p):
        line = _mock_lt(LTTextLine, "   ")
        out = p.parse_layout(self._layout([line]))
        assert out == ""

    def test_multiple_lines_joined_with_newlines(self, p):
        a = _mock_lt(LTTextLine, "Line A\n")
        b = _mock_lt(LTTextLine, "Line B\n")
        out = p.parse_layout(self._layout([a, b]))
        assert "Line A" in out
        assert "Line B" in out

    def test_excessive_blank_lines_collapsed(self, p):
        a = _mock_lt(LTTextLine, "Line A\n")
        c = _mock_lt(LTTextLine, "Line C\n")
        out = p.parse_layout(self._layout([a, c]))
        assert "\n\n\n" not in out


# ---------------------------------------------------------------------------
# PDFProcessor.process_page
# ---------------------------------------------------------------------------


class TestPDFProcessorProcessPage:

    def test_calls_interpreter_and_returns_layout_text(self):
        p = PDFProcessor()
        mock_page = MagicMock()
        mock_layout = MagicMock(spec=LTPage)
        mock_layout.__iter__ = MagicMock(return_value=iter([_mock_lt(LTTextLine, "Page text\n")]))
        p.interpreter.process_page = MagicMock()
        p.device.get_result = MagicMock(return_value=mock_layout)

        result = p.process_page(mock_page)

        p.interpreter.process_page.assert_called_once_with(mock_page)
        assert "Page text" in result


# ---------------------------------------------------------------------------
# PDFProcessor.process_pdf
# ---------------------------------------------------------------------------


class TestPDFProcessorProcessPdf:

    def test_delegates_to_pdfpage_get_pages(self):
        p = PDFProcessor()
        mock_file = MagicMock()
        sentinel = object()
        with patch("src.processors.pdf_processor.PDFPage.get_pages", return_value=sentinel) as mock_get:
            result = p.process_pdf(mock_file)
        mock_get.assert_called_once_with(mock_file)
        assert result is sentinel
