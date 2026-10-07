"""What every plugin's tests need before they can import that plugin's own files.

A plugin keeps its services and helpers in its own folder, and its
``plugin.py`` files each one under a ``src.*`` name when the sandbox starts
(see ``docs/plugin-authoring-guide.md``). A test cannot wait for that: it
imports ``src.services.translation_service`` at the top of the file, before any
plugin has been loaded. So each plugin's ``conftest.py`` files them itself,
with ``register()`` below — the plugins' own ``register_plugin_module()`` — as
its tests are being collected.
"""

from unittest.mock import MagicMock

import pytest

from src.runtime.plugin import register_plugin_module


# The same function each plugin's plugin.py uses, so a test's modules are filed
# exactly as the running sandbox files them.
register = register_plugin_module


@pytest.fixture(autouse=True)
def no_real_token_tracker(monkeypatch):
    """Keep services from recording usage to real files while their tests run.

    A service given no tracker of its own makes one in ``BaseService.__init__``,
    and a real one writes to the usage files. A plugin's conftest uses this by
    importing it, which is all pytest needs to apply it to that plugin's tests.
    """
    def _make_tracker(**_):
        tracker = MagicMock()
        usage = MagicMock()
        usage.total_cost = 0.0
        tracker.record_usage.return_value = usage
        return tracker

    monkeypatch.setattr("src.services.base_service.TokenTracker", _make_tracker)
