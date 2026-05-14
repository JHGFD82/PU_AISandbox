"""Image OCR and image translation mixin: single-image and folder batch processing."""

import logging
import os
from typing import Optional, List

from ..errors import CLIError
from ..models import OutputOptions
from ..processors.constants import IMAGE_EXTENSIONS
from ..settings import MAX_PARALLEL_WORKERS
from ..services.parallel_utils import cap_worker_count, run_folder_parallel
from ..console import print_section

logger = logging.getLogger(__name__)


def _collect_image_files(folder_path: str) -> List[str]:
    """Return sorted absolute paths of image files in *folder_path*."""
    return [
        os.path.join(folder_path, name)
        for name in sorted(os.listdir(folder_path))
        if name.lower().endswith(IMAGE_EXTENSIONS)
        and os.path.isfile(os.path.join(folder_path, name))
    ]


class _ImageHandlerMixin:
    """Mixin that adds image OCR and image translation capabilities to SandboxProcessor.

    Expects the following attributes set by the host class __init__:
        self.image_processor_service, self.image_translation_service,
        self.token_tracker, self.file_output
    """

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
                if not transcript and not translation:
                    blank_count += 1
                    combined_parts.append(f"=== {filename} ===\n")
                    continue
                if transcript:
                    print_section("Transcript", transcript)
                print_section("Translation", translation)
                combined_parts.append(f"=== {filename} ===\n{translation}")
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

    def process_image(
        self,
        file_path: str,
        target_language: str,
        output_file: Optional[str] = None,
        vertical: bool = False,
        spread: bool = False,
        passes: int = 1,
    ) -> None:
        """Process an image file with OCR (transcribe command)."""
        logger.info(f"Starting OCR processing: {os.path.basename(file_path)} → {target_language}")

        try:
            extracted_text = self.image_processor_service.process_image_ocr(  # type: ignore[attr-defined]
                file_path, target_language, output_format="console",
                vertical=vertical, spread=spread, passes=passes
            )

            print_section("Extracted Text", extracted_text)

            if output_file:
                self.file_output.save_translation_output(  # type: ignore[attr-defined]
                    extracted_text, file_path, output_file, False,
                    target_language, target_language,
                    label="Transcription",
                )

        except Exception as e:
            logger.error(f"Error processing image: {e}", exc_info=True)
            raise CLIError(f"Error processing image: {e}") from e

    def process_image_folder(
        self,
        folder_path: str,
        target_language: str,
        output_file: Optional[str] = None,
        vertical: bool = False,
        spread: bool = False,
        passes: int = 1,
        workers: int = 1,
    ) -> None:
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
                    extracted_text = self.image_processor_service.process_image_ocr(  # type: ignore[attr-defined]
                        img_path, target_language, output_format="console",
                        vertical=vertical, spread=spread, passes=passes
                    )
                except Exception as e:
                    logger.error(f"Error processing '{filename}': {e}", exc_info=True)
                    print(f"  ERROR: {e}")
                    extracted_text = f"[Error processing {filename}: {e}]"

                print_section("Extracted Text", extracted_text)
                combined_parts.append(f"=== {filename} ===\n{extracted_text}")

            if output_file:
                self.file_output.save_translation_output(  # type: ignore[attr-defined]
                    "\n\n".join(combined_parts), None, output_file, False,
                    target_language, target_language,
                    label="Transcription",
                )
            return

        # --- parallel path ---
        actual_workers = cap_worker_count(workers, len(image_files), MAX_PARALLEL_WORKERS, "image", "folder")

        # Warm the pricing cache on the main thread so workers share the fast path.
        # Also suppress per-image/per-pass prints that would interleave with tqdm.
        self.image_processor_service._get_model()  # type: ignore[attr-defined]
        self.image_processor_service._suppress_inline_print = True  # type: ignore[attr-defined]

        def _ocr_one(idx: int, img_path: str) -> tuple:
            filename = os.path.basename(img_path)
            extracted = self.image_processor_service.process_image_ocr(  # type: ignore[attr-defined]
                img_path, target_language, output_format="console",
                vertical=vertical, spread=spread, passes=passes
            )
            return idx, filename, extracted

        results_map = run_folder_parallel(
            image_files, _ocr_one,
            lambda fname, e: (fname, f"[Error processing {fname}: {e}]"),
            self.token_tracker.usage_data,  # type: ignore[attr-defined]
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
            self.file_output.save_translation_output(  # type: ignore[attr-defined]
                "\n\n".join(combined_parts_p), None, output_file, False,
                target_language, target_language,
                label="Transcription",
            )
