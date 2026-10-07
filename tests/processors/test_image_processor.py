"""Tests for src/processors/image_processor.py: recognising, checking and preparing image files."""

import base64

import pytest

from src.processors.image_processor import ImageProcessor
from tests.helpers import RED, png


# ---------------------------------------------------------------------------
# ImageProcessor
# ---------------------------------------------------------------------------


class TestImageProcessor:

    # --- is_image_file -------------------------------------------------------

    @pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"])
    def test_recognized_extensions_return_true(self, ext):
        assert ImageProcessor.is_image_file(f"photo{ext}") is True

    @pytest.mark.parametrize("ext", [".pdf", ".docx", ".txt", ".mp4", ""])
    def test_non_image_extensions_return_false(self, ext):
        assert ImageProcessor.is_image_file(f"file{ext}") is False

    def test_uppercase_extension_recognized(self):
        assert ImageProcessor.is_image_file("PHOTO.JPG") is True

    def test_mixed_case_extension_recognized(self):
        assert ImageProcessor.is_image_file("scan.Png") is True

    # --- validate_image_file -------------------------------------------------

    def test_valid_image_file_returns_true(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(png(colour=RED))
        assert ImageProcessor.validate_image_file(str(img)) is True

    def test_missing_file_returns_false(self, tmp_path):
        assert ImageProcessor.validate_image_file(str(tmp_path / "missing.jpg")) is False

    def test_wrong_extension_returns_false(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF")
        assert ImageProcessor.validate_image_file(str(f)) is False

    # --- local_image_to_data_url ---------------------------------------------

    def test_returns_data_url_format(self, tmp_path):
        img = tmp_path / "photo.png"
        img.write_bytes(png(colour=RED))
        result = ImageProcessor().local_image_to_data_url(str(img))
        assert result.startswith("data:image/png;base64,")

    def test_base64_payload_is_valid(self, tmp_path):
        img = tmp_path / "photo.png"
        png_bytes = png(colour=RED)
        img.write_bytes(png_bytes)
        result = ImageProcessor().local_image_to_data_url(str(img))
        payload = result.split(",", 1)[1]
        decoded = base64.b64decode(payload)
        assert decoded == png_bytes

    def test_jpeg_uses_jpeg_mime_type(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)  # minimal JPEG header
        result = ImageProcessor().local_image_to_data_url(str(img))
        assert "image/jpeg" in result

    def test_unknown_mime_type_falls_back_to_octet_stream(self, tmp_path):
        # Use an extension that has no registered MIME type on any platform
        img = tmp_path / "photo.zzunknownzz"
        img.write_bytes(b"\x00\x01\x02\x03")
        result = ImageProcessor().local_image_to_data_url(str(img))
        assert "application/octet-stream" in result

    # --- is_blank_image ------------------------------------------------------

    def test_blank_image_returns_true(self, tmp_path, monkeypatch):
        """A pixmap of all-white samples is detected as blank."""
        import sys
        import types

        img = tmp_path / "blank.png"
        img.write_bytes(png(colour=RED))

        fake_gray_pix = types.SimpleNamespace(colorspace=None, samples=bytes([255] * 400))
        fake_cs_gray = object()

        class _FakeColorPix:
            colorspace = types.SimpleNamespace(n=3)
            samples = bytes([255] * 1200)

        fake_fitz = types.ModuleType("fitz")
        fake_fitz.csGRAY = fake_cs_gray
        fake_fitz.Pixmap = lambda *a: fake_gray_pix if a[0] is fake_cs_gray else _FakeColorPix()
        monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

        # Clear cached import in the processor module so it uses our mock
        import importlib

        import src.processors.image_processor as _mod
        importlib.reload(_mod)
        from src.processors.image_processor import ImageProcessor as _IP

        assert _IP.is_blank_image(str(img)) is True

    def test_non_blank_image_returns_false(self, tmp_path, monkeypatch):
        """A pixmap with mixed dark/light pixels is not blank."""
        import sys
        import types

        img = tmp_path / "content.png"
        img.write_bytes(png(colour=RED))

        fake_pix = types.SimpleNamespace(colorspace=None, samples=bytes([0] * 200 + [255] * 200))
        fake_fitz = types.ModuleType("fitz")
        fake_fitz.csGRAY = object()
        fake_fitz.Pixmap = lambda *a: fake_pix
        monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

        import importlib

        import src.processors.image_processor as _mod
        importlib.reload(_mod)
        from src.processors.image_processor import ImageProcessor as _IP

        assert _IP.is_blank_image(str(img)) is False

    def test_fitz_exception_returns_false(self, tmp_path, monkeypatch):
        """Any fitz error returns False (safe fallback — always process)."""
        import sys
        import types

        img = tmp_path / "err.png"
        img.write_bytes(png(colour=RED))

        fake_fitz = types.ModuleType("fitz")
        fake_fitz.csGRAY = object()

        def _raise(*a):
            raise RuntimeError("boom")

        fake_fitz.Pixmap = _raise
        monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

        import importlib

        import src.processors.image_processor as _mod
        importlib.reload(_mod)
        from src.processors.image_processor import ImageProcessor as _IP

        assert _IP.is_blank_image(str(img)) is False

    def test_empty_pixmap_returns_true(self, tmp_path, monkeypatch):
        """A pixmap with no sample data (zero-size image) is treated as blank."""
        import sys
        import types

        img = tmp_path / "empty.png"
        img.write_bytes(png(colour=RED))

        fake_pix = types.SimpleNamespace(colorspace=None, samples=bytes())
        fake_fitz = types.ModuleType("fitz")
        fake_fitz.csGRAY = object()
        fake_fitz.Pixmap = lambda *a: fake_pix
        monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

        import importlib

        import src.processors.image_processor as _mod
        importlib.reload(_mod)
        from src.processors.image_processor import ImageProcessor as _IP

        assert _IP.is_blank_image(str(img)) is True
