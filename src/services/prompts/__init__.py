"""Prompt schema classes for the built-in prompt service.

Translation, OCR, image-translation, and transcription-review prompt specs
have been moved to the respective plugin repos.  They are registered into
sys.modules as ``src.services.prompts.<submodule>`` by each plugin's
``_register()`` calls.  The ``__getattr__`` below makes those names available
via ``from src.services.prompts import <ClassName>`` without requiring
changes to the plugin service files.
"""

import sys


def __getattr__(name: str):
    """Search registered src.services.prompts.* submodules for *name*."""
    prefix = "src.services.prompts."
    for mod_name, mod in sys.modules.items():
        if mod_name.startswith(prefix) and mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__: list[str] = []
