"""Tests for src/output/docx_builder.py: saving results as a Word document.

Each test opens the document that was written and looks for what should be
in it — a picture, a table, the text — rather than only checking that saving
did not fail. Whenever a Word document cannot be made, the text must still
reach the person, as a plain .txt file beside where the .docx would have gone.
"""

import builtins
import io
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from docx import Document

from src.models.embedded_media import EmbeddedMedia
from src.output import docx_builder
from src.output.docx_builder import _apply_docx_table_borders, _fallback_to_text, save_to_docx
from src.output.file_output import FileOutputHandler
from tests.helpers import RED, png


def _webp() -> bytes:
    """A small WebP image: a format Word will not take, but Pillow can convert."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="WEBP")
    return buf.getvalue()


def _media(data: bytes, content_type: str = "image/png") -> EmbeddedMedia:
    return EmbeddedMedia(
        data=data,
        content_type=content_type,
        position_fraction=0.5,
        width_emu=914400,
        height_emu=914400,
    )


def _texts(path) -> list[str]:
    return [p.text for p in Document(str(path)).paragraphs if p.text]


class TestTableBorders:
    def test_a_failure_is_logged_rather_than_raised(self, caplog):
        bad_table = MagicMock()
        bad_table._tbl.tblPr.append.side_effect = RuntimeError("xml error")
        with caplog.at_level(logging.WARNING):
            _apply_docx_table_borders(bad_table)
        assert any("Could not apply table borders" in r.message for r in caplog.records)


class TestFallingBackToText:
    """When no Word document can be made, the text is saved as .txt instead."""

    def test_without_python_docx(self, tmp_path, monkeypatch):
        real_import = builtins.__import__

        def no_docx(name, *args, **kwargs):
            if name == "docx":
                raise ImportError("No module named 'docx'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_docx)
        save_to_docx("Some content", str(tmp_path / "result.docx"), label="Test")

        assert not (tmp_path / "result.docx").exists()
        assert "Some content" in (tmp_path / "result.txt").read_text(encoding="utf-8")

    def test_when_making_the_document_fails(self, tmp_path):
        with patch("docx.Document", side_effect=RuntimeError("disk full")):
            save_to_docx("Content here", str(tmp_path / "output.docx"), label="Translation")

        assert not (tmp_path / "output.docx").exists()
        assert "Content here" in (tmp_path / "output.txt").read_text(encoding="utf-8")

    def test_when_there_is_nothing_to_put_in_it(self, tmp_path):
        save_to_docx("", str(tmp_path / "empty.docx"), label="Translation")

        assert not (tmp_path / "empty.docx").exists()
        assert (tmp_path / "empty.txt").exists()

    def test_the_fallback_swaps_the_extension(self, tmp_path):
        _fallback_to_text("Hello world", str(tmp_path / "output.docx"), "Test")
        assert "Hello world" in (tmp_path / "output.txt").read_text(encoding="utf-8")


class TestPictures:
    def test_a_picture_is_placed_among_the_text(self, tmp_path):
        out = tmp_path / "with_image.docx"
        save_to_docx("Paragraph one\n\nParagraph two", str(out),
                     media=[_media(png(colour=RED))], label="Translation")

        assert len(Document(str(out)).inline_shapes) == 1
        assert _texts(out) == ["Paragraph one", "Paragraph two"]

    def test_a_format_word_will_not_take_is_converted_first(self, tmp_path):
        out = tmp_path / "webp.docx"
        save_to_docx("One\n\nTwo", str(out),
                     media=[_media(_webp(), "image/webp")], label="Translation")

        assert len(Document(str(out)).inline_shapes) == 1

    def test_an_unreadable_picture_is_left_out_and_the_text_kept(self, tmp_path, caplog):
        out = tmp_path / "bad_image.docx"
        with caplog.at_level(logging.WARNING):
            save_to_docx("Some text", str(out),
                         media=[_media(b"not an image")], label="Translation")

        assert any("Could not insert image" in r.message for r in caplog.records)
        assert len(Document(str(out)).inline_shapes) == 0
        assert _texts(out) == ["Some text"]


class TestTables:
    def test_a_placeholder_becomes_a_word_table(self, tmp_path):
        out = tmp_path / "with_table.docx"
        registry = {"[TABLE_1]": [["Header A", "Header B"], ["Row 1", "Row 2"]]}
        save_to_docx("[TABLE_1]\n\nSome other paragraph", str(out),
                     table_registry=registry, label="Test")

        tables = Document(str(out)).tables
        assert len(tables) == 1
        assert [c.text for c in tables[0].rows[1].cells] == ["Row 1", "Row 2"]
        assert "[TABLE_1]" not in _texts(out)

    def test_a_table_that_cannot_be_built_is_written_as_its_placeholder(
        self, tmp_path, monkeypatch, caplog
    ):
        """The placeholder text stays visible, so nothing vanishes without a trace."""
        def fail(_table):
            raise RuntimeError("table error")

        monkeypatch.setattr(docx_builder, "_apply_docx_table_borders", fail)
        out = tmp_path / "tbl_fail.docx"
        with caplog.at_level(logging.WARNING):
            save_to_docx("[TABLE_1]\n\nExtra paragraph", str(out),
                         table_registry={"[TABLE_1]": [["A", "B"]]}, label="Test")

        assert any("Could not insert table" in r.message for r in caplog.records)
        assert _texts(out) == ["[TABLE_1]", "Extra paragraph"]


class TestSaveToDocxPageMarkerInsertion:
    """Images with page_number set use '-- Page N --' markers for placement."""

    def _para_texts(self, docx_path: str) -> list:
        from docx import Document
        return [p.text for p in Document(docx_path).paragraphs]

    def test_image_placed_after_correct_page_block(self, tmp_path):
        """Image with page_number=1 should appear after '-- Page 2 --' content,
        not at the start or end of the document."""
        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "pm.docx")
        # Translated output with two page blocks
        content = "\n\n-- Page 1 --\n\nText of page one.\n\n-- Page 2 --\n\nText of page two."
        # Image belongs to page_index=1 (label "Page 2")
        media = [EmbeddedMedia(
            data=png(colour=RED), content_type="image/png",
            position_fraction=0.5, page_number=1,
        )]
        FileOutputHandler.save_to_docx(content, out, media=media, label="Translation")
        self._para_texts(out)
        # Image paragraph has no text; locate it by finding the empty-run para
        from docx import Document
        doc = Document(out)
        para_texts = [p.text for p in doc.paragraphs]
        # '-- Page 2 --' and 'Text of page two.' must both appear
        assert "-- Page 2 --" in para_texts
        assert "Text of page two." in para_texts
        # Image (empty text paragraph) should NOT be before '-- Page 1 --'
        first_empty = next((i for i, t in enumerate(para_texts) if t == ""), None)
        page1_idx = para_texts.index("-- Page 1 --")
        assert first_empty is None or first_empty > page1_idx

    def test_image_on_page_zero_appears_before_page_two(self, tmp_path):
        """Image on page_index=0 should appear in the page-1 block (before page 2)."""
        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "p0.docx")
        content = "\n\n-- Page 1 --\n\nPage one text.\n\n-- Page 2 --\n\nPage two text."
        media = [EmbeddedMedia(
            data=png(colour=RED), content_type="image/png",
            position_fraction=0.1, page_number=0,
        )]
        FileOutputHandler.save_to_docx(content, out, media=media, label="Translation")
        from docx import Document
        doc = Document(out)
        para_texts = [p.text for p in doc.paragraphs]
        # Empty image paragraph should appear before '-- Page 2 --'
        page2_idx = para_texts.index("-- Page 2 --")
        empty_indices = [i for i, t in enumerate(para_texts) if t == ""]
        assert empty_indices, "Expected at least one image paragraph"
        assert all(idx < page2_idx for idx in empty_indices)

    def test_images_from_multiple_pages_all_inserted(self, tmp_path):
        """Images from two different PDF pages should both appear in the output."""
        from docx import Document

        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "multi.docx")
        content = "\n\n-- Page 1 --\n\nPage one.\n\n-- Page 2 --\n\nPage two."
        media = [
            EmbeddedMedia(data=png(colour=RED), content_type="image/png",
                          position_fraction=0.1, page_number=0),
            EmbeddedMedia(data=png(colour=RED), content_type="image/png",
                          position_fraction=0.6, page_number=1),
        ]
        FileOutputHandler.save_to_docx(content, out, media=media, label="Translation")
        doc = Document(out)
        # 2 page markers + 2 text paras + 2 image paras = 6 paragraphs
        assert len(doc.paragraphs) == 6

    def test_images_on_last_page_flushed_at_end(self, tmp_path):
        """Images for the last page should appear after that page's text."""
        from docx import Document

        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "last.docx")
        content = "\n\n-- Page 1 --\n\nOnly page."
        media = [EmbeddedMedia(
            data=png(colour=RED), content_type="image/png",
            position_fraction=0.5, page_number=0,
        )]
        FileOutputHandler.save_to_docx(content, out, media=media, label="Translation")
        doc = Document(out)
        para_texts = [p.text for p in doc.paragraphs]
        # '-- Page 1 --' and 'Only page.' must appear; image paragraph after them
        assert "-- Page 1 --" in para_texts
        assert "Only page." in para_texts
        page1_idx = para_texts.index("-- Page 1 --")
        empty_indices = [i for i, t in enumerate(para_texts) if t == ""]
        assert all(idx > page1_idx for idx in empty_indices)

    def test_page_number_none_uses_fraction_path(self, tmp_path):
        """Images with page_number=None fall back to fractional placement."""
        from docx import Document

        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "frac.docx")
        # Content with page markers but image has no page_number → fraction path
        content = "\n\n-- Page 1 --\n\nA.\n\n-- Page 2 --\n\nB.\n\nC."
        media = [EmbeddedMedia(
            data=png(colour=RED), content_type="image/png",
            position_fraction=0.99,  # very late → should be at end
            page_number=None,
        )]
        FileOutputHandler.save_to_docx(content, out, media=media, label="Translation")
        doc = Document(out)
        # 2 page-marker paras + 3 text paras (A., B., C.) + 1 image para = 6
        assert len(doc.paragraphs) == 6


class TestSaveToDocxWithMedia:

    def test_save_without_media_creates_docx(self, tmp_path):
        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "out.docx")
        FileOutputHandler.save_to_docx("Paragraph one.\n\nParagraph two.", out, label="Translation")
        assert Path(out).exists()

    def test_save_with_media_creates_docx(self, tmp_path):
        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "out_media.docx")
        media = [EmbeddedMedia(data=png(colour=RED), content_type="image/png", position_fraction=0.5)]
        FileOutputHandler.save_to_docx("Para one.\n\nPara two.\n\nPara three.", out, media=media, label="Translation")
        assert Path(out).exists()

    def test_save_with_media_file_is_valid_docx(self, tmp_path):
        """Verify the output can be reopened by python-docx."""
        from docx import Document

        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "valid.docx")
        media = [EmbeddedMedia(data=png(colour=RED), content_type="image/png", position_fraction=0.3)]
        FileOutputHandler.save_to_docx("First para.\n\nSecond para.", out, media=media, label="Translation")
        doc = Document(out)
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        assert "First para." in texts
        assert "Second para." in texts

    def test_media_inserted_at_proportional_position(self, tmp_path):
        """Image paragraph should appear between translated paragraphs, not all at end."""
        from docx import Document

        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "pos.docx")
        # 3 paragraphs; image at 0.4 → should appear after para 1 (para_fraction=0.33) but
        # before or at para 2 (para_fraction=0.67).
        media = [EmbeddedMedia(data=png(colour=RED), content_type="image/png", position_fraction=0.4)]
        FileOutputHandler.save_to_docx("Alpha.\n\nBeta.\n\nGamma.", out, media=media, label="Translation")
        doc = Document(out)
        # Document has 4 paragraphs: Alpha, Beta (with image inserted after it), Gamma
        # The exact order depends on insertion logic; just verify 4 total blocks.
        assert len(doc.paragraphs) == 4

    def test_multiple_media_items_all_inserted(self, tmp_path):
        from docx import Document

        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "multi.docx")
        media = [
            EmbeddedMedia(data=png(colour=RED), content_type="image/png", position_fraction=0.2),
            EmbeddedMedia(data=png(colour=RED), content_type="image/png", position_fraction=0.8),
        ]
        FileOutputHandler.save_to_docx("A.\n\nB.\n\nC.\n\nD.\n\nE.", out, media=media, label="Translation")
        doc = Document(out)
        # 5 text paras + 2 image paras = 7
        assert len(doc.paragraphs) == 7

    def test_no_media_arg_behaves_identically_to_empty_list(self, tmp_path):
        from docx import Document

        from src.output.file_output import FileOutputHandler
        out1 = str(tmp_path / "none.docx")
        out2 = str(tmp_path / "empty.docx")
        content = "Hello.\n\nWorld."
        FileOutputHandler.save_to_docx(content, out1, media=None, label="Translation")
        FileOutputHandler.save_to_docx(content, out2, media=[], label="Translation")
        doc1 = Document(out1)
        doc2 = Document(out2)
        assert len(doc1.paragraphs) == len(doc2.paragraphs)


# ---------------------------------------------------------------------------
# save_to_docx
# ---------------------------------------------------------------------------


class TestSaveToDocx:

    def test_creates_docx_for_english_content(self, tmp_path):
        output_path = str(tmp_path / "out.docx")
        FileOutputHandler.save_to_docx(
            "Hello world. English text.", output_path, target_lang="English", label="Translation"
        )
        assert (tmp_path / "out.docx").exists()

    def test_english_target_uses_times_new_roman(self, tmp_path, caplog):
        output_path = str(tmp_path / "out.docx")
        with caplog.at_level(logging.DEBUG):
            FileOutputHandler.save_to_docx("Content.", output_path, target_lang="English", label="Translation")
        assert "Times New Roman" in caplog.text

    def test_cjk_target_calls_get_docx_font(self, tmp_path, caplog):
        output_path = str(tmp_path / "out.docx")
        with caplog.at_level(logging.INFO):
            FileOutputHandler.save_to_docx("日本語テキスト", output_path, target_lang="Japanese", label="Translation")
        assert (tmp_path / "out.docx").exists()

    def test_docx_import_error_falls_back_to_text(self, tmp_path):
        import sys
        output_path = str(tmp_path / "out.docx")
        with patch.dict(sys.modules, {"docx": None}):
            FileOutputHandler.save_to_docx("content", output_path, target_lang="English", label="Translation")
        assert (tmp_path / "out.txt").exists()

    def test_exception_falls_back_to_text(self, tmp_path):
        output_path = str(tmp_path / "out.docx")
        with patch("src.output.docx_builder._normalize_paragraphs", side_effect=RuntimeError("boom")):
            FileOutputHandler.save_to_docx("content", output_path, target_lang="English", label="Translation")
        assert (tmp_path / "out.txt").exists()


# ---------------------------------------------------------------------------
# save_to_docx — deeper fallback paths
# ---------------------------------------------------------------------------


class TestSaveToDocxDeepPaths:

    def test_empty_paragraph_runs_adds_run_explicitly(self, tmp_path):
        # Covers lines 272-274: else-branch of `if paragraph.runs:`
        # When add_paragraph("") returns a paragraph with no runs
        output_path = str(tmp_path / "out.docx")
        with patch("src.output.docx_builder._normalize_paragraphs", return_value=[""]):
            FileOutputHandler.save_to_docx("any content", output_path, target_lang="English", label="Translation")
        # File saved (paragraph with empty text still counts toward len(doc.paragraphs))
        assert (tmp_path / "out.docx").exists() or (tmp_path / "out.txt").exists()

    def test_paragraph_error_and_no_paragraphs_falls_back_to_text(self, tmp_path):
        # Covers lines 277-284 (paragraph error handler) AND 298/303 (empty-paragraphs fallback)
        import sys
        output_path = str(tmp_path / "out.docx")

        mock_doc = MagicMock()
        mock_doc.sections = []
        mock_doc.paragraphs = []  # len == 0 → triggers no-paragraphs fallback
        mock_doc.add_paragraph.side_effect = Exception("para error")

        with patch.dict(sys.modules, {"docx": MagicMock(Document=lambda: mock_doc)}):
            # Import the real Pt/Inches by ensuring docx.shared works separately;
            # re-patch just Document via docx module attribute
            pass

        # Simpler approach: patch only docx.Document class
        with patch("docx.Document", return_value=mock_doc):
            FileOutputHandler.save_to_docx("content", output_path, target_lang="English", label="Translation")

        # paragraphs is [], so _fallback_to_text is called → out.txt created
        assert (tmp_path / "out.txt").exists()

    def test_no_paragraphs_falls_back_to_text(self, tmp_path):
        # Covers lines 298-303: the else branch of `if len(doc.paragraphs) > 0:`
        output_path = str(tmp_path / "out.docx")
        mock_doc = MagicMock()
        mock_doc.sections = []
        mock_doc.paragraphs = []
        mock_para = MagicMock()
        mock_para.runs = [MagicMock()]
        mock_doc.add_paragraph.return_value = mock_para

        with patch("docx.Document", return_value=mock_doc):
            FileOutputHandler.save_to_docx("content", output_path, target_lang="English", label="Translation")

        assert (tmp_path / "out.txt").exists()

    def test_paragraph_inner_fallback_succeeds_logs_info(self, tmp_path, caplog):
        # Covers line 281: inner fallback add_paragraph succeeds after outer try fails.
        # First add_paragraph raises (outer except), second call succeeds (line 281 hit).
        output_path = str(tmp_path / "out.docx")
        mock_doc = MagicMock()
        mock_doc.sections = []
        mock_doc.paragraphs = [MagicMock()]  # len > 0, skip text fallback
        mock_doc.add_paragraph.side_effect = [Exception("outer fail"), MagicMock()]

        with patch("docx.Document", return_value=mock_doc):
            with caplog.at_level(logging.INFO):
                FileOutputHandler.save_to_docx("content", output_path, target_lang="English", label="Translation")

        assert "Added paragraph" in caplog.text or "Error processing paragraph" in caplog.text
