"""PDF processor: extracts text page-by-page with CJK-optimised LAParams and page-range support."""

from typing import Iterator, BinaryIO
from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams, LTTextContainer, LTTextBox, LTTextLine, LTFigure, LTChar, LTPage
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
import re


def detect_numbered_content(text: str) -> bool:
    """Detect if text contains numbered lists, references, or citations."""
    # Look for various numbered patterns including Japanese/CJK formats
    patterns = [
        r'\d+\.\s+[^\d]',     # "1. Some text"
        r'\d+　',             # "1　" (Japanese full-width space)
        r'\d+\s+[^\d]',       # "1 Some text" (regular space)
        r'\[\d+\]',           # "[1]"
        r'\(\d+\)',           # "(1)"
        r'（\d+）',            # "（1）"
        r'\d+）',             # "1）"
        r'①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩',  # Circled numbers
        r'一、|二、|三、|四、|五、',     # Chinese numerals
        r'^\d+$',             # Standalone numbers on their own line
    ]
    
    for pattern in patterns:
        if re.search(pattern, text, re.MULTILINE):
            return True
    return False


def generate_process_text(abstract_text: str, page_text: str, previous_page: str, context_percentage: float = 0.65,
                         previous_translated: str = "") -> str:
    """Generate text for processing with context from both source and translated text."""
    # Use abstract if available, otherwise use source context
    source_context = abstract_text if abstract_text else previous_page[int(len(previous_page) * context_percentage):]
    
    # For now, let's simplify - only use translated context if there's clear numbered content
    # and only provide a hint about continuation rather than full context
    context_parts: list[str] = []
    if source_context:
        context_parts.append(source_context)
    
    # Only add translated context hint if we detect numbered content that might continue
    if previous_translated and detect_numbered_content(page_text):
        # Extract just the last few lines of translated text that contain numbers
        import re
        translated_lines = previous_translated.strip().split('\n')[-5:]  # Last 5 lines only
        # Look for various numbering patterns in translated text
        numbered_patterns = [r'\d+\.', r'\d+\)', r'（\d+）', r'\[\d+\]', r'\d+　', r'^\d+\s']
        numbered_lines: list[str] = []
        for line in translated_lines:
            for pattern in numbered_patterns:
                if re.search(pattern, line):
                    numbered_lines.append(line)
                    break
        if numbered_lines:
            context_parts.append(f"Previous numbering ended with: {numbered_lines[-1]}")
    
    if context_parts:
        context = "--Context: \n" + "\n".join(context_parts)
    else:
        context = ""
    
    return f"--Current Page: \n{page_text}\n{context}"


class PDFProcessor:
    """Extracts text from PDF files page by page, with tuning for CJK and vertical text layouts.

    Uses the ``pdfminer.six`` library to parse PDF structure and pull out
    text content. The layout analysis settings are pre-tuned for documents
    that contain Chinese, Japanese, or Korean characters, including support
    for vertical text columns.
    """

    def __init__(self):
        """Set up the PDF text extractor with layout parameters tuned for CJK documents.

        Configures character grouping margins, enables vertical text detection,
        and disables settings that can break CJK character clusters. These
        parameters are applied to every page processed by this instance.
        """
        self.rsrcmgr = PDFResourceManager()
        # Improved LAParams for better CJK text extraction
        self.laparams = LAParams(
            char_margin=0.5,  # Increase margin to better group characters
            line_margin=0.5,  # Increase line margin
            word_margin=0.1,  # Reduce word margin to avoid breaking CJK characters
            detect_vertical=True,  # Enable vertical text detection for CJK
            all_texts=False,  # Only extract text, not non-text elements
            boxes_flow=None,  # Use None for better CJK handling
        )
        self.device = PDFPageAggregator(self.rsrcmgr, laparams=self.laparams)
        self.interpreter = PDFPageInterpreter(self.rsrcmgr, self.device)
    
    def _clean_text(self, text: str) -> str:
        """Remove encoding artefacts and normalise whitespace in text extracted from a PDF.

        Strips null characters, byte-order marks, and ``(cid:N)`` references
        that appear when a PDF's character mapping is incomplete. Collapses
        runs of spaces and tabs to a single space while preserving line breaks.

        Args:
            text: Raw text as extracted from the PDF layout engine.

        Returns:
            Cleaned text ready for further processing, or an empty string if
            the input was empty or whitespace-only.
        """
        if not text:
            return ""
        
        # Remove null characters and other control characters
        cleaned_text = text.replace('\x00', '').replace('\ufeff', '')
        
        # Remove CID references like (cid:123) which appear when character mapping fails
        cleaned_text = re.sub(r'\(cid:\d+\)', '', cleaned_text)
        
        # Remove excessive whitespace but preserve line breaks
        cleaned_text = re.sub(r'[ \t]+', ' ', cleaned_text)
        
        return cleaned_text.strip()
    
    def process_pdf(self, file_handle: BinaryIO) -> Iterator[PDFPage]:
        """Open a PDF file and return an object that steps through its pages one at a time.

        Using an iterator (a step-through object) rather than loading all pages
        at once keeps memory use low for large documents. Callers typically pass
        the result directly to ``process_page`` in a loop.

        Args:
            file_handle: An open binary file object pointing to a PDF file.

        Returns:
            An iterator that yields one ``PDFPage`` object per page in the
            document, in order from first to last.
        """
        return PDFPage.get_pages(file_handle)
    
    def parse_layout(self, layout: LTPage) -> str:
        """
        Parse the layout tree of a PDF page and extract text content.

        Args:
            layout: The LTPage object representing the layout of a PDF page.

        Returns:
            A string containing the extracted and cleaned text from the page.
        """
        result: list[str] = []
        stack = list(layout)  # Using a list as a stack

        while stack:
            lt_obj = stack.pop(0)
            if isinstance(lt_obj, LTTextLine):
                text = lt_obj.get_text()
                cleaned_text = self._clean_text(text)
                if cleaned_text:
                    result.append(cleaned_text)
            elif isinstance(lt_obj, (LTChar, LTTextContainer)):
                text = lt_obj.get_text()
                cleaned_text = self._clean_text(text)
                if cleaned_text:
                    result.append(cleaned_text)
            elif isinstance(lt_obj, (LTFigure, LTTextBox)):
                stack.extend(list(lt_obj))  # Add children to the stack

        # Join with line breaks to preserve document structure
        final_text = '\n'.join(result)
        # Clean up excessive line breaks but preserve paragraph structure
        final_text = re.sub(r'\n\s*\n', '\n\n', final_text)  # Preserve paragraph breaks
        final_text = re.sub(r'\n{3,}', '\n\n', final_text)  # Remove excessive line breaks
        return final_text.strip()
    
    def process_page(self, page: PDFPage) -> str:
        """
        Process a single PDF page and extract its text content.

        Args:
            page: The PDFPage object representing the page to process.

        Returns:
            A string containing the extracted and cleaned text from the page.
        """
        self.interpreter.process_page(page)
        layout = self.device.get_result()
        return self.parse_layout(layout)
