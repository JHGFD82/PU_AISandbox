"""A single image extracted from a source document, ready to be reinserted after translation."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class EmbeddedMedia:
    """One image copied out of a source document so it can be reinserted after translation.

    Documents are translated as plain text, which strips out any images along
    the way. To bring those images back into the finished document, each one
    is captured as an ``EmbeddedMedia`` object before translation begins,
    along with enough position information to place it back in roughly the
    same spot afterward.

    Attributes:
        data: The image's raw file bytes, exactly as stored in the source
              document.
        content_type: The image's file format, given as a MIME type — a
                      standard label describing a file's format (e.g.
                      ``'image/png'``, ``'image/jpeg'``).
        position_fraction: Roughly where in the document this image appeared,
                           expressed as a fraction from ``0.0`` (very start of
                           the document) to ``1.0`` (very end). Because the
                           translated document may have a different number of
                           paragraphs than the original, this fraction — not
                           an exact paragraph number — is used to place the
                           image proportionally in the new document.
        width_emu: The image's original width, for Word documents only,
                   measured in English Metric Units (EMU) — the unit Word
                   uses internally for object sizing (914,400 EMU = 1 inch).
                   ``None`` keeps the image at its original size.
        height_emu: The image's original height in EMU, for Word documents
                    only. ``None`` keeps the image at its original size.
    """

    data: bytes
    content_type: str
    position_fraction: float
    width_emu: Optional[int] = None
    height_emu: Optional[int] = None
    page_number: Optional[int] = None
    """For images extracted from a PDF, the page they came from (page 1 is
    ``0``, page 2 is ``1``, and so on). Set automatically by
    ``PdfMediaExtractor`` when processing PDF files. ``None`` for images
    extracted from Word documents, which use ``position_fraction`` instead
    to decide where to reinsert the image.
    """
