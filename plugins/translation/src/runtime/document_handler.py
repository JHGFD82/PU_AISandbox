"""Document and image translation mixin: detects file types and routes each format to the right translation pipeline.

This module handles everything between receiving a file path on the command
line and delivering translated text to the output layer. It recognises PDFs,
Word documents, plain text files, Excel spreadsheets, JSON files, and Markdown
files, extracts their text content in logical pages, sends each page to the
translation service, and assembles the results. Image files (single file or
whole folder) are routed through the combined OCR-and-translate pipeline.

Registered by ``plugins/translation/plugin.py`` into ``sys.modules`` under the
key ``"src.runtime.document_handler"``, where ``SandboxProcessor`` discovers
the ``Mixin`` class below and adds it as one of its base classes.
"""

import logging
import os
import tempfile
from typing import Optional, List, Tuple

from ..console import print_section
from ..errors import CLIError
from ..models import EmbeddedMedia, OutputOptions
from ..processors.docx_processor import DocxProcessor
from ..processors.docx_translation import process_docx_for_translation
from ..processors.excel_processor import ExcelProcessor
from ..processors.json_processor import JsonProcessor
from ..processors.markdown_processor import MarkdownProcessor
from ..processors.pdf_media_extractor import PdfMediaExtractor
from ..processors.txt_processor import TxtProcessor
from ..runtime.ui_action import PageTextCallback, ProgressCallback
from ..services.parallel_utils import cap_worker_count, collect_image_files, run_folder_parallel
from ..settings import DEFAULT_PAGE_SIZE, MAX_PARALLEL_WORKERS

logger = logging.getLogger(__name__)


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


class Mixin:
    """Document and image translation capabilities added to SandboxProcessor.

    Provides methods for translating documents of any supported format, plus
    single-image and whole-folder image translation. The host class
    (``SandboxProcessor``) supplies the services this mixin calls —
    ``translation_service``, ``image_translation_service``, ``pdf_processor``,
    ``file_output``, and ``image_processor`` — so no setup is needed beyond
    constructing a ``SandboxProcessor``. File-type detection
    (``_detect_and_validate_file``) comes from the core ``_FileTypeMixin``,
    always present on ``SandboxProcessor``.
    """

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
        on_progress: Optional[ProgressCallback] = None,
        on_page_text: Optional[PageTextCallback] = None,
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
            on_progress: Called with ``(completed_count, total_count)`` after
                         each page finishes, counted across every page range
                         requested (usually just one), on either the
                         sequential or parallel path — see
                         ``translation_service._translate_page_sequence``.
                         ``None`` (the default) means no progress reporting.
            on_page_text: Called with ``(page_number, translated_text)``
                          right after each page finishes, with
                          ``page_number`` counted the same
                          across-every-range way as ``on_progress``'s
                          count (a page range starting partway through the
                          document doesn't restart page numbering at 1).
                          Only meaningful when ``workers`` is ``1`` — see
                          ``_translate_page_sequence``'s docstring for why.

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
                    all_pages, source_table_registry = process_docx_for_translation(
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

        page_ranges = _parse_page_ranges(page_nums)
        # Known up front (unlike the PDF branch below, which only knows this
        # once fitz has opened the file) so on_progress's "total" is
        # accurate even across more than one requested range.
        total_requested = sum(
            (min(end, len(all_pages) - 1) if end is not None else len(all_pages) - 1) - start + 1
            for start, end in page_ranges
        )

        results: List[str] = []
        completed_so_far = 0
        for start_page, end_page in page_ranges:
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

            segment_progress = None
            if on_progress is not None:
                _base = completed_so_far

                def segment_progress(done: int, _total: int, _base=_base) -> None:
                    on_progress(_base + done, total_requested)

            segment_page_text = None
            if on_page_text is not None:
                _text_base = completed_so_far

                def segment_page_text(page_number: int, text: str, _text_base=_text_base) -> None:
                    on_page_text(_text_base + page_number, text)

            results.extend(self.translation_service.translate_text_pages(  # type: ignore[attr-defined]
                segment, abstract_text, source_language, target_language, opts, file_path,
                workers=workers, on_progress=segment_progress, on_page_text=segment_page_text,
            ))
            completed_so_far += len(segment)
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
        on_progress: Optional[ProgressCallback] = None,
        on_page_text: Optional[PageTextCallback] = None,
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
            on_progress: Called with ``(completed_count, total_count)`` after
                         each page or image finishes, on either the
                         sequential or parallel path. ``None`` (the default,
                         and what every CLI call passes) means no progress
                         reporting — only the webui's background job runner
                         passes one (see ``docs/webui-plugin-plan.md``
                         section 10). Ignored by the single-image path (one
                         image has nothing to report progress *between*).
            on_page_text: Called with ``(page_number, translated_text)``
                          right after each page finishes — a sibling to
                          ``on_progress`` carrying the actual translated
                          text instead of just a count, so a caller (the
                          webui's background job runner) can show each
                          page's translation as it completes rather than
                          only a percentage. ``None`` (the default, and
                          what every CLI call passes — the CLI already
                          prints each page's text to the terminal via
                          ``generate_text``'s inline ``print()``) means no
                          such reporting. Only meaningful when ``workers``
                          is ``1`` — unlike ``on_progress``, streaming a
                          specific page's text out of completion order
                          would be actively confusing.
        """
        file_path = os.path.abspath(file_path)
        file_type = self._detect_and_validate_file(file_path)  # type: ignore[attr-defined]

        # Image files bypass the document translation pipeline entirely.
        if file_type == 'image':
            try:
                self.process_image_translation(
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
                    self.process_image_translation_folder(
                        tmpdir, source_language, target_language, opts,
                        workers=workers, spread=spread, on_progress=on_progress,
                        on_page_text=on_page_text,
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
                    pdf_ranges = _parse_page_ranges(page_nums)
                    total_pdf_pages = sum(
                        (min(end, len(all_pdf_pages) - 1) if end is not None else len(all_pdf_pages) - 1) - start + 1
                        for start, end in pdf_ranges
                    )
                    pdf_completed_so_far = 0
                    for start_page, end_page in pdf_ranges:
                        range_progress = None
                        if on_progress is not None:
                            _pdf_base = pdf_completed_so_far

                            def range_progress(done: int, _total: int, _base=_pdf_base) -> None:
                                on_progress(_base + done, total_pdf_pages)

                        range_page_text = None
                        if on_page_text is not None:
                            _pdf_text_base = pdf_completed_so_far

                            def range_page_text(page_number: int, text: str, _base=_pdf_text_base) -> None:
                                on_page_text(_base + page_number, text)

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
                            on_progress=range_progress,
                            on_page_text=range_page_text,
                        ))
                        actual_end = min(end_page, len(all_pdf_pages) - 1) if end_page is not None else len(all_pdf_pages) - 1
                        pdf_completed_so_far += actual_end - start_page + 1
            elif file_type == 'txt':
                document_text, _ = self._process_text_based_file(
                    file_path, 'txt', page_nums, abstract_text,
                    source_language, target_language, opts, workers=workers,
                    on_progress=on_progress, on_page_text=on_page_text,
                )
            elif file_type == 'docx':
                output_is_docx = bool(
                    opts.output_file and opts.output_file.lower().endswith('.docx')
                )
                document_text, source_table_registry = self._process_text_based_file(
                    file_path, 'docx', page_nums, abstract_text,
                    source_language, target_language, opts, workers=workers,
                    table_aware=output_is_docx, on_progress=on_progress, on_page_text=on_page_text,
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
                    on_progress=on_progress, on_page_text=on_page_text,
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

    def process_image_translation(
        self,
        file_path: str,
        source_language: str,
        target_language: str,
        opts: OutputOptions = OutputOptions(),
        spread: bool = False,
    ) -> None:
        """Transcribe and translate a single image file in one step.

        Sends the image to the AI with a combined transcription-and-translation
        prompt. This single-pass approach lets the model use the translation
        context to resolve ambiguous characters in the transcription, which
        improves accuracy compared to running OCR and translation separately.
        Both the original transcription and the translation are printed to the
        terminal.

        Args:
            file_path: Absolute path to the image file.
            source_language: Full name of the language in the image
                             (e.g. ``'Japanese'``).
            target_language: Full name of the language to translate to
                             (e.g. ``'English'``).
            opts: Output and formatting options. If an output path is set or
                  auto-save is enabled, the translation is also saved to a file.
            spread: When ``True``, treats the image as a double-page spread
                    (two facing pages photographed together).
        """
        logger.info(
            f"Starting image translation: {os.path.basename(file_path)} "
            f"{source_language} → {target_language}"
        )

        transcript, translation = self.image_translation_service.process_image_translation(  # type: ignore[attr-defined]
            file_path, source_language, target_language, spread=spread
        )

        if transcript:
            print_section("Transcript", transcript)

        print_section("Translation", translation)

        if opts.output_file or opts.auto_save:
            self.file_output.save_translation_output(  # type: ignore[attr-defined]
                translation,
                file_path,
                opts.output_file,
                opts.auto_save,
                source_language,
                target_language,
                opts.custom_font,
                font_size=opts.font_size,
                label="Translation",
            )

    def process_image_translation_folder(
        self,
        folder_path: str,
        source_language: str,
        target_language: str,
        opts: OutputOptions = OutputOptions(),
        workers: int = 1,
        spread: bool = False,
        on_progress: Optional[ProgressCallback] = None,
        on_page_text: Optional[PageTextCallback] = None,
    ) -> None:
        """Translate all image files in a folder and optionally save the combined output.

        Processes images in natural filename order (so ``page_2.jpg`` comes
        before ``page_10.jpg``). When more than one worker is requested, images
        are processed in parallel to speed up large batches, and a progress bar
        is shown while they run. Results are always printed and assembled in the
        original sorted order, regardless of which order the workers finish.

        Args:
            folder_path: Path to the folder containing the image files.
            source_language: Full name of the language in the images
                             (e.g. ``'Japanese'``).
            target_language: Full name of the language to translate to
                             (e.g. ``'English'``).
            opts: Output and formatting options. If an output path is set or
                  auto-save is enabled, the combined translation is saved.
            workers: Number of images to process in parallel. Defaults to
                     ``1`` (sequential). Capped at the system maximum.
            spread: When ``True``, treats each image as a double-page spread.
            on_progress: Called with ``(completed_count, total_count)`` after
                         each image finishes, on *either* path — sequential
                         or parallel (reported via ``run_folder_parallel``'s
                         own ``on_progress`` on the parallel path — see
                         ``src/services/parallel_utils.py``). ``None`` (the
                         default) means no progress reporting. Unlike
                         ``on_page_text`` below, a plain count is safe to
                         report the moment any image finishes regardless of
                         completion order.
            on_page_text: Called with ``(image_number, translated_text)``
                          right after each image finishes — a sibling to
                          ``on_progress`` carrying the actual text. Only
                          honored on the sequential (``workers <= 1``) path —
                          unlike ``on_progress``, showing a specific image's
                          text out of order would be actively confusing, not
                          just a cosmetic gap, so this restriction stays,
                          for the same reason
                          ``translation_service._translate_page_sequence``'s
                          own ``on_page_text`` does.

        Raises:
            CLIError: If no image files are found in the folder.
        """
        folder_path = os.path.abspath(folder_path)
        image_files = collect_image_files(folder_path)

        if not image_files:
            raise CLIError(f"No image files found in folder '{folder_path}'.")

        logger.info(f"Processing {len(image_files)} image(s) in folder: {os.path.basename(folder_path)}")
        print(f"Found {len(image_files)} image(s) to translate.\n")

        # --- sequential path ---
        if workers <= 1:
            combined_parts: List[str] = []
            blank_count = 0
            for idx, img_path in enumerate(image_files, start=1):
                filename = os.path.basename(img_path)
                print(f"[{idx}/{len(image_files)}] {filename}")
                try:
                    transcript, translation = self.image_translation_service.process_image_translation(  # type: ignore[attr-defined]
                        img_path, source_language, target_language, spread=spread
                    )
                except Exception as e:
                    logger.error(f"Error processing '{filename}': {e}", exc_info=True)
                    print(f"  ERROR: {e}")
                    transcript, translation = "", f"[Error processing {filename}: {e}]"
                finally:
                    if on_progress is not None:
                        on_progress(idx, len(image_files))
                if not transcript and not translation:
                    blank_count += 1
                    combined_parts.append(f"=== {filename} ===\n")
                    continue
                if transcript:
                    print_section("Transcript", transcript)
                print_section("Translation", translation)
                combined_parts.append(f"=== {filename} ===\n{translation}")
                if on_page_text is not None:
                    on_page_text(idx, translation)
            if blank_count:
                unit_label = "page" if blank_count == 1 else "pages"
                msg = (
                    f"  {blank_count} image-only {unit_label}(s) had no readable text and were skipped"
                    " (run with --verbose for details)."
                )
                print(msg)
                logging.info(msg.strip())
            if opts.output_file or opts.auto_save:
                self.file_output.save_translation_output(  # type: ignore[attr-defined]
                    "\n\n".join(combined_parts), None, opts.output_file, opts.auto_save,
                    source_language, target_language, opts.custom_font,
                    font_size=opts.font_size,
                    label="Translation",
                )
            return

        # --- parallel path ---
        actual_workers = cap_worker_count(workers, len(image_files), MAX_PARALLEL_WORKERS, "image", "folder")

        # Warm pricing cache and suppress per-image prints before dispatching workers
        self.image_translation_service._get_model()  # type: ignore[attr-defined]
        self.image_translation_service._suppress_inline_print = True  # type: ignore[attr-defined]

        def _translate_one(idx: int, img_path: str) -> tuple:
            filename = os.path.basename(img_path)
            transcript, translation = self.image_translation_service.process_image_translation(  # type: ignore[attr-defined]
                img_path, source_language, target_language, spread=spread
            )
            return idx, filename, transcript, translation

        results_map = run_folder_parallel(
            image_files, _translate_one,
            lambda fname, e: (fname, "", f"[Error processing {fname}: {e}]"),
            self.token_tracker.usage_data,  # type: ignore[attr-defined]
            actual_workers,
            desc=f"Translating ({actual_workers} workers)... ",
            on_progress=on_progress,
        )

        # Print and assemble in sorted-filename (original) order
        combined_parts_p: List[str] = []
        blank_count_p = 0
        for idx in range(len(image_files)):
            filename, transcript, translation = results_map[idx]
            print(f"[{idx + 1}/{len(image_files)}] {filename}")
            if not transcript and not translation:
                blank_count_p += 1
                combined_parts_p.append(f"=== {filename} ===\n")
                continue
            if transcript:
                print_section("Transcript", transcript)
            print_section("Translation", translation)
            combined_parts_p.append(f"=== {filename} ===\n{translation}")
        if blank_count_p:
            unit_label = "page" if blank_count_p == 1 else "pages"
            msg = (
                f"  {blank_count_p} image-only {unit_label}(s) had no readable text and were skipped"
                " (run with --verbose for details)."
            )
            print(msg)
            logging.info(msg.strip())

        if opts.output_file or opts.auto_save:
            self.file_output.save_translation_output(  # type: ignore[attr-defined]
                "\n\n".join(combined_parts_p), None, opts.output_file, opts.auto_save,
                source_language, target_language, opts.custom_font,
                font_size=opts.font_size,
                label="Translation",
            )
