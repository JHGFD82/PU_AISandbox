"""Table-aware Word document extraction for round-trip DOCX translation.

Used only when translating a .docx file to a .docx output — extracts each
table into a separate registry so it can be translated (via
``TranslationService.translate_table_grid``) and reinserted as a real Word
table by ``FileOutputHandler.save_to_docx`` rather than being flattened into
prose.

Registered by ``plugins/translation/plugin.py`` into ``sys.modules`` under the
key ``"src.processors.docx_translation"``.
"""

import logging
from typing import BinaryIO, Dict, List, Tuple

from .docx_processor import DocxProcessor
from ..models.doc_block import ParagraphBlock, TableBlock
from ..settings import DEFAULT_PAGE_SIZE


def process_docx_for_translation(
    file_obj: BinaryIO,
    target_page_size: int = DEFAULT_PAGE_SIZE,
) -> Tuple[List[str], Dict[str, List[List[str]]]]:
    """Extract pages and table data from a Word document for round-trip DOCX translation.

    Like ``DocxProcessor.process_docx_with_pages``, but also extracts each
    table's cell contents into a separate registry. In the extracted pages,
    every table is replaced by its placeholder token (``[TABLE_1]`` etc.) so
    the translation prompt stays clean. The table cells are translated
    separately via ``TranslationService.translate_table_grid`` and the
    translated grids are later reinserted as proper Word tables by
    ``FileOutputHandler.save_to_docx``.

    Use this function (instead of ``DocxProcessor.process_docx_with_pages``)
    whenever the output will be a ``.docx`` file so that tables survive
    translation as proper Word table objects rather than being flattened to
    prose.

    Args:
        file_obj: An open binary file object pointing to a ``.docx`` file.
        target_page_size: Approximate maximum characters per page.

    Returns:
        A two-item tuple of ``(pages, table_registry)``. ``pages`` is a
        list of page strings with table placeholders embedded.
        ``table_registry`` maps each placeholder (e.g. ``'[TABLE_1]'``)
        to the original untranslated cell grid (a list of rows, each row
        a list of cell strings).
    """
    processor = DocxProcessor()
    blocks = DocxProcessor.extract_blocks(file_obj)

    table_registry: Dict[str, List[List[str]]] = {}
    text_parts: List[str] = []

    for block in blocks:
        if isinstance(block, ParagraphBlock):
            text_parts.append(block.text)
        elif isinstance(block, TableBlock):
            table_registry[block.placeholder] = block.rows
            text_parts.append(block.placeholder)

    combined = '\n\n'.join(text_parts) if text_parts else ""
    if not combined:
        logging.warning("No content found in Word document")
        return [""], {}

    paragraphs = processor.parse_text_into_paragraphs(combined)
    pages = processor.split_text_into_pages(paragraphs, target_page_size)
    logging.info(
        f"Split Word document into {len(pages)} logical page(s) "
        f"with {len(table_registry)} table(s) extracted for separate translation"
    )
    return pages, table_registry
