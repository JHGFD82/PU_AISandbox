"""Settings that control how a translated document is saved to disk."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class OutputOptions:
    """The output-related settings for a single translation or transcription run.

    These settings all start out together at the command line and are needed
    by the very last step of processing (writing the finished file to disk),
    so they are grouped into one object and passed through unchanged rather
    than being threaded individually through every function in between.

    Attributes:
        output_file: The exact file path to save to, if the user specified
                     one with ``-o`` (e.g. ``'results/translated.docx'``).
                     ``None`` if no explicit path was given.
        auto_save: When ``True``, automatically generate a timestamped output
                   filename instead of requiring the user to specify one.
        progressive_save: When ``True``, save each page to disk as soon as it
                          finishes processing, rather than waiting until the
                          entire document is done. This means partial results
                          are preserved even if the run is interrupted partway
                          through.
        custom_font: The name of a specific font to use in PDF or Word output
                     (e.g. ``'Times New Roman'``). ``None`` uses the tool's
                     default font.
        preserve_media: When ``True``, images found in the source document are
                        copied into the translated Word document at roughly
                        their original positions.
        font_size: The body text size, in points, for PDF or Word output
                   (e.g. ``11``). ``None`` uses the default size of 9pt.
    """

    output_file: Optional[str] = None
    auto_save: bool = False
    progressive_save: bool = False
    custom_font: Optional[str] = None
    preserve_media: bool = False
    font_size: Optional[int] = None
