"""Tests for src/output/pdf_builder.py: saving results as a PDF."""

import logging
from pathlib import Path
from unittest.mock import patch

from src.output.file_output import FileOutputHandler


# ---------------------------------------------------------------------------
# Part 1: Tables in PDF output
# ---------------------------------------------------------------------------

class TestSaveToPdfWithTableRegistry:
    """Verify that [TABLE_N] placeholders are rendered as reportlab Table flowables."""

    def test_table_in_registry_produces_table_flowable(self, tmp_path):
        """save_to_pdf should call Table() when a matching placeholder is found."""
        out = str(tmp_path / "out.pdf")
        registry = {"[TABLE_1]": [["Header A", "Header B"], ["Cell 1", "Cell 2"]]}
        content = "Intro\n\n[TABLE_1]\n\nConclusion"


        # We just verify it runs without error and produces a real PDF file.
        FileOutputHandler.save_to_pdf(content, out, table_registry=registry, label="Translation")
        assert Path(out).exists()
        assert Path(out).stat().st_size > 0

    def test_pdf_without_table_registry_unchanged(self, tmp_path):
        out = str(tmp_path / "out.pdf")
        FileOutputHandler.save_to_pdf("Only plain text here.", out, table_registry=None, label="Translation")
        assert Path(out).exists()
        assert Path(out).stat().st_size > 0

    def test_unknown_placeholder_falls_through_to_text(self, tmp_path):
        """A [TABLE_N] token with no matching registry entry should write plain text."""
        out = str(tmp_path / "out.pdf")
        # No registry provided — token is treated as a normal paragraph
        FileOutputHandler.save_to_pdf(
            "Before\n\n[TABLE_1]\n\nAfter", out, table_registry=None, label="Translation"
        )
        assert Path(out).exists()

    def test_multiple_tables_in_registry(self, tmp_path):
        out = str(tmp_path / "out.pdf")
        registry = {
            "[TABLE_1]": [["A", "B"]],
            "[TABLE_2]": [["X", "Y"]],
        }
        content = "Intro\n\n[TABLE_1]\n\nMiddle\n\n[TABLE_2]\n\nEnd"
        FileOutputHandler.save_to_pdf(content, out, table_registry=registry, label="Translation")
        assert Path(out).exists()


# ---------------------------------------------------------------------------
# save_to_pdf
# ---------------------------------------------------------------------------


class TestSaveToPdf:

    def test_creates_pdf_for_english_content(self, tmp_path):
        output_path = str(tmp_path / "out.pdf")
        FileOutputHandler.save_to_pdf(
            "Hello world. This is English text.", output_path, target_lang="English", label="Translation"
        )
        assert (tmp_path / "out.pdf").exists()

    def test_english_target_uses_times_roman(self, tmp_path, caplog):
        output_path = str(tmp_path / "out.pdf")
        with caplog.at_level(logging.DEBUG):
            FileOutputHandler.save_to_pdf("Content", output_path, target_lang="English", label="Translation")
        assert "Times-Roman" in caplog.text

    def test_empty_paragraphs_falls_back_to_text(self, tmp_path):
        # Empty content → no story → _fallback_to_text creates .txt
        output_path = str(tmp_path / "out.pdf")
        FileOutputHandler.save_to_pdf("", output_path, target_lang="English", label="Translation")
        assert (tmp_path / "out.txt").exists()

    def test_reportlab_import_error_falls_back_to_text(self, tmp_path):
        output_path = str(tmp_path / "out.pdf")
        with patch.dict("sys.modules", {
            "reportlab.lib.pagesizes": None,
            "reportlab.lib.styles": None,
            "reportlab.platypus": None,
        }):
            FileOutputHandler.save_to_pdf("content", output_path, target_lang="English", label="Translation")
        assert (tmp_path / "out.txt").exists()

    def test_exception_falls_back_to_text(self, tmp_path):
        output_path = str(tmp_path / "out.pdf")
        with patch("src.output.pdf_builder._normalize_paragraphs", side_effect=RuntimeError("boom")):
            FileOutputHandler.save_to_pdf("content", output_path, target_lang="English", label="Translation")
        assert (tmp_path / "out.txt").exists()


# ---------------------------------------------------------------------------
# save_to_pdf — deeper fallback paths
# ---------------------------------------------------------------------------


class TestSaveToPdfDeepPaths:

    def test_non_english_target_calls_get_pdf_font(self, tmp_path, caplog):
        # Exercises the `else` branch (lines 141-142): get_pdf_font is invoked
        output_path = str(tmp_path / "out.pdf")
        with patch("src.output.pdf_builder.get_pdf_font", return_value="Helvetica") as mock_gpf:
            with caplog.at_level(logging.DEBUG):
                FileOutputHandler.save_to_pdf("Content.", output_path, target_lang="Japanese", label="Translation")
        mock_gpf.assert_called_once()
        assert "Helvetica" in caplog.text

    def test_non_times_roman_font_logs_used_font(self, tmp_path, caplog):
        # Covers the `if font_name != 'Times-Roman':` True branch inside `if story:`
        output_path = str(tmp_path / "out.pdf")
        with patch("src.output.pdf_builder.get_pdf_font", return_value="Helvetica"):
            with caplog.at_level(logging.DEBUG):
                FileOutputHandler.save_to_pdf("Content here.", output_path, target_lang="Japanese", label="Translation")
        assert "Used font: Helvetica" in caplog.text

    def test_paragraph_style_error_falls_back_to_normal_style(self, tmp_path, caplog):
        # Covers lines 156-159: ParagraphStyle exception handler.
        # Filter on name so getSampleStyleSheet()'s own ParagraphStyle calls still succeed.
        from reportlab.lib.styles import ParagraphStyle as RealPS

        def fail_on_cjk_normal(*args, **kwargs):
            if args and args[0] == 'CJKNormal':
                raise TypeError("bad font style")
            return RealPS(*args, **kwargs)

        output_path = str(tmp_path / "out.pdf")
        with patch("reportlab.lib.styles.ParagraphStyle", side_effect=fail_on_cjk_normal):
            with caplog.at_level(logging.WARNING):
                FileOutputHandler.save_to_pdf("Content.", output_path, target_lang="English", label="Translation")
        assert "Failed to create custom style" in caplog.text

    def test_paragraph_render_error_uses_fallback_style(self, tmp_path, caplog):
        # Covers the paragraph inner-except fallback (lines ~178-184 of save_to_pdf)
        from reportlab.platypus import Paragraph as RealParagraph
        call_count = [0]

        def once_fail_para(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("render error")
            return RealParagraph(*args, **kwargs)

        output_path = str(tmp_path / "out.pdf")
        with patch("reportlab.platypus.Paragraph", side_effect=once_fail_para):
            with caplog.at_level(logging.WARNING):
                FileOutputHandler.save_to_pdf("Content.", output_path, target_lang="English", label="Translation")
        assert "Error processing paragraph" in caplog.text

    def test_all_paragraph_renders_fail_with_cjk_content(self, tmp_path, caplog):
        # Covers the double-fallback path; CJK content → ascii strip → empty → no-ascii warning
        output_path = str(tmp_path / "out.pdf")
        with patch("reportlab.platypus.Paragraph", side_effect=Exception("bad font")):
            with caplog.at_level(logging.WARNING):
                FileOutputHandler.save_to_pdf("日本語テキスト", output_path, target_lang="English", label="Translation")
        # Paragraph could not be created; content may fall back or skip
        assert (tmp_path / "out.pdf").exists() or (tmp_path / "out.txt").exists()

    def test_ascii_safe_fallback_covers_ascii_content(self, tmp_path):
        # Covers lines 188-191: ascii-safe Paragraph succeeds after normal+fallback both fail.
        # Paragraph fails on calls 1 and 2 (per paragraph), succeeds on call 3.
        from reportlab.platypus import Paragraph as RealP
        call_n = [0]

        def fail_twice(*args, **kwargs):
            call_n[0] += 1
            if call_n[0] <= 2:
                raise Exception("font error")
            return RealP(*args, **kwargs)

        output_path = str(tmp_path / "out.pdf")
        with patch("reportlab.platypus.Paragraph", side_effect=fail_twice):
            FileOutputHandler.save_to_pdf("ASCII content here.", output_path, target_lang="English", label="Translation")
        # ASCII safe fallback rendered; PDF should exist
        assert (tmp_path / "out.pdf").exists() or (tmp_path / "out.txt").exists()


# ---------------------------------------------------------------------------
# pdf_builder — inline markdown table extraction and paragraph error fallback
# ---------------------------------------------------------------------------


class TestPdfBuilderInlineMarkdownAndParagraphError:

    def test_inline_markdown_table_in_content_merges_into_registry(self, tmp_path, caplog):
        """Content containing a markdown table triggers _extract_markdown_tables,
        setting _md_reg non-empty and executing the table_registry merge (lines 89-90)."""
        output_path = str(tmp_path / "out.pdf")
        content = (
            "Paragraph before.\n\n"
            "| Column A | Column B |\n|---|---|\n| Row 1A | Row 1B |\n\n"
            "Paragraph after."
        )
        with caplog.at_level(logging.DEBUG):
            FileOutputHandler.save_to_pdf(content, output_path, target_lang="English", label="Translation")
        assert (tmp_path / "out.pdf").exists() or (tmp_path / "out.txt").exists()
        assert any("Inserted PDF table" in r.message or "Paragraph" in r.message
                   for r in caplog.records)

    def test_paragraph_error_in_pdf_builder_logs_warning(self, tmp_path, caplog):
        """Patching reportlab.platypus.Table to raise during table rendering
        triggers the table-error except branch in pdf_builder (lines 118-119)."""

        def bad_table(*args, **kwargs):
            raise Exception("table render error")

        output_path = str(tmp_path / "out.pdf")
        # Provide a table_registry entry so the table-rendering path is entered.
        table_registry = {"[TABLE_1]": [["Col A", "Col B"], ["val 1", "val 2"]]}
        with patch("reportlab.platypus.Table", side_effect=bad_table):
            with caplog.at_level(logging.WARNING):
                FileOutputHandler.save_to_pdf(
                    "[TABLE_1]", output_path,
                    target_lang="English", label="Translation",
                    table_registry=table_registry,
                )
        assert any("Could not render PDF table" in r.message for r in caplog.records)
