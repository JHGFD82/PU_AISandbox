"""Image OCR and image translation: single-image and folder batch processing for transcribe and translate commands."""

import logging
import os
import re
from typing import Optional, List, Any

from ..errors import CLIError
from ..models import OutputOptions
from ..processors.constants import IMAGE_EXTENSIONS
from ..settings import MAX_PARALLEL_WORKERS
from ..services.parallel_utils import cap_worker_count, run_folder_parallel
from ..console import print_section

logger = logging.getLogger(__name__)

_NATURAL_SPLIT_RE = re.compile(r"(\d+)")


def _natural_sort_key(name: str) -> List[Any]:
    """Return a key that sorts embedded digit runs numerically.

    Example: page_2.jpg comes before page_10.jpg.
    """
    parts = _NATURAL_SPLIT_RE.split(name)
    return [int(part) if part.isdigit() else part.casefold() for part in parts]


def _collect_image_files(folder_path: str) -> List[str]:
    """Return a sorted list of absolute paths to all image files in a folder.

    Files are sorted so that names with embedded numbers appear in the natural
    reading order (e.g. ``page_2.jpg`` before ``page_10.jpg``) rather than
    alphabetical order (which would put ``page_10.jpg`` before ``page_2.jpg``).
    """
    return [
        os.path.join(folder_path, name)
        for name in sorted(os.listdir(folder_path), key=_natural_sort_key)
        if name.lower().endswith(IMAGE_EXTENSIONS)
        and os.path.isfile(os.path.join(folder_path, name))
    ]


class _ImageHandlerMixin:
    """Image OCR and translation capabilities added to SandboxProcessor.

    Provides methods for transcribing images to text (``transcribe`` command)
    and for translating images in a single pass (``translate`` command on image
    files). Both single-file and whole-folder modes are supported, with optional
    parallel processing when multiple workers are requested.
    """

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

        Raises:
            CLIError: If no image files are found in the folder.
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

        Raises:
            CLIError: If no image files are found in the folder.
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
