"""Load user-customizable settings from settings.toml at the repository root.

All tuneable runtime defaults live in that file. This module reads it once at
import time and exposes each value as a typed module-level constant so other
modules can import them directly.
"""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parent.parent  # src/ -> repo root
_TOML_PATH = _ROOT / "settings.toml"

try:
    with _TOML_PATH.open("rb") as _f:
        _s = tomllib.load(_f)
except FileNotFoundError:
    raise FileNotFoundError(
        f"settings.toml not found at {_TOML_PATH}. "
        "Copy settings.toml from the repository root and edit it to configure the sandbox."
    )

# ── Translation ────────────────────────────────────────────────────────────────
TRANSLATION_TEMPERATURE: float = _s["translation"]["temperature"]
TRANSLATION_TOP_P: float = _s["translation"]["top_p"]
TRANSLATION_MAX_TOKENS: int = _s["translation"]["max_tokens"]
CONTEXT_PERCENTAGE: float = _s["translation"]["context_percentage"]

# ── OCR ────────────────────────────────────────────────────────────────────────
OCR_TEMPERATURE: float = _s["ocr"]["temperature"]
OCR_TOP_P: float = _s["ocr"]["top_p"]
OCR_MAX_TOKENS: int = _s["ocr"]["max_tokens"]
OCR_FREQUENCY_PENALTY: float = _s["ocr"]["frequency_penalty"]
OCR_PRESENCE_PENALTY: float = _s["ocr"]["presence_penalty"]

# ── Image translation ──────────────────────────────────────────────────────────
IMAGE_TRANSLATION_TEMPERATURE: float = _s["image_translation"]["temperature"]
IMAGE_TRANSLATION_MAX_TOKENS: int = _s["image_translation"]["max_tokens"]

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

# ── Transcription review ───────────────────────────────────────────────────────
TRANSCRIPTION_REVIEW_TEMPERATURE: float = _s["transcription_review"]["temperature"]
TRANSCRIPTION_REVIEW_TOP_P: float = _s["transcription_review"]["top_p"]
TRANSCRIPTION_REVIEW_MAX_TOKENS: int = _s["transcription_review"]["max_tokens"]

# ── Output formatting ─────────────────────────────────────────────────────────
DEFAULT_FONT_SIZE: int = _s["output"]["default_font_size"]

# ── Budget ─────────────────────────────────────────────────────────────────────
BUDGET_WARNING_THRESHOLD: int = _s["budget"]["warning_threshold_pct"]
