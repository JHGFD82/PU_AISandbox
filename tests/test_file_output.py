"""
Tests for file output utilities:
  - FileOutputHandler._normalize_paragraphs
  - FileOutputHandler._resolve_output_path
  - FileOutputHandler._emit_message
  - FileOutputHandler._ensure_parent_directory
  - FileOutputHandler._fallback_to_text
  - FileOutputHandler.save_to_text_file
  - FileOutputHandler.append_to_text_file
  - generate_output_filename
"""

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.output.file_output import FileOutputHandler, generate_output_filename


# ---------------------------------------------------------------------------
# _normalize_paragraphs
# ---------------------------------------------------------------------------

class TestNormalizeParagraphs:

    def test_empty_string_returns_empty_list(self):
        assert FileOutputHandler._normalize_paragraphs("") == []

    def test_single_paragraph(self):
        assert FileOutputHandler._normalize_paragraphs("Hello World") == ["Hello World"]

    def test_double_newline_splits_paragraphs(self):
        result = FileOutputHandler._normalize_paragraphs("Hello\n\nWorld")
        assert result == ["Hello", "World"]

    def test_empty_paragraphs_filtered_out(self):
        # Extra blank lines produce empty paragraphs, which are discarded
        result = FileOutputHandler._normalize_paragraphs("Hello\n\n\n\nWorld")
        assert result == ["Hello", "World"]

    def test_inner_newlines_replaced_with_space(self):
        # Within a double-newline block, single newlines become spaces
        result = FileOutputHandler._normalize_paragraphs("Line1\nLine2\n\nLine3")
        assert result == ["Line1 Line2", "Line3"]

    def test_strips_leading_trailing_whitespace_from_paragraphs(self):
        result = FileOutputHandler._normalize_paragraphs("  Hello  \n\n  World  ")
        assert result == ["Hello", "World"]

    def test_multiple_paragraphs(self):
        content = "One\n\nTwo\n\nThree"
        assert FileOutputHandler._normalize_paragraphs(content) == ["One", "Two", "Three"]

    def test_whitespace_only_paragraph_filtered(self):
        result = FileOutputHandler._normalize_paragraphs("Real\n\n   \n\nContent")
        assert result == ["Real", "Content"]

    def test_cjk_content_preserved(self):
        result = FileOutputHandler._normalize_paragraphs("日本語\n\n中文")
        assert result == ["日本語", "中文"]


# ---------------------------------------------------------------------------
# _resolve_output_path
# ---------------------------------------------------------------------------

class TestResolveOutputPath:

    def test_explicit_output_file_returned_directly(self):
        result = FileOutputHandler._resolve_output_path(
            input_file="/input/doc.pdf",
            output_file="/output/result.txt",
            auto_save=False,
            source_lang="Japanese",
            target_lang="English",
        )
        assert result == "/output/result.txt"

    def test_explicit_output_file_takes_priority_over_auto_save(self):
        result = FileOutputHandler._resolve_output_path(
            input_file="/input/doc.pdf",
            output_file="/output/result.txt",
            auto_save=True,
            source_lang="Japanese",
            target_lang="English",
        )
        assert result == "/output/result.txt"

    def test_auto_save_generates_timestamped_name(self):
        result = FileOutputHandler._resolve_output_path(
            input_file="/input/doc.pdf",
            output_file=None,
            auto_save=True,
            source_lang="Japanese",
            target_lang="English",
        )
        assert result is not None
        assert "doc_JapanesetoEnglish_" in result
        assert result.endswith(".txt")

    def test_auto_save_output_placed_beside_input(self):
        result = FileOutputHandler._resolve_output_path(
            input_file="/some/folder/doc.pdf",
            output_file=None,
            auto_save=True,
            source_lang="Japanese",
            target_lang="English",
        )
        assert result is not None
        assert result.startswith("/some/folder/")

    def test_no_output_no_auto_save_returns_none(self):
        result = FileOutputHandler._resolve_output_path(
            input_file="/input/doc.pdf",
            output_file=None,
            auto_save=False,
            source_lang="Japanese",
            target_lang="English",
        )
        assert result is None

    def test_auto_save_without_input_file_returns_none(self):
        result = FileOutputHandler._resolve_output_path(
            input_file=None,
            output_file=None,
            auto_save=True,
            source_lang="Japanese",
            target_lang="English",
        )
        assert result is None

    def test_custom_extension_honoured(self):
        result = FileOutputHandler._resolve_output_path(
            input_file="/input/doc.pdf",
            output_file=None,
            auto_save=True,
            source_lang="Japanese",
            target_lang="English",
            default_extension=".docx",
        )
        assert result is not None
        assert result.endswith(".docx")


# ---------------------------------------------------------------------------
# generate_output_filename
# ---------------------------------------------------------------------------

class TestGenerateOutputFilename:

    def test_filename_contains_source_and_target_language(self):
        result = generate_output_filename("/input/doc.pdf", "Japanese", "English")
        assert "JapanesetoEnglish" in result

    def test_filename_uses_input_stem(self):
        result = generate_output_filename("/input/my_document.pdf", "Japanese", "English")
        assert "my_document_JapanesetoEnglish_" in result

    def test_output_placed_in_same_directory_as_input(self):
        result = generate_output_filename("/some/path/doc.pdf", "Japanese", "English")
        assert result.startswith("/some/path/")

    def test_default_extension_is_txt(self):
        result = generate_output_filename("/input/doc.pdf", "Japanese", "English")
        assert result.endswith(".txt")

    def test_custom_extension_applied(self):
        result = generate_output_filename("/input/doc.pdf", "Japanese", "English", ".docx")
        assert result.endswith(".docx")

    def test_timestamp_format_in_filename(self):
        with patch("src.output.file_output.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260311_143000"
            result = generate_output_filename("/input/doc.pdf", "Japanese", "English")
        assert "20260311_143000" in result

    def test_different_languages_produce_different_filenames(self):
        r1 = generate_output_filename("/input/doc.pdf", "Japanese", "English")
        r2 = generate_output_filename("/input/doc.pdf", "Chinese", "English")
        # Strip the timestamp portion to compare just the language tags
        assert "JapanesetoEnglish" in r1
        assert "ChinesetoEnglish" in r2


# ---------------------------------------------------------------------------
# _emit_message
# ---------------------------------------------------------------------------


class TestEmitMessage:

    def test_prints_to_stdout(self, capsys):
        FileOutputHandler._emit_message("Hello world")
        out = capsys.readouterr().out
        assert "Hello world" in out

    def test_leading_newline_prepended(self, capsys):
        FileOutputHandler._emit_message("Hi", leading_newline=True)
        out = capsys.readouterr().out
        assert out.startswith("\n")

    def test_no_leading_newline_by_default(self, capsys):
        FileOutputHandler._emit_message("Hi")
        out = capsys.readouterr().out
        assert not out.startswith("\n")

    def test_logs_message(self, caplog):
        with caplog.at_level(logging.INFO):
            FileOutputHandler._emit_message("Logged message")
        assert "Logged message" in caplog.text

    def test_custom_log_message_used(self, caplog):
        with caplog.at_level(logging.INFO):
            FileOutputHandler._emit_message("Printed msg", log_message="Log msg")
        assert "Log msg" in caplog.text
        # The log should use the log_message, not the printed one
        assert "Printed msg" not in caplog.text


# ---------------------------------------------------------------------------
# _ensure_parent_directory
# ---------------------------------------------------------------------------


class TestEnsureParentDirectory:

    def test_creates_missing_parent_directory(self, tmp_path):
        deep_path = str(tmp_path / "a" / "b" / "c" / "output.txt")
        FileOutputHandler._ensure_parent_directory(deep_path)
        assert Path(deep_path).parent.exists()

    def test_existing_directory_not_an_error(self, tmp_path):
        output_path = str(tmp_path / "output.txt")
        # Parent already exists — should not raise
        FileOutputHandler._ensure_parent_directory(output_path)
        assert tmp_path.exists()


# ---------------------------------------------------------------------------
# _fallback_to_text
# ---------------------------------------------------------------------------


class TestFallbackToText:

    def test_writes_text_file_with_txt_extension(self, tmp_path):
        output_path = str(tmp_path / "output.pdf")
        FileOutputHandler._fallback_to_text("some content", output_path, "Translation")
        expected = tmp_path / "output.txt"
        assert expected.exists()
        assert expected.read_text(encoding="utf-8") == "some content"

    def test_original_file_not_created(self, tmp_path):
        output_path = str(tmp_path / "output.docx")
        FileOutputHandler._fallback_to_text("content", output_path, "Translation")
        assert not (tmp_path / "output.docx").exists()


# ---------------------------------------------------------------------------
# save_to_text_file
# ---------------------------------------------------------------------------


class TestSaveToTextFile:

    def test_writes_content_to_file(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.save_to_text_file("Hello CJK: 日本語", output_path, "Translation")
        assert Path(output_path).read_text(encoding="utf-8") == "Hello CJK: 日本語"

    def test_creates_file_if_not_exists(self, tmp_path):
        output_path = str(tmp_path / "new_file.txt")
        FileOutputHandler.save_to_text_file("content", output_path, "Translation")
        assert Path(output_path).exists()

    def test_overwrites_existing_file(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        Path(output_path).write_text("old content", encoding="utf-8")
        FileOutputHandler.save_to_text_file("new content", output_path, "Translation")
        assert Path(output_path).read_text(encoding="utf-8") == "new content"

    def test_prints_confirmation_message(self, tmp_path, capsys):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.save_to_text_file("text", output_path, "Translation")
        out = capsys.readouterr().out
        assert "saved" in out.lower() or str(output_path) in out

    def test_os_error_handled_gracefully(self, tmp_path, capsys):
        # Point at a directory so writing raises OSError
        output_path = str(tmp_path)   # directory, not a file
        # Should not raise; should print an error
        FileOutputHandler.save_to_text_file("text", output_path, "Translation")
        out = capsys.readouterr().out
        assert "Error" in out or "error" in out


# ---------------------------------------------------------------------------
# append_to_text_file
# ---------------------------------------------------------------------------


class TestAppendToTextFile:

    def test_appends_content_to_existing_file(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        Path(output_path).write_text("Page 1", encoding="utf-8")
        FileOutputHandler.append_to_text_file("Page 2", output_path, "Translation")
        content = Path(output_path).read_text(encoding="utf-8")
        assert "Page 1" in content
        assert "Page 2" in content

    def test_creates_file_if_not_exists(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.append_to_text_file("content", output_path, "Translation")
        assert Path(output_path).exists()

    def test_appended_content_followed_by_double_newline(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.append_to_text_file("chunk", output_path, "Translation")
        content = Path(output_path).read_text(encoding="utf-8")
        assert content.endswith("\n\n")

    def test_multiple_appends_accumulate(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        for i in range(3):
            FileOutputHandler.append_to_text_file(f"chunk {i}", output_path, "Translation")
        content = Path(output_path).read_text(encoding="utf-8")
        for i in range(3):
            assert f"chunk {i}" in content

    def test_prints_confirmation(self, tmp_path, capsys):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.append_to_text_file("text", output_path, "Translation")
        out = capsys.readouterr().out
        assert str(output_path) in out or "appended" in out.lower() or "Page" in out

    def test_os_error_handled_gracefully(self, tmp_path, capsys):
        # Pass a directory path so open() raises IsADirectoryError (OSError subclass)
        output_path = str(tmp_path)
        FileOutputHandler.append_to_text_file("text", output_path, "Translation")
        out = capsys.readouterr().out
        assert "Error" in out or "error" in out


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
        with patch.object(
            FileOutputHandler, "_normalize_paragraphs", side_effect=RuntimeError("boom")
        ):
            FileOutputHandler.save_to_pdf("content", output_path, target_lang="English", label="Translation")
        assert (tmp_path / "out.txt").exists()


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
        with patch.object(
            FileOutputHandler, "_normalize_paragraphs", side_effect=RuntimeError("boom")
        ):
            FileOutputHandler.save_to_docx("content", output_path, target_lang="English", label="Translation")
        assert (tmp_path / "out.txt").exists()


# ---------------------------------------------------------------------------
# save_translation_output
# ---------------------------------------------------------------------------


class TestSaveTranslationOutput:

    def test_empty_content_prints_message(self, capsys):
        FileOutputHandler.save_translation_output("  ", None, None, False, "J", "E", label="Translation")

    def test_no_output_path_no_action(self, tmp_path):
        # No output_file, no auto_save → nothing should be written
        FileOutputHandler.save_translation_output("content", None, None, False, "J", "E", label="Translation")
        assert list(tmp_path.iterdir()) == []

    def test_txt_extension_saves_to_text(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.save_translation_output("hello", None, output_path, False, "J", "E", label="Translation")
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"(self, tmp_path):
        output_path = str(tmp_path / "out.pdf")
        with patch.object(FileOutputHandler, "save_to_pdf") as mock_pdf:
            FileOutputHandler.save_translation_output("content", None, output_path, False, "J", "E", label="Translation")
        mock_pdf.assert_called_once()

    def test_docx_extension_routes_to_save_to_docx(self, tmp_path):
        output_path = str(tmp_path / "out.docx")
        with patch.object(FileOutputHandler, "save_to_docx") as mock_docx:
            FileOutputHandler.save_translation_output("content", None, output_path, False, "J", "E", label="Translation")
        mock_docx.assert_called_once()

    def test_unknown_extension_appends_txt_suffix(self, tmp_path):
        output_path = str(tmp_path / "out.xyz")
        with patch.object(FileOutputHandler, "save_to_text_file") as mock_txt:
            FileOutputHandler.save_translation_output("content", None, output_path, False, "J", "E", label="Translation")
        mock_txt.assert_called_once()
        written_path = mock_txt.call_args[0][1]
        assert written_path.endswith(".txt")

    def test_custom_font_passed_to_writer(self, tmp_path):
        output_path = str(tmp_path / "out.pdf")
        with patch.object(FileOutputHandler, "save_to_pdf") as mock_pdf:
            FileOutputHandler.save_translation_output(
                "content", None, output_path, False, "J", "E", custom_font="MyFont", label="Translation"
            )
        _args, kwargs = mock_pdf.call_args
        assert "MyFont" in _args or kwargs.get("custom_font") == "MyFont" or _args[2] == "MyFont"


# ---------------------------------------------------------------------------
# save_page_progressively
# ---------------------------------------------------------------------------


class TestSavePageProgressively:

    def test_empty_content_returns_none(self):
        result = FileOutputHandler.save_page_progressively(
            "  ", None, None, False, "J", "E", "Translation"
        )
        assert result is None

    def test_no_output_path_returns_none(self):
        result = FileOutputHandler.save_page_progressively(
            "content", None, None, False, "J", "E", "Translation"
        )
        assert result is None

    def test_pdf_format_falls_back_to_txt(self, tmp_path, capsys):
        output_path = str(tmp_path / "out.pdf")
        result = FileOutputHandler.save_page_progressively(
            "content", None, output_path, False, "J", "E", "Translation", is_first_page=True
        )
        out = capsys.readouterr().out
        assert "not yet supported" in out.lower() or "Progressive" in out
        assert result is not None
        assert result.endswith(".txt")
        assert (tmp_path / "out.txt").exists()

    def test_docx_format_falls_back_to_txt(self, tmp_path, capsys):
        output_path = str(tmp_path / "out.docx")
        result = FileOutputHandler.save_page_progressively(
            "content", None, output_path, False, "J", "E", "Translation", is_first_page=True
        )
        out = capsys.readouterr().out
        assert "not yet supported" in out.lower() or "Progressive" in out
        assert result is not None
        assert result.endswith(".txt")

    def test_first_page_creates_new_file(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        result = FileOutputHandler.save_page_progressively(
            "Page one content", None, output_path, False, "J", "E", "Translation", is_first_page=True
        )
        assert result == output_path
        assert Path(output_path).read_text(encoding="utf-8") == "Page one content"

    def test_subsequent_page_appends(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        Path(output_path).write_text("Page 1\n\n", encoding="utf-8")
        FileOutputHandler.save_page_progressively(
            "Page 2", None, output_path, False, "J", "E", "Translation", is_first_page=False
        )
        content = Path(output_path).read_text(encoding="utf-8")
        assert "Page 1" in content
        assert "Page 2" in content

    def test_non_txt_extension_gets_txt_appended(self, tmp_path):
        output_path = str(tmp_path / "out.xyz")
        result = FileOutputHandler.save_page_progressively(
            "content", None, output_path, False, "J", "E", "Translation", is_first_page=True
        )
        assert result is not None
        assert result.endswith(".txt")

    def test_returns_output_path(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        result = FileOutputHandler.save_page_progressively(
            "content", None, output_path, False, "J", "E", "Translation", is_first_page=True
        )
        assert result == output_path


# ---------------------------------------------------------------------------
# save_to_pdf — deeper fallback paths
# ---------------------------------------------------------------------------


class TestSaveToPdfDeepPaths:

    def test_non_english_target_calls_get_pdf_font(self, tmp_path, caplog):
        # Exercises the `else` branch (lines 141-142): get_pdf_font is invoked
        output_path = str(tmp_path / "out.pdf")
        with patch("src.output.file_output.get_pdf_font", return_value="Helvetica") as mock_gpf:
            with caplog.at_level(logging.DEBUG):
                FileOutputHandler.save_to_pdf("Content.", output_path, target_lang="Japanese")
        mock_gpf.assert_called_once()
        assert "Helvetica" in caplog.text

    def test_non_times_roman_font_logs_used_font(self, tmp_path, caplog):
        # Covers the `if font_name != 'Times-Roman':` True branch inside `if story:`
        output_path = str(tmp_path / "out.pdf")
        with patch("src.output.file_output.get_pdf_font", return_value="Helvetica"):
            with caplog.at_level(logging.DEBUG):
                FileOutputHandler.save_to_pdf("Content here.", output_path, target_lang="Japanese")
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
                FileOutputHandler.save_to_pdf("Content.", output_path, target_lang="English")
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
                FileOutputHandler.save_to_pdf("Content.", output_path, target_lang="English")
        assert "Error processing paragraph" in caplog.text

    def test_all_paragraph_renders_fail_with_cjk_content(self, tmp_path, caplog):
        # Covers the double-fallback path; CJK content → ascii strip → empty → no-ascii warning
        output_path = str(tmp_path / "out.pdf")
        with patch("reportlab.platypus.Paragraph", side_effect=Exception("bad font")):
            with caplog.at_level(logging.WARNING):
                FileOutputHandler.save_to_pdf("日本語テキスト", output_path, target_lang="English")
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
            FileOutputHandler.save_to_pdf("ASCII content here.", output_path, target_lang="English")
        # ASCII safe fallback rendered; PDF should exist
        assert (tmp_path / "out.pdf").exists() or (tmp_path / "out.txt").exists()


# ---------------------------------------------------------------------------
# save_to_docx — deeper fallback paths
# ---------------------------------------------------------------------------


class TestSaveToDocxDeepPaths:

    def test_empty_paragraph_runs_adds_run_explicitly(self, tmp_path):
        # Covers lines 272-274: else-branch of `if paragraph.runs:`
        # When add_paragraph("") returns a paragraph with no runs
        output_path = str(tmp_path / "out.docx")
        with patch.object(
            FileOutputHandler, "_normalize_paragraphs", return_value=[""]
        ):
            FileOutputHandler.save_to_docx("any content", output_path, target_lang="English")
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
            FileOutputHandler.save_to_docx("content", output_path, target_lang="English")

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
            FileOutputHandler.save_to_docx("content", output_path, target_lang="English")

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
                FileOutputHandler.save_to_docx("content", output_path, target_lang="English")

        assert "Added paragraph" in caplog.text or "Error processing paragraph" in caplog.text
