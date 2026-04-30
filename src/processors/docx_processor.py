"""Word document processor: extracts text from .docx files into logical sections."""

import logging
from typing import List, BinaryIO, TYPE_CHECKING

from .base_text_processor import BaseTextProcessor
from .constants import DEFAULT_PAGE_SIZE

if TYPE_CHECKING:
    from ..models.embedded_media import EmbeddedMedia


class DocxProcessor(BaseTextProcessor):
    """Handles extraction of text from Word documents."""
    
    def extract_raw_content(self, file_obj: BinaryIO) -> str:
        """Extract raw text content from a Word document."""
        try:
            from docx import Document
            
            # Load the document
            doc = Document(file_obj)
            
            # Extract text from all paragraphs
            paragraphs: List[str] = []
            for paragraph in doc.paragraphs:
                text = paragraph.text.strip()
                if text:  # Only add non-empty paragraphs
                    paragraphs.append(text)
            
            # Join paragraphs with double newlines
            return '\n\n'.join(paragraphs) if paragraphs else ""
                
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
