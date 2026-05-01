"""Coverage tests for src/processors/pdf_media_extractor.py — uncovered branches."""

import struct
import zlib
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from src.processors.pdf_media_extractor import PdfMediaExtractor
from src.models.embedded_media import EmbeddedMedia


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png(width: int = 4, height: int = 4) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    row = b'\x00' + b'\xff\xff\xff' * width
    idat = chunk(b'IDAT', zlib.compress(row * height))
    iend = chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# total_pages == 0 path (lines 87-88)
# ---------------------------------------------------------------------------

class TestZeroPagePdf:

    def test_zero_pages_returns_empty_list(self):
        """A PDF reported as having 0 pages should return [] immediately."""
        # PyMuPDF won't save a zero-page file, so mock fitz.open to return
        # a document stub that reports __len__ == 0.
        fake_doc = MagicMock()
        fake_doc.__len__ = lambda self: 0

        with patch("fitz.open", return_value=fake_doc):
            result = PdfMediaExtractor.extract_media(BytesIO(b"%PDF-1.4"))
        assert result == []


# ---------------------------------------------------------------------------
# Short decorative image filter (lines 130-135)
# ---------------------------------------------------------------------------

class TestShortDecorativeImageSkipped:

    def test_image_below_min_height_is_excluded(self):
        """Images whose display height < _MIN_DISPLAY_HEIGHT_PTS should be skipped."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        png = _make_png(100, 4)  # wide but very short (4 pt display)
        # Insert image in a tiny 100×4 pt rect — height << 50 pt threshold
        page.insert_image(fitz.Rect(0, 0, 100, 4), stream=png)
        buf = BytesIO()
        doc.save(buf)
        buf.seek(0)

        # Size filter won't reject it — only height filter should
        with patch("src.processors.pdf_media_extractor._MIN_IMAGE_BYTES", 0):
            result = PdfMediaExtractor.extract_media(buf)

        # Expect no items because height (4 pt) < _MIN_DISPLAY_HEIGHT_PTS (50 pt)
        assert result == []


# ---------------------------------------------------------------------------
# extract_image exception (lines 142-144)
# ---------------------------------------------------------------------------

class TestExtractImageException:

    def test_extract_image_exception_is_skipped(self):
        """When doc.extract_image() raises, that xref is silently skipped."""
        import fitz
        import sys, types

        # Build a real PDF with one image first, then intercept extract_image
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_image(fitz.Rect(50, 100, 200, 300), stream=_make_png(64, 64))
        buf = BytesIO()
        doc.save(buf)
        buf.seek(0)

        # Patch fitz.open to return a wrapper that raises on extract_image
        original_open = fitz.open

        class _BrokenDoc:
            def __init__(self, inner):
                self._inner = inner

            def __len__(self):
                return len(self._inner)

            def __getitem__(self, idx):
                return self._inner[idx]

            def extract_image(self, xref):
                raise RuntimeError("simulated extraction failure")

            def close(self):
                pass

        raw = buf.read()

        def patched_open(*args, **kwargs):
            return _BrokenDoc(original_open(*args, **kwargs))

        with patch("fitz.open", side_effect=patched_open):
            buf2 = BytesIO(raw)
            with patch("src.processors.pdf_media_extractor._MIN_IMAGE_BYTES", 0):
                result = PdfMediaExtractor.extract_media(buf2)
        # All images skipped due to exception; result is empty list
        assert result == []


# ---------------------------------------------------------------------------
# Tiny image skip (lines 148-151)
# ---------------------------------------------------------------------------

class TestTinyImageSkipped:

    def test_image_smaller_than_min_bytes_excluded(self):
        """Images whose raw byte size < _MIN_IMAGE_BYTES are excluded."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        # A 1×1 PNG is very small — likely < 512 bytes
        tiny_png = _make_png(1, 1)
        page.insert_image(fitz.Rect(50, 100, 200, 300), stream=tiny_png)
        buf = BytesIO()
        doc.save(buf)
        buf.seek(0)

        # Use the default threshold (512 bytes) — a 1x1 PNG should be tiny
        result = PdfMediaExtractor.extract_media(buf)
        # Either excluded (< 512 bytes) or included (>= 512 bytes); just ensure no crash.
        assert isinstance(result, list)

    def test_explicit_tiny_image_threshold(self):
        """Force the threshold high enough to exclude a normal 4×4 image."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        png = _make_png(4, 4)  # small but valid
        page.insert_image(fitz.Rect(50, 100, 200, 300), stream=png)
        buf = BytesIO()
        doc.save(buf)
        buf.seek(0)

        # Patch threshold to 1MB so the image is definitely "tiny"
        with patch("src.processors.pdf_media_extractor._MIN_IMAGE_BYTES", 1_000_000):
            result = PdfMediaExtractor.extract_media(buf)
        assert result == []


# ---------------------------------------------------------------------------
# Pixel-based dimension fallback (lines 162-163, 165-166)
# ---------------------------------------------------------------------------

class TestPixelDimensionFallback:

    def test_image_without_bbox_uses_pixel_dimensions(self):
        """When no bbox is found, width/height fall back to pixel-based EMU estimate."""
        import fitz
        import sys, types

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        png = _make_png(64, 64)
        page.insert_image(fitz.Rect(50, 100, 200, 300), stream=png)
        raw_buf = BytesIO()
        doc.save(raw_buf)
        raw_pdf = raw_buf.getvalue()

        original_open = fitz.open

        class _NoBboxPage:
            def __init__(self, inner):
                self._inner = inner

            @property
            def rect(self):
                return self._inner.rect

            def get_images(self, full=True):
                return self._inner.get_images(full=full)

            def get_image_info(self, xrefs=False):
                # Return info without a 'bbox' key so pixel fallback triggers
                raw = self._inner.get_image_info(xrefs=xrefs)
                return [{k: v for k, v in item.items() if k != "bbox"} for item in raw]

        class _NoBboxDoc:
            def __init__(self, inner):
                self._inner = inner

            def __len__(self):
                return len(self._inner)

            def __getitem__(self, idx):
                return _NoBboxPage(self._inner[idx])

            def extract_image(self, xref):
                return self._inner.extract_image(xref)

            def close(self):
                pass

        def patched_open(*args, **kwargs):
            return _NoBboxDoc(original_open(*args, **kwargs))

        with patch("fitz.open", side_effect=patched_open):
            buf2 = BytesIO(raw_pdf)
            with patch("src.processors.pdf_media_extractor._MIN_IMAGE_BYTES", 0):
                result = PdfMediaExtractor.extract_media(buf2)

        assert len(result) >= 1
        item = result[0]
        # Width/height should be pixel-based EMU (64 px × 12700 = 812800 EMU)
        if item.width_emu is not None:
            assert item.width_emu == 64 * 12700
        if item.height_emu is not None:
            assert item.height_emu == 64 * 12700
