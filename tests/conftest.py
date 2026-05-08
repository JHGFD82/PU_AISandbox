"""
Shared pytest fixtures for the PU AI Sandbox test suite.

All services are instantiated with a fake API key and a mocked TokenTracker so
no real network calls or file I/O are triggered during tests.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.models.catalog as _catalog_module

_TEMPLATE_PATH = Path(__file__).parent.parent / "src" / "model_catalog.template.json"


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
def _no_sleep():
    """Patch time.sleep globally so retry-backoff tests don't actually wait."""
    with patch("time.sleep"):
        yield


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



