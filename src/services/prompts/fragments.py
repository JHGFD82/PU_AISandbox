"""Backward-compatibility re-export shim.

All prompt fragments have been split into:
  - prompt_fragments.py       (shared: ADDITIONAL_INSTRUCTIONS, TABLE_HINT_*)
  - translation_fragments.py  (translation + image-translation)
  - ocr_fragments.py          (OCR + transcription-review)

This file re-exports every public name so that any import of the form
  ``from .prompts import fragments as F``  or  ``from . import fragments as F``
continues to work during the transition period.  It will be removed in Phase 4
main-repo cleanup once all direct importers have been updated.
"""

from .prompt_fragments import *  # noqa: F401,F403
from .translation_fragments import *  # noqa: F401,F403
from .ocr_fragments import *  # noqa: F401,F403
