"""Pytest set-up for the translation plugin's tests.

Files this plugin's own modules under the ``src.*`` names its code and tests
import them by (see tests/plugin_modules.py), and keeps its services from
recording usage to real files.
"""

from functools import partial
from pathlib import Path

from tests.plugin_modules import no_real_token_tracker  # noqa: F401  (importing it applies it here)
from tests.plugin_modules import register

_register = partial(register, Path(__file__).resolve().parent)

# Register in dependency order: settings → fragments → specs → services.
_register("pu_plugin.translation.settings", "src/settings.py")
_register(
    "src.services.prompts.translation_fragments",
    "src/services/prompts/translation_fragments.py",
)
_register(
    "src.services.prompts.translation",
    "src/services/prompts/translation.py",
)
_register(
    "src.services.prompts.image_translation",
    "src/services/prompts/image_translation.py",
)
_register(
    "src.services.translation_service",
    "src/services/translation_service.py",
)
_register(
    "src.services.image_translation_service",
    "src/services/image_translation_service.py",
)
_register(
    "src.processors.docx_translation",
    "src/processors/docx_translation.py",
)
_register(
    "src.runtime.document_handler",
    "src/runtime/document_handler.py",
)
