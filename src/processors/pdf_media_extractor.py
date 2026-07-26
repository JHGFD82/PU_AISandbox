"""PDF media extractor: extracts embedded images from PDF files using PyMuPDF."""

import logging
from typing import List, Optional, BinaryIO, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.embedded_media import EmbeddedMedia


# Minimum raw image size (bytes) to bother embedding — skips tiny icons/artifacts.
_MIN_IMAGE_BYTES = 512

# Minimum display height in PDF points for an image to be included.
# Decorative elements (navigation tabs, horizontal rules) are typically < 35 pt
# tall; real figures are at least 50 pt. 72 pts = 1 inch.
_MIN_DISPLAY_HEIGHT_PTS: float = 50.0

# Image types reported by PyMuPDF that map cleanly to MIME types.
_EXT_TO_MIME: dict[str, str] = {
    "png":  "image/png",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "gif":  "image/gif",
    "bmp":  "image/bmp",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "tif":  "image/tiff",
    "jxl":  "image/jxl",
}


class PdfMediaExtractor:
    """Extracts images from PDF files with positional information.

    Uses PyMuPDF (``import fitz``) which must be installed separately::

        pip install pymupdf

    Each returned :class:`~src.models.embedded_media.EmbeddedMedia` item
    carries a ``position_fraction`` (0.0–1.0) computed from the image's
    location across the whole document:

    .. code-block:: text

        position_fraction = (page_index + y_frac_on_page) / total_pages

    where ``y_frac_on_page = rect.y0 / page_height`` (0 = top of page).

    This mirrors the per-paragraph fraction used by
    :meth:`~src.processors.docx_processor.DocxProcessor.extract_media` and
    is used for proportional reinsertion in
    :meth:`~src.output.file_output.FileOutputHandler.save_to_docx`.
    """

    @staticmethod
    def extract_media(file_obj: BinaryIO) -> "List[EmbeddedMedia]":
        """Extract embedded images from a PDF binary stream.

        Parameters
        ----------
        file_obj:
            Seekable binary stream of the PDF (e.g. an open file in ``'rb'``
            mode, or a :class:`~io.BytesIO` buffer).

        Returns
        -------
        List[EmbeddedMedia]
            Images in document order (page 0 top → last page bottom).
            Returns an empty list when PyMuPDF is not installed or the PDF
            contains no extractable images.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError(
                "PyMuPDF is required for PDF media extraction. "
                "Install it with: pip install pymupdf"
            ) from None

        from ..models.embedded_media import EmbeddedMedia

        raw = file_obj.read()
        doc = fitz.open(stream=raw, filetype="pdf")
        total_pages = len(doc)

        if total_pages == 0:
            logging.warning("PDF has no pages; no images extracted.")
            return []

        media_items: List[EmbeddedMedia] = []
        seen_xrefs: set = set()  # deduplicate images referenced on multiple pages

        for page_index in range(total_pages):
            page = doc[page_index]
            page_height = page.rect.height or 1.0

            for img_info in page.get_images(full=True):
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                # Locate the image on the page to compute y-position AND display size.
                # bbox is in PDF points (1 pt = 1/72 inch = 12700 EMU).
                y_frac = 0.0
                display_width_emu: Optional[int] = None
                display_height_emu: Optional[int] = None
                display_height_pts: Optional[float] = None
                for item in page.get_image_info(xrefs=True):
                    if item.get("xref") == xref:
                        bbox = item.get("bbox")  # (x0, y0, x1, y1) in page points
                        if bbox:
                            y_frac = bbox[1] / page_height
                            w_pts = bbox[2] - bbox[0]
                            h_pts = bbox[3] - bbox[1]
                            if w_pts > 0:
                                display_width_emu = int(w_pts * 12700)
                            if h_pts > 0:
                                display_height_emu = int(h_pts * 12700)
                                display_height_pts = h_pts
                        break

                # Skip images that are too short to be real content (decorative
                # tabs, horizontal rules, etc.).  Only applies when a bbox was
                # found; unknown-size images are allowed through.
                if (
                    display_height_pts is not None
                    and display_height_pts < _MIN_DISPLAY_HEIGHT_PTS
                ):
                    logging.debug(
                        f"Skipping short decorative image xref={xref} "
                        f"(height={display_height_pts:.1f}pt < {_MIN_DISPLAY_HEIGHT_PTS}pt)"
                    )
                    seen_xrefs.discard(xref)  # allow it to be re-evaluated on later pages
                    continue

                position_fraction = (page_index + y_frac) / total_pages

                # Extract raw image bytes and metadata.
                try:
                    img_dict = doc.extract_image(xref)
                except Exception as exc:
                    logging.debug(f"Could not extract image xref={xref}: {exc}")
                    continue

                data: bytes = img_dict.get("image", b"")
                if len(data) < _MIN_IMAGE_BYTES:
                    logging.debug(
                        f"Skipping tiny image xref={xref} ({len(data)} bytes)"
                    )
                    continue

                ext: str = img_dict.get("ext", "").lower()
                content_type = _EXT_TO_MIME.get(ext, f"image/{ext}" if ext else "image/png")

                # Use display dimensions derived from the page bbox (PDF points
                # → EMU) rather than raw pixel dimensions.  A 300 DPI embedded
                # image has ~4× more pixels than a 72 DPI one even when displayed
                # at the same size, so pixel-based EMU would be grossly oversized.
                # Fall back to pixel-based estimate only if bbox was unavailable.
                if display_width_emu is None:
                    width_px: int = img_dict.get("width", 0)
                    display_width_emu = width_px * 12700 if width_px else None
                if display_height_emu is None:
                    height_px: int = img_dict.get("height", 0)
                    display_height_emu = height_px * 12700 if height_px else None

                media_items.append(EmbeddedMedia(
                    data=data,
                    content_type=content_type,
                    position_fraction=position_fraction,
                    width_emu=display_width_emu,
                    height_emu=display_height_emu,
                    page_number=page_index,
                ))

        doc.close()
        logging.info(f"Extracted {len(media_items)} image(s) from PDF.")
        return media_items
