"""Data shapes representing the pieces of a Word document during translation.

When a Word document is translated, its body is broken into a sequence of
these two block types, in the order they appear in the original document.
Plain paragraphs are translated directly, while tables are set aside and
translated separately (cell by cell) so their row-and-column structure isn't
lost — see ``src/processors/docx_translation.py`` for how these blocks are
produced and later reassembled into the finished document.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class ParagraphBlock:
    """A single paragraph of plain text taken from a Word document."""

    text: str


@dataclass
class TableBlock:
    """A table taken from a Word document, held aside for separate translation.

    Tables are translated cell-by-cell rather than as part of the surrounding
    prose, so that rows and columns are preserved exactly in the translated
    document. To make this possible, the table's text is temporarily replaced
    in the document body by a placeholder — a short marker string like
    ``'[TABLE_1]'`` — before the AI model sees the page. Once translation is
    complete, the placeholder is swapped back out for a real Word table
    containing the translated cell text.

    Attributes:
        rows: The table's cell text, organized as a list of rows, where each
              row is itself a list of cell strings — e.g.
              ``[["Name", "Age"], ["Ada", "36"]]`` for a two-row, two-column
              table.
        placeholder: The marker string standing in for this table in the page
                     text sent for translation, e.g. ``'[TABLE_1]'``.
    """

    rows: List[List[str]]
    placeholder: str
