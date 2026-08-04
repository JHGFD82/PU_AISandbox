"""Fixtures that apply to every test in the project, wherever it lives.

This file sits at the repository root rather than in ``tests/`` so that the
plugin suites listed in ``pytest.ini``'s ``testpaths`` get it too — pytest
only applies a ``conftest.py`` to the directory it sits in and below, and
``plugins/*/tests/`` is not below ``tests/``.
"""

from pathlib import Path

import pytest

import src.models.catalog as _catalog_module
from src import paths

# The tests' own catalogue, not the one the product ships. Those are two
# different decisions: a new installation starts with no models, so that nobody
# is handed names their institution may not offer, while the tests need a
# couple of known models at known prices. Sharing one file meant emptying the
# shipped one broke forty-three tests that had nothing to do with it.
_FIXTURE_CATALOG = Path(__file__).parent / "tests" / "fixtures" / "model_catalog.json"


@pytest.fixture(autouse=True)
def _use_fixture_catalog(monkeypatch):
    """Point every test at the fixture catalogue, wherever the test lives.

    This one is at the repository root rather than in ``tests/`` for the reason
    given above: the plugin suites are not below ``tests/``, and they need a
    catalogue as much as anything else does.

    A test needing its own catalogue replaces this again in its own fixture;
    the later monkeypatch wins.
    """
    monkeypatch.setattr(_catalog_module, "get_model_catalog_path", lambda: _FIXTURE_CATALOG)


@pytest.fixture(autouse=True)
def _isolated_extras_folder(tmp_path_factory, monkeypatch):
    """Give every test its own empty folder to treat as "this person's files".

    The sandbox keeps settings, the model catalogue and usage history in a
    folder chosen during setup, and finds it through a small marker file
    inside the package (``src/paths.py``). That marker is deliberately not
    committed — it describes one installation and must never travel to
    another — so a fresh checkout has no marker at all, and anything asking
    where the files are raises ``NotSetUpError``.

    Without this fixture the test suite passes only on a machine that has
    already run setup, and it passes there by reading that person's real
    folder. On a continuous-integration runner, which has never run setup,
    the same tests fail before they begin.

    Pointing the marker at a throwaway folder fixes both halves: every test
    starts from an empty, predictable place, and no test can see or touch
    anyone's real settings or usage history.

    A test that needs a different answer — the setup flow itself, which has
    to see what an un-set-up package looks like — replaces ``INSTALL_MARKER``
    again in its own fixture. That runs after this one, so it wins.

    Note:
        The throwaway folders come from ``tmp_path_factory`` rather than the
        usual ``tmp_path``, because ``tmp_path`` belongs to the test itself
        and at least one test asserts that it is still empty at the end.
    """
    package = tmp_path_factory.mktemp("package")
    extras = tmp_path_factory.mktemp("extras")

    marker = package / ".installation"
    marker.write_text(f"{extras}\n", encoding="utf-8")
    monkeypatch.setattr(paths, "INSTALL_MARKER", marker)
