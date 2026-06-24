"""Document translation mixin: detects file types and routes each format to the right translation pipeline.

This module handles everything between receiving a file path on the command
line and delivering translated text to the output layer. It recognises PDFs,
Word documents, plain text files, Excel spreadsheets, JSON files, and Markdown
files, extracts their text content in logical pages, sends each page to the
translation service, and assembles the results. Image files are routed to the
combined OCR-and-translate pipeline instead.
"""

import logging
import os
import tempfile
from typing import Optional, List, Tuple

from ..errors import CLIError
from ..models import EmbeddedMedia, OutputOptions
from ..processors.docx_processor import DocxProcessor
from ..processors.excel_processor import ExcelProcessor
from ..processors.json_processor import JsonProcessor
from ..processors.markdown_processor import MarkdownProcessor
from ..processors.pdf_media_extractor import PdfMediaExtractor
from ..processors.txt_processor import TxtProcessor
from ..settings import DEFAULT_PAGE_SIZE

logger = logging.getLogger(__name__)

# Maps file extension → (file_type token, human-readable label).
_EXT_TYPES: dict[str, tuple[str, str]] = {
    '.pdf':  ('pdf',      'PDF file'),
    '.docx': ('docx',     'Word document'),
    '.txt':  ('txt',      'text file'),
    '.xlsx': ('excel',    'Excel spreadsheet'),
    '.xls':  ('excel',    'Excel spreadsheet'),
    '.json': ('json',     'JSON file'),
    '.md':   ('markdown', 'Markdown file'),
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
    """Document translation capabilities added to SandboxProcessor.

    Provides methods for translating documents of any supported format. The
    host class (``SandboxProcessor``) supplies the services this mixin calls —
    ``translation_service``, ``pdf_processor``, ``file_output``, and
    ``image_processor`` — so no setup is needed beyond constructing a
    ``SandboxProcessor``.
    """

    def _detect_and_validate_file(self, file_path: str) -> str:
        """Check that a file exists and identify its type.

        Recognises image files by content as well as by extension, so formats
        like ``.jpg`` and ``.png`` are handled alongside document formats.

        Args:
            file_path: Absolute path to the file to inspect.

        Returns:
            A short type token: one of ``'image'``, ``'pdf'``, ``'docx'``,
            ``'txt'``, ``'excel'``, ``'json'``, or ``'markdown'``.

        Raises:
            CLIError: If the file does not exist, fails image validation, or
                has an extension not supported by any installed plugin.
        """
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
            "Unsupported file format. Supported formats: PDF, DOCX, TXT, XLSX, JSON, MD, "
            "or image files (JPG, PNG, etc.)"
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
        """Extract text from a document, split it into pages, and translate the requested page range.

        Handles DOCX, TXT, Excel, JSON, and Markdown files through their
        respective processors, then passes the extracted pages to the
        translation service. The page range filtering (``page_nums``) is
        applied after extraction so only the requested portion is translated.

        Args:
            file_path: Absolute path to the document file.
            file_type: The type token returned by ``_detect_and_validate_file``
                       (e.g. ``'docx'``, ``'txt'``, ``'excel'``).
            page_nums: A page selection string in the same format accepted on
                       the command line (e.g. ``'1-5'``, ``'3,7,10-12'``), or
                       ``None`` to process the whole document.
            abstract_text: An optional abstract or summary of the document to
                           provide the AI as context when translating. Improves
                           accuracy for dense academic texts.
            source_language: Full name of the language to translate from
                             (e.g. ``'Japanese'``).
            target_language: Full name of the language to translate to
                             (e.g. ``'English'``).
            opts: Output and formatting options for this translation job.
            workers: Number of pages to translate in parallel. Defaults to
                     ``1`` (sequential).
            table_aware: When ``True`` and ``file_type`` is ``'docx'``, also
                         extracts table data separately so tables can be
                         reconstructed in the output DOCX file.

        Returns:
            A two-item tuple of ``(translated_pages, table_registry)``.
            ``translated_pages`` is a list of translated text strings, one per
            logical page. ``table_registry`` is a dictionary mapping table
            identifiers to their extracted cell grids — populated only when
            ``file_type`` is ``'docx'`` and ``table_aware`` is ``True``,
            otherwise ``None``.
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
        elif file_type == 'excel':
            all_pages = ExcelProcessor.process_excel_with_pages(file_path, target_page_size=DEFAULT_PAGE_SIZE)
            file_label = "Excel spreadsheet"
        elif file_type == 'json':
            all_pages = JsonProcessor.process_json_with_pages(file_path, target_page_size=DEFAULT_PAGE_SIZE)
            file_label = "JSON file"
        elif file_type == 'markdown':
            all_pages = MarkdownProcessor.process_markdown_with_pages(file_path, target_page_size=DEFAULT_PAGE_SIZE)
            file_label = "Markdown file"
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
        """Translate a document file and optionally save the result.

        Accepts PDF, Word (DOCX), plain text, Excel, JSON, Markdown, and image
        files. Detects the format automatically, extracts the text, and routes
        it to the appropriate translation pipeline. Progress and the translated
        text are printed to the terminal as each page completes.

        Args:
            file_path: Path to the source document (relative or absolute).
            source_language: Full name of the language to translate from
                             (e.g. ``'Japanese'``).
            target_language: Full name of the language to translate to
                             (e.g. ``'English'``).
            page_nums: A page selection string limiting which pages to
                       translate (e.g. ``'1-5'``, ``'3,7,10-12'``). Pass
                       ``None`` to translate the full document.
            abstract: When ``True``, the user is prompted to type an abstract
                      of the document before translation begins. This context
                      can improve translation accuracy for dense academic texts.
            opts: Output and formatting options for this job, including the
                  output file path, auto-save behaviour, font, and font size.
            workers: Number of pages to translate in parallel. Defaults to
                     ``1`` (sequential). Larger values speed up long documents
                     but use more API quota simultaneously.
            spread: When ``True``, treats the document as a double-page spread
                    (left and right pages combined into one image). Used for
                    scanned books or manuscripts.
            scanned: When ``True`` and the input is a PDF, renders each page as
                     an image first and passes it through the OCR-and-translate
                     pipeline. Use this for PDFs that contain scanned images
                     rather than selectable text.
        """
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
            elif file_type in ('excel', 'json', 'markdown'):
                document_text, _ = self._process_text_based_file(
                    file_path, file_type, page_nums, abstract_text,
                    source_language, target_language, opts, workers=workers,
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
            if "openpyxl" in str(e):
                raise CLIError(
                    "openpyxl is required to process Excel files. Install it with: pip install openpyxl"
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
        """Prompt the user to type text directly in the terminal and translate it.

        Collects multi-line input interactively (the user ends input with
        ``---``), then sends the text to the translation service. The
        translation is printed to the terminal and, if an output path is
        configured in ``opts``, also saved to a file.

        Args:
            source_language: Full name of the language the user will type in
                             (e.g. ``'Japanese'``).
            target_language: Full name of the language to translate to
                             (e.g. ``'English'``).
            abstract: When ``True``, the user is first prompted to type an
                      abstract for context before entering the main text.
            opts: Output and formatting options, including whether to save
                  the result and where.
        """
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
