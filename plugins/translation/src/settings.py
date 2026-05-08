"""Translation plugin settings — loaded from the nearest settings.toml containing plugin sections."""

import tomllib
from pathlib import Path

_PLUGIN_SECTIONS = ("translation", "image_translation")


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
_translation = _s.get("translation", {})
_image_translation = _s.get("image_translation", {})

# ── Translation ────────────────────────────────────────────────────────────────
TRANSLATION_TEMPERATURE: float = _translation.get("temperature", 0.5)
TRANSLATION_TOP_P: float = _translation.get("top_p", 0.5)
TRANSLATION_MAX_TOKENS: int = _translation.get("max_tokens", 4000)
CONTEXT_PERCENTAGE: float = _translation.get("context_percentage", 0.65)

# ── Image translation ──────────────────────────────────────────────────────────
IMAGE_TRANSLATION_TEMPERATURE: float = _image_translation.get("temperature", 0.3)
IMAGE_TRANSLATION_MAX_TOKENS: int = _image_translation.get("max_tokens", 8000)
