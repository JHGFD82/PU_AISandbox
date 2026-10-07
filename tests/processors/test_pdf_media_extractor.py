"""Tests for src/processors/pdf_media_extractor.py: finding the pictures in a PDF.

These are what ``--preserve-media`` puts back into the translated document, so
each one found has to say where it sat (which page, and how far down the
document) and how large it was shown — and the small decorative ones, such as
rules and bullets, have to be left out.
"""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from src.models.embedded_media import EmbeddedMedia
from src.processors.pdf_media_extractor import PdfMediaExtractor
from tests.helpers import png


_EMU_PER_POINT = 12700


def _pdf(*pages) -> BytesIO:
    """A PDF with one A4 page per argument; each is a list of (rect, png) to place on it."""
    import fitz
    doc = fitz.open()
    for pictures in pages:
        page = doc.new_page(width=595, height=842)
        for rect, picture in pictures:
            page.insert_image(fitz.Rect(*rect), stream=picture)
    out = BytesIO()
    doc.save(out)
    out.seek(0)
    return out


@pytest.fixture
def no_size_minimum():
    """Accept pictures of any size, so the small ones made here are not filtered out."""
    with patch("src.processors.pdf_media_extractor._MIN_IMAGE_BYTES", 0):
        yield


class TestWhatIsFound:
    def test_a_pdf_without_pictures_gives_none(self, no_size_minimum):
        assert PdfMediaExtractor.extract_media(_pdf([])) == []

    def test_a_picture_is_described_by_where_it_sat_and_how_large_it_was_shown(
        self, no_size_minimum
    ):
        items = PdfMediaExtractor.extract_media(_pdf([((100, 100, 200, 200), png(64, 64))]))

        assert len(items) == 1
        item = items[0]
        assert isinstance(item, EmbeddedMedia)
        assert item.data
        assert item.content_type == "image/png"
        assert item.page_number == 0
        assert 0.0 <= item.position_fraction <= 1.0
        # Shown 100 points square on the page, whatever its pixel count.
        assert item.width_emu == 100 * _EMU_PER_POINT
        assert item.height_emu == 100 * _EMU_PER_POINT

    def test_a_picture_further_into_the_document_has_a_later_position(self, no_size_minimum):
        items = PdfMediaExtractor.extract_media(
            _pdf([], [((50, 50, 150, 150), png(64, 64))]))

        assert len(items) == 1
        assert items[0].page_number == 1
        assert items[0].position_fraction >= 0.5

    def test_a_picture_used_on_two_pages_is_given_back_once(self, no_size_minimum):
        """The PDF stores it once, so it is found once, not once per page it appears on."""
        import fitz

        picture = png(64, 64)
        buf = _pdf([((10, 10, 100, 100), picture)], [((10, 10, 100, 100), picture)])
        stored = {x[0] for page in fitz.open(stream=buf.getvalue()) for x in page.get_images(full=True)}

        assert len(PdfMediaExtractor.extract_media(buf)) == len(stored)

    def test_without_a_place_on_the_page_its_pixel_size_is_used(self, no_size_minimum):
        import fitz

        raw = _pdf([((50, 100, 200, 300), png(64, 64))]).getvalue()
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
                return [{k: v for k, v in item.items() if k != "bbox"}
                        for item in self._inner.get_image_info(xrefs=xrefs)]

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

        with patch("fitz.open", side_effect=lambda *a, **k: _NoBboxDoc(original_open(*a, **k))):
            items = PdfMediaExtractor.extract_media(BytesIO(raw))

        assert len(items) == 1
        assert items[0].width_emu == 64 * _EMU_PER_POINT
        assert items[0].height_emu == 64 * _EMU_PER_POINT


class TestWhatIsLeftOut:
    def test_a_short_decorative_picture(self, no_size_minimum):
        """Shown 4 points high: a rule or border, not a figure."""
        assert PdfMediaExtractor.extract_media(_pdf([((0, 0, 100, 4), png(100, 4))])) == []

    def test_a_picture_below_the_size_minimum(self):
        with patch("src.processors.pdf_media_extractor._MIN_IMAGE_BYTES", 1_000_000):
            result = PdfMediaExtractor.extract_media(_pdf([((50, 100, 200, 300), png(4, 4))]))
        assert result == []

    def test_a_picture_that_cannot_be_read_out(self, no_size_minimum):
        import fitz

        raw = _pdf([((50, 100, 200, 300), png(64, 64))]).getvalue()
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

        with patch("fitz.open", side_effect=lambda *a, **k: _BrokenDoc(original_open(*a, **k))):
            assert PdfMediaExtractor.extract_media(BytesIO(raw)) == []

    def test_a_pdf_with_no_pages(self):
        # PyMuPDF will not save a PDF with no pages, so one is pretended.
        fake_doc = MagicMock()
        fake_doc.__len__ = lambda self: 0
        with patch("fitz.open", return_value=fake_doc):
            assert PdfMediaExtractor.extract_media(BytesIO(b"%PDF-1.4")) == []


class TestWithoutPyMuPDF:
    def test_it_says_what_is_missing(self):
        with patch.dict("sys.modules", {"fitz": None}):
            with pytest.raises(ImportError, match="PyMuPDF"):
                PdfMediaExtractor.extract_media(BytesIO(b"%PDF-1.4"))
