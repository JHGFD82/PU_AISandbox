"""PDF media extractor: extracts embedded images from PDF files using PyMuPDF."""

import logging
from typing import List, BinaryIO, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.embedded_media import EmbeddedMedia


# Minimum raw image size (bytes) to bother embedding — skips tiny icons/artifacts.
_MIN_IMAGE_BYTES = 512

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
            )

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

                # Locate the image on the page to compute y-position.
                y_frac = 0.0
                for item in page.get_image_info(xrefs=True):
                    if item.get("xref") == xref:
                        bbox = item.get("bbox")  # (x0, y0, x1, y1) in page coords
                        if bbox:
                            y_frac = bbox[1] / page_height
                        break

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

                # PyMuPDF returns width/height in pixels; convert to EMU
                # (1 inch = 914400 EMU; assume 72 DPI → 12700 EMU per pixel).
                width_px: int = img_dict.get("width", 0)
                height_px: int = img_dict.get("height", 0)
                width_emu = width_px * 12700 if width_px else None
                height_emu = height_px * 12700 if height_px else None

                media_items.append(EmbeddedMedia(
                    data=data,
                    content_type=content_type,
                    position_fraction=position_fraction,
                    width_emu=width_emu,
                    height_emu=height_emu,
                ))

        doc.close()
        logging.info(f"Extracted {len(media_items)} image(s) from PDF.")
        return media_items
