"""
Shared pytest fixtures for the PU AI Sandbox test suite.

All services are instantiated with a fake API key and a mocked TokenTracker so
no real network calls or file I/O are triggered during tests.
"""

import importlib.util
import sys
from pathlib import Path
import pytest

import src.models.catalog as _catalog_module

_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "model_catalog.template.json"
_REPO_ROOT = Path(__file__).parent.parent


def _register(module_name: str, rel_path: str, plugin_dir: Path) -> None:
    """Inject a plugin-owned module into sys.modules under its src.* name.

    Mirrors the _register() helper already used by plugins/translation/plugin.py
    and plugins/translation/conftest.py. Needed here because pytest.ini's
    testpaths collects tests/ before plugins/translation/tests or
    plugins/transcription/tests — without this, tests/test_sandbox_processor.py
    would import SandboxProcessor before any plugin's runtime mixin (e.g.
    document_handler.py, image_handler.py) is registered.
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
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    # Expose the module as an attribute on its parent package so that
    # pytest's string-based monkeypatch.setattr("src.runtime.<name>.*")
    # resolution (which walks package attributes, not sys.modules) works.
    # Falls back to importlib.import_module because src.runtime may not
    # have been imported yet at this point — its own relative imports (e.g.
    # "from ..errors import CLIError") only pull in "src" and "src.errors",
    # never "src.runtime" itself as a side effect.
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


# Registered at collection time (module scope, not inside a fixture) so that
# SandboxProcessor composes its plugin-provided mixins correctly the first
# time it's imported by any test module in this directory. docx_translation
# must be registered before document_handler, which imports it directly.
_register("src.processors.docx_translation", "src/processors/docx_translation.py", _REPO_ROOT / "plugins/translation")
_register("src.runtime.document_handler", "src/runtime/document_handler.py", _REPO_ROOT / "plugins/translation")
_register("src.runtime.image_handler", "src/runtime/image_handler.py", _REPO_ROOT / "plugins/transcription")


@pytest.fixture(autouse=True)
def _use_template_catalog(monkeypatch):
    """Redirect get_model_catalog_path to the template file for all tests.

    This allows tests to run in CI where src/model_catalog.json is git-ignored.
    Tests that need a specific catalog (tmp_path or SAMPLE_CATALOG) override
    get_model_catalog_path or load_model_catalog themselves — the last
    monkeypatch.setattr call wins within the same test.
    """
    monkeypatch.setattr(_catalog_module, "get_model_catalog_path", lambda: _TEMPLATE_PATH)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make time.sleep do nothing, so retry-backoff tests don't actually wait.

    Uses monkeypatch rather than ``mock.patch`` deliberately. This replaces an
    attribute on the real ``time`` module, and several tests replace the very
    same attribute themselves (``src.services.base_service`` does a plain
    ``import time``, so ``base_service.time`` *is* the ``time`` module).
    Two different undo mechanisms unwinding the same attribute only restore it
    correctly if they happen to finish in the right order — and which order
    that is depends on when pytest first had to build the ``monkeypatch``
    fixture, which changes the moment anyone adds a fixture above this one.
    Get it wrong and ``time.sleep`` stays stubbed for the rest of the session,
    so every later test that waits for a background thread sees its polling
    loop spin instantly and fail for reasons having nothing to do with it.
    Going through monkeypatch for both puts every change on one undo stack.
    """
    monkeypatch.setattr("time.sleep", lambda _: None)


@pytest.fixture(autouse=True)
def _register_base_languages():
    """Populate LANGUAGE_MAP with the four built-in codes for every test.

    In production, plugins call register_language() at import time.  The test
    suite doesn't load plugins, so we seed the registry here to keep tests that
    exercise parse_language_code / parse_single_language_code working correctly.
    """
    from src.config import register_language
    register_language('en', 'English')
    register_language('zh', 'Chinese')
    register_language('jp', 'Japanese')
    register_language('kr', 'Korean')



