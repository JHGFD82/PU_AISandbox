"""Tests for MarkdownProcessor."""

import pytest

from src.processors.markdown_processor import MarkdownProcessor


class TestMarkdownProcessor:

    def test_simple_markdown_file(self, tmp_path):
        content = "# Hello\n\nThis is a **test**.\n\nAnother paragraph."
        p = tmp_path / "test.md"
        p.write_text(content, encoding="utf-8")
        pages = MarkdownProcessor.process_markdown_with_pages(str(p))
        combined = "\n".join(pages)
        assert "Hello" in combined
        assert "test" in combined

    def test_preserves_markdown_syntax(self, tmp_path):
        content = "# Header\n\n**bold** and _italic_"
        p = tmp_path / "formatted.md"
        p.write_text(content, encoding="utf-8")
        pages = MarkdownProcessor.process_markdown_with_pages(str(p))
        combined = "\n".join(pages)
        assert "**bold**" in combined
        assert "_italic_" in combined

    def test_returns_list_of_strings(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("Hello world", encoding="utf-8")
        pages = MarkdownProcessor.process_markdown_with_pages(str(p))
        assert isinstance(pages, list)
        assert all(isinstance(x, str) for x in pages)

    def test_empty_file_returns_empty_page(self, tmp_path):
        p = tmp_path / "empty.md"
        p.write_text("", encoding="utf-8")
        pages = MarkdownProcessor.process_markdown_with_pages(str(p))
        assert pages == [""]

    def test_missing_file_raises_exception(self, tmp_path):
        missing = str(tmp_path / "nonexistent.md")
        # The code under test raises a bare Exception today. Narrow this to
        # CLIError once processors stop doing that (§5.5 of the code review).
        with pytest.raises(Exception):  # noqa: B017 — see note below
            MarkdownProcessor.process_markdown_with_pages(missing)

    def test_large_file_splits_into_pages(self, tmp_path):
        paragraphs = [f"Paragraph {i}.\n\nMore content {i}." for i in range(30)]
        content = "\n\n".join(paragraphs)
        p = tmp_path / "large.md"
        p.write_text(content, encoding="utf-8")
        pages_small = MarkdownProcessor.process_markdown_with_pages(str(p), target_page_size=100)
        pages_large = MarkdownProcessor.process_markdown_with_pages(str(p), target_page_size=10000)
        assert len(pages_small) >= len(pages_large)

    def test_extract_raw_content(self, tmp_path):
        content = "# Title\n\nBody text."
        p = tmp_path / "content.md"
        p.write_text(content, encoding="utf-8")
        processor = MarkdownProcessor()
        with open(str(p), encoding="utf-8") as fh:
            result = processor.extract_raw_content(fh)
        assert result == content.strip()
