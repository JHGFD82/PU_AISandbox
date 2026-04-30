"""Word document processor: extracts text from .docx files into logical sections."""

import logging
from typing import Dict, List, BinaryIO, Tuple, Union, TYPE_CHECKING

from .base_text_processor import BaseTextProcessor
from .constants import DEFAULT_PAGE_SIZE

if TYPE_CHECKING:
    from ..models.embedded_media import EmbeddedMedia
    from ..models.doc_block import ParagraphBlock, TableBlock


class DocxProcessor(BaseTextProcessor):
    """Handles extraction of text from Word documents."""
    
    def extract_raw_content(self, file_obj: BinaryIO) -> str:
        """Extract raw text content from a Word document, including table cells.

        Walks the document body in document order so that table content appears
        at its correct position relative to surrounding paragraphs.  Each table
        row is rendered as a tab-separated line; rows are separated by newlines.
        """
        try:
            from docx import Document
            from docx.oxml.ns import qn

            doc = Document(file_obj)
            blocks: List[str] = []

            for child in doc.element.body:
                tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag

                if tag == 'p':
                    # Regular paragraph
                    text = ''.join(node.text or '' for node in child.iter(qn('w:t'))).strip()
                    if text:
                        blocks.append(text)

                elif tag == 'tbl':
                    # Table — collect rows, cells separated by tabs
                    rows: List[str] = []
                    for row in child.iter(qn('w:tr')):
                        cells: List[str] = []
                        for cell in row.iter(qn('w:tc')):
                            cell_text = ''.join(
                                node.text or '' for node in cell.iter(qn('w:t'))
                            ).strip()
                            cells.append(cell_text)
                        row_text = '\t'.join(cells)
                        if row_text.strip():
                            rows.append(row_text)
                    if rows:
                        blocks.append('\n'.join(rows))

            return '\n\n'.join(blocks) if blocks else ""

        except ImportError:
            raise ImportError(
                "python-docx is required to process Word documents. "
                "Install it with: pip install python-docx"
            )
    
    @staticmethod
    def process_docx_with_pages(file_obj: BinaryIO, target_page_size: int = DEFAULT_PAGE_SIZE) -> List[str]:
        """
        Extract text from a Word document and split into logical pages based on content size.
        
        Args:
            file_obj: Binary file object of the Word document
            target_page_size: Target number of characters per "page"
            
        Returns:
            List of strings, each representing a logical "page" of content
        """
        try:
            processor = DocxProcessor()
            content = processor.extract_raw_content(file_obj)
            
            if not content:
                logging.warning("No text content found in Word document")
                return [""]
            
            # Parse content into paragraphs and split into pages
            paragraphs = processor.parse_text_into_paragraphs(content)
            pages = processor.split_text_into_pages(paragraphs, target_page_size)
            
            logging.info(f"Split Word document into {len(pages)} logical pages")
            return pages
                
        except Exception as e:
            logging.error(f"Error processing Word document: {e}")
            raise Exception(f"Failed to process Word document: {e}")

    @staticmethod
    def extract_blocks(file_obj: BinaryIO) -> "List[Union[ParagraphBlock, TableBlock]]":
        """Extract the document body as an ordered list of typed blocks.

        Paragraphs become :class:`~src.models.doc_block.ParagraphBlock` items;
        tables become :class:`~src.models.doc_block.TableBlock` items with
        unique ``[TABLE_N]`` placeholder tokens that can be embedded in
        translation prompts and resolved back to Word table objects later.

        Walks ``doc.element.body`` in document order so tables appear at their
        correct position relative to surrounding paragraphs.
        """
        from docx import Document
        from docx.oxml.ns import qn
        from ..models.doc_block import ParagraphBlock, TableBlock

        doc = Document(file_obj)
        blocks: List[Union[ParagraphBlock, TableBlock]] = []
        table_counter = 0

        for child in doc.element.body:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag

            if tag == 'p':
                text = ''.join(node.text or '' for node in child.iter(qn('w:t'))).strip()
                if text:
                    blocks.append(ParagraphBlock(text=text))

            elif tag == 'tbl':
                table_counter += 1
                placeholder = f"[TABLE_{table_counter}]"
                rows: List[List[str]] = []
                for row in child.iter(qn('w:tr')):
                    cells: List[str] = [
                        ''.join(node.text or '' for node in cell.iter(qn('w:t'))).strip()
                        for cell in row.iter(qn('w:tc'))
                    ]
                    if any(c for c in cells):
                        rows.append(cells)
                if rows:
                    blocks.append(TableBlock(rows=rows, placeholder=placeholder))

        return blocks

    @staticmethod
    def process_docx_for_translation(
        file_obj: BinaryIO,
        target_page_size: int = DEFAULT_PAGE_SIZE,
    ) -> "Tuple[List[str], Dict[str, List[List[str]]]]":
        """Extract pages and a table registry for Markdown-round-trip translation.

        Returns ``(pages, table_registry)`` where:

        * ``pages`` — text strings split to ``target_page_size`` just like
          :meth:`process_docx_with_pages`, but each table is replaced by its
          unique ``[TABLE_N]`` placeholder token.
        * ``table_registry`` — mapping from ``"[TABLE_N]"`` to the raw cell
          grid (list-of-lists).  The grid is translated separately via
          :meth:`~src.services.translation_service.TranslationService.translate_table_grid`
          and later reinserted by
          :meth:`~src.output.file_output.FileOutputHandler.save_to_docx`.

        Use this method (instead of :meth:`process_docx_with_pages`) whenever
        the output format is ``.docx``, so that tables survive translation as
        proper Word table objects rather than being flattened to prose.
        """
        from ..models.doc_block import ParagraphBlock, TableBlock

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

    @staticmethod
    def extract_media(file_obj: BinaryIO) -> "List[EmbeddedMedia]":
        """Extract embedded images from a Word document with positional information.

        Each returned :class:`EmbeddedMedia` item carries a ``position_fraction``
        (0.0–1.0) that represents the image's approximate location in the source
        document relative to total paragraph count.  The fraction is used for
        proportional reinsertion when the translated paragraph count differs.

        Requires ``python-docx``.
        """
        from ..models.embedded_media import EmbeddedMedia
        from docx import Document
        from docx.oxml.ns import qn

        doc = Document(file_obj)
        paragraphs = doc.paragraphs
        total = len(paragraphs) or 1

        media_items: List[EmbeddedMedia] = []
        seen_r_ids: set = set()  # deduplicate reused image parts within the document

        for para_idx, para in enumerate(paragraphs):
            position_fraction = para_idx / total
            for run in para.runs:
                for drawing in run._element.iter(qn('w:drawing')):
                    for blip in drawing.iter(qn('a:blip')):
                        r_id = blip.get(qn('r:embed'))
                        if not r_id or r_id in seen_r_ids:
                            continue
                        try:
                            image_part = para.part.related_parts[r_id]
                        except KeyError:
                            logging.debug(f"Could not resolve image relationship '{r_id}'; skipping.")
                            continue

                        # Read EMU dimensions from the nearest wp:extent element.
                        width_emu: int | None = None
                        height_emu: int | None = None
                        for extent in drawing.iter(qn('wp:extent')):
                            try:
                                width_emu = int(extent.get('cx'))
                                height_emu = int(extent.get('cy'))
                            except (TypeError, ValueError):
                                pass
                            break  # only the first extent per drawing

                        seen_r_ids.add(r_id)
                        media_items.append(EmbeddedMedia(
                            data=image_part.blob,
                            content_type=image_part.content_type,
                            position_fraction=position_fraction,
                            width_emu=width_emu,
                            height_emu=height_emu,
                        ))

        logging.info(f"Extracted {len(media_items)} image(s) from Word document.")
        return media_items
