"""Pytest set-up for the prompt plugin's tests: files its modules under their ``src.*`` names (see tests/plugin_modules.py)."""

from functools import partial
from pathlib import Path

from tests.plugin_modules import register

_register = partial(register, Path(__file__).resolve().parent.parent)

# Same order plugin.py uses: the settings module first, so the service can
# import PROMPT_ROLE from it through src.settings.
_register("pu_plugin.prompt.settings", "src/settings.py")
_register("src.services.prompt_service", "src/services/prompt_service.py")

