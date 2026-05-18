"""Markdown file processor: reads .md files and splits into logical text pages."""

import logging
from typing import List, TextIO

from .base_text_processor import BaseTextProcessor
from .constants import DEFAULT_PAGE_SIZE


class MarkdownProcessor(BaseTextProcessor):
    """Handles extraction of text from Markdown (.md) files.

    Markdown content is preserved as-is so that translations and other
    processing steps can work with the original formatting.
    """

    def extract_raw_content(self, file_obj: TextIO) -> str:
        """Extract raw Markdown content from a file object."""
        return file_obj.read().strip()

    @staticmethod
    def process_markdown_with_pages(
        file_path: str, target_page_size: int = DEFAULT_PAGE_SIZE
    ) -> List[str]:
        """Read a Markdown file and return its content as logical text pages.

        Markdown formatting is preserved so that any downstream service (e.g.
        translation) can see the structure.  The result is split into logical
        pages the same way as :meth:`TxtProcessor.process_txt_with_pages`.

        Args:
            file_path: Absolute path to the .md file.
            target_page_size: Target number of characters per logical page.

        Returns:
            List of strings, each representing a logical page of content.
        """
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                processor = MarkdownProcessor()
                content = processor.extract_raw_content(fh)
        except OSError as exc:
            raise Exception(f"Failed to read Markdown file '{file_path}': {exc}") from exc

        if not content:
            logging.warning("No content found in Markdown file '%s'", file_path)
            return [""]

        paragraphs = processor.parse_text_into_paragraphs(content)
        pages = processor.split_text_into_pages(paragraphs, target_page_size)

        logging.info("Split Markdown file into %d logical page(s)", len(pages))
        return pages
