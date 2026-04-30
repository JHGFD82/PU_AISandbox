"""Output configuration shared across the translation pipeline."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OutputOptions:
    """Output configuration forwarded unchanged through the translation pipeline.

    Groups the four output-related parameters that travel together from the CLI
    handler all the way down to FileOutputHandler, eliminating the need to
    thread them individually through every intermediate method signature.

    Attributes:
        output_file:      Explicit output file path requested by the user.
        auto_save:        When True, auto-generate a timestamped output file.
        progressive_save: When True, save each page immediately after translation.
        custom_font:      Custom font name for PDF/Word output (None = default).
    """

    output_file: Optional[str] = None
    auto_save: bool = False
    progressive_save: bool = False
    custom_font: Optional[str] = None
    preserve_media: bool = False
