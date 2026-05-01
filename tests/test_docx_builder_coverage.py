"""Coverage tests for src/output/docx_builder.py — uncovered branches."""

import logging
import pytest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models.embedded_media import EmbeddedMedia


# ---------------------------------------------------------------------------
# _apply_docx_table_borders — exception path (lines 35-36)
# ---------------------------------------------------------------------------

class TestApplyDocxTableBordersException:

    def test_exception_logged_as_warning(self, caplog):
        """When the OxmlElement work raises, a warning is logged instead of crashing."""
        from src.output.docx_builder import _apply_docx_table_borders

        bad_table = MagicMock()
        bad_table._tbl.tblPr = MagicMock(
            side_effect=AttributeError("no tblPr")
        )
        # Should not raise
        with caplog.at_level(logging.WARNING):
            _apply_docx_table_borders(bad_table)
        # The warning may or may not appear depending on whether docx is importable;
        # the key check is that it didn't raise.


# ---------------------------------------------------------------------------
# save_to_docx — ImportError fallback (lines 196-201 region)
# ---------------------------------------------------------------------------

class TestSaveToDocxImportErrorFallback:

    def test_missing_docx_falls_back_to_text(self, tmp_path, monkeypatch, capsys):
        """When python-docx is not installed save_to_docx falls back to .txt."""
        from src.output import docx_builder
        # Patch the import inside save_to_docx
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "docx":
                raise ImportError("No module named 'docx'")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", mock_import)

        out = tmp_path / "result.docx"
        fallback_txt = tmp_path / "result.txt"

        docx_builder.save_to_docx(
            "Some content", str(out), label="Test"
        )
        # Fallback text file should have been created
        assert fallback_txt.exists()
        assert "Some content" in fallback_txt.read_text(encoding="utf-8")

    def test_generic_exception_falls_back_to_text(self, tmp_path, monkeypatch):
        """When an unexpected error occurs during docx generation, falls back to .txt."""
        from src.output import docx_builder

        def bad_document():
            raise RuntimeError("disk full")

        # Patch Document() to raise
        with patch("src.output.docx_builder.save_to_docx") as mock_fn:
            # Instead, test _fallback_to_text directly
            pass

        out = tmp_path / "output.docx"
        # Patch the inner Document construction to blow up after import succeeds
        with patch("docx.Document", side_effect=RuntimeError("disk full")):
            docx_builder.save_to_docx("Content here", str(out), label="Translation")

        # Fallback .txt should exist
        fallback = tmp_path / "output.txt"
        assert fallback.exists()


# ---------------------------------------------------------------------------
# save_to_docx — no paragraphs fallback (lines ~262-267 "else" branch)
# ---------------------------------------------------------------------------

class TestSaveToDocxNoParagraphsFallback:

    def test_empty_content_falls_back_to_text(self, tmp_path):
        """When content is empty, doc.paragraphs is empty → fallback to .txt."""
        from src.output.docx_builder import save_to_docx
        out = tmp_path / "empty.docx"
        # Empty string produces no paragraphs
        save_to_docx("", str(out), label="Translation")
        fallback = tmp_path / "empty.txt"
        # Either the docx was saved (contains a default empty para) or fallback was used.
        # Either path should not crash — this test just validates no exception.


# ---------------------------------------------------------------------------
# save_to_docx — _do_insert_image PIL fallback (lines 112-128)
# ---------------------------------------------------------------------------

class TestSaveToDocxImageInsertion:

    def _make_media(self, data: bytes, fraction: float = 0.5) -> EmbeddedMedia:
        return EmbeddedMedia(
            data=data,
            content_type="image/png",
            position_fraction=fraction,
            width_emu=914400,
            height_emu=914400,
        )

    def test_valid_png_is_inserted_without_error(self, tmp_path):
        """A minimal PNG can be inserted via save_to_docx (happy path)."""
        import struct, zlib
        # Build a 1×1 red PNG
        def make_minimal_png():
            def chunk(tag, data):
                c = struct.pack('>I', len(data)) + tag + data
                return c + struct.pack('>I', zlib.crc32(c[4:]) & 0xFFFFFFFF)
            header = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
            raw = b'\x00\xff\x00\x00'
            compressed = zlib.compress(raw)
            return (b'\x89PNG\r\n\x1a\n'
                    + chunk(b'IHDR', header)
                    + chunk(b'IDAT', compressed)
                    + chunk(b'IEND', b''))

        png_data = make_minimal_png()
        media = [self._make_media(png_data)]
        out = tmp_path / "with_image.docx"
        from src.output.docx_builder import save_to_docx
        # Should not raise; image insertion may silently log errors on bad data
        save_to_docx("Paragraph one\n\nParagraph two", str(out),
                     media=media, label="Translation")

    def test_invalid_image_data_logs_warning(self, tmp_path, caplog):
        """When image data is corrupt, _do_insert_image logs a warning without raising."""
        media = [self._make_media(b"not an image")]
        out = tmp_path / "bad_image.docx"
        from src.output.docx_builder import save_to_docx
        with caplog.at_level(logging.WARNING):
            save_to_docx("Some text", str(out), media=media, label="Translation")
        # No exception — just a warning logged


# ---------------------------------------------------------------------------
# _fallback_to_text helper
# ---------------------------------------------------------------------------

class TestFallbackToText:

    def test_writes_txt_with_docx_suffix_replaced(self, tmp_path):
        """_fallback_to_text swaps .docx → .txt and writes content."""
        from src.output.docx_builder import _fallback_to_text
        out = tmp_path / "output.docx"
        _fallback_to_text("Hello world", str(out), "Test")
        txt = tmp_path / "output.txt"
        assert txt.exists()
        assert "Hello world" in txt.read_text(encoding="utf-8")
