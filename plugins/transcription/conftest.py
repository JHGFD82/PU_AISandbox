"""Pytest configuration for the transcription base plugin.

Adds the main PU_AISandbox repo root to sys.path so that ``src.*`` imports
resolve correctly when running tests from within this plugin directory.

Also pre-registers this plugin's service and runtime-mixin modules into
sys.modules (mirroring plugins/translation/conftest.py) so tests can import
them via their src.services.*/src.runtime.* names without depending on
another test file incidentally loading the real plugin.py first.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.models.catalog as _catalog_module  # noqa: E402 — must follow sys.path setup

_TEMPLATE_PATH = _REPO_ROOT / "templates" / "model_catalog.template.json"

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
    # Expose the module as an attribute on its parent package so that
    # pytest's string-based monkeypatch.setattr("src.runtime.<name>.*")
    # resolution (which walks package attributes, not sys.modules) works.
    # Falls back to importlib.import_module because src.runtime may not
    # have been imported yet at this point.
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


# Register in dependency order: settings → fragments → specs → services → runtime.
_register("pu_plugin.transcription.settings", "src/settings.py")
_register(
    "src.services.prompts.ocr_fragments",
    "src/services/prompts/ocr_fragments.py",
)
_register(
    "src.services.prompts.ocr",
    "src/services/prompts/ocr.py",
)
_register(
    "src.services.prompts.transcription_review",
    "src/services/prompts/transcription_review.py",
)
_register(
    "src.services.image_processor_service",
    "src/services/image_processor_service.py",
)
_register(
    "src.services.transcription_review_service",
    "src/services/transcription_review_service.py",
)
_register(
    "src.runtime.image_handler",
    "src/runtime/image_handler.py",
)


@pytest.fixture(autouse=True)
def _use_template_catalog(monkeypatch):
    """Redirect get_model_catalog_path to the template file for all tests.

    This lets tests run in CI, where no real model_catalog.json exists.
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
