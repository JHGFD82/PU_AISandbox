"""Tests for plugins/transcription/plugin.py's run(): what ``transcribe`` and ``transcription_review`` do when typed.

The sandbox is a stand-in that records what it is asked to do, so these tests
check which work each input leads to — not the reading itself, which is tested
with the services and runtime handlers.
"""

import argparse
import os
from unittest.mock import MagicMock

import pytest

import plugins.transcription.plugin as plugin_mod
from src.errors import CLIError


@pytest.fixture
def sandbox(monkeypatch):
    """The SandboxProcessor run() builds, replaced by a recording stand-in."""
    fake = MagicMock()
    fake._sampling_kwargs.return_value = {}
    fake._resolve_output_path.return_value = "/out/result.txt"
    fake._collect_notes.return_value = ("system note", "user note")
    fake.image_processor_service.build_prompts.return_value = ("ocr system", "ocr user")
    fake.image_processor_service._get_model.return_value = "gpt-4o"
    fake.transcription_review_service.build_prompts.return_value = ("review system", "review user")
    fake.transcription_review_service._get_model.return_value = "gpt-4o"
    fake.transcription_review_service.review_transcription.return_value = '{"errors": []}'
    built = MagicMock(return_value=fake)
    monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", built)
    fake.built_with = built
    return fake


def _run(command, **flags):
    defaults = dict(command=command, language_code="English", input_file=None, notes=False,
                    dry_run=False, workers=1, custom_text=False, output_file=None)
    args = argparse.Namespace(**(defaults | flags))
    plugin_mod.plugin.run(args, "heller", "gpt-4o", 0.2, 0.9, 500)


class TestTheSandbox:
    def test_it_is_built_for_the_person_with_the_settings_given(self, sandbox):
        _run("transcribe", dry_run=True)
        sandbox.built_with.assert_called_once_with(
            "heller", model="gpt-4o", temperature=0.2, top_p=0.9, max_tokens=500)


class TestTranscribe:
    def test_a_picture_is_read(self, sandbox, tmp_path):
        picture = tmp_path / "page.png"
        picture.write_bytes(b"x")
        sandbox._detect_and_validate_file.return_value = "image"
        _run("transcribe", input_file=str(picture))
        sandbox.process_image.assert_called_once_with(str(picture), "English", "/out/result.txt")

    def test_a_folder_of_pictures_is_read_with_the_workers_asked_for(self, sandbox, tmp_path):
        _run("transcribe", input_file=str(tmp_path), workers=4)
        sandbox.process_image_folder.assert_called_once_with(
            str(tmp_path), "English", "/out/result.txt", workers=4)

    def test_a_pdf_of_scans_is_read_page_by_page(self, sandbox, tmp_path):
        pdf = tmp_path / "scans.pdf"
        pdf.write_bytes(b"%PDF")
        sandbox._detect_and_validate_file.return_value = "pdf"
        _run("transcribe", input_file=str(pdf), workers=3)
        sandbox.process_scanned_pdf.assert_called_once_with(
            str(pdf), "English", "/out/result.txt", workers=3)

    def test_a_relative_path_is_made_whole_first(self, sandbox, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "page.png").write_bytes(b"x")
        sandbox._detect_and_validate_file.return_value = "image"
        _run("transcribe", input_file="page.png")
        assert sandbox.process_image.call_args.args[0] == os.path.join(str(tmp_path), "page.png")

    def test_a_document_that_is_not_a_picture_is_refused_by_name(self, sandbox, tmp_path):
        letter = tmp_path / "letter.docx"
        letter.write_bytes(b"x")
        sandbox._detect_and_validate_file.return_value = "docx"
        with pytest.raises(CLIError, match="'letter.docx' is docx"):
            _run("transcribe", input_file=str(letter))

    def test_no_input_says_how_to_give_one(self, sandbox):
        with pytest.raises(CLIError, match="Use -i"):
            _run("transcribe")

    def test_a_dry_run_shows_the_prompts_and_reads_nothing(self, sandbox, tmp_path):
        _run("transcribe", input_file=str(tmp_path), dry_run=True)
        model, system, user = sandbox._dry_run_display.call_args.args
        assert (model, system, user) == ("gpt-4o", "ocr system", "ocr user")
        sandbox.process_image_folder.assert_not_called()

    def test_notes_asked_for_are_put_on_the_service(self, sandbox):
        _run("transcribe", notes=True, dry_run=True)
        sandbox._collect_notes.assert_called_once_with("ocr system", "ocr user")
        assert sandbox.image_processor_service.system_note == "system note"
        assert sandbox.image_processor_service.user_note == "user note"

    def test_notes_typed_on_the_command_line_are_applied(self, sandbox):
        _run("transcribe", dry_run=True)
        assert sandbox._apply_inline_notes.call_args.args[0] is sandbox.image_processor_service


class TestTranscriptionReview:
    def test_a_saved_transcription_is_reviewed(self, sandbox, tmp_path):
        saved = tmp_path / "transcription.txt"
        saved.write_text("Teh quick brown fox", encoding="utf-8")
        _run("transcription_review", input_file=str(saved))
        sandbox.transcription_review_service.review_transcription.assert_called_once_with(
            "Teh quick brown fox", "English")

    def test_the_report_is_saved_where_asked(self, sandbox, tmp_path):
        saved = tmp_path / "transcription.txt"
        saved.write_text("text", encoding="utf-8")
        report = tmp_path / "report.json"
        sandbox._resolve_output_path.return_value = str(report)
        _run("transcription_review", input_file=str(saved))
        assert report.read_text(encoding="utf-8") == '{"errors": []}'

    def test_pasted_text_is_reviewed(self, sandbox):
        sandbox._collect_multiline.return_value = "pasted text"
        _run("transcription_review", custom_text=True)
        sandbox.transcription_review_service.review_transcription.assert_called_once_with(
            "pasted text", "English")

    def test_a_file_that_is_not_there_is_named(self, sandbox, tmp_path):
        with pytest.raises(CLIError, match="not found"):
            _run("transcription_review", input_file=str(tmp_path / "missing.txt"))

    def test_an_empty_file_is_refused(self, sandbox, tmp_path):
        empty = tmp_path / "empty.txt"
        empty.write_text("  \n", encoding="utf-8")
        with pytest.raises(CLIError, match="is empty"):
            _run("transcription_review", input_file=str(empty))

    def test_nothing_pasted_is_refused(self, sandbox):
        sandbox._collect_multiline.return_value = "   "
        with pytest.raises(CLIError, match="No transcription text"):
            _run("transcription_review", custom_text=True)

    def test_no_input_explains_what_the_review_expects(self, sandbox):
        """People reach for the original scan; the review wants the transcription of it."""
        with pytest.raises(CLIError, match="not the original document or image"):
            _run("transcription_review")

    def test_a_dry_run_shows_the_prompts_and_reviews_nothing(self, sandbox):
        _run("transcription_review", dry_run=True)
        model, system, user = sandbox._dry_run_display.call_args.args
        assert (model, system, user) == ("gpt-4o", "review system", "review user")
        sandbox.transcription_review_service.review_transcription.assert_not_called()

    def test_notes_asked_for_are_put_on_the_service(self, sandbox):
        _run("transcription_review", notes=True, dry_run=True)
        sandbox._collect_notes.assert_called_once_with("review system", "review user")
        assert sandbox.transcription_review_service.system_note == "system note"
        assert sandbox.transcription_review_service.user_note == "user note"


class TestFromTheWebForm:
    """The two paths of run_ui_action that its own tests do not reach."""

    def _run_ui(self, sandbox, tmp_path, file_path, **fields):
        return plugin_mod.plugin.run_ui_action(
            fields={"target_language": "en", "file_path": str(file_path), **fields},
            professor="heller", model=None, on_progress=None,
            output_dir=str(tmp_path / "job_output"),
        )

    def test_a_pdf_is_read_page_by_page(self, sandbox, tmp_path):
        pdf = tmp_path / "scans.pdf"
        pdf.write_bytes(b"%PDF")

        def write_output(file_path, language, output_path, **kwargs):
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("page text")

        sandbox.process_scanned_pdf.side_effect = write_output
        result = self._run_ui(sandbox, tmp_path, pdf)
        sandbox.process_scanned_pdf.assert_called_once()
        assert "pages of 'scans.pdf'" in result.summary

    def test_a_temperature_that_is_not_a_number_is_refused(self, sandbox, tmp_path):
        picture = tmp_path / "page.png"
        picture.write_bytes(b"x")
        with pytest.raises(CLIError, match="Invalid temperature 'warm'"):
            self._run_ui(sandbox, tmp_path, picture, temperature="warm")
