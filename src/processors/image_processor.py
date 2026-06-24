"""Image file processor: converts image files to base64 data URLs for vision API calls."""

import logging
import os
import base64
from mimetypes import guess_type

from .constants import IMAGE_EXTENSIONS


class ImageProcessor():
    """Prepares image files for submission to the AI vision API.

    Provides helpers to identify image files, check whether an image is blank
    (so API calls can be skipped for empty pages), and encode image data into
    the base64 data URL format that vision-capable AI models expect.
    """

    @staticmethod
    def is_image_file(file_path: str) -> bool:
        """Return ``True`` if the file's extension is a recognised image format."""
        return file_path.lower().endswith(IMAGE_EXTENSIONS)

    @staticmethod
    def is_blank_image(
        file_path: str,
        white_threshold: int = 240,
        blank_fraction: float = 0.99,
    ) -> bool:
        """Return ``True`` when the image appears to be a blank or near-white page.

        Converts the image to grayscale and samples up to 2,000 evenly-spaced
        pixels. If at least ``blank_fraction`` of those samples are brighter
        than ``white_threshold`` (on a 0–255 scale), the page is treated as
        blank and the API call can be safely skipped, saving both time and
        quota. Returns ``False`` on any error so that borderline images are
        always sent to the model rather than silently dropped.

        Args:
            file_path: Path to the image file to check.
            white_threshold: Brightness level above which a pixel is counted
                             as white (0 = black, 255 = pure white). Defaults
                             to ``240``.
            blank_fraction: The proportion of sampled pixels that must be
                            above the threshold for the image to be considered
                            blank. Defaults to ``0.99`` (99%).

        Returns:
            ``True`` if the image is predominantly blank, ``False`` otherwise
            or if the check could not be completed.
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
        """Return ``True`` if the file has a recognised image extension and exists on disk."""
        if not ImageProcessor.is_image_file(file_path):
            return False

        if not os.path.exists(file_path):
            return False

        return True

    def local_image_to_data_url(self, file_path: str):
        """Read an image file from disk and encode it as a self-contained data URL string.

        A data URL embeds the image's content type and its raw bytes encoded
        in base64 directly in the string, so no separate file reference is
        needed when submitting the image to the AI vision API. The resulting
        string looks like ``data:image/png;base64,iVBOR...`` and can be placed
        directly in the ``image_url`` field of an API message.

        Args:
            file_path: Path to the image file to encode (e.g.
                       ``'/path/to/page_001.jpg'``).

        Returns:
            A data URL string containing the full encoded image, ready to
            include in an API request.
        """
        mime_type, _ = guess_type(file_path)

        if mime_type is None:
            mime_type = "application/octet-stream"

        with open(file_path, "rb") as image_file:
            base64_encoded_data = base64.b64encode(image_file.read()).decode("utf-8")

        return f"data:{mime_type};base64,{base64_encoded_data}"
