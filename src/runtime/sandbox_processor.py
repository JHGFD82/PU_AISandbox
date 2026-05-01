"""Runtime processing orchestration for translation and OCR commands."""

import logging
import os
import tempfile
from typing import Optional, List, Tuple, TypedDict

from ..config import get_api_key
from ..errors import CLIError
from ..models import EmbeddedMedia, OutputOptions
from ..output.file_output import FileOutputHandler
from ..processors.constants import IMAGE_EXTENSIONS
from ..processors.docx_processor import DocxProcessor
from ..processors.image_processor import ImageProcessor
from ..processors.pdf_media_extractor import PdfMediaExtractor
from ..processors.pdf_processor import PDFProcessor
from ..processors.txt_processor import TxtProcessor
from ..settings import DEFAULT_PAGE_SIZE, MAX_PARALLEL_WORKERS
from ..services.image_processor_service import ImageProcessorService
from ..services.image_translation_service import ImageTranslationService
from ..services.parallel_utils import tqdm_logging, update_pbar_postfix, cap_worker_count, run_folder_parallel
from ..services.prompt_service import PromptService
from ..services.transcription_review_service import TranscriptionReviewService
from ..services.translation_service import TranslationService
from ..tracking.token_tracker import TokenTracker
from .command_runner import _CommandMixin
from ..console import print_section

logger = logging.getLogger(__name__)


# Maps file extension → (file_type token, human-readable label) for _detect_and_validate_file.
_EXT_TYPES: dict[str, tuple[str, str]] = {
    '.pdf':  ('pdf',  'PDF file'),
    '.docx': ('docx', 'Word document'),
    '.txt':  ('txt',  'text file'),
}


def _collect_image_files(folder_path: str) -> List[str]:
    """Return sorted absolute paths of image files in *folder_path*."""
    return [
        os.path.join(folder_path, name)
        for name in sorted(os.listdir(folder_path))
        if name.lower().endswith(IMAGE_EXTENSIONS)
        and os.path.isfile(os.path.join(folder_path, name))
    ]


class _SvcKwargs(TypedDict, total=False):
    """Shared keyword arguments passed to every BaseService subclass."""
    token_tracker: TokenTracker
    model: Optional[str]
    temperature: Optional[float]
    top_p: Optional[float]
    max_tokens: Optional[int]


def _parse_page_ranges(page_nums_str: Optional[str]) -> List[Tuple[int, Optional[int]]]:
    """Parse a page selection string into a list of zero-based (start, end) index pairs.

    Returns [(0, None)] when no selection is specified (meaning all pages).
    Each entry in the list is translated independently so context never bleeds
    across range boundaries.

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


class SandboxProcessor(_CommandMixin):
    """Main application class for processing inputs to the Princeton AI Sandbox."""

    def __init__(self, professor_name: str, model: Optional[str] = None,
                 temperature: Optional[float] = None, top_p: Optional[float] = None,
                 max_tokens: Optional[int] = None):
        """Initialize the processor for the specified professor."""
        try:
            api_key, self.professor_display_name = get_api_key(professor_name)
            self.professor_name = professor_name

            logger.debug(f"Initializing processor for professor: {self.professor_display_name}")

            self.token_tracker = TokenTracker(professor=professor_name)
            _svc_kwargs: _SvcKwargs = {"token_tracker": self.token_tracker, "model": model, "temperature": temperature, "top_p": top_p, "max_tokens": max_tokens}
            self.translation_service = TranslationService(api_key, professor_name, **_svc_kwargs)
            self.image_processor_service = ImageProcessorService(api_key, professor_name, **_svc_kwargs)
            self.image_translation_service = ImageTranslationService(api_key, professor_name, **_svc_kwargs)
            self.prompt_service = PromptService(api_key, professor_name, **_svc_kwargs)
            self.transcription_review_service = TranscriptionReviewService(api_key, professor_name, **_svc_kwargs)

            self.image_processor = ImageProcessor()
            self.pdf_processor = PDFProcessor()
            self.file_output = FileOutputHandler()
        except ValueError as e:
            raise CLIError(f"Configuration error: {e}") from e

    def _detect_and_validate_file(self, file_path: str) -> str:
        """Detect file type and validate the file. Caller must pass an absolute path."""
        if not os.path.exists(file_path):
            raise CLIError(f"File '{file_path}' not found.")

        logger.debug(f"Validating file: {file_path}")
        lower_path = file_path.lower()

        if self.image_processor.is_image_file(file_path):
            if not self.image_processor.validate_image_file(file_path):
                raise CLIError(f"Image file '{file_path}' is not valid.")
            logger.debug(f"Detected image file: {file_path}")
            return 'image'

        _, ext = os.path.splitext(lower_path)
        if ext in _EXT_TYPES:
            file_type, label = _EXT_TYPES[ext]
            logger.debug(f"Detected {label}: {file_path}")
            return file_type

        raise CLIError("Unsupported file format. Supported formats: PDF, DOCX, TXT, or image files (JPG, PNG, etc.)")

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
                raise CLIError(f"Page {start_page + 1} does not exist. Document has {len(all_pages)} logical pages.")
            actual_end = min(end_page, len(all_pages) - 1) if end_page is not None else len(all_pages) - 1
            segment = all_pages[start_page:actual_end + 1]
            if page_nums:
                logger.info(
                    f"Processing pages {start_page + 1}-{actual_end + 1} of {file_label} "
                    f"(logical pages based on content length)"
                )
            logger.info(f"Translating {len(segment)} page(s) from {source_language} to {target_language}")
            results.extend(self.translation_service.translate_text_pages(
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
        # A single combined OCR + translation prompt gives reasoning models
        # (e.g. gpt-5) the ability to resolve ambiguous characters using
        # translation context before committing to a transcript.
        if file_type == 'image':
            try:
                self.process_image_translation(
                    file_path,
                    source_language,
                    target_language,
                    opts,
                    spread=spread,
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
                        tmpdir,
                        source_language,
                        target_language,
                        opts,
                        workers=workers,
                        spread=spread,
                    )
            except CLIError:
                raise
            except Exception as e:
                logger.debug(f"Error processing scanned PDF: {e}", exc_info=True)
                raise CLIError(f"Error processing scanned PDF: {e}") from e
            return

        abstract_text: Optional[str] = self._collect_multiline("Abstract text") or None if abstract else None

        logger.info(f"Starting translation: {source_language} → {target_language}")

        # Extract media from the source document before translation begins so the
        # file handle is not consumed by later processing.
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
                    all_pdf_pages = list(self.pdf_processor.process_pdf(f))
                    for start_page, end_page in _parse_page_ranges(page_nums):
                        document_text.extend(self.translation_service.translate_document(
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
                # Table-aware path (→ .docx output): tables translated separately
                # and reinserted as proper Word table objects.
                # Standard path (→ .txt/.pdf): tables rendered as plain text.
                document_text, source_table_registry = self._process_text_based_file(
                    file_path, 'docx', page_nums, abstract_text,
                    source_language, target_language, opts, workers=workers,
                    table_aware=output_is_docx,
                )

                # Translate each extracted table via Markdown round-trip.
                if source_table_registry:
                    docx_table_registry = {}
                    for key, rows in source_table_registry.items():
                        ncols = len(rows[0]) if rows else 0
                        logger.info(
                            f"Translating {key} "
                            f"({len(rows)} row(s) × {ncols} col(s))…"
                        )
                        docx_table_registry[key] = (
                            self.translation_service.translate_table_grid(
                                rows, source_language, target_language
                            )
                        )
            else:
                raise CLIError(f"Cannot translate file type '{file_type}'.")

            full_translation = "".join(document_text)

            if not opts.progressive_save and (opts.output_file or opts.auto_save):
                logger.info("Saving translation output")
                self.file_output.save_translation_output(
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
        abstract_text: Optional[str] = self._collect_multiline("Abstract text") or None if abstract else None

        try:
            custom_text = self._collect_multiline(f"Enter the {source_language} text you want to translate to {target_language}")

            if not custom_text.strip():
                logger.warning("No text provided for translation")
                print("No text provided.")
                return

            logger.debug(f"Starting custom text translation: {source_language} -> {target_language}")
            print("\nTranslating...")
            if abstract_text:
                translated_text = self.translation_service.translate_page_text(
                    abstract_text, custom_text, '', source_language, target_language
                )
            else:
                translated_text = self.translation_service.translate_text(custom_text, source_language, target_language)

            if opts.output_file or opts.auto_save:
                input_filename = f"custom_text_{source_language}to{target_language}.txt"
                self.file_output.save_translation_output(
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
        """Transcribe and translate an image in a single API call (translate command).

        Uses ImageTranslationService to send one combined prompt, allowing
        reasoning models to resolve ambiguous characters using translation context.
        Prints both the transcript and the translation; saves the translation if
        an output path is specified or auto_save is enabled.
        """
        logger.info(
            f"Starting image translation: {os.path.basename(file_path)} "
            f"{source_language} → {target_language}"
        )

        transcript, translation = self.image_translation_service.process_image_translation(
            file_path, source_language, target_language, spread=spread
        )

        if transcript:
            print_section("Transcript", transcript)

        print_section("Translation", translation)

        if opts.output_file or opts.auto_save:
            self.file_output.save_translation_output(
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
    ) -> None:
        """Translate all images in a folder using the combined OCR+translation service.

        When ``workers > 1`` images are dispatched in parallel via a ThreadPoolExecutor.
        Results are printed and assembled in sorted-filename order after all workers finish.
        """
        folder_path = os.path.abspath(folder_path)
        image_files = _collect_image_files(folder_path)

        if not image_files:
            raise CLIError(f"No image files found in folder '{folder_path}'.")

        logger.info(f"Processing {len(image_files)} image(s) in folder: {os.path.basename(folder_path)}")
        print(f"Found {len(image_files)} image(s) to translate.\n")

        # --- sequential path ---
        if workers <= 1:
            combined_parts: List[str] = []
            for idx, img_path in enumerate(image_files, start=1):
                filename = os.path.basename(img_path)
                print(f"[{idx}/{len(image_files)}] {filename}")
                try:
                    transcript, translation = self.image_translation_service.process_image_translation(
                        img_path, source_language, target_language, spread=spread
                    )
                except Exception as e:
                    logger.error(f"Error processing '{filename}': {e}", exc_info=True)
                    print(f"  ERROR: {e}")
                    transcript, translation = "", f"[Error processing {filename}: {e}]"
                if transcript:
                    print_section("Transcript", transcript)
                print_section("Translation", translation)
                combined_parts.append(f"=== {filename} ===\n{translation}")
            if opts.output_file or opts.auto_save:
                self.file_output.save_translation_output(
                    "\n\n".join(combined_parts), None, opts.output_file, opts.auto_save,
                    source_language, target_language, opts.custom_font,
                    font_size=opts.font_size,
                    label="Translation",
                )
            return

        # --- parallel path ---
        actual_workers = cap_worker_count(workers, len(image_files), MAX_PARALLEL_WORKERS, "image", "folder")

        # Warm pricing cache and suppress per-image prints before dispatching workers
        self.image_translation_service._get_model()
        self.image_translation_service._suppress_inline_print = True

        def _translate_one(idx: int, img_path: str) -> tuple:
            filename = os.path.basename(img_path)
            transcript, translation = self.image_translation_service.process_image_translation(
                img_path, source_language, target_language, spread=spread
            )
            return idx, filename, transcript, translation

        results_map = run_folder_parallel(
            image_files, _translate_one,
            lambda fname, e: (fname, "", f"[Error processing {fname}: {e}]"),
            self.token_tracker.usage_data,
            actual_workers,
            desc=f"Translating ({actual_workers} workers)... ",
        )

        # Print and assemble in sorted-filename (original) order
        combined_parts_p: List[str] = []
        for idx in range(len(image_files)):
            filename, transcript, translation = results_map[idx]
            print(f"[{idx + 1}/{len(image_files)}] {filename}")
            if transcript:
                print_section("Transcript", transcript)
            print_section("Translation", translation)
            combined_parts_p.append(f"=== {filename} ===\n{translation}")

        if opts.output_file or opts.auto_save:
            self.file_output.save_translation_output(
                "\n\n".join(combined_parts_p), None, opts.output_file, opts.auto_save,
                source_language, target_language, opts.custom_font,
                font_size=opts.font_size,
                label="Translation",
            )

    def process_image(self, file_path: str, target_language: str, output_file: Optional[str] = None, vertical: bool = False, spread: bool = False, passes: int = 1) -> None:
        """Process an image file with OCR (transcribe command)."""
        logger.info(f"Starting OCR processing: {os.path.basename(file_path)} → {target_language}")

        try:
            extracted_text = self.image_processor_service.process_image_ocr(file_path, target_language, output_format="console", vertical=vertical, spread=spread, passes=passes)

            print_section("Extracted Text", extracted_text)

            if output_file:
                self.file_output.save_translation_output(
                    extracted_text, file_path, output_file, False,
                    target_language, target_language,
                    label="Transcription",
                )

        except Exception as e:
            logger.error(f"Error processing image: {e}", exc_info=True)
            raise CLIError(f"Error processing image: {e}") from e

    def process_image_folder(self, folder_path: str, target_language: str, output_file: Optional[str] = None, vertical: bool = False, spread: bool = False, passes: int = 1, workers: int = 1) -> None:
        """Process all images in a folder with OCR, printing each result and optionally saving combined output.

        When ``workers > 1`` images are dispatched in parallel via a ThreadPoolExecutor.
        Multi-pass OCR within each image always runs sequentially inside the worker.
        Results are printed and assembled in the original sorted-filename order.
        """
        folder_path = os.path.abspath(folder_path)
        image_files = _collect_image_files(folder_path)

        if not image_files:
            raise CLIError(f"No image files found in folder '{folder_path}'.")

        logger.info(f"Processing {len(image_files)} image(s) in folder: {os.path.basename(folder_path)}")
        print(f"Found {len(image_files)} image(s) to process.\n")

        # --- sequential path ---
        if workers <= 1:
            combined_parts: List[str] = []
            for idx, img_path in enumerate(image_files, start=1):
                filename = os.path.basename(img_path)
                print(f"[{idx}/{len(image_files)}] {filename}")
                try:
                    extracted_text = self.image_processor_service.process_image_ocr(
                        img_path, target_language, output_format="console", vertical=vertical, spread=spread, passes=passes
                    )
                except Exception as e:
                    logger.error(f"Error processing '{filename}': {e}", exc_info=True)
                    print(f"  ERROR: {e}")
                    extracted_text = f"[Error processing {filename}: {e}]"

                print_section("Extracted Text", extracted_text)
                combined_parts.append(f"=== {filename} ===\n{extracted_text}")

            if output_file:
                self.file_output.save_translation_output(
                    "\n\n".join(combined_parts), None, output_file, False,
                    target_language, target_language,
                    label="Transcription",
                )
            return

        # --- parallel path ---
        actual_workers = cap_worker_count(workers, len(image_files), MAX_PARALLEL_WORKERS, "image", "folder")

        # Warm the pricing cache on the main thread so workers share the fast path.
        # Also suppress per-image/per-pass prints that would interleave with tqdm.
        self.image_processor_service._get_model()
        self.image_processor_service._suppress_inline_print = True

        def _ocr_one(idx: int, img_path: str) -> tuple:
            filename = os.path.basename(img_path)
            extracted = self.image_processor_service.process_image_ocr(
                img_path, target_language, output_format="console", vertical=vertical, spread=spread, passes=passes
            )
            return idx, filename, extracted

        results_map = run_folder_parallel(
            image_files, _ocr_one,
            lambda fname, e: (fname, f"[Error processing {fname}: {e}]"),
            self.token_tracker.usage_data,
            actual_workers,
            desc=f"Transcribing ({actual_workers} workers)... ",
        )

        # Print and assemble in sorted-filename (original) order
        combined_parts_p: List[str] = []
        for idx in range(len(image_files)):
            filename, extracted_text = results_map[idx]
            print(f"[{idx + 1}/{len(image_files)}] {filename}")
            print_section("Extracted Text", extracted_text)
            combined_parts_p.append(f"=== {filename} ===\n{extracted_text}")

        if output_file:
            self.file_output.save_translation_output(
                "\n\n".join(combined_parts_p), None, output_file, False,
                target_language, target_language,
                label="Transcription",
            )

    def process_prompt(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        output_file: Optional[str] = None,
    ) -> None:
        """Send a custom prompt and print (and optionally save) the response."""
        try:
            response = self.prompt_service.send_prompt(user_prompt, system_prompt)
            print("\n" + response)
            if output_file:
                FileOutputHandler.save_to_text_file(response, output_file, label="Response")
        except Exception as e:
            logger.error(f"Error sending prompt: {e}", exc_info=True)
            raise CLIError(f"Error sending prompt: {e}") from e

    def process_transcription_review(
        self,
        text: str,
        language: str,
        kanbun: bool = False,
        kanbun_main: bool = False,
        output_file: Optional[str] = None,
    ) -> None:
        """Review a transcription for OCR errors and print (and optionally save) the JSON report."""
        try:
            result_json = self.transcription_review_service.review_transcription(
                text, language, kanbun=kanbun, kanbun_main=kanbun_main
            )
            print("\n" + result_json)
            if output_file:
                FileOutputHandler.save_to_text_file(result_json, output_file, label="Review")
        except Exception as e:
            logger.error(f"Error during transcription review: {e}", exc_info=True)
            raise CLIError(f"Error during transcription review: {e}") from e
