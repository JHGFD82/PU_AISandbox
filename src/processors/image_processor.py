"""Image file processor: converts image files to base64 data URLs for vision API calls."""

import logging
import os
import base64
from mimetypes import guess_type

from .constants import IMAGE_EXTENSIONS


class ImageProcessor():
    """Handles extraction of text from image files."""

    @staticmethod
    def is_image_file(file_path: str) -> bool:
        """Check if a file is an image file based on its extension."""
        return file_path.lower().endswith(IMAGE_EXTENSIONS)

    @staticmethod
    def is_blank_image(
        file_path: str,
        white_threshold: int = 240,
        blank_fraction: float = 0.99,
    ) -> bool:
        """Return True when the image is predominantly blank (near-white).

        Converts the image to grayscale via PyMuPDF and samples up to 2 000
        evenly-spaced pixels.  If at least *blank_fraction* of those samples
        are brighter than *white_threshold* (0–255), the page is considered
        blank and the API call can be safely skipped.

        Returns False on any error so that borderline images are always sent
        to the model rather than silently dropped.
        """
        try:
            import fitz  # PyMuPDF — required dependency (pymupdf)

            pix = fitz.Pixmap(file_path)
            if pix.colorspace and pix.colorspace.n > 1:
                gray_pix = fitz.Pixmap(fitz.csGRAY, pix)
            else:
                gray_pix = pix
            samples = gray_pix.samples  # bytes / memoryview of per-pixel values
            total = len(samples)
            if total == 0:
                return True
            # Sub-sample for speed: check at most ~2 000 evenly-spaced pixels.
            step = max(1, total // 2000)
            sampled = samples[::step]
            light = sum(1 for b in sampled if b >= white_threshold)
            result = (light / len(sampled)) >= blank_fraction
            if result:
                logging.debug(
                    f"Blank image detected: {os.path.basename(file_path)} "
                    f"({light}/{len(sampled)} sampled pixels ≥ {white_threshold})"
                )
            return result
        except Exception as exc:
            logging.debug(f"is_blank_image check failed for {file_path!r}: {exc}")
            return False

    @staticmethod
    def validate_image_file(file_path: str) -> bool:
        """Validate that a file is a valid image file."""
        if not ImageProcessor.is_image_file(file_path):
            return False

        if not os.path.exists(file_path):
            return False

        return True

    # Base 64 encode local image and return text to be included in AI prompt
    def local_image_to_data_url(self, file_path: str):
        """
        Get the url of a local image
        """
        mime_type, _ = guess_type(file_path)

        if mime_type is None:
            mime_type = "application/octet-stream"

        with open(file_path, "rb") as image_file:
            base64_encoded_data = base64.b64encode(image_file.read()).decode("utf-8")

        return f"data:{mime_type};base64,{base64_encoded_data}"
