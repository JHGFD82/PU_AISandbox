"""Transcription plugin settings — loaded from the nearest settings.toml containing plugin sections."""

import tomllib
from pathlib import Path

_PLUGIN_SECTIONS = ("ocr", "transcription_review")


def _load_settings() -> dict:
    """Walk up from this file to find the nearest settings.toml with plugin sections."""
    p = Path(__file__).resolve().parent
    while p != p.parent:
        candidate = p / "settings.toml"
        if candidate.exists():
            with candidate.open("rb") as f:
                data = tomllib.load(f)
            if any(k in data for k in _PLUGIN_SECTIONS):
                return data
        p = p.parent
    return {}


_s = _load_settings()
_ocr = _s.get("ocr", {})
_transcription_review = _s.get("transcription_review", {})

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
