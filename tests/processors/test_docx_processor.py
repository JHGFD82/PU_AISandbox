"""Tests for src/processors/docx_processor.py: reading a Word document's text, tables and pictures."""

from io import BytesIO
from unittest.mock import patch

import pytest

from src.errors import CLIError
from src.models.doc_block import ParagraphBlock, TableBlock
from src.processors.docx_processor import DocxProcessor
from tests.helpers import docx_bytes, png


# ---------------------------------------------------------------------------
# extract_blocks
# ---------------------------------------------------------------------------

class TestExtractBlocks:

    def test_paragraphs_only(self):
        data = docx_bytes(["Hello", "World"])
        blocks = DocxProcessor.extract_blocks(BytesIO(data))
        para_texts = [b.text for b in blocks if isinstance(b, ParagraphBlock)]
        assert "Hello" in para_texts
        assert "World" in para_texts

    def test_table_produces_table_block(self):
        data = docx_bytes(
            ["Before"],
            tables=[[["A", "B"], ["C", "D"]]]
        )
        blocks = DocxProcessor.extract_blocks(BytesIO(data))
        table_blocks = [b for b in blocks if isinstance(b, TableBlock)]
        assert len(table_blocks) == 1
        assert table_blocks[0].placeholder == "[TABLE_1]"
        assert table_blocks[0].rows == [["A", "B"], ["C", "D"]]

    def test_multiple_tables_numbered_sequentially(self):
        data = docx_bytes(
            [],
            tables=[[["X"]], [["Y"]]]
        )
        blocks = DocxProcessor.extract_blocks(BytesIO(data))
        table_blocks = [b for b in blocks if isinstance(b, TableBlock)]
        placeholders = [b.placeholder for b in table_blocks]
        assert "[TABLE_1]" in placeholders
        assert "[TABLE_2]" in placeholders

    def test_empty_document_returns_empty_list(self):
        data = docx_bytes([])
        blocks = DocxProcessor.extract_blocks(BytesIO(data))
        # python-docx may add a default empty paragraph; filtering to non-empty text
        para_blocks = [b for b in blocks if isinstance(b, ParagraphBlock)]
        assert all(b.text.strip() for b in para_blocks)


# ---------------------------------------------------------------------------
# extract_media — image-free document
# ---------------------------------------------------------------------------

class TestExtractMedia:

    def test_no_images_returns_empty_list(self):
        data = docx_bytes(["Plain text paragraph"])
        media = DocxProcessor.extract_media(BytesIO(data))
        assert media == []


# ---------------------------------------------------------------------------
# DocxProcessor.extract_media
# ---------------------------------------------------------------------------

class TestDocxProcessorExtractMedia:

    def _make_docx_with_image(self) -> BytesIO:
        """Return an in-memory .docx that has one embedded PNG image."""
        from docx import Document
        from docx.shared import Inches

        buf = BytesIO()
        doc = Document()
        doc.add_paragraph("Before image")
        para = doc.add_paragraph()
        run = para.add_run()
        run.add_picture(BytesIO(png()), width=Inches(1))
        doc.add_paragraph("After image")
        doc.save(buf)
        buf.seek(0)
        return buf

    def test_returns_list_of_embedded_media(self):
        from src.processors.docx_processor import DocxProcessor
        buf = self._make_docx_with_image()
        items = DocxProcessor.extract_media(buf)
        assert isinstance(items, list)
        assert len(items) == 1

    def test_embedded_media_has_data(self):
        from src.processors.docx_processor import DocxProcessor
        buf = self._make_docx_with_image()
        items = DocxProcessor.extract_media(buf)
        assert len(items[0].data) > 0

    def test_position_fraction_in_range(self):
        from src.processors.docx_processor import DocxProcessor
        buf = self._make_docx_with_image()
        items = DocxProcessor.extract_media(buf)
        assert 0.0 <= items[0].position_fraction <= 1.0

    def test_empty_docx_returns_empty_list(self):
        from docx import Document

        from src.processors.docx_processor import DocxProcessor
        buf = BytesIO()
        doc = Document()
        doc.add_paragraph("No images here")
        doc.save(buf)
        buf.seek(0)
        items = DocxProcessor.extract_media(buf)
        assert items == []

    def test_content_type_is_image_mime(self):
        from src.processors.docx_processor import DocxProcessor
        buf = self._make_docx_with_image()
        items = DocxProcessor.extract_media(buf)
        assert items[0].content_type.startswith("image/")


# ---------------------------------------------------------------------------
# DocxProcessor.extract_raw_content — table extraction
# ---------------------------------------------------------------------------

class TestDocxProcessorTableExtraction:
    """Verify that text inside tables is included in extracted content."""

    def _make_docx_with_table(self, rows=2, cols=2) -> BytesIO:
        from docx import Document
        buf = BytesIO()
        doc = Document()
        doc.add_paragraph("Before table")
        table = doc.add_table(rows=rows, cols=cols)
        for r_idx, row in enumerate(table.rows):
            for c_idx, cell in enumerate(row.cells):
                cell.text = f"R{r_idx}C{c_idx}"
        doc.add_paragraph("After table")
        doc.save(buf)
        buf.seek(0)
        return buf

    def test_table_text_is_included(self):
        from src.processors.docx_processor import DocxProcessor
        buf = self._make_docx_with_table()
        processor = DocxProcessor()
        content = processor.extract_raw_content(buf)
        assert "R0C0" in content
        assert "R1C1" in content

    def test_table_appears_between_surrounding_paragraphs(self):
        from src.processors.docx_processor import DocxProcessor
        buf = self._make_docx_with_table()
        processor = DocxProcessor()
        content = processor.extract_raw_content(buf)
        before_pos = content.index("Before table")
        after_pos = content.index("After table")
        cell_pos = content.index("R0C0")
        assert before_pos < cell_pos < after_pos

    def test_table_cells_tab_separated(self):
        from src.processors.docx_processor import DocxProcessor
        self._make_docx_with_table(rows=1, cols=3)
        # Overwrite cells so we know exact content
        from docx import Document
        buf2 = BytesIO()
        doc = Document()
        tbl = doc.add_table(rows=1, cols=3)
        tbl.rows[0].cells[0].text = "Alpha"
        tbl.rows[0].cells[1].text = "Beta"
        tbl.rows[0].cells[2].text = "Gamma"
        doc.save(buf2)
        buf2.seek(0)
        content = DocxProcessor().extract_raw_content(buf2)
        assert "Alpha\tBeta\tGamma" in content

    def test_pure_paragraph_doc_unchanged(self):
        from docx import Document

        from src.processors.docx_processor import DocxProcessor
        buf = BytesIO()
        doc = Document()
        doc.add_paragraph("First")
        doc.add_paragraph("Second")
        doc.save(buf)
        buf.seek(0)
        content = DocxProcessor().extract_raw_content(buf)
        assert "First" in content
        assert "Second" in content

    def test_process_docx_with_pages_includes_table_text(self):
        from src.processors.docx_processor import DocxProcessor
        buf = self._make_docx_with_table()
        pages = DocxProcessor.process_docx_with_pages(buf)
        combined = "\n".join(pages)
        assert "R0C0" in combined


# ---------------------------------------------------------------------------
# DocxProcessor
# ---------------------------------------------------------------------------


class TestDocxProcessor:

    def test_extract_raw_content_returns_paragraph_text(self):
        buf = BytesIO(docx_bytes(["Hello", "World"]))
        p = DocxProcessor()
        result = p.extract_raw_content(buf)
        assert "Hello" in result
        assert "World" in result

    def test_extract_raw_content_empty_document_returns_empty_string(self):
        buf = BytesIO(docx_bytes([]))
        p = DocxProcessor()
        result = p.extract_raw_content(buf)
        assert result == ""

    def test_extract_raw_content_preserves_cjk(self):
        buf = BytesIO(docx_bytes(["日本語テキスト", "中文内容"]))
        p = DocxProcessor()
        result = p.extract_raw_content(buf)
        assert "日本語テキスト" in result
        assert "中文内容" in result

    def test_extract_raw_content_paragraphs_joined_with_double_newline(self):
        buf = BytesIO(docx_bytes(["Para1", "Para2"]))
        p = DocxProcessor()
        result = p.extract_raw_content(buf)
        assert "\n\n" in result

    def test_process_docx_with_pages_single_page(self):
        buf = BytesIO(docx_bytes(["Short text"]))
        pages = DocxProcessor.process_docx_with_pages(buf)
        assert len(pages) >= 1
        assert "Short text" in pages[0]

    def test_process_docx_with_pages_empty_doc_returns_empty_string(self):
        buf = BytesIO(docx_bytes([]))
        pages = DocxProcessor.process_docx_with_pages(buf)
        assert pages == [""]

    def test_process_docx_with_pages_splits_large_content(self):
        big_paragraphs = ["Paragraph " + str(i) * 80 for i in range(15)]
        buf = BytesIO(docx_bytes(big_paragraphs))
        pages = DocxProcessor.process_docx_with_pages(buf, target_page_size=200)
        assert len(pages) > 1

    def test_process_docx_with_pages_preserves_all_paragraphs(self):
        buf = BytesIO(docx_bytes(["Alpha", "Beta", "Gamma"]))
        pages = DocxProcessor.process_docx_with_pages(buf)
        combined = "\n\n".join(pages)
        for word in ("Alpha", "Beta", "Gamma"):
            assert word in combined

    def test_extract_raw_content_raises_import_error_when_docx_not_installed(self, monkeypatch):
        import sys
        buf = BytesIO(docx_bytes(["Hello"]))  # build before patching
        monkeypatch.setitem(sys.modules, "docx", None)
        p = DocxProcessor()
        with pytest.raises(ImportError, match="python-docx is required"):
            p.extract_raw_content(buf)

    def test_process_docx_with_pages_wraps_error_as_cli_error(self):
        """The underlying cause stays visible in the message, wrapped in a CLIError.

        CLIError is what main() knows to print plainly instead of showing a
        non-CS professor a traceback; keeping the original text in it means
        the reason is still there for whoever has to diagnose it.
        """
        buf = BytesIO(docx_bytes(["Hello"]))
        with patch.object(DocxProcessor, "extract_raw_content", side_effect=RuntimeError("disk error")):
            with pytest.raises(CLIError, match="disk error"):
                DocxProcessor.process_docx_with_pages(buf)
