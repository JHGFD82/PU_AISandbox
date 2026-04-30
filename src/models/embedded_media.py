"""Data model for media items extracted from source documents."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class EmbeddedMedia:
    """Represents an image extracted from a source document for reinsertion.

    Attributes:
        data:              Raw image bytes.
        content_type:      MIME type (e.g. 'image/png', 'image/jpeg').
        position_fraction: Approximate location in the document expressed as a
                           fraction of total paragraphs (0.0 = beginning,
                           1.0 = end).  Used for proportional reinsertion when
                           the translated paragraph count differs from the source.
        width_emu:         Original width in English Metric Units (DOCX only).
                           None = use original size.
        height_emu:        Original height in English Metric Units (DOCX only).
                           None = use original size.
    """

    data: bytes
    content_type: str
    position_fraction: float
    width_emu: Optional[int] = None
    height_emu: Optional[int] = None
