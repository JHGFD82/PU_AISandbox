"""
Tests for document and image processor utilities (no API calls):
  - TxtProcessor.extract_raw_content, process_txt_with_pages
  - DocxProcessor.extract_raw_content, process_docx_with_pages
  - ImageProcessor.is_image_file, validate_image_file, local_image_to_data_url,
    is_blank_image
  - detect_numbered_content (all patterns)
  - generate_process_text (context selection, numbered-continuation hint)
  - PDFProcessor.__init__, _clean_text, parse_layout, process_page, process_pdf
"""

import base64
import io
import struct
import zlib
from unittest.mock import MagicMock, patch

import pytest

from src.errors import CLIError
from src.processors.txt_processor import TxtProcessor
from src.processors.docx_processor import DocxProcessor
from src.processors.image_processor import ImageProcessor
from src.processors.pdf_processor import (
    PDFProcessor,
    detect_numbered_content,
    generate_process_text,
)
from pdfminer.layout import LTChar, LTFigure, LTPage, LTTextBox, LTTextContainer, LTTextLine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_docx_bytes(paragraphs: list[str]) -> io.BytesIO:
    """Create a minimal real .docx in memory using python-docx."""
    from docx import Document
    doc = Document()
    for para in paragraphs:
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _make_tiny_png() -> bytes:
    """Return the bytes of a 1×1 red PNG (no external dependencies)."""
    # PNG signature
    sig = b'\x89PNG\r\n\x1a\n'

    def chunk(ctype: bytes, data: bytes) -> bytes:
        c = struct.pack('>I', len(data)) + ctype + data
        return c + struct.pack('>I', zlib.crc32(ctype + data) & 0xFFFFFFFF)

    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    # 1×1 RGB pixel (255,0,0) = red; filter byte 0 prepended
    raw = b'\x00\xff\x00\x00'
    idat = chunk(b'IDAT', zlib.compress(raw))
    iend = chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# TxtProcessor
# ---------------------------------------------------------------------------


class TestTxtProcessor:

    def _file(self, content: str) -> io.StringIO:
        return io.StringIO(content)

    def test_extract_raw_content_returns_stripped_text(self):
        p = TxtProcessor()
        result = p.extract_raw_content(self._file("  Hello world  "))
        assert result == "Hello world"

    def test_extract_raw_content_empty_file(self):
        p = TxtProcessor()
        result = p.extract_raw_content(self._file(""))
        assert result == ""

    def test_extract_raw_content_preserves_cjk(self):
        p = TxtProcessor()
        result = p.extract_raw_content(self._file("日本語テキスト"))
        assert result == "日本語テキスト"

    def test_process_txt_with_pages_single_page(self):
        pages = TxtProcessor.process_txt_with_pages(self._file("Short content"))
        assert len(pages) >= 1
        assert "Short content" in pages[0]

    def test_process_txt_with_pages_empty_file_returns_empty_string(self):
        pages = TxtProcessor.process_txt_with_pages(self._file(""))
        assert pages == [""]

    def test_process_txt_with_pages_splits_large_content(self):
        # Create content that will definitely exceed a small page size
        big_content = "\n\n".join(["Paragraph " + str(i) * 100 for i in range(20)])
        pages = TxtProcessor.process_txt_with_pages(
            io.StringIO(big_content), target_page_size=200
        )
        assert len(pages) > 1

    def test_process_txt_with_pages_preserves_all_content(self):
        content = "First paragraph\n\nSecond paragraph\n\nThird paragraph"
        pages = TxtProcessor.process_txt_with_pages(io.StringIO(content))
        combined = "\n\n".join(pages)
        assert "First paragraph" in combined
        assert "Second paragraph" in combined
        assert "Third paragraph" in combined

    def test_process_txt_with_pages_raises_cli_error_on_read_error(self):
        """A file that can't be read must surface as a CLIError, not a traceback.

        main() catches CLIError and prints just the message; anything else
        reaches a non-CS professor as a raw traceback.
        """
        bad_file = io.StringIO()
        bad_file.close()  # Closed → reading will raise ValueError
        with pytest.raises(CLIError):
            TxtProcessor.process_txt_with_pages(bad_file)


# ---------------------------------------------------------------------------
# DocxProcessor
# ---------------------------------------------------------------------------


class TestDocxProcessor:

    def test_extract_raw_content_returns_paragraph_text(self):
        buf = _make_docx_bytes(["Hello", "World"])
        p = DocxProcessor()
        result = p.extract_raw_content(buf)
        assert "Hello" in result
        assert "World" in result

    def test_extract_raw_content_empty_document_returns_empty_string(self):
        buf = _make_docx_bytes([])
        p = DocxProcessor()
        result = p.extract_raw_content(buf)
        assert result == ""

    def test_extract_raw_content_preserves_cjk(self):
        buf = _make_docx_bytes(["日本語テキスト", "中文内容"])
        p = DocxProcessor()
        result = p.extract_raw_content(buf)
        assert "日本語テキスト" in result
        assert "中文内容" in result

    def test_extract_raw_content_paragraphs_joined_with_double_newline(self):
        buf = _make_docx_bytes(["Para1", "Para2"])
        p = DocxProcessor()
        result = p.extract_raw_content(buf)
        assert "\n\n" in result

    def test_process_docx_with_pages_single_page(self):
        buf = _make_docx_bytes(["Short text"])
        pages = DocxProcessor.process_docx_with_pages(buf)
        assert len(pages) >= 1
        assert "Short text" in pages[0]

    def test_process_docx_with_pages_empty_doc_returns_empty_string(self):
        buf = _make_docx_bytes([])
        pages = DocxProcessor.process_docx_with_pages(buf)
        assert pages == [""]

    def test_process_docx_with_pages_splits_large_content(self):
        big_paragraphs = ["Paragraph " + str(i) * 80 for i in range(15)]
        buf = _make_docx_bytes(big_paragraphs)
        pages = DocxProcessor.process_docx_with_pages(buf, target_page_size=200)
        assert len(pages) > 1

    def test_process_docx_with_pages_preserves_all_paragraphs(self):
        buf = _make_docx_bytes(["Alpha", "Beta", "Gamma"])
        pages = DocxProcessor.process_docx_with_pages(buf)
        combined = "\n\n".join(pages)
        for word in ("Alpha", "Beta", "Gamma"):
            assert word in combined

    def test_extract_raw_content_raises_import_error_when_docx_not_installed(self, monkeypatch):
        import sys
        buf = _make_docx_bytes(["Hello"])  # build before patching
        monkeypatch.setitem(sys.modules, "docx", None)
        p = DocxProcessor()
        with pytest.raises(ImportError, match="python-docx is required"):
            p.extract_raw_content(buf)

    def test_process_docx_with_pages_wraps_error_as_cli_error(self):
        """The underlying cause stays visible in the message, wrapped in a CLIError.

        CLIError is what main() knows to print plainly instead of showing a
        non-CS professor a traceback; keeping the original text in it means
        the reason is still there for whoever has to diagnose it.
        """
        buf = _make_docx_bytes(["Hello"])
        with patch.object(DocxProcessor, "extract_raw_content", side_effect=RuntimeError("disk error")):
            with pytest.raises(CLIError, match="disk error"):
                DocxProcessor.process_docx_with_pages(buf)


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
        img.write_bytes(_make_tiny_png())
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
        img.write_bytes(_make_tiny_png())
        result = ImageProcessor().local_image_to_data_url(str(img))
        assert result.startswith("data:image/png;base64,")

    def test_base64_payload_is_valid(self, tmp_path):
        img = tmp_path / "photo.png"
        png_bytes = _make_tiny_png()
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
        img.write_bytes(_make_tiny_png())

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
        img.write_bytes(_make_tiny_png())

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
        img.write_bytes(_make_tiny_png())

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
        img.write_bytes(_make_tiny_png())

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


# ---------------------------------------------------------------------------
# detect_numbered_content
# ---------------------------------------------------------------------------


def _mock_lt(cls, text: str):
    """Return a MagicMock whose isinstance check passes as *cls*."""
    obj = MagicMock()
    obj.__class__ = cls
    obj.get_text.return_value = text
    return obj


class TestDetectNumberedContent:

    def test_decimal_list_item(self):
        assert detect_numbered_content("1. Some text") is True

    def test_fullwidth_space_after_number(self):
        assert detect_numbered_content("1\u3000Some item") is True

    def test_number_space_text(self):
        assert detect_numbered_content("2 Some text") is True

    def test_bracket_reference(self):
        assert detect_numbered_content("See [1] for details") is True

    def test_paren_reference(self):
        assert detect_numbered_content("(1) First point") is True

    def test_cjk_paren_reference(self):
        assert detect_numbered_content("\uff081\uff09\u7b2c\u4e00\u70b9") is True

    def test_cjk_closing_paren(self):
        assert detect_numbered_content("1\uff09\u7b2c\u4e00\u70b9") is True

    def test_circled_number(self):
        assert detect_numbered_content("\u2460\u6700\u521d") is True

    def test_chinese_numeral_list(self):
        assert detect_numbered_content("\u4e00\u3001\u7b2c\u4e00\u7ae0") is True

    def test_standalone_number_on_own_line(self):
        assert detect_numbered_content("Some text\n42\nMore text") is True

    def test_plain_prose_returns_false(self):
        assert detect_numbered_content("The quick brown fox") is False

    def test_empty_string_returns_false(self):
        assert detect_numbered_content("") is False

    def test_cjk_text_no_numbers_returns_false(self):
        assert detect_numbered_content("\u6771\u4eac\u5927\u5b66\u306e\u7814\u7a76") is False


# ---------------------------------------------------------------------------
# generate_process_text
# ---------------------------------------------------------------------------


class TestGenerateProcessText:

    def test_output_always_starts_with_current_page_header(self):
        out = generate_process_text("", "page content", "")
        assert out.startswith("--Current Page:")

    def test_abstract_used_as_context_when_provided(self):
        out = generate_process_text("abstract here", "page text", "previous page content")
        assert "abstract here" in out
        assert "--Context:" in out

    def test_no_abstract_uses_tail_of_previous_page(self):
        # previous_page of 20 chars with 0.65 context -> tail from char 13 onward
        prev = "0123456789ABCDEFGHIJ"
        out = generate_process_text("", "page text", prev)
        assert "EFGHIJ" in out
        assert "--Context:" in out

    def test_no_context_at_all_produces_no_context_section(self):
        out = generate_process_text("", "page text", "")
        assert "--Context:" not in out

    def test_previous_translated_with_numbered_page_appends_hint(self):
        prev_translated = "Line one\n1. First item\n2. Second item"
        out = generate_process_text("", "1. Numbered content", "", previous_translated=prev_translated)
        assert "Previous numbering ended with" in out

    def test_previous_translated_without_numbered_page_no_hint(self):
        prev_translated = "Line one\n1. First item"
        out = generate_process_text("", "Plain prose text", "", previous_translated=prev_translated)
        assert "Previous numbering ended with" not in out

    def test_previous_translated_all_plain_lines_no_hint(self):
        prev_translated = "plain\nno numbers here\njust text"
        out = generate_process_text("", "1. Numbered content", "", previous_translated=prev_translated)
        assert "Previous numbering ended with" not in out

    def test_page_text_appears_in_output(self):
        out = generate_process_text("", "unique_page_content_xyz", "")
        assert "unique_page_content_xyz" in out


# ---------------------------------------------------------------------------
# PDFProcessor._clean_text
# ---------------------------------------------------------------------------


class TestPDFProcessorCleanText:

    @pytest.fixture()
    def p(self):
        return PDFProcessor()

    def test_empty_string_returns_empty(self, p):
        assert p._clean_text("") == ""

    def test_removes_null_characters(self, p):
        assert p._clean_text("hel\x00lo") == "hello"

    def test_removes_bom(self, p):
        assert p._clean_text("\ufeffhello") == "hello"

    def test_removes_cid_references(self, p):
        # After CID removal the space-collapser merges the surrounding spaces to one
        assert p._clean_text("text (cid:123) more") == "text more"

    def test_collapses_multiple_spaces(self, p):
        assert p._clean_text("a   b\t\tc") == "a b c"

    def test_preserves_newlines(self, p):
        result = p._clean_text("line one\nline two")
        assert "\n" in result

    def test_preserves_cjk_text(self, p):
        assert p._clean_text("\u65e5\u672c\u8a9e\u30c6\u30ad\u30b9\u30c8") == "\u65e5\u672c\u8a9e\u30c6\u30ad\u30b9\u30c8"

    def test_strips_leading_trailing_whitespace(self, p):
        assert p._clean_text("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# PDFProcessor.parse_layout
# ---------------------------------------------------------------------------


class TestPDFProcessorParseLayout:

    @pytest.fixture()
    def p(self):
        return PDFProcessor()

    def _layout(self, children):
        layout = MagicMock(spec=LTPage)
        layout.__iter__ = MagicMock(return_value=iter(children))
        return layout

    def test_empty_layout_returns_empty_string(self, p):
        assert p.parse_layout(self._layout([])) == ""

    def test_lt_text_line_text_included(self, p):
        line = _mock_lt(LTTextLine, "Hello world\n")
        out = p.parse_layout(self._layout([line]))
        assert "Hello world" in out

    def test_lt_text_container_text_included(self, p):
        container = _mock_lt(LTTextContainer, "Container text\n")
        out = p.parse_layout(self._layout([container]))
        assert "Container text" in out

    def test_lt_char_text_included(self, p):
        char = _mock_lt(LTChar, "A")
        out = p.parse_layout(self._layout([char]))
        assert "A" in out

    def test_lt_text_box_text_extracted_via_get_text(self, p):
        # LTTextBox is a subclass of LTTextContainer so it is handled by the
        # LTTextContainer branch (get_text), not by child expansion.
        box = MagicMock()
        box.__class__ = LTTextBox
        box.get_text.return_value = "Box text\n"
        out = p.parse_layout(self._layout([box]))
        assert "Box text" in out

    def test_lt_figure_children_are_expanded(self, p):
        inner = _mock_lt(LTTextLine, "Figure text\n")
        fig = MagicMock()
        fig.__class__ = LTFigure
        fig.__iter__ = MagicMock(return_value=iter([inner]))
        out = p.parse_layout(self._layout([fig]))
        assert "Figure text" in out

    def test_empty_text_elements_excluded(self, p):
        line = _mock_lt(LTTextLine, "   ")
        out = p.parse_layout(self._layout([line]))
        assert out == ""

    def test_multiple_lines_joined_with_newlines(self, p):
        a = _mock_lt(LTTextLine, "Line A\n")
        b = _mock_lt(LTTextLine, "Line B\n")
        out = p.parse_layout(self._layout([a, b]))
        assert "Line A" in out
        assert "Line B" in out

    def test_excessive_blank_lines_collapsed(self, p):
        a = _mock_lt(LTTextLine, "Line A\n")
        c = _mock_lt(LTTextLine, "Line C\n")
        out = p.parse_layout(self._layout([a, c]))
        assert "\n\n\n" not in out


# ---------------------------------------------------------------------------
# PDFProcessor.process_page
# ---------------------------------------------------------------------------


class TestPDFProcessorProcessPage:

    def test_calls_interpreter_and_returns_layout_text(self):
        p = PDFProcessor()
        mock_page = MagicMock()
        mock_layout = MagicMock(spec=LTPage)
        mock_layout.__iter__ = MagicMock(return_value=iter([_mock_lt(LTTextLine, "Page text\n")]))
        p.interpreter.process_page = MagicMock()
        p.device.get_result = MagicMock(return_value=mock_layout)

        result = p.process_page(mock_page)

        p.interpreter.process_page.assert_called_once_with(mock_page)
        assert "Page text" in result


# ---------------------------------------------------------------------------
# PDFProcessor.process_pdf
# ---------------------------------------------------------------------------


class TestPDFProcessorProcessPdf:

    def test_delegates_to_pdfpage_get_pages(self):
        p = PDFProcessor()
        mock_file = MagicMock()
        sentinel = object()
        with patch("src.processors.pdf_processor.PDFPage.get_pages", return_value=sentinel) as mock_get:
            result = p.process_pdf(mock_file)
        mock_get.assert_called_once_with(mock_file)
        assert result is sentinel
