"""Transcription plugin settings.

Defaults come from this plugin's own ``settings.toml``. Anyone can override any
of them without touching that file — a shared settings file, then
``preferences.toml``, apply on top, in that order. See ``plugin_settings()`` in
``src/settings.py``.
"""

from src.runtime.model_role import ModelRole
from src.settings import plugin_settings

_s = plugin_settings(__file__, "ocr", "transcription_review")
_ocr = _s["ocr"]
_transcription_review = _s["transcription_review"]

# ── OCR ───────────────────────────────────────────────────────────────────────
OCR_TEMPERATURE: float = _ocr.get("temperature", 0.0)
OCR_TOP_P: float = _ocr.get("top_p", 0.1)
OCR_MAX_TOKENS: int = _ocr.get("max_tokens", 4000)
OCR_FREQUENCY_PENALTY: float = _ocr.get("frequency_penalty", 0.5)
OCR_PRESENCE_PENALTY: float = _ocr.get("presence_penalty", 0.3)
# Exported as DEFAULT_OCR_PASSES (not OCR_DEFAULT_PASSES) because
# src/settings.py's __getattr__ delegation looks up this exact attribute name,
# and plugins/transcription-ea/plugin.py imports it as `from src.settings
# import DEFAULT_OCR_PASSES`.
DEFAULT_OCR_PASSES: int = _ocr.get("default_ocr_passes", 1)

# ── Transcription review ──────────────────────────────────────────────────────
TRANSCRIPTION_REVIEW_TEMPERATURE: float = _transcription_review.get("temperature", 0.1)
TRANSCRIPTION_REVIEW_TOP_P: float = _transcription_review.get("top_p", 0.5)
TRANSCRIPTION_REVIEW_MAX_TOKENS: int = _transcription_review.get("max_tokens", 4000)

# ── Which models each job should use ──────────────────────────────────────────
OCR_ROLE = ModelRole(
    models=_ocr.get("models", ["gpt-4o", "gemini-2.5-flash", "gpt-4o-mini"]),
    requires_vision=True,   # it is reading an image
)
TRANSCRIPTION_REVIEW_ROLE = ModelRole(
    models=_transcription_review.get("models", ["gpt-4o", "gpt-4o-mini"]),
)
