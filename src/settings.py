"""Load user-customizable settings from settings.toml at the repository root.

All tuneable runtime defaults live in that file. This module reads it once at
import time and exposes each value as a typed module-level constant so other
modules can import them directly.

Settings are merged from up to three layers, in this order (later layers
override earlier ones, key by key — a layer only needs to mention the
settings it wants to change):

1. ``settings.toml`` — the repo's checked-in defaults, same for everyone.
2. A lab-shared file, if ``PU_SANDBOX_LAB_SETTINGS`` is set (an environment
   variable pointing at a ``.toml`` file, e.g. one synced with Dropbox
   between everyone in a lab). Lets a group share defaults — a shared
   cluster's worker count, a lab-wide font size — without anyone needing to
   hand-edit their own copy. Absent by default; nothing changes unless this
   variable is set.
3. ``settings.local.toml`` — this machine's personal overrides, git-ignored.
   Still the last word: even with a lab-shared file in play, a setting
   placed here wins, so one person can override just their own quirk
   without touching the file everyone else reads.
"""

import os
import sys
from pathlib import Path

import tomllib

from .config import register_env_field

_ROOT = Path(__file__).parent.parent  # src/ -> repo root
_TOML_PATH = _ROOT / "settings.toml"
_LOCAL_TOML_PATH = _ROOT / "settings.local.toml"
_LAB_SETTINGS_ENV_VAR = "PU_SANDBOX_LAB_SETTINGS"

register_env_field(
    _LAB_SETTINGS_ENV_VAR,
    "Path to a lab-shared settings.toml, merged between settings.toml and settings.local.toml",
    section="Core",
    secret=False,
)


def _merge_layer(base: dict, layer_path: Path) -> None:
    """Merge one TOML file's sections into *base* in place.

    Only the keys present in *layer_path* are overridden — a layer that
    doesn't mention a section, or a key within one, leaves whatever's
    already in *base* for that key untouched.
    """
    with layer_path.open("rb") as _f:
        layer = tomllib.load(_f)
    for section, values in layer.items():
        if section in base and isinstance(base[section], dict) and isinstance(values, dict):
            base[section].update(values)
        else:
            base[section] = values


try:
    with _TOML_PATH.open("rb") as _f:
        _s = tomllib.load(_f)
except FileNotFoundError:
    raise FileNotFoundError(
        f"settings.toml not found at {_TOML_PATH}. "
        "Copy settings.toml from the repository root and edit it to configure the sandbox."
    )

# Layer 2: lab-shared settings, only if PU_SANDBOX_LAB_SETTINGS points at a real file.
_lab_settings_path = os.environ.get(_LAB_SETTINGS_ENV_VAR)
if _lab_settings_path:
    _resolved_lab_path = Path(_lab_settings_path).expanduser()
    if _resolved_lab_path.exists():
        _merge_layer(_s, _resolved_lab_path)
    else:
        import logging
        logging.getLogger(__name__).warning(
            f"{_LAB_SETTINGS_ENV_VAR} is set to '{_lab_settings_path}', but no file exists "
            "there — lab-shared settings were not applied."
        )

# Layer 3: settings.local.toml — this machine's personal overrides, still the last word.
if _LOCAL_TOML_PATH.exists():
    _merge_layer(_s, _LOCAL_TOML_PATH)

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
DEFAULT_PAGE_SIZE: int = _s["processing"]["default_page_size"]
MAX_PARALLEL_WORKERS: int = _s["processing"]["max_parallel_workers"]

# ── Transcription (plugin) ────────────────────────────────────────────────────
# DEFAULT_OCR_PASSES and TRANSCRIPTION_REVIEW_* constants are provided by the
# transcription plugin. Access via src.settings.__getattr__ below, which
# searches pu_plugin.*.settings.

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
