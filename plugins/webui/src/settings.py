"""webui plugin settings.

Defaults come from this plugin's own ``settings.toml``
(``plugins/webui/settings.toml``). Anyone can override any of them without
touching that file — a shared settings file, then ``preferences.toml``, apply
on top under a ``[webui]`` section, in that order. See ``plugin_settings()`` in
``src/settings.py``.
"""

from src.runtime.model_role import ModelRole
from src.settings import plugin_settings

_webui = plugin_settings(__file__, "webui")["webui"]

WEBUI_HOST: str = _webui.get("host", "127.0.0.1")
WEBUI_PORT: int = _webui.get("port", 8000)
WEBUI_SESSION_COOKIE_NAME: str = _webui.get("session_cookie_name", "pu_sandbox_session")
WEBUI_COMPACTION_THRESHOLD: float = _webui.get("compaction_threshold", 0.70)
WEBUI_COMPACTION_MODEL: str = _webui.get("compaction_model", "gpt-4o-mini")

# ── Which models each job should use ──────────────────────────────────────────
# Chat needs to read images because a question in the browser can carry a
# document; writing an eight-word title does not.
CHAT_ROLE = ModelRole(
    models=_webui.get("chat_models", ["gpt-4o-mini", "gemini-2.5-flash-lite", "gpt-4o"]),
    requires_vision=True,
)
TITLE_ROLE = ModelRole(
    models=_webui.get("title_models", ["gpt-4o-mini", "gpt-4.1-nano", "gemini-2.5-flash-lite"]),
)
