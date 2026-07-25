"""Image OCR: single-image and folder batch processing for the transcribe command.

Registered by ``plugins/transcription/plugin.py`` into ``sys.modules`` under
the key ``"src.runtime.image_handler"``, where ``SandboxProcessor`` discovers
the ``Mixin`` class below and adds it as one of its base classes.
"""

import logging
import os
from typing import Optional

from ..console import print_section
from ..errors import CLIError
from ..runtime.ui_action import ProgressCallback
from ..services.parallel_utils import cap_worker_count, collect_image_files, run_folder_parallel
from ..settings import MAX_PARALLEL_WORKERS

logger = logging.getLogger(__name__)


class Mixin:
    """Image OCR (transcription) capabilities added to SandboxProcessor.

    Provides methods for transcribing images to text (``transcribe`` command),
    for both single-file and whole-folder modes, with optional parallel
    processing when multiple workers are requested. File-type detection
    (``_detect_and_validate_file``) comes from the core ``_FileTypeMixin``,
    always present on ``SandboxProcessor``.
    """

    def process_image(
        self,
        file_path: str,
        target_language: str,
        output_file: Optional[str] = None,
        vertical: bool = False,
        spread: bool = False,
        passes: int = 1,
    ) -> None:
        """Transcribe an image file to text using OCR (transcribe command, single file).

        Sends the image to the AI and extracts any readable text, printing the
        result to the terminal. With ``passes > 1``, the transcription is run
        multiple times and the results are reconciled to reduce errors — useful
        for low-quality scans or difficult handwriting.

        Args:
            file_path: Absolute path to the image file.
            target_language: Full name of the language in the image, used to
                             guide the AI (e.g. ``'Japanese'``).
            output_file: Path to save the extracted text. ``None`` means
                         print to terminal only.
            vertical: When ``True``, tells the AI the text is arranged in
                      vertical columns (common in classical East Asian texts).
            spread: When ``True``, treats the image as a double-page spread.
            passes: Number of OCR passes to run per image. Multiple passes
                    improve accuracy by letting the model cross-check its own
                    output. Defaults to ``1``.
        """
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
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        """Transcribe all image files in a folder and optionally save the combined output.

        Processes images in natural filename order. When more than one worker
        is requested, images are transcribed in parallel and a progress bar is
        shown. Results are always printed and assembled in the original sorted
        order. If an image cannot be processed, an error message is recorded
        in its place and the remaining images continue.

        Args:
            folder_path: Path to the folder containing the image files.
            target_language: Full name of the language in the images
                             (e.g. ``'Japanese'``).
            output_file: Path to save the combined transcription. ``None``
                         means print to terminal only.
            vertical: When ``True``, tells the AI the text is in vertical
                      columns.
            spread: When ``True``, treats each image as a double-page spread.
            passes: Number of OCR passes per image. Defaults to ``1``.
            workers: Number of images to process in parallel. Defaults to
                     ``1`` (sequential). Capped at the system maximum.
            on_progress: Called with ``(completed_count, total_count)`` after
                         each image finishes. ``None`` (the default, and what
                         every CLI call passes) means no progress reporting —
                         only the webui's background job runner passes one
                         (see docs/webui-plugin-plan.md section 10). Only
                         honored on the sequential (``workers <= 1``) path,
                         same restriction as the translation plugin's
                         equivalent parameter.

        Raises:
            CLIError: If no image files are found in the folder.
        """
        folder_path = os.path.abspath(folder_path)
        image_files = collect_image_files(folder_path)

        if not image_files:
            raise CLIError(f"No image files found in folder '{folder_path}'.")

        logger.info(f"Processing {len(image_files)} image(s) in folder: {os.path.basename(folder_path)}")
        print(f"Found {len(image_files)} image(s) to process.\n")

        # --- sequential path ---
        if workers <= 1:
            combined_parts: list[str] = []
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
                finally:
                    if on_progress is not None:
                        on_progress(idx, len(image_files))

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
        combined_parts_p: list[str] = []
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
