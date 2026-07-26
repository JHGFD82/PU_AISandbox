"""Coverage tests for src/runtime/document_handler.py — uncovered branches."""

import pytest
from unittest.mock import MagicMock

from src.errors import CLIError
from src.models import OutputOptions
from src.runtime.sandbox_processor import SandboxProcessor


# ---------------------------------------------------------------------------
# Shared processor factory (same pattern as test_sandbox_processor.py)
# ---------------------------------------------------------------------------

def _make_processor(monkeypatch) -> SandboxProcessor:
    monkeypatch.setattr("src.runtime.sandbox_processor.get_api_key",
                        lambda name: ("fake-key", "Professor Fake"))
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
    proc.image_processor_service = MagicMock()
    proc.image_translation_service = MagicMock()
    proc.prompt_service = MagicMock()
    proc.transcription_review_service = MagicMock()
    proc.image_processor = MagicMock()
    proc.pdf_processor = MagicMock()
    proc.file_output = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# _process_text_based_file — table_aware=True path (lines 116-117)
# ---------------------------------------------------------------------------

class TestProcessTextBasedFileTableAware:

    def test_table_aware_calls_process_docx_for_translation(self, tmp_path, monkeypatch):
        """When table_aware=True the method uses process_docx_for_translation."""
        proc = _make_processor(monkeypatch)
        fake_registry = {"[TABLE_1]": [["Header"], ["Cell"]]}
        monkeypatch.setattr(
            "src.runtime.document_handler.process_docx_for_translation",
            lambda f, target_page_size: (["Page text"], fake_registry),
        )
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake")
        proc.translation_service.translate_text_pages.return_value = ["翻訳"]
        pages, registry = proc._process_text_based_file(
            str(f), "docx", None, None, "Japanese", "English",
            OutputOptions(), table_aware=True,
        )
        assert pages == ["翻訳"]
        assert registry == fake_registry

    def test_table_aware_false_returns_none_registry(self, tmp_path, monkeypatch):
        """When table_aware=False registry is None."""
        proc = _make_processor(monkeypatch)
        monkeypatch.setattr(
            "src.runtime.document_handler.DocxProcessor.process_docx_with_pages",
            lambda f, target_page_size: ["Page text"],
        )
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake")
        proc.translation_service.translate_text_pages.return_value = ["翻訳"]
        pages, registry = proc._process_text_based_file(
            str(f), "docx", None, None, "Japanese", "English",
            OutputOptions(), table_aware=False,
        )
        assert registry is None


# ---------------------------------------------------------------------------
# translate_document — preserve_media + docx (lines 219-222)
# ---------------------------------------------------------------------------

class TestTranslateDocumentPreserveMediaDocx:

    def test_preserve_media_docx_extracts_media(self, tmp_path, monkeypatch):
        """preserve_media=True with a DOCX file calls DocxProcessor.extract_media."""
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake docx content")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "docx",
        )
        fake_media = [MagicMock()]
        monkeypatch.setattr(
            "src.runtime.document_handler.DocxProcessor.extract_media",
            lambda fobj: fake_media,
        )
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._process_text_based_file",
            lambda self, *a, **kw: (["Translated"], None),
        )
        opts = OutputOptions(output_file=str(tmp_path / "out.docx"), preserve_media=True)
        proc.translate_document(str(f), "Chinese", "English", opts=opts)
        # save_translation_output must have received media
        call_kwargs = proc.file_output.save_translation_output.call_args[1]
        assert call_kwargs.get("media") is fake_media


# ---------------------------------------------------------------------------
# translate_document — preserve_media + pdf (lines 224-227)
# ---------------------------------------------------------------------------

class TestTranslateDocumentPreserveMediaPdf:

    def test_preserve_media_pdf_calls_pdf_media_extractor(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "pdf",
        )
        fake_media = [MagicMock()]
        monkeypatch.setattr(
            "src.runtime.document_handler.PdfMediaExtractor.extract_media",
            lambda fobj: fake_media,
        )
        proc.pdf_processor.process_pdf = MagicMock(return_value=iter([MagicMock()]))
        proc.translation_service.translate_document = MagicMock(return_value=["翻訳"])

        opts = OutputOptions(output_file=str(tmp_path / "out.docx"), preserve_media=True)
        proc.translate_document(str(f), "Japanese", "English", opts=opts)
        call_kwargs = proc.file_output.save_translation_output.call_args[1]
        assert call_kwargs.get("media") is fake_media


# ---------------------------------------------------------------------------
# translate_document — docx with source_table_registry (lines 263-268)
# ---------------------------------------------------------------------------

class TestTranslateDocumentTableRegistry:

    def test_table_registry_entries_are_translated(self, tmp_path, monkeypatch):
        """When _process_text_based_file returns a table registry it is translated
        row-by-row before being forwarded to save_translation_output."""
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake docx content")

        raw_registry = {"[TABLE_1]": [["Header"], ["Row 1"]]}
        translated_grid = [["ヘッダー"], ["行 1"]]

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "docx",
        )
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._process_text_based_file",
            lambda self, *a, **kw: (["[TABLE_1]"], raw_registry),
        )
        proc.translation_service.translate_table_grid = MagicMock(return_value=translated_grid)

        opts = OutputOptions(output_file=str(tmp_path / "out.docx"))
        proc.translate_document(str(f), "English", "Japanese", opts=opts)

        proc.translation_service.translate_table_grid.assert_called_once_with(
            [["Header"], ["Row 1"]], "English", "Japanese"
        )
        call_kwargs = proc.file_output.save_translation_output.call_args[1]
        assert call_kwargs.get("table_registry") == {"[TABLE_1]": translated_grid}


# ---------------------------------------------------------------------------
# translate_document — scanned PDF (lines 180-211)
# ---------------------------------------------------------------------------

class TestTranslateDocumentScannedPdf:

    def test_scanned_pdf_missing_fitz_raises_cli_error(self, tmp_path, monkeypatch):
        """When fitz (PyMuPDF) is not installed, scanned=True raises a CLIError."""
        proc = _make_processor(monkeypatch)
        f = tmp_path / "scan.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "pdf",
        )
        # Simulate missing fitz
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "fitz":
                raise ImportError("No module named 'fitz'")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", mock_import)

        with pytest.raises(CLIError, match="pymupdf"):
            proc.translate_document(str(f), "Japanese", "English", scanned=True)

    def test_scanned_pdf_calls_process_image_translation_folder(self, tmp_path, monkeypatch):
        """When fitz is available, scanned PDF renders pages and calls process_image_translation_folder."""
        import sys
        import types

        proc = _make_processor(monkeypatch)
        f = tmp_path / "scan.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "pdf",
        )

        # Build a minimal fitz stub
        fake_page = MagicMock()
        fake_pixmap = MagicMock()
        fake_pixmap.save = MagicMock()
        fake_page.get_pixmap = MagicMock(return_value=fake_pixmap)

        fake_doc = MagicMock()
        fake_doc.__len__ = lambda self: 2
        fake_doc.__getitem__ = lambda self, i: fake_page
        fake_doc.close = MagicMock()

        fake_fitz = types.ModuleType("fitz")
        fake_fitz.open = MagicMock(return_value=fake_doc)
        monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

        proc.process_image_translation_folder = MagicMock()

        proc.translate_document(str(f), "Japanese", "English", scanned=True)

        proc.process_image_translation_folder.assert_called_once()

    def test_scanned_pdf_page_out_of_range_raises_cli_error(self, tmp_path, monkeypatch):
        """Page number beyond total_pages in scanned PDF path raises CLIError."""
        import sys
        import types

        proc = _make_processor(monkeypatch)
        f = tmp_path / "scan.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "pdf",
        )

        fake_doc = MagicMock()
        fake_doc.__len__ = lambda self: 1  # only 1 page
        fake_doc.close = MagicMock()

        fake_fitz = types.ModuleType("fitz")
        fake_fitz.open = MagicMock(return_value=fake_doc)
        monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

        # Request page 5 which doesn't exist
        with pytest.raises(CLIError, match="does not exist"):
            proc.translate_document(str(f), "Japanese", "English", scanned=True, page_nums="5")

    def test_scanned_pdf_generic_exception_raises_cli_error(self, tmp_path, monkeypatch):
        """Non-CLIError inside scanned path is wrapped in CLIError."""
        import sys
        import types

        proc = _make_processor(monkeypatch)
        f = tmp_path / "scan.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor._detect_and_validate_file",
            lambda self, fp: "pdf",
        )

        fake_fitz = types.ModuleType("fitz")
        fake_fitz.open = MagicMock(side_effect=RuntimeError("corrupt PDF"))
        monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

        with pytest.raises(CLIError, match="corrupt PDF"):
            proc.translate_document(str(f), "Japanese", "English", scanned=True)
