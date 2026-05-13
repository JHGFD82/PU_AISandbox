"""Output configuration for file-producing commands."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class OutputOptions:
    """Output configuration forwarded unchanged through a processing pipeline.

    Groups the four output-related parameters that travel together from the CLI
    handler all the way down to FileOutputHandler, eliminating the need to
    thread them individually through every intermediate method signature.

    Attributes:
        output_file:      Explicit output file path requested by the user.
        auto_save:        When True, auto-generate a timestamped output file.
        progressive_save: When True, save each page immediately after processing.
        custom_font:      Custom font name for PDF/Word output (None = default).
        preserve_media:   When True, images from the source document are reinserted
                          into the output Word document.
        font_size:        Body font size in points for PDF/Word output (None = default 9pt).
    """

    output_file: Optional[str] = None
    auto_save: bool = False
    progressive_save: bool = False
    custom_font: Optional[str] = None
    preserve_media: bool = False
    font_size: Optional[int] = None
