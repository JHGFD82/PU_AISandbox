"""Block-level document models used for structured DOCX translation."""

from dataclasses import dataclass
from typing import List


@dataclass
class ParagraphBlock:
    """A plain text paragraph extracted from a Word document body."""

    text: str


@dataclass
class TableBlock:
    """A table extracted from a Word document, ready for placeholder-based translation.

    ``rows[row_idx][col_idx]`` holds the raw cell text.
    ``placeholder`` is the unique token (e.g. ``[TABLE_1]``) embedded in the
    page-text string that is sent to the translation LLM.  After translation
    the token is replaced by a proper Word table object in the output document.
    """

    rows: List[List[str]]
    placeholder: str
