"""
Pytest configuration for the translation base plugin.

Ensures the PU_AISandbox repo root is on sys.path so that src.* imports
(src.cli, src.runtime.plugin_loader, etc.) resolve when pytest is run from
this plugin's directory (plugins/translation/).

Also pre-registers translation plugin service modules into sys.modules so
tests can import them via their src.services.* names.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# This file lives at plugins/translation/conftest.py.
# parents[0] = plugins/translation/
# parents[1] = plugins/
# parents[2] = PU_AISandbox/  ← main repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.models.catalog as _catalog_module  # noqa: E402 — must follow sys.path setup

_TEMPLATE_PATH = _REPO_ROOT / "src" / "model_catalog.template.json"

_PLUGIN_DIR = Path(__file__).resolve().parent


def _register(module_name: str, rel_path: str) -> None:
    """Inject a plugin-owned module into sys.modules under its src.* name."""
    if module_name in sys.modules:
        return
    path = _PLUGIN_DIR / rel_path
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    # Make the module accessible as an attribute on its parent package so that
    # pytest monkeypatch.setattr("src.services.<name>.*") works. Falls back to
    # importlib.import_module when the parent (a real package, e.g.
    # src.runtime) hasn't been imported yet; synthetic parents like
    # pu_plugin.translation aren't real importable packages, so ImportError
    # there just means "no attribute to expose" — safe to skip.
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is None:
            try:
                parent = importlib.import_module(parts[0])
            except ImportError:
                parent = None
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])


# Register in dependency order: settings → fragments → specs → services.
_register("pu_plugin.translation.settings", "src/settings.py")
_register(
    "src.services.prompts.translation_fragments",
    "src/services/prompts/translation_fragments.py",
)
_register(
    "src.services.prompts.translation",
    "src/services/prompts/translation.py",
)
_register(
    "src.services.prompts.image_translation",
    "src/services/prompts/image_translation.py",
)
_register(
    "src.services.translation_service",
    "src/services/translation_service.py",
)
_register(
    "src.services.image_translation_service",
    "src/services/image_translation_service.py",
)
_register(
    "src.processors.docx_translation",
    "src/processors/docx_translation.py",
)
_register(
    "src.runtime.document_handler",
    "src/runtime/document_handler.py",
)


@pytest.fixture(autouse=True)
def _use_template_catalog(monkeypatch):
    """Redirect get_model_catalog_path to the template file for all tests.

    This allows tests to run in CI where src/model_catalog.json is git-ignored.
    """
    monkeypatch.setattr(_catalog_module, "get_model_catalog_path", lambda: _TEMPLATE_PATH)


@pytest.fixture(autouse=True)
def _mock_token_tracker(monkeypatch):
    """Prevent real TokenTracker instances from writing to data/ during tests.

    All service constructors that receive no explicit token_tracker fall back to
    ``TokenTracker(professor=..., data_file=...)`` inside BaseService.__init__.
    Patching it here keeps test runs side-effect-free without requiring every
    call site to pass a mock explicitly.
    """
    def _make_tracker(**_):
        tracker = MagicMock()
        usage = MagicMock()
        usage.total_cost = 0.0
        tracker.record_usage.return_value = usage
        return tracker

    monkeypatch.setattr("src.services.base_service.TokenTracker", _make_tracker)
