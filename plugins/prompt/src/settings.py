"""Prompt plugin settings.

Defaults come from this plugin's own ``settings.toml``. Anyone can override any
of them without touching that file — a shared settings file, then
``preferences.toml``, apply on top, in that order. See ``plugin_settings()`` in
``src/settings.py``.
"""

from src.runtime.model_role import ModelRole
from src.settings import plugin_settings

_prompt = plugin_settings(__file__, "prompt_command")["prompt_command"]

# Which models the `prompt` command should use, in order of preference. Named
# here rather than left to the sandbox: without a preference, resolution falls
# straight through to the cheapest model in the catalog, which for a freeform
# question is not what anyone means.
PROMPT_ROLE = ModelRole(
    models=_prompt.get(
        "models", ["gpt-4o-mini", "gpt-4.1-nano", "gemini-2.5-flash-lite", "gpt-4o"]
    ),
)
