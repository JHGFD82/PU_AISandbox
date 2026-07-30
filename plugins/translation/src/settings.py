"""Translation plugin settings.

Defaults come from this plugin's own ``settings.toml``. Anyone can override any
of them without touching that file — a shared settings file, then
``preferences.toml``, apply on top, in that order. See ``plugin_settings()`` in
``src/settings.py``.

The model lists are the exception to "has a default": there is no fallback for
them in this file, because a list of models is what gets edited when a provider
retires something, and a second copy here would drift out of step silently. If
``settings.toml`` doesn't name them, loading fails and says so.
"""

from src.runtime.model_role import ModelRole
from src.settings import plugin_settings, required_models

_s = plugin_settings(__file__, "translation", "image_translation")
_translation = _s["translation"]
_image_translation = _s["image_translation"]

# ── Translation ────────────────────────────────────────────────────────────────
TRANSLATION_TEMPERATURE: float = _translation.get("temperature", 0.5)
TRANSLATION_TOP_P: float = _translation.get("top_p", 0.5)
TRANSLATION_MAX_TOKENS: int = _translation.get("max_tokens", 4000)
CONTEXT_PERCENTAGE: float = _translation.get("context_percentage", 0.65)

# ── Image translation ──────────────────────────────────────────────────────────
IMAGE_TRANSLATION_TEMPERATURE: float = _image_translation.get("temperature", 0.3)
IMAGE_TRANSLATION_MAX_TOKENS: int = _image_translation.get("max_tokens", 8000)

# ── Which models each job should use ──────────────────────────────────────────
# Best first. More than one on purpose: providers retire models, and a second
# and third choice means the work carries on instead of stopping until someone
# picks a new default.
TRANSLATION_ROLE = ModelRole(
    models=required_models(
        _translation, "models",
        where="[translation] in plugins/translation/settings.toml",
    ),
)
IMAGE_TRANSLATION_ROLE = ModelRole(
    models=required_models(
        _image_translation, "models",
        where="[image_translation] in plugins/translation/settings.toml",
    ),
    requires_vision=True,   # it is reading a scan
)
