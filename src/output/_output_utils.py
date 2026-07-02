"""Small helper functions shared by the PDF, Word, and Excel output builders."""

import logging
import re
from pathlib import Path
from typing import List, Optional

# Matches page-header lines like "-- Page 4 --" that generate_text() inserts
# between pages (trailing whitespace is stripped by _normalize_paragraphs first).
_PAGE_MARKER_RE = re.compile(r'^--\s*Page\s+(\d+)\s*--\s*$')


def _emit_message(
    message: str,
    level: int = logging.INFO,
    log_message: Optional[str] = None,
    leading_newline: bool = False,
) -> None:
    """Show a message to the user on-screen and record the same event in the log file.

    Args:
        message: The text to print to the terminal for the user to see.
        level: The logging severity to record this event at (e.g.
               ``logging.INFO`` for routine progress, ``logging.WARNING`` for
               a problem the user should know about but that didn't stop
               processing).
        log_message: A more detailed version of the message to write to the
                     log file, if different from what's shown on-screen.
                     ``None`` reuses ``message`` for both.
        leading_newline: When ``True``, prints a blank line before the
                         message for visual spacing in the terminal.
    """
    logging.log(level, log_message or message)
    prefix = "\n" if leading_newline else ""
    print(f"{prefix}{message}")


def _normalize_paragraphs(content: str) -> list[str]:
    """Split a block of text into a clean list of paragraphs, ready for document output.

    Paragraphs are separated by blank lines in the source text. Within each
    paragraph, any remaining line breaks are collapsed into single spaces so
    the paragraph reads as one continuous block of text, and any empty
    paragraphs are dropped entirely.

    Args:
        content: The full text to split, with paragraphs separated by blank
                 lines.

    Returns:
        A list of paragraph strings with no blank lines or empty entries.
    """
    paragraphs: list[str] = []
    for paragraph in content.split('\n\n'):
        stripped = paragraph.strip()
        if stripped:
            paragraphs.append(stripped.replace('\n', ' '))
    return paragraphs


def _parse_md_table_block(block: str) -> Optional[List[List[str]]]:
    """Check whether a block of text is a Markdown table, and if so, extract its cells.

    Markdown is a simple text formatting style where a table is written using
    vertical bars (``|``) to separate columns and a row of dashes to mark the
    header, e.g.::

        | Name | Age |
        | ---- | --- |
        | Ada  | 36  |

    Args:
        block: A chunk of text that might be a Markdown table (typically one
               paragraph's worth of text).

    Returns:
        The table's cell text as a list of rows (each row a list of cell
        strings) if ``block`` is a valid Markdown table, or ``None`` if it
        isn't a table at all.
    """
    # Normalise <br> variants that models sometimes emit instead of real newlines.
    block = re.sub(r'<br\s*/?>', '\n', block, flags=re.IGNORECASE)
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    pipe_re = re.compile(r'^\|.*\|$')
    sep_re = re.compile(r'^\|[\s\-\|:]+\|$')
    if not all(pipe_re.match(line) for line in lines):
        return None
    if not any(sep_re.match(line) for line in lines):
        return None
    rows: List[List[str]] = []
    for line in lines:
        if sep_re.match(line):
            continue
        cells = [cell.strip() for cell in line[1:-1].split('|')]
        rows.append(cells)
    return rows if rows else None


def _extract_markdown_tables(content: str) -> tuple[str, dict[str, List[List[str]]]]:
    """Pull Markdown tables out of a block of text and replace each with a placeholder marker.

    This lets each output builder (Word, PDF) insert a real table object at
    the placeholder's position later, instead of trying to render pipe
    characters and dashes as literal text.

    Args:
        content: The full text that may contain one or more Markdown tables.

    Returns:
        A two-item tuple: the text with each table replaced by a marker like
        ``'[MD_TABLE_1]'``, and a dictionary mapping each marker to that
        table's cell data (a list of rows, each row a list of cell strings).
        This dictionary can be merged into an existing table registry.
    """
    registry: dict[str, List[List[str]]] = {}
    counter = 0
    new_blocks: list[str] = []
    for block in content.split('\n\n'):
        rows = _parse_md_table_block(block)
        if rows is not None:
            counter += 1
            key = f"[MD_TABLE_{counter}]"
            registry[key] = rows
            new_blocks.append(key)
        else:
            new_blocks.append(block)
    return '\n\n'.join(new_blocks), registry


def _render_table_as_ascii(rows: List[List[str]]) -> str:
    """Draw a table's rows and columns using plain text characters (for .txt output).

    Since plain text files can't display real tables, this builds a
    text-based box drawing using ``+``, ``-``, and ``|`` characters so the
    rows and columns still line up visually when the file is opened.

    Args:
        rows: The table's cell text, as a list of rows (each row a list of
              cell strings).

    Returns:
        A multi-line string containing the text-drawn table, or an empty
        string if ``rows`` is empty.
    """
    if not rows:
        return ''
    n_cols = max(len(row) for row in rows)
    padded = [row + [''] * (n_cols - len(row)) for row in rows]
    col_widths = [
        max(max(len(padded[r][c]) for r in range(len(padded))), 3)
        for c in range(n_cols)
    ]
    sep = '+' + '+'.join('-' * (w + 2) for w in col_widths) + '+'
    lines = [sep]
    for i, row in enumerate(padded):
        cells = ' | '.join(cell.ljust(col_widths[j]) for j, cell in enumerate(row))
        lines.append(f'| {cells} |')
        if i == 0:
            lines.append(sep)
    lines.append(sep)
    return '\n'.join(lines)


def _render_markdown_tables_as_ascii(content: str) -> str:
    """Replace every Markdown table in a block of text with a plain-text drawn table.

    Used specifically for .txt output, where there's no way to render an
    actual table — this keeps the rows and columns readable by drawing them
    with text characters instead.

    Args:
        content: The full text that may contain one or more Markdown tables.

    Returns:
        The same text with each Markdown table replaced by its plain-text
        drawn equivalent.
    """
    new_blocks: list[str] = []
    for block in content.split('\n\n'):
        rows = _parse_md_table_block(block)
        if rows is not None:
            new_blocks.append(_render_table_as_ascii(rows))
        else:
            new_blocks.append(block)
    return '\n\n'.join(new_blocks)


def save_to_text_file(content: str, output_path: str, label: str) -> None:
    """Save text to a plain .txt file, replacing the file if it already exists.

    Args:
        content: The text to write to the file.
        output_path: The file path to write to, e.g. ``'response.txt'``.
        label: A short description used in the saved-confirmation message
               shown to the user (e.g. ``'Translation'`` or ``'Response'``).
    """
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        _emit_message(
            f"{label} saved to: {Path(output_path).name}",
            level=logging.INFO,
            log_message=f'{label} saved to text file: {output_path}',
            leading_newline=True,
        )
    except (OSError, UnicodeError) as e:
        _emit_message(
            f"Error saving to text file: {e}",
            level=logging.ERROR,
        )


def append_to_text_file(content: str, output_path: str, label: str) -> None:
    """Add text to the end of an existing .txt file, followed by a blank line.

    Used for progressive saving, where each page of a document is written to
    disk as soon as it's finished rather than waiting for the whole document
    to complete, so no progress is lost if the run is interrupted.

    Args:
        content: The text to append.
        output_path: The file path to append to, e.g. ``'response.txt'``.
        label: A short description used in the saved-confirmation message
               shown to the user (e.g. ``'Translation'`` or ``'Response'``).
    """
    try:
        with open(output_path, 'a', encoding='utf-8') as f:
            f.write(content + '\n\n')
        _emit_message(
            f"Page appended to: {Path(output_path).name}",
            level=logging.INFO,
            log_message=f'{label} appended to text file: {output_path}',
        )
    except (OSError, UnicodeError) as e:
        _emit_message(
            f"Error appending to text file: {e}",
            level=logging.ERROR,
        )
