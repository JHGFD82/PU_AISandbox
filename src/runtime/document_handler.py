"""Document translation mixin: file-type detection, text-file processing, and document translation."""

import logging
import os
import tempfile
from typing import Optional, List, Tuple

from ..errors import CLIError
from ..models import EmbeddedMedia, OutputOptions
from ..processors.docx_processor import DocxProcessor
from ..processors.pdf_media_extractor import PdfMediaExtractor
from ..processors.txt_processor import TxtProcessor
from ..settings import DEFAULT_PAGE_SIZE

logger = logging.getLogger(__name__)

# Maps file extension → (file_type token, human-readable label).
_EXT_TYPES: dict[str, tuple[str, str]] = {
    '.pdf':  ('pdf',  'PDF file'),
    '.docx': ('docx', 'Word document'),
    '.txt':  ('txt',  'text file'),
}


def _parse_page_ranges(page_nums_str: Optional[str]) -> List[Tuple[int, Optional[int]]]:
    """Parse a page selection string into a list of zero-based (start, end) index pairs.

    Returns [(0, None)] when no selection is specified (meaning all pages).

    Examples::

        "5"          -> [(4, 4)]
        "1-10"       -> [(0, 9)]
        "4,15-17,20" -> [(3, 3), (14, 16), (19, 19)]
    """
    if page_nums_str is None:
        return [(0, None)]
    ranges: List[Tuple[int, Optional[int]]] = []
    for part in page_nums_str.split(','):
        part = part.strip()
        if '-' in part:
            start, end = map(int, part.split('-'))
            if start <= 0 or end <= 0 or start > end:
                raise ValueError(f"Invalid page range '{part}'.")
            ranges.append((start - 1, end - 1))
        else:
            page = int(part)
            if page <= 0:
                raise ValueError(f"'{part}' is not a valid page number.")
            ranges.append((page - 1, page - 1))
    return ranges


class _DocumentHandlerMixin:
    """Mixin that adds document-translation capabilities to SandboxProcessor.

    Expects the following attributes set by the host class __init__:
        self.translation_service, self.pdf_processor, self.file_output,
        self.image_processor   (for is_image_file / validate_image_file)

    Also expects these methods from co-mixed classes:
        self._collect_multiline()         (_CommandMixin)
        self.process_image_translation()  (_ImageHandlerMixin)
        self.process_image_translation_folder()  (_ImageHandlerMixin)
    """

    def _detect_and_validate_file(self, file_path: str) -> str:
        """Detect file type and validate the file. Caller must pass an absolute path."""
        if not os.path.exists(file_path):
            raise CLIError(f"File '{file_path}' not found.")

        logger.debug(f"Validating file: {file_path}")
        lower_path = file_path.lower()

        if self.image_processor.is_image_file(file_path):  # type: ignore[attr-defined]
            if not self.image_processor.validate_image_file(file_path):  # type: ignore[attr-defined]
                raise CLIError(f"Image file '{file_path}' is not valid.")
            logger.debug(f"Detected image file: {file_path}")
            return 'image'

        _, ext = os.path.splitext(lower_path)
        if ext in _EXT_TYPES:
            file_type, label = _EXT_TYPES[ext]
            logger.debug(f"Detected {label}: {file_path}")
            return file_type

        raise CLIError(
            "Unsupported file format. Supported formats: PDF, DOCX, TXT, or image files (JPG, PNG, etc.)"
        )

    def _process_text_based_file(
        self,
        file_path: str,
        file_type: str,
        page_nums: Optional[str],
        abstract_text: Optional[str],
        source_language: str,
        target_language: str,
        opts: OutputOptions,
        workers: int = 1,
        table_aware: bool = False,
    ) -> Tuple[List[str], Optional[dict]]:
        """Process text-based files (DOCX, TXT) with common logic.

        Returns ``(translated_pages, source_table_registry)``.  The registry
        is only populated when *file_type* is ``'docx'`` and *table_aware* is
        ``True``; it contains the *untranslated* table grids extracted by
        :meth:`DocxProcessor.process_docx_for_translation` and must be
        translated by the caller before use.
        """
        logger.info(f"Processing {file_type.upper()} file: {os.path.basename(file_path)}")

        source_table_registry: Optional[dict] = None
        if file_type == 'docx':
            if table_aware:
                with open(file_path, 'rb') as f:
                    all_pages, source_table_registry = DocxProcessor.process_docx_for_translation(
                        f, target_page_size=DEFAULT_PAGE_SIZE
                    )
            else:
                with open(file_path, 'rb') as f:
                    all_pages = DocxProcessor.process_docx_with_pages(f, target_page_size=DEFAULT_PAGE_SIZE)
            file_label = "Word document"
        elif file_type == 'txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                all_pages = TxtProcessor.process_txt_with_pages(f, target_page_size=DEFAULT_PAGE_SIZE)
            file_label = "text file"
        else:
            raise ValueError(f"Unsupported text file type: {file_type}")

        results: List[str] = []
        for start_page, end_page in _parse_page_ranges(page_nums):
            if start_page >= len(all_pages):
                raise CLIError(
                    f"Page {start_page + 1} does not exist. Document has {len(all_pages)} logical pages."
                )
            actual_end = min(end_page, len(all_pages) - 1) if end_page is not None else len(all_pages) - 1
            segment = all_pages[start_page:actual_end + 1]
            if page_nums:
                logger.info(
                    f"Processing pages {start_page + 1}-{actual_end + 1} of {file_label} "
                    f"(logical pages based on content length)"
                )
            logger.info(f"Translating {len(segment)} page(s) from {source_language} to {target_language}")
            results.extend(self.translation_service.translate_text_pages(  # type: ignore[attr-defined]
                segment, abstract_text, source_language, target_language, opts, file_path, workers=workers,
            ))
        return results, source_table_registry

    def translate_document(
        self,
        file_path: str,
        source_language: str,
        target_language: str,
        page_nums: Optional[str] = None,
        abstract: bool = False,
        opts: OutputOptions = OutputOptions(),
        workers: int = 1,
        spread: bool = False,
        scanned: bool = False,
    ) -> None:
        """Translate a document file (PDF, Word document, or text file)."""
        file_path = os.path.abspath(file_path)
        file_type = self._detect_and_validate_file(file_path)

        # Image files bypass the document translation pipeline entirely.
        if file_type == 'image':
            try:
                self.process_image_translation(  # type: ignore[attr-defined]
                    file_path, source_language, target_language, opts, spread=spread,
                )
            except Exception as e:
                logger.debug(f"Error processing image: {e}", exc_info=True)
                raise CLIError(f"Error processing image: {e}") from e
            return

        # --scanned: render each PDF page as a PNG and route through the
        # combined OCR+translation pipeline (same as image folder mode).
        if file_type == 'pdf' and scanned:
            try:
                import fitz  # type: ignore[import]
            except ImportError as exc:
                raise CLIError(
                    "PyMuPDF (pymupdf) is required for --scanned. Install it with: pip install pymupdf"
                ) from exc
            print("Scanned PDF — rendering pages as images for OCR+translation.")
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    doc = fitz.open(file_path)
                    total_pages = len(doc)
                    page_indices: List[int] = []
                    for start, end in _parse_page_ranges(page_nums):
                        actual_end = min(end, total_pages - 1) if end is not None else total_pages - 1
                        if start >= total_pages:
                            raise CLIError(f"Page {start + 1} does not exist. PDF has {total_pages} pages.")
                        page_indices.extend(range(start, actual_end + 1))
                    logger.info(f"Rendering {len(page_indices)} page(s) from scanned PDF.")
                    for page_idx in page_indices:
                        pix = doc[page_idx].get_pixmap(dpi=300)  # type: ignore[union-attr]
                        pix.save(os.path.join(tmpdir, f"page_{page_idx + 1:04d}.png"))
                    doc.close()
                    self.process_image_translation_folder(  # type: ignore[attr-defined]
                        tmpdir, source_language, target_language, opts,
                        workers=workers, spread=spread,
                    )
            except CLIError:
                raise
            except Exception as e:
                logger.debug(f"Error processing scanned PDF: {e}", exc_info=True)
                raise CLIError(f"Error processing scanned PDF: {e}") from e
            return

        abstract_text: Optional[str] = self._collect_multiline("Abstract text") or None if abstract else None  # type: ignore[attr-defined]

        logger.info(f"Starting translation: {source_language} → {target_language}")

        embedded_media: Optional[List[EmbeddedMedia]] = None
        if opts.preserve_media and file_type == 'docx':
            logger.info("Extracting embedded media from source Word document.")
            with open(file_path, 'rb') as _mf:
                embedded_media = DocxProcessor.extract_media(_mf)
            logger.info(f"Found {len(embedded_media)} embedded image(s).")
        elif opts.preserve_media and file_type == 'pdf':
            logger.info("Extracting embedded media from source PDF.")
            with open(file_path, 'rb') as _mf:
                embedded_media = PdfMediaExtractor.extract_media(_mf)
            logger.info(f"Found {len(embedded_media)} embedded image(s).")

        try:
            document_text: List[str] = []
            docx_table_registry: Optional[dict] = None

            if file_type == 'pdf':
                with open(file_path, 'rb') as f:
                    all_pdf_pages = list(self.pdf_processor.process_pdf(f))  # type: ignore[attr-defined]
                    for start_page, end_page in _parse_page_ranges(page_nums):
                        document_text.extend(self.translation_service.translate_document(  # type: ignore[attr-defined]
                            iter(all_pdf_pages),
                            abstract_text,
                            start_page,
                            end_page,
                            source_language,
                            target_language,
                            opts,
                            file_path,
                            workers=workers,
                        ))
            elif file_type == 'txt':
                document_text, _ = self._process_text_based_file(
                    file_path, 'txt', page_nums, abstract_text,
                    source_language, target_language, opts, workers=workers,
                )
            elif file_type == 'docx':
                output_is_docx = bool(
                    opts.output_file and opts.output_file.lower().endswith('.docx')
                )
                document_text, source_table_registry = self._process_text_based_file(
                    file_path, 'docx', page_nums, abstract_text,
                    source_language, target_language, opts, workers=workers,
                    table_aware=output_is_docx,
                )

                if source_table_registry:
                    docx_table_registry = {}
                    for key, rows in source_table_registry.items():
                        ncols = len(rows[0]) if rows else 0
                        logger.info(f"Translating {key} ({len(rows)} row(s) × {ncols} col(s))…")
                        docx_table_registry[key] = (
                            self.translation_service.translate_table_grid(  # type: ignore[attr-defined]
                                rows, source_language, target_language
                            )
                        )
            else:
                raise CLIError(f"Cannot translate file type '{file_type}'.")

            full_translation = "".join(document_text)

            if not opts.progressive_save and (opts.output_file or opts.auto_save):
                logger.info("Saving translation output")
                self.file_output.save_translation_output(  # type: ignore[attr-defined]
                    full_translation,
                    file_path,
                    opts.output_file,
                    opts.auto_save,
                    source_language,
                    target_language,
                    opts.custom_font,
                    media=embedded_media,
                    table_registry=docx_table_registry,
                    font_size=opts.font_size,
                    label="Translation",
                )

        except ImportError as e:
            if "python-docx" in str(e):
                raise CLIError(
                    "python-docx is required to process Word documents. Install it with: pip install python-docx"
                ) from e
            raise CLIError(f"Import error: {e}") from e
        except Exception as e:
            logger.debug(f"Error processing document: {e}", exc_info=True)
            raise CLIError(f"Error processing document: {e}") from e

    def translate_custom_text(
        self,
        source_language: str,
        target_language: str,
        abstract: bool = False,
        opts: OutputOptions = OutputOptions(),
    ) -> None:
        """Translate custom text input by the user."""
        abstract_text: Optional[str] = self._collect_multiline("Abstract text") or None if abstract else None  # type: ignore[attr-defined]

        try:
            custom_text = self._collect_multiline(  # type: ignore[attr-defined]
                f"Enter the {source_language} text you want to translate to {target_language}"
            )

            if not custom_text.strip():
                logger.warning("No text provided for translation")
                print("No text provided.")
                return

            logger.debug(f"Starting custom text translation: {source_language} -> {target_language}")
            print("\nTranslating...")
            if abstract_text:
                translated_text = self.translation_service.translate_page_text(  # type: ignore[attr-defined]
                    abstract_text, custom_text, '', source_language, target_language
                )
            else:
                translated_text = self.translation_service.translate_text(  # type: ignore[attr-defined]
                    custom_text, source_language, target_language
                )

            if opts.output_file or opts.auto_save:
                input_filename = f"custom_text_{source_language}to{target_language}.txt"
                self.file_output.save_translation_output(  # type: ignore[attr-defined]
                    translated_text,
                    input_filename,
                    opts.output_file,
                    opts.auto_save,
                    source_language,
                    target_language,
                    opts.custom_font,
                    font_size=opts.font_size,
                    label="Translation",
                )

        except KeyboardInterrupt:
            logger.info("Translation cancelled by user")
            print("\nTranslation cancelled.")
        except Exception as e:
            logger.debug(f"Error during translation: {e}", exc_info=True)
            raise CLIError(f"Error during translation: {e}") from e
