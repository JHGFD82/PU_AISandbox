"""Pytest set-up for the transcription plugin's tests.

Files this plugin's own modules under the ``src.*`` names its code and tests
import them by (see tests/plugin_modules.py), and keeps its services from
recording usage to real files.
"""

from functools import partial
from pathlib import Path

from tests.plugin_modules import no_real_token_tracker  # noqa: F401  (importing it applies it here)
from tests.plugin_modules import register

_register = partial(register, Path(__file__).resolve().parent)

# Register in dependency order: settings → fragments → specs → services → runtime.
_register("pu_plugin.transcription.settings", "src/settings.py")
_register(
    "src.services.prompts.ocr_fragments",
    "src/services/prompts/ocr_fragments.py",
)
_register(
    "src.services.prompts.ocr",
    "src/services/prompts/ocr.py",
)
_register(
    "src.services.prompts.transcription_review",
    "src/services/prompts/transcription_review.py",
)
_register(
    "src.services.image_processor_service",
    "src/services/image_processor_service.py",
)
_register(
    "src.services.transcription_review_service",
    "src/services/transcription_review_service.py",
)
_register(
    "src.runtime.image_handler",
    "src/runtime/image_handler.py",
)
