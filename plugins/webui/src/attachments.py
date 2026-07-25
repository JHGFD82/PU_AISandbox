"""Turns an uploaded document into plain text the chat model can read.

The AI gateway this project talks to (PortKey) has no native file-upload
support, so a professor can't just hand the model a PDF the way they might
in a consumer chat product. This module works around that by reusing the
same document readers the ``translate``/``transcribe`` commands already
rely on (``src/processors/``) to pull the text out on our side first, then
sending that text to the model as ordinary chat content — the model never
sees the original file, only the words inside it.

Kept as a plain module (not a ``SandboxProcessor``-registered service) because
extracting text costs no AI-gateway tokens and needs no professor API key —
it's pure local file reading, the same as any of the CLI's own document
processors.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.processors.docx_processor import DocxProcessor
from src.processors.excel_processor import ExcelProcessor
from src.processors.json_processor import JsonProcessor
from src.processors.markdown_processor import MarkdownProcessor
from src.processors.pdf_processor import PDFProcessor
from src.processors.txt_processor import TxtProcessor
from src.runtime.file_types import _EXT_TYPES

# Extraction pages are rejoined into one block regardless of size, so this
# just needs to be comfortably larger than MAX_ATTACHMENT_CHARS to avoid the
# page splitter fragmenting a document unnecessarily on the way there.
_PAGE_SPLIT_TARGET = 1_000_000

# Rejected up front, before any parsing is attempted, so a huge file doesn't
# tie up the request just to fail later — 20 MB comfortably covers a long
# scanned-text PDF or a large spreadsheet while staying well short of
# anything that would take noticeable time to read into memory.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Applied after extraction, since a small file can still expand into a lot
# of text (e.g. a JSON file's indented rendering). ~120,000 characters is
# roughly 30,000 tokens at a conservative 4-characters-per-token estimate —
# generous for a reference document, but small enough that one attachment
# can't quietly dominate a professor's monthly budget once it's resent with
# every later turn (see ChatService's docstring on why the whole
# conversation, attachments included, is resent on every turn).
MAX_ATTACHMENT_CHARS = 120_000

# Extensions this module can read text out of. Deliberately narrower than
# the CLI's file-type detection (which also recognises images, handled by
# ImageProcessor for OCR/vision use cases that don't apply to a text
# attachment) — an image isn't "text to extract", so it isn't offered here.
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(
    ext for ext, (file_type, _label) in _EXT_TYPES.items() if file_type != "image"
)


class AttachmentError(Exception):
    """Raised when an uploaded file can't be turned into a chat attachment.

    Always carries a message written to be shown directly to the professor
    who uploaded the file, not just logged — e.g. "that file type isn't
    supported" or "that document is too long to attach."
    """


@dataclass
class ExtractedDocument:
    """The text pulled out of one uploaded document, ready to attach to a chat turn.

    Args:
        filename: The original filename, used to label the attachment in
                  the conversation.
        text: The document's extracted text content.
        char_count: ``len(text)`` — kept as its own field so the UI can show
                    a size hint without recomputing it.
    """

    filename: str
    text: str
    char_count: int


def extract_text(file_path: str, filename: str) -> ExtractedDocument:
    """Read an uploaded document from disk and return its text content.

    Args:
        file_path: Where the uploaded file was saved (typically a temporary
                   path) — this is what actually gets opened and read.
        filename: The original filename the professor uploaded it as. Only
                  its extension is used (to pick the right reader); the rest
                  is carried through purely as a label.

    Returns:
        The document's extracted text, ready to send to the chat model.

    Raises:
        AttachmentError: If the file's extension isn't one this module can
                         read, the file is larger than ``MAX_UPLOAD_BYTES``,
                         the extracted text is longer than
                         ``MAX_ATTACHMENT_CHARS``, the file couldn't be
                         parsed (corrupted, password-protected, etc.), or no
                         readable text was found at all.
    """
    _, ext = os.path.splitext(filename.lower())
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise AttachmentError(
            f"'{filename}' isn't a supported file type for attachments. Supported: {supported}."
        )

    try:
        size = os.path.getsize(file_path)
    except OSError as e:
        raise AttachmentError(f"Couldn't read '{filename}': {e}") from e
    if size > MAX_UPLOAD_BYTES:
        raise AttachmentError(
            f"'{filename}' is too large to attach "
            f"({size / (1024 * 1024):.1f} MB, limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
        )

    file_type = _EXT_TYPES[ext][0]
    try:
        pages = _extract_pages(file_type, file_path)
    except AttachmentError:
        raise
    except Exception as e:
        raise AttachmentError(f"Couldn't read '{filename}': {e}") from e

    text = "\n\n".join(page for page in pages if page).strip()
    if not text:
        raise AttachmentError(f"No readable text was found in '{filename}'.")

    if len(text) > MAX_ATTACHMENT_CHARS:
        raise AttachmentError(
            f"'{filename}' is too long to attach ({len(text):,} characters, limit is "
            f"{MAX_ATTACHMENT_CHARS:,}). Try attaching a shorter excerpt, or use this "
            "package's `translate`/`transcribe` commands, which are built for full-length documents."
        )

    return ExtractedDocument(filename=filename, text=text, char_count=len(text))


def _extract_pages(file_type: str, file_path: str) -> list[str]:
    """Dispatch to the right processor for *file_type* and return its text pages."""
    if file_type == "pdf":
        processor = PDFProcessor()
        with open(file_path, "rb") as f:
            return [processor.process_page(page) for page in processor.process_pdf(f)]
    if file_type == "docx":
        with open(file_path, "rb") as f:
            return DocxProcessor.process_docx_with_pages(f, _PAGE_SPLIT_TARGET)
    if file_type == "txt":
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return TxtProcessor.process_txt_with_pages(f, _PAGE_SPLIT_TARGET)
    if file_type == "excel":
        return ExcelProcessor.process_excel_with_pages(file_path, _PAGE_SPLIT_TARGET)
    if file_type == "json":
        return JsonProcessor.process_json_with_pages(file_path, _PAGE_SPLIT_TARGET)
    if file_type == "markdown":
        return MarkdownProcessor.process_markdown_with_pages(file_path, _PAGE_SPLIT_TARGET)
    # SUPPORTED_EXTENSIONS is derived from _EXT_TYPES minus 'image', so every
    # file_type reaching here should already be one of the branches above —
    # this only fires if a new extension is added to _EXT_TYPES without a
    # matching reader here.
    raise AttachmentError(f"No reader configured for file type '{file_type}'.")
