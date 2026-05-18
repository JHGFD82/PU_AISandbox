"""Tests for MarkdownBuilder (save_to_markdown)."""

from pathlib import Path

import pytest

from src.output.markdown_builder import save_to_markdown


class TestSaveToMarkdown:

    def test_creates_file(self, tmp_path):
        out = str(tmp_path / "out.md")
        save_to_markdown("# Hello", out, label="Test")
        assert Path(out).exists()

    def test_content_is_preserved(self, tmp_path):
        content = "# Title\n\n**Bold** and _italic_.\n\n- item 1\n- item 2"
        out = str(tmp_path / "content.md")
        save_to_markdown(content, out, label="Test")
        result = Path(out).read_text(encoding="utf-8")
        assert "**Bold**" in result
        assert "_italic_" in result
        assert "- item 1" in result

    def test_trailing_newline_added_when_missing(self, tmp_path):
        out = str(tmp_path / "nonewline.md")
        save_to_markdown("No trailing newline", out, label="Test")
        raw = Path(out).read_text(encoding="utf-8")
        assert raw.endswith("\n")

    def test_trailing_newline_not_doubled(self, tmp_path):
        out = str(tmp_path / "already.md")
        save_to_markdown("Has newline\n", out, label="Test")
        raw = Path(out).read_text(encoding="utf-8")
        assert raw == "Has newline\n"

    def test_unicode_content(self, tmp_path):
        content = "# 日本語\n\nこれはテストです。"
        out = str(tmp_path / "unicode.md")
        save_to_markdown(content, out, label="Test")
        result = Path(out).read_text(encoding="utf-8")
        assert "日本語" in result

    def test_write_error_raises(self, tmp_path):
        # Point to a directory, not a file, to trigger OSError.
        with pytest.raises(OSError):
            save_to_markdown("content", str(tmp_path), label="Test")
