"""webui plugin settings — loaded from the nearest settings.toml containing a [webui] section.

Follows the same walk-up pattern as plugins/translation/src/settings.py:
starting from this file's own folder, walk up the directory tree until a
settings.toml is found that has a [webui] section, and read defaults from
there. This lets the plugin ship its own settings.toml (plugins/webui/settings.toml)
without needing any change to the repository-root settings.toml, while still
letting a person override individual values in the repo root's
settings.local.toml under a [webui] section, exactly like every other
plugin's settings already work.
"""

import tomllib
from pathlib import Path

_PLUGIN_SECTIONS = ("webui",)


def _load_settings() -> dict:
    """Walk up from this file to find the nearest settings.toml with a [webui] section."""
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
_webui = _s.get("webui", {})

WEBUI_HOST: str = _webui.get("host", "127.0.0.1")
WEBUI_PORT: int = _webui.get("port", 8000)
WEBUI_SESSION_COOKIE_NAME: str = _webui.get("session_cookie_name", "pu_sandbox_session")
WEBUI_COMPACTION_THRESHOLD: float = _webui.get("compaction_threshold", 0.70)
WEBUI_COMPACTION_MODEL: str = _webui.get("compaction_model", "gpt-4o-mini")
