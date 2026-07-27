"""Text file processor: reads .txt files and splits into paragraph-delimited sections."""

import logging
from typing import List, TextIO

from ..errors import CLIError
from .base_text_processor import BaseTextProcessor
from .constants import DEFAULT_PAGE_SIZE


class TxtProcessor(BaseTextProcessor):
    """Handles extraction of text from plain text files."""
    
    def extract_raw_content(self, file_obj: TextIO) -> str:
        """Extract raw text content from a text file."""
        return file_obj.read().strip()
    
    @staticmethod
    def process_txt_with_pages(file_obj: TextIO, target_page_size: int = DEFAULT_PAGE_SIZE) -> List[str]:
        """
        Extract text from a plain text file and split into logical pages based on content size.
        
        Args:
            file_obj: Text file object
            target_page_size: Target number of characters per "page"
            
        Returns:
            List of strings, each representing a logical "page" of content
        """
        try:
            processor = TxtProcessor()
            content = processor.extract_raw_content(file_obj)
            
            if not content:
                logging.warning("No text content found in text file")
                return [""]
            
            # Parse content into paragraphs and split into pages
            paragraphs = processor.parse_text_into_paragraphs(content)
            pages = processor.split_text_into_pages(paragraphs, target_page_size)
            
            logging.info(f"Split text file into {len(pages)} logical pages")
            return pages
                
        except Exception as e:
            # The full traceback goes to the log for whoever maintains the
            # installation; the person at the keyboard gets a sentence they
            # can act on. Raised as CLIError so main() prints it plainly and
            # exits, rather than showing a non-CS user a raw traceback.
            logging.exception("Error processing text file")
            raise CLIError(
                f"Could not read this text file ({e}). If it came from another "
                "program, it may be saved in a character encoding the sandbox "
                "doesn't recognise — re-saving it as UTF-8 usually fixes that."
            ) from e
    
