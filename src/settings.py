"""Load user-customizable settings from settings.toml at the repository root.

All tuneable runtime defaults live in that file. This module reads it once at
import time and exposes each value as a typed module-level constant so other
modules can import them directly.
"""

import sys
import tomllib
from pathlib import Path

_ROOT = Path(__file__).parent.parent  # src/ -> repo root
_TOML_PATH = _ROOT / "settings.toml"
_LOCAL_TOML_PATH = _ROOT / "settings.local.toml"

try:
    with _TOML_PATH.open("rb") as _f:
        _s = tomllib.load(_f)
except FileNotFoundError:
    raise FileNotFoundError(
        f"settings.toml not found at {_TOML_PATH}. "
        "Copy settings.toml from the repository root and edit it to configure the sandbox."
    )

# Merge settings.local.toml on top if present — only the keys you specify are overridden.
if _LOCAL_TOML_PATH.exists():
    with _LOCAL_TOML_PATH.open("rb") as _f:
        _local = tomllib.load(_f)
    for _section, _values in _local.items():
        if _section in _s and isinstance(_s[_section], dict):
            _s[_section].update(_values)
        else:
            _s[_section] = _values

# ── Custom prompt ──────────────────────────────────────────────────────────────
DEFAULT_SYSTEM_PROMPT: str = _s["prompt"]["default_system_prompt"]
PROMPT_TEMPERATURE: float = _s["prompt"]["temperature"]
PROMPT_TOP_P: float = _s["prompt"]["top_p"]
PROMPT_MAX_TOKENS: int = _s["prompt"]["max_tokens"]

# ── Retry / rate limiting ──────────────────────────────────────────────────────
PAGE_DELAY_SECONDS: float = _s["retry"]["page_delay_seconds"]
MAX_RETRIES: int = _s["retry"]["max_retries"]
BASE_RETRY_DELAY: float = _s["retry"]["base_retry_delay"]

# ── Parallelism & document processing ─────────────────────────────────────────
DEFAULT_PARALLEL_WORKERS: int = _s["processing"]["default_parallel_workers"]
DEFAULT_OCR_PASSES: int = _s["processing"]["default_ocr_passes"]
DEFAULT_PAGE_SIZE: int = _s["processing"]["default_page_size"]
MAX_PARALLEL_WORKERS: int = _s["processing"]["max_parallel_workers"]

# ── Transcription review (plugin) ─────────────────────────────────────────────
# TRANSCRIPTION_REVIEW_* constants are provided by the transcription plugin.
# Access via src.settings.__getattr__ which searches pu_plugin.*.settings.

# ── Output formatting ─────────────────────────────────────────────────────────
DEFAULT_FONT_SIZE: int = _s["output"]["default_font_size"]

# ── Budget ─────────────────────────────────────────────────────────────────────
BUDGET_WARNING_THRESHOLD: int = _s["budget"]["warning_threshold_pct"]


def __getattr__(name: str):
    """Delegate plugin-specific settings lookups to registered plugin settings modules.

    Any plugin that registers a module as ``pu_plugin.<name>.settings`` via
    ``_register()`` will have its constants available via ``from src.settings
    import <CONSTANT>`` without any changes to this file.
    """
    for mod_name, mod in sys.modules.items():
        if (
            mod_name.startswith("pu_plugin.") and
            mod_name.endswith(".settings") and
            mod is not None and
            hasattr(mod, name)
        ):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
