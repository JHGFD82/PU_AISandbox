"""Shared base class for all text-based document processors.

Subclasses (such as ``DocxProcessor``, ``TxtProcessor``, and
``MarkdownProcessor``) inherit two static methods from this class that handle
the common work of turning a flat string of text into paragraph-sized chunks
ready for translation.
"""

import logging
from typing import List


class BaseTextProcessor:
    """Foundation for processors that extract text from document files.

    Provides ``split_text_into_pages`` and ``parse_text_into_paragraphs`` —
    the two shared steps that every text-based processor needs before handing
    content to the translation service.

    Not an abstract base class, despite the name: it declares nothing that a
    subclass is obliged to implement, and both of its methods are usable
    directly off the class (the tests do exactly that). It was previously
    marked ``ABC``, which implied "you can't use this on its own" — untrue,
    since Python only enforces that when there are abstract methods to leave
    unimplemented.
    """
    
    @staticmethod
    def split_text_into_pages(paragraphs: List[str], target_page_size: int = 2000) -> List[str]:
        """Group paragraphs into logical pages so that no single page sent to the AI is too long.

        Accumulates paragraphs until adding the next one would push the page
        over the character target, then starts a new page. This keeps each
        translation request within the model's comfortable working range while
        preserving natural paragraph boundaries. A page that exceeds the target
        on its own is still kept as a single page rather than being split
        mid-paragraph.

        Args:
            paragraphs: A list of paragraph strings as returned by
                        ``parse_text_into_paragraphs``.
            target_page_size: The approximate maximum number of characters per
                              page. Defaults to ``2000``. Larger values produce
                              fewer, longer pages; smaller values produce more,
                              shorter ones.

        Returns:
            A list of page strings, each containing one or more paragraphs
            joined by double newlines. Returns ``['']`` if ``paragraphs`` is
            empty.
        """
        if not paragraphs:
            logging.warning("No paragraphs provided for page splitting")
            return [""]
        
        # Split into logical pages based on content size
        pages: List[str] = []
        current_page: List[str] = []
        current_size = 0
        
        for paragraph in paragraphs:
            para_size = len(paragraph)
            
            # If adding this paragraph would exceed target size and we have content, start new page
            if current_size + para_size > target_page_size and current_page:
                pages.append('\n\n'.join(current_page))
                current_page = [paragraph]
                current_size = para_size
            else:
                current_page.append(paragraph)
                current_size += para_size + 2  # +2 for the '\n\n' separator
        
        # Add the last page if it has content
        if current_page:
            pages.append('\n\n'.join(current_page))
        
        # If no pages were created (all paragraphs were very small), create one page
        if not pages:
            pages = ['\n\n'.join(paragraphs)]
        
        return pages
    
    @staticmethod
    def parse_text_into_paragraphs(content: str) -> List[str]:
        """Split a raw text string into a list of individual paragraphs.

        Splits first on double newlines (the standard paragraph separator),
        then falls back to single newlines if no double newlines are found,
        and finally returns the whole string as one paragraph if neither
        separator is present. Empty strings and whitespace-only lines are
        discarded.

        Args:
            content: The full text content of a document as a single string.

        Returns:
            A list of non-empty paragraph strings. Returns an empty list if
            ``content`` contains only whitespace.
        """
        if not content.strip():
            return []
        
        # Split by double newlines (paragraph breaks) first
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        
        if not paragraphs:
            # If no paragraph breaks, split by single newlines
            paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
        
        if not paragraphs:
            # If still no content, return the raw content as a single paragraph
            return [content.strip()]
        
        return paragraphs
