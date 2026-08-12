"""Prompt plugin settings.

Defaults come from this plugin's own ``settings.toml``. Anyone can override any
of them without touching that file — a shared settings file, then
``preferences.toml``, apply on top, in that order. See ``plugin_settings()`` in
``src/settings.py``.

The model lists are the exception to "has a default": there is no fallback for
them in this file, because a list of models is what needs changing when a
provider retires something, and a second copy here would drift out of step
silently. A missing list is a fault in the plugin, reported as such.

Nobody has to open this file, or the plugin's ``settings.toml``, to change any
of it: every setting below is listed in the person's own ``preferences.toml``
for them, commented out, ready to uncomment — see
``src/plugin_preferences.py``.
"""

from src.runtime.model_role import ModelRole
from src.settings import plugin_settings, required_models

_prompt = plugin_settings(__file__, "prompt_command")["prompt_command"]

# Which models the `prompt` command should use, in order of preference. Named at
# all — rather than left to the sandbox — because without a preference,
# resolution falls straight through to the cheapest model in the catalog,
# which for a freeform question is not what anyone means.
PROMPT_ROLE = ModelRole(
    models=required_models(
        _prompt, "models", where="[prompt_command] in plugins/prompt/settings.toml",
    ),
)
