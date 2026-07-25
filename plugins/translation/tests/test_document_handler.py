"""Tests for plugins/translation/src/runtime/document_handler.py (registered as src.runtime.document_handler).

Covers document translation and image translation: _parse_page_ranges,
_process_text_based_file, translate_document, translate_custom_text,
process_image_translation, process_image_translation_folder.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

from src.errors import CLIError
from src.models import OutputOptions
from src.runtime.sandbox_processor import SandboxProcessor

_parse_page_ranges = sys.modules["src.runtime.document_handler"]._parse_page_ranges


# ---------------------------------------------------------------------------
# Helpers — build a SandboxProcessor bypassing real service init
# ---------------------------------------------------------------------------

def _make_processor(monkeypatch) -> SandboxProcessor:
    """Create a SandboxProcessor with all real services replaced by MagicMocks."""
    monkeypatch.setattr("src.runtime.sandbox_processor.get_api_key",
                        lambda name: ("fake-key", "Professor Fake"))

    for svc_path in (
        "src.services.base_service.resolve_model",
        "src.services.base_service.maybe_sync_model_pricing",
        "src.services.base_service.get_model_system_role",
        "src.services.base_service.get_model_max_completion_tokens",
    ):
        monkeypatch.setattr(svc_path, MagicMock(return_value="gpt-4o"), raising=False)

    monkeypatch.setattr(
        "src.tracking.token_tracker.TokenTracker.__init__",
        lambda self, professor: None,
    )

    proc = SandboxProcessor.__new__(SandboxProcessor)
    proc.professor_name = "fake"
    proc.professor_display_name = "Professor Fake"
    proc.token_tracker = MagicMock()
    proc.token_tracker.usage_data = {"total_usage": {"total_tokens": 0, "total_cost": 0.0}}
    proc.translation_service = MagicMock()
    proc.image_translation_service = MagicMock()
    proc.image_processor = MagicMock()
    proc.pdf_processor = MagicMock()
    proc.file_output = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# _parse_page_ranges
# ---------------------------------------------------------------------------

class TestParsePageRanges:

    def test_none_returns_all_pages(self):
        assert _parse_page_ranges(None) == [(0, None)]

    def test_single_page(self):
        assert _parse_page_ranges("5") == [(4, 4)]

    def test_range(self):
        assert _parse_page_ranges("1-10") == [(0, 9)]

    def test_multi_range(self):
        result = _parse_page_ranges("4,15-17,20")
        assert result == [(3, 3), (14, 16), (19, 19)]

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError):
            _parse_page_ranges("10-5")

    def test_zero_page_raises(self):
        with pytest.raises(ValueError):
            _parse_page_ranges("0")

    def test_zero_in_range_raises(self):
        with pytest.raises(ValueError):
            _parse_page_ranges("0-5")


# ---------------------------------------------------------------------------
# _process_text_based_file
# ---------------------------------------------------------------------------

class TestProcessTextBasedFile:

    def test_txt_file_translated(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "source.txt"
        f.write_text("Hello world\n\nSecond paragraph", encoding="utf-8")
        proc.translation_service.translate_text_pages.return_value = ["Bonjour monde"]
        pages, registry = proc._process_text_based_file(
            str(f), "txt", None, None, "English", "French",
            OutputOptions(),
        )
        assert pages == ["Bonjour monde"]
        assert registry is None
        proc.translation_service.translate_text_pages.assert_called_once()

    def test_docx_file_translated(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        # Mock DocxProcessor to avoid needing a real docx file
        monkeypatch.setattr(
            "src.runtime.document_handler.DocxProcessor.process_docx_with_pages",
            lambda f, target_page_size: ["Page one text"],
        )
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake")
        proc.translation_service.translate_text_pages.return_value = ["翻訳"]
        pages, registry = proc._process_text_based_file(
            str(f), "docx", None, None, "English", "Japanese",
            OutputOptions(),
        )
        assert pages == ["翻訳"]

    def test_unsupported_file_type_raises_value_error(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "file.csv"
        f.write_text("a,b,c")
        with pytest.raises(ValueError, match="Unsupported text file type"):
            proc._process_text_based_file(
                str(f), "csv", None, None, "English", "French",
                OutputOptions(),
            )

    def test_page_range_beyond_document_raises_cli_error(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        monkeypatch.setattr(
            "src.runtime.document_handler.TxtProcessor.process_txt_with_pages",
            lambda f, target_page_size: ["Only one page"],
        )
        f = tmp_path / "file.txt"
        f.write_text("Only one page")
        with pytest.raises(CLIError, match="does not exist"):
            proc._process_text_based_file(
                str(f), "txt", "5", None, "English", "French",
                OutputOptions(),
            )

    def test_on_progress_threaded_to_translate_text_pages(self, tmp_path, monkeypatch):
        # _process_text_based_file itself doesn't call on_progress directly —
        # it wraps whatever it's given and hands the wrapper to
        # translation_service.translate_text_pages(). Assert that wrapper
        # was actually passed, then drive it by hand to confirm it reports
        # (completed, total) against the mocked service the same way the
        # real one would.
        proc = _make_processor(monkeypatch)
        monkeypatch.setattr(
            "src.runtime.document_handler.TxtProcessor.process_txt_with_pages",
            lambda f, target_page_size: ["one", "two", "three"],
        )
        f = tmp_path / "source.txt"
        f.write_text("one\n\ntwo\n\nthree", encoding="utf-8")
        proc.translation_service.translate_text_pages.return_value = ["a", "b", "c"]
        calls: list = []
        proc._process_text_based_file(
            str(f), "txt", None, None, "English", "French",
            OutputOptions(), on_progress=lambda done, total: calls.append((done, total)),
        )
        _, kwargs = proc.translation_service.translate_text_pages.call_args
        wrapper = kwargs["on_progress"]
        assert wrapper is not None
        wrapper(1, 3)
        wrapper(3, 3)
        assert calls == [(1, 3), (3, 3)]

    def test_on_progress_none_by_default(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "source.txt"
        f.write_text("one", encoding="utf-8")
        proc.translation_service.translate_text_pages.return_value = ["a"]
        proc._process_text_based_file(
            str(f), "txt", None, None, "English", "French", OutputOptions(),
        )
        _, kwargs = proc.translation_service.translate_text_pages.call_args
        assert kwargs["on_progress"] is None

    def test_on_page_text_threaded_to_translate_text_pages(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        monkeypatch.setattr(
            "src.runtime.document_handler.TxtProcessor.process_txt_with_pages",
            lambda f, target_page_size: ["one", "two", "three"],
        )
        f = tmp_path / "source.txt"
        f.write_text("one\n\ntwo\n\nthree", encoding="utf-8")
        proc.translation_service.translate_text_pages.return_value = ["a", "b", "c"]
        calls: list = []
        proc._process_text_based_file(
            str(f), "txt", None, None, "English", "French",
            OutputOptions(), on_page_text=lambda page_number, text: calls.append((page_number, text)),
        )
        _, kwargs = proc.translation_service.translate_text_pages.call_args
        wrapper = kwargs["on_page_text"]
        assert wrapper is not None
        wrapper(1, "translated one")
        wrapper(2, "translated two")
        assert calls == [(1, "translated one"), (2, "translated two")]

    def test_on_page_text_offset_by_completed_pages_across_ranges(self, tmp_path, monkeypatch):
        # A page-range request starting partway through the document (e.g.
        # "6-10") must not restart page numbering at 1 for the second
        # onward range — same offset-correction on_progress already gets.
        proc = _make_processor(monkeypatch)
        monkeypatch.setattr(
            "src.runtime.document_handler.TxtProcessor.process_txt_with_pages",
            lambda f, target_page_size: ["p" + str(i) for i in range(10)],
        )
        f = tmp_path / "source.txt"
        f.write_text("content", encoding="utf-8")
        proc.translation_service.translate_text_pages.return_value = ["x"]
        calls: list = []
        proc._process_text_based_file(
            str(f), "txt", "1,6", None, "English", "French",
            OutputOptions(), on_page_text=lambda page_number, text: calls.append((page_number, text)),
        )
        wrappers = [c.kwargs["on_page_text"] for c in proc.translation_service.translate_text_pages.call_args_list]
        assert len(wrappers) == 2
        wrappers[0](1, "first range page 1")
        wrappers[1](1, "second range page 1")
        # First range starts at page 1 (offset 0); second range ("6") is the
        # 2nd requested page overall, so its own "page 1" reports as
        # absolute page 2.
        assert calls == [(1, "first range page 1"), (2, "second range page 1")]

    def test_on_page_text_none_by_default(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "source.txt"
        f.write_text("one", encoding="utf-8")
        proc.translation_service.translate_text_pages.return_value = ["a"]
        proc._process_text_based_file(
            str(f), "txt", None, None, "English", "French", OutputOptions(),
        )
        _, kwargs = proc.translation_service.translate_text_pages.call_args
        assert kwargs["on_page_text"] is None


# ---------------------------------------------------------------------------
# process_image_translation
# ---------------------------------------------------------------------------

class TestProcessImageTranslation:

    def test_prints_transcript_and_translation(self, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)
        proc.image_translation_service.process_image_translation.return_value = (
            "OCR transcript", "Translated text"
        )
        proc.process_image_translation("/fake/img.jpg", "Chinese", "English")
        out = capsys.readouterr().out
        assert "OCR transcript" in out
        assert "Translated text" in out

    def test_saves_translation_when_output_file_set(self, monkeypatch, tmp_path):
        proc = _make_processor(monkeypatch)
        proc.image_translation_service.process_image_translation.return_value = (
            "", "Translation only"
        )
        opts = OutputOptions(output_file=str(tmp_path / "out.txt"))
        proc.process_image_translation("/fake/img.jpg", "Chinese", "English", opts)
        proc.file_output.save_translation_output.assert_called_once()

    def test_no_transcript_skips_transcript_section(self, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)
        proc.image_translation_service.process_image_translation.return_value = (
            None, "Translation"
        )
        proc.process_image_translation("/fake/img.jpg", "Japanese", "English")
        out = capsys.readouterr().out
        assert "Transcript" not in out


# ---------------------------------------------------------------------------
# translate_custom_text
# ---------------------------------------------------------------------------

class TestTranslateCustomText:

    def test_translates_and_calls_service(self, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)
        inputs = iter(["Hello world", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        proc.translation_service.translate_text.return_value = "Bonjour monde"
        proc.translate_custom_text("English", "French")
        proc.translation_service.translate_text.assert_called_once_with(
            "Hello world", "English", "French"
        )

    def test_empty_text_returns_without_error(self, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)
        inputs = iter(["---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        proc.translate_custom_text("English", "French")
        out = capsys.readouterr().out
        assert "No text provided" in out

    def test_keyboard_interrupt_prints_cancelled(self, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)

        def raise_interrupt(*_):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", raise_interrupt)
        proc.translate_custom_text("English", "French")
        out = capsys.readouterr().out
        assert "cancelled" in out.lower()

    def test_with_abstract_uses_translate_page_text(self, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)
        # First _collect_multiline is for abstract, second for text
        call_count = [0]
        def fake_input():
            call_count[0] += 1
            if call_count[0] <= 2:
                return "abstract text" if call_count[0] == 1 else "---"
            if call_count[0] == 3:
                return "translate this"
            return "---"
        monkeypatch.setattr("builtins.input", fake_input)
        proc.translation_service.translate_page_text.return_value = "结果"
        proc.translate_custom_text("English", "Chinese", abstract=True)
        proc.translation_service.translate_page_text.assert_called_once()


# ---------------------------------------------------------------------------
# translate_document — ImportError for python-docx
# ---------------------------------------------------------------------------

class TestTranslateDocumentImportError:

    def test_missing_python_docx_raises_cli_error(self, monkeypatch, tmp_path):
        """When DocxProcessor raises ImportError mentioning python-docx, CLIError wraps it."""
        proc = _make_processor(monkeypatch)
        docx_file = tmp_path / "doc.docx"
        docx_file.write_bytes(b"fake docx content")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "docx"
        )

        def raise_import(*a, **kw):
            raise ImportError("No module named 'python-docx'")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._process_text_based_file",
            raise_import,
        )

        with pytest.raises(CLIError, match="pip install python-docx"):
            proc.translate_document(str(docx_file), "Chinese", "English")


# ---------------------------------------------------------------------------
# translate_document — image file path
# ---------------------------------------------------------------------------

class TestTranslateDocumentImagePath:

    def test_image_file_delegates_to_process_image_translation(self, monkeypatch, tmp_path):
        proc = _make_processor(monkeypatch)
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"fake image")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "image"
        )
        proc.process_image_translation = MagicMock()
        proc.translate_document(str(img), "Chinese", "English")
        proc.process_image_translation.assert_called_once()

    def test_image_file_exception_raises_cli_error(self, monkeypatch, tmp_path):
        proc = _make_processor(monkeypatch)
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"fake image")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "image"
        )
        proc.process_image_translation = MagicMock(side_effect=RuntimeError("API down"))
        with pytest.raises(CLIError, match="API down"):
            proc.translate_document(str(img), "Chinese", "English")


# ---------------------------------------------------------------------------
# translate_document — save output path
# ---------------------------------------------------------------------------

class TestTranslateDocumentSaveOutput:

    def test_saves_output_when_output_file_specified(self, monkeypatch, tmp_path):
        proc = _make_processor(monkeypatch)
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("Hello content", encoding="utf-8")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "txt"
        )
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._process_text_based_file",
            lambda *a, **kw: (["Translated text"], None)
        )

        out_file = str(tmp_path / "out.txt")
        opts = OutputOptions(output_file=out_file)
        proc.translate_document(str(txt_file), "Chinese", "English", opts=opts)
        proc.file_output.save_translation_output.assert_called_once()

    def test_on_progress_threaded_to_process_text_based_file(self, monkeypatch, tmp_path):
        proc = _make_processor(monkeypatch)
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("Hello content", encoding="utf-8")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "txt"
        )
        received = {}

        def fake_process(self, *a, **kw):
            received["on_progress"] = kw.get("on_progress")
            return ["Translated text"], None

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._process_text_based_file",
            fake_process,
        )
        calls: list = []
        proc.translate_document(
            str(txt_file), "Chinese", "English",
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert received["on_progress"] is not None
        # translate_document passes its on_progress straight through for the
        # simple text-file branches (no offset/wrapping needed — that only
        # happens for the PDF branch, which spans more than one page range).
        received["on_progress"](2, 5)
        assert calls == [(2, 5)]


# ---------------------------------------------------------------------------
# translate_custom_text — save output path
# ---------------------------------------------------------------------------

class TestTranslateCustomTextSaveOutput:

    def test_saves_output_when_output_file_specified(self, monkeypatch, tmp_path, capsys):
        proc = _make_processor(monkeypatch)
        inputs = iter(["Translate this", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

        proc.translation_service.translate_text.return_value = "翻訳結果"
        out_file = str(tmp_path / "result.txt")
        opts = OutputOptions(output_file=out_file)
        proc.translate_custom_text("English", "Japanese", opts=opts)
        proc.file_output.save_translation_output.assert_called_once()


# ---------------------------------------------------------------------------
# process_image_translation_folder — parallel path
# ---------------------------------------------------------------------------

class TestProcessImageTranslationFolderParallel:

    def test_parallel_path_processes_images(self, monkeypatch, tmp_path, capsys):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        for name in ["a.jpg", "b.jpg"]:
            (folder / name).write_bytes(b"fake")

        proc.image_translation_service.process_image_translation = MagicMock(
            return_value=("transcript", "translation")
        )
        proc.image_translation_service._get_model = MagicMock(return_value="gpt-4o")
        proc.image_translation_service._suppress_inline_print = False

        proc.process_image_translation_folder(
            str(folder), "Chinese", "English", OutputOptions(), workers=2
        )
        assert proc.image_translation_service.process_image_translation.call_count == 2

    def test_parallel_worker_exception_stored_as_error(self, monkeypatch, tmp_path, capsys):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "err_imgs"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")

        proc.image_translation_service.process_image_translation = MagicMock(
            side_effect=RuntimeError("worker failed")
        )
        proc.image_translation_service._get_model = MagicMock(return_value="gpt-4o")
        proc.image_translation_service._suppress_inline_print = False

        proc.process_image_translation_folder(
            str(folder), "Chinese", "English", OutputOptions(), workers=2
        )
        out = capsys.readouterr().out
        assert "worker failed" in out or "Error" in out


# ---------------------------------------------------------------------------
# Additional branch-coverage tests
# ---------------------------------------------------------------------------

class TestProcessTextBasedFilePageRange:
    """end_page not None → actual_end = min(end_page, ...) branch."""

    def test_page_range_with_explicit_end_clamps_to_document_length(self, tmp_path, monkeypatch):
        """When page_nums='1-5' but doc only has 2 pages, actual_end is clamped to 1."""
        proc = _make_processor(monkeypatch)
        monkeypatch.setattr(
            "src.runtime.document_handler.TxtProcessor.process_txt_with_pages",
            lambda f, target_page_size: ["Page one", "Page two"],
        )
        f = tmp_path / "short.txt"
        f.write_text("Page one\n\nPage two", encoding="utf-8")
        proc.translation_service.translate_text_pages.return_value = ["翻訳"]
        pages, _ = proc._process_text_based_file(
            str(f), "txt", "1-5", None, "English", "Japanese",
            OutputOptions(),
        )
        assert pages == ["翻訳"]
        proc.translation_service.translate_text_pages.assert_called_once()

    def test_exact_page_range_within_document(self, tmp_path, monkeypatch):
        """page_nums='1-2' with 3-page doc — end_page=1 (0-based) is within range."""
        proc = _make_processor(monkeypatch)
        monkeypatch.setattr(
            "src.runtime.document_handler.TxtProcessor.process_txt_with_pages",
            lambda f, target_page_size: ["Page one", "Page two", "Page three"],
        )
        f = tmp_path / "three.txt"
        f.write_text("Page one\n\nPage two\n\nPage three", encoding="utf-8")
        proc.translation_service.translate_text_pages.return_value = ["結果"]
        pages, _ = proc._process_text_based_file(
            str(f), "txt", "1-2", None, "English", "Japanese",
            OutputOptions(),
        )
        assert pages == ["結果"]


class TestTranslateCustomTextExceptionHandling:
    """Generic Exception from translate_text raises CLIError."""

    def test_api_exception_raises_cli_error(self, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)
        inputs = iter(["Hello world", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        proc.translation_service.translate_text.side_effect = RuntimeError("API failure")
        with pytest.raises(CLIError, match="API failure"):
            proc.translate_custom_text("English", "French")


class TestTranslateDocumentAbstractFlag:
    """abstract=True triggers _collect_multiline for abstract text."""

    def test_abstract_flag_collects_abstract_then_translates(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.txt"
        f.write_text("Main content", encoding="utf-8")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "txt",
        )
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._process_text_based_file",
            lambda self, *a, **kw: (["Translated"], None),
        )
        abstract_calls = []
        def fake_collect(self_inner, label: str) -> str:
            abstract_calls.append(label)
            return "Abstract text here"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._collect_multiline",
            fake_collect,
        )
        proc.translate_document(str(f), "English", "Japanese", abstract=True)
        assert any("Abstract" in c for c in abstract_calls)


class TestProcessImageTranslationFolderSequentialTranscript:
    """Sequential path prints transcript when non-empty."""

    def test_non_empty_transcript_is_printed(self, monkeypatch, tmp_path, capsys):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "scan.jpg").write_bytes(b"fake")

        proc.image_translation_service.process_image_translation = MagicMock(
            return_value=("OCR transcript text", "Translation result")
        )
        proc.process_image_translation_folder(
            str(folder), "Japanese", "English", OutputOptions(), workers=1
        )
        out = capsys.readouterr().out
        assert "OCR transcript text" in out
        assert "Translation result" in out

    def test_empty_transcript_is_not_printed_as_section(self, monkeypatch, tmp_path, capsys):
        """Sequential path: empty transcript skips print_section for Transcript."""
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "scan.jpg").write_bytes(b"fake")

        proc.image_translation_service.process_image_translation = MagicMock(
            return_value=("", "Translation only")
        )
        proc.process_image_translation_folder(
            str(folder), "Japanese", "English", OutputOptions(), workers=1
        )
        out = capsys.readouterr().out
        assert "Translation only" in out


class TestTranslateDocumentUnknownFileType:
    """else-branch when file_type is unrecognised in translate_document."""

    def test_unknown_type_raises_cli_error(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "data.csv"
        f.write_text("a,b,c")
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "csv",
        )
        with pytest.raises(CLIError, match="Cannot translate file type"):
            proc.translate_document(str(f), "English", "Japanese")


class TestTranslateDocumentPDFPath:
    """PDF branch in translate_document."""

    def test_pdf_file_delegates_to_pdf_processor(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "pdf",
        )
        proc.pdf_processor.process_pdf = MagicMock(return_value=iter([MagicMock()]))
        proc.translation_service.translate_document = MagicMock(return_value=["翻訳結果"])
        proc.translate_document(str(f), "Japanese", "English")
        proc.translation_service.translate_document.assert_called_once()


class TestTranslateDocumentExceptionHandlers:
    """ImportError (non-python-docx) and generic Exception in translate_document."""

    def test_non_python_docx_import_error_raises_cli_error(self, tmp_path, monkeypatch):
        """ImportError without 'python-docx' in message wraps as 'Import error'."""
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake")
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "docx",
        )
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._process_text_based_file",
            lambda self, *a, **kw: (_ for _ in ()).throw(ImportError("some other import error")),
        )
        with pytest.raises(CLIError, match="Import error"):
            proc.translate_document(str(f), "English", "Japanese")

    def test_generic_exception_from_processing_raises_cli_error(self, tmp_path, monkeypatch):
        """Generic Exception from _process_text_based_file raises CLIError."""
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.txt"
        f.write_text("text content")
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "txt",
        )
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._process_text_based_file",
            lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("translation api down")),
        )
        with pytest.raises(CLIError, match="translation api down"):
            proc.translate_document(str(f), "English", "Japanese")


class TestProcessImageTranslationFolderEdgeCases:
    """Remaining gaps in process_image_translation_folder."""

    def test_empty_folder_raises_cli_error(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "empty"
        folder.mkdir()
        with pytest.raises(CLIError, match="No image files found"):
            proc.process_image_translation_folder(str(folder), "Japanese", "English")

    def test_sequential_exception_in_loop_continues(self, tmp_path, monkeypatch, capsys):
        """Exception in sequential loop prints error and continues."""
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "err.jpg").write_bytes(b"fake")
        (folder / "ok.jpg").write_bytes(b"fake")

        call_count = [0]
        def flaky(path, src_lang, tgt_lang):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("translation failed")
            return ("", "translation")

        proc.image_translation_service.process_image_translation = flaky
        proc.process_image_translation_folder(str(folder), "Japanese", "English", workers=1)
        out = capsys.readouterr().out
        assert "ERROR" in out

    def test_sequential_with_auto_save_calls_file_output(self, tmp_path, monkeypatch):
        """Sequential path with auto_save=True calls file_output.save_translation_output."""
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")

        proc.image_translation_service.process_image_translation = MagicMock(
            return_value=("", "Translation")
        )
        opts = OutputOptions(auto_save=True)
        proc.process_image_translation_folder(str(folder), "Japanese", "English", opts=opts, workers=1)
        proc.file_output.save_translation_output.assert_called_once()

    def test_on_progress_called_per_image_in_order(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")
        (folder / "b.jpg").write_bytes(b"fake")
        (folder / "c.jpg").write_bytes(b"fake")

        proc.image_translation_service.process_image_translation = MagicMock(
            return_value=("", "Translation")
        )
        calls: list = []
        proc.process_image_translation_folder(
            str(folder), "Japanese", "English", workers=1,
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert calls == [(1, 3), (2, 3), (3, 3)]

    def test_on_progress_called_even_when_an_image_errors(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "err.jpg").write_bytes(b"fake")
        (folder / "ok.jpg").write_bytes(b"fake")

        def flaky(path, src_lang, tgt_lang):
            if "err" in path:
                raise RuntimeError("boom")
            return ("", "translation")

        proc.image_translation_service.process_image_translation = flaky
        calls: list = []
        proc.process_image_translation_folder(
            str(folder), "Japanese", "English", workers=1,
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert calls == [(1, 2), (2, 2)]

    def test_on_progress_none_by_default(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")
        proc.image_translation_service.process_image_translation = MagicMock(
            return_value=("", "Translation")
        )
        # Must not raise (i.e. must not try calling None()).
        proc.process_image_translation_folder(str(folder), "Japanese", "English", workers=1)

    def test_parallel_with_auto_save_calls_file_output(self, tmp_path, monkeypatch):
        """Parallel path with auto_save=True calls file_output.save_translation_output."""
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")
        (folder / "b.jpg").write_bytes(b"fake")

        proc.image_translation_service.process_image_translation = MagicMock(
            return_value=("", "Translation")
        )
        proc.image_translation_service._get_model = MagicMock(return_value="gpt-4o")
        proc.image_translation_service._suppress_inline_print = False
        opts = OutputOptions(auto_save=True)
        proc.process_image_translation_folder(str(folder), "Japanese", "English", opts=opts, workers=2)
        proc.file_output.save_translation_output.assert_called_once()

    def test_parallel_translation_output_uses_natural_filename_order(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "imgs_order"
        folder.mkdir()
        for name in ["page_1.jpg", "page_2.jpg", "page_10.jpg"]:
            (folder / name).write_bytes(b"fake")

        def _translate_side_effect(file_path, *_args, **_kwargs):
            name = os.path.basename(file_path)
            return "", f"TR({name})"

        proc.image_translation_service.process_image_translation = MagicMock(
            side_effect=_translate_side_effect
        )
        proc.image_translation_service._get_model = MagicMock(return_value="gpt-4o")
        proc.image_translation_service._suppress_inline_print = False

        opts = OutputOptions(output_file="out.txt")
        proc.process_image_translation_folder(str(folder), "Japanese", "English", opts=opts, workers=2)

        saved_text = proc.file_output.save_translation_output.call_args.args[0]
        i1 = saved_text.index("=== page_1.jpg ===")
        i2 = saved_text.index("=== page_2.jpg ===")
        i10 = saved_text.index("=== page_10.jpg ===")
        assert i1 < i2 < i10
