"""Tests for plugins/translation/src/processors/docx_translation.py (registered as src.processors.docx_translation)."""

import sys
from io import BytesIO

from tests.helpers import docx_bytes


process_docx_for_translation = sys.modules["src.processors.docx_translation"].process_docx_for_translation


# ---------------------------------------------------------------------------
# process_docx_for_translation
# ---------------------------------------------------------------------------

class TestProcessDocxForTranslation:

    def test_paragraphs_become_pages(self):
        data = docx_bytes(["First paragraph", "Second paragraph"])
        pages, registry = process_docx_for_translation(BytesIO(data))
        assert isinstance(pages, list)
        assert len(pages) >= 1
        combined = "\n".join(pages)
        assert "First paragraph" in combined or "Second" in combined

    def test_table_appears_as_placeholder_in_pages_and_registry(self):
        data = docx_bytes(
            ["Intro"],
            tables=[[["Col A", "Col B"], ["Val 1", "Val 2"]]]
        )
        pages, registry = process_docx_for_translation(BytesIO(data))
        assert "[TABLE_1]" in registry
        assert registry["[TABLE_1]"] == [["Col A", "Col B"], ["Val 1", "Val 2"]]
        combined = "\n".join(pages)
        assert "[TABLE_1]" in combined

    def test_empty_document_returns_empty_string_page(self):
        data = docx_bytes([])
        pages, registry = process_docx_for_translation(BytesIO(data))
        assert pages == [""]
        assert registry == {}
