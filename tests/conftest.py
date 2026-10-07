"""Pytest set-up for the core tests in tests/.

What applies to every test, plugin tests included, is in the conftest.py at
the repository root; this adds what the core tests need on top.
"""

from pathlib import Path

import pytest

from tests.plugin_modules import register

_PLUGINS = Path(__file__).parent.parent / "plugins"

# The plugins' runtime mixins, filed under their src.* names when these tests
# are collected rather than inside a fixture. pytest.ini's testpaths collects
# tests/ before any plugin's own tests, so without this
# tests/test_sandbox_processor.py would import SandboxProcessor before any
# plugin had added its mixin (document_handler.py, image_handler.py), and the
# class would be put together without them. docx_translation must come before
# document_handler, which imports it directly.
register(_PLUGINS / "translation", "src.processors.docx_translation", "src/processors/docx_translation.py")
register(_PLUGINS / "translation", "src.runtime.document_handler", "src/runtime/document_handler.py")
register(_PLUGINS / "transcription", "src.runtime.image_handler", "src/runtime/image_handler.py")


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



