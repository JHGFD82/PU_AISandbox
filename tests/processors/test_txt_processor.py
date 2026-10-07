"""Tests for src/processors/txt_processor.py: reading a plain-text file into pages."""

import io

import pytest

from src.errors import CLIError
from src.processors.txt_processor import TxtProcessor


# ---------------------------------------------------------------------------
# TxtProcessor
# ---------------------------------------------------------------------------


class TestTxtProcessor:

    def _file(self, content: str) -> io.StringIO:
        return io.StringIO(content)

    def test_extract_raw_content_returns_stripped_text(self):
        p = TxtProcessor()
        result = p.extract_raw_content(self._file("  Hello world  "))
        assert result == "Hello world"

    def test_extract_raw_content_empty_file(self):
        p = TxtProcessor()
        result = p.extract_raw_content(self._file(""))
        assert result == ""

    def test_extract_raw_content_preserves_cjk(self):
        p = TxtProcessor()
        result = p.extract_raw_content(self._file("日本語テキスト"))
        assert result == "日本語テキスト"

    def test_process_txt_with_pages_single_page(self):
        pages = TxtProcessor.process_txt_with_pages(self._file("Short content"))
        assert len(pages) >= 1
        assert "Short content" in pages[0]

    def test_process_txt_with_pages_empty_file_returns_empty_string(self):
        pages = TxtProcessor.process_txt_with_pages(self._file(""))
        assert pages == [""]

    def test_process_txt_with_pages_splits_large_content(self):
        # Create content that will definitely exceed a small page size
        big_content = "\n\n".join(["Paragraph " + str(i) * 100 for i in range(20)])
        pages = TxtProcessor.process_txt_with_pages(
            io.StringIO(big_content), target_page_size=200
        )
        assert len(pages) > 1

    def test_process_txt_with_pages_preserves_all_content(self):
        content = "First paragraph\n\nSecond paragraph\n\nThird paragraph"
        pages = TxtProcessor.process_txt_with_pages(io.StringIO(content))
        combined = "\n\n".join(pages)
        assert "First paragraph" in combined
        assert "Second paragraph" in combined
        assert "Third paragraph" in combined

    def test_process_txt_with_pages_raises_cli_error_on_read_error(self):
        """A file that can't be read must surface as a CLIError, not a traceback.

        main() catches CLIError and prints just the message; anything else
        reaches a non-CS professor as a raw traceback.
        """
        bad_file = io.StringIO()
        bad_file.close()  # Closed → reading will raise ValueError
        with pytest.raises(CLIError):
            TxtProcessor.process_txt_with_pages(bad_file)
