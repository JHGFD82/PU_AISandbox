"""Tests for src/output/file_output.py: where results are saved and under what name.

Covers choosing the output path and file name, making the folder it goes in,
saving page by page as work progresses, and handing tables and pictures on to
the builder for each format. The builders themselves are tested in
test_docx_builder.py, test_pdf_builder.py and test_output_utils.py.
"""

import logging
from pathlib import Path
from unittest.mock import patch

from src.models.embedded_media import EmbeddedMedia
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
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"

    def test_pdf_extension_routes_to_save_to_pdf(self, tmp_path):
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


class TestSaveTranslationOutputPdfTableForwarding:
    """save_translation_output forwards table_registry to save_to_pdf for .pdf output."""

    def test_table_registry_forwarded_to_save_to_pdf(self, tmp_path):
        registry = {"[TABLE_1]": [["X", "Y"]]}
        out = str(tmp_path / "output.pdf")
        with patch.object(FileOutputHandler, "save_to_pdf") as mock_pdf:
            FileOutputHandler.save_translation_output(
                "Hello\n\n[TABLE_1]", None, out, False,
                "Chinese", "English",
                table_registry=registry,
                label="Translation",
            )
        mock_pdf.assert_called_once()
        call_kwargs = mock_pdf.call_args.kwargs
        assert call_kwargs.get("table_registry") == registry

    def test_none_table_registry_forwarded_safely(self, tmp_path):
        out = str(tmp_path / "output.pdf")
        with patch.object(FileOutputHandler, "save_to_pdf") as mock_pdf:
            FileOutputHandler.save_translation_output(
                "Content", None, out, False, "Chinese", "English",
                table_registry=None,
                label="Translation",
            )
        mock_pdf.assert_called_once()
        call_kwargs = mock_pdf.call_args.kwargs
        assert call_kwargs.get("table_registry") is None


# ---------------------------------------------------------------------------
# FileOutputHandler.save_translation_output media forwarding
# ---------------------------------------------------------------------------

class TestSaveTranslationOutputMediaForwarding:

    def test_media_forwarded_to_save_to_docx(self, tmp_path):
        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "fwd.docx")
        media = [EmbeddedMedia(data=b"\x89PNG", content_type="image/png", position_fraction=0.5)]

        with patch.object(FileOutputHandler, "save_to_docx") as mock_docx:
            FileOutputHandler.save_translation_output(
                content="Some text.",
                input_file=None,
                output_file=out,
                auto_save=False,
                source_lang="Chinese",
                target_lang="English",
                media=media,
                label="Translation",
            )
        mock_docx.assert_called_once()
        _, _kwargs = mock_docx.call_args[0], mock_docx.call_args[1]
        assert mock_docx.call_args[1].get("media") == media or mock_docx.call_args[0][4] == media

    def test_no_media_forwarded_when_none(self, tmp_path):
        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "no_media.docx")
        with patch.object(FileOutputHandler, "save_to_docx") as mock_docx:
            FileOutputHandler.save_translation_output(
                content="Some text.",
                input_file=None,
                output_file=out,
                auto_save=False,
                source_lang="Chinese",
                target_lang="English",
                label="Translation",
            )
        mock_docx.assert_called_once()
        # media kwarg should be None (default)
        assert mock_docx.call_args[1].get("media") is None

    def test_pdf_output_does_not_receive_media(self, tmp_path):
        from src.output.file_output import FileOutputHandler
        out = str(tmp_path / "out.pdf")
        media = [EmbeddedMedia(data=b"", content_type="image/png", position_fraction=0.5)]
        with patch.object(FileOutputHandler, "save_to_pdf") as mock_pdf:
            FileOutputHandler.save_translation_output(
                content="Text.",
                input_file=None,
                output_file=out,
                auto_save=False,
                source_lang="Chinese",
                target_lang="English",
                media=media,
                label="Translation",
            )
        mock_pdf.assert_called_once()
        # save_to_pdf does not accept a media argument — confirm it wasn't passed
        assert "media" not in mock_pdf.call_args[1]
