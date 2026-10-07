"""What every plugin's tests need before they can import that plugin's own files.

A plugin keeps its services and helpers in its own folder, and its
``plugin.py`` files each one under a ``src.*`` name when the sandbox starts
(see ``docs/plugin-authoring-guide.md``). A test cannot wait for that: it
imports ``src.services.translation_service`` at the top of the file, before any
plugin has been loaded. So each plugin's ``conftest.py`` files them itself,
with ``register()`` below, as its tests are being collected.

Shared here rather than copied into every conftest, so that a fix to how it is
done reaches every plugin at once.
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def register(plugin_dir: Path, module_name: str, rel_path: str) -> None:
    """File one of a plugin's own modules under the name its code imports it by.

    Does nothing if something is already filed under that name, or if the
    file does not exist — an optional part of a plugin that is not there.

    The module is also set as an attribute of the package above it, so that a
    test can replace something in it by name, as in
    ``monkeypatch.setattr("src.services.translation_service.X", ...)``: pytest
    finds the module by walking from ``src`` down through those attributes,
    not by looking the full name up.

    Args:
        plugin_dir: The plugin's own folder, e.g. ``plugins/translation``.
        module_name: The name to file it under, e.g.
            ``"src.services.translation_service"``.
        rel_path: Where the file is, relative to *plugin_dir*.
    """
    if module_name in sys.modules:
        return
    path = plugin_dir / rel_path
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    parent_name, _, attribute = module_name.rpartition(".")
    if not parent_name:
        return
    parent = sys.modules.get(parent_name)
    if parent is None:
        # A real package such as src.runtime that nothing has imported yet.
        # A made-up parent such as pu_plugin.translation cannot be imported,
        # and has nothing to set the attribute on.
        try:
            parent = importlib.import_module(parent_name)
        except ImportError:
            return
    setattr(parent, attribute, sys.modules[module_name])


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
