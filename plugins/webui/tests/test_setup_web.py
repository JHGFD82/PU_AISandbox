"""Tests for first-time setup in a browser (plugins/webui/src/setup_web.py).

The same questions the command line asks, asked as a form. What matters is
that both routes end up in the same place — ``src/first_run.py`` — so
neither can drift from the other about what an existing setup is, or about
what must never be overwritten.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import paths

setup_web = sys.modules["_pu_webui_setup_web"]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    package = tmp_path / "package"
    templates = package / "templates"
    templates.mkdir(parents=True)
    (templates / "settings.template").write_text("# starting point\n", encoding="utf-8")
    (templates / "preferences.template.toml").write_text("# preferences\n", encoding="utf-8")
    (templates / "model_catalog.template.json").write_text('{"models": {}}\n', encoding="utf-8")
    monkeypatch.setattr(paths, "PACKAGE_ROOT", package)
    monkeypatch.setattr(paths, "TEMPLATES_DIR", templates)
    monkeypatch.setattr(paths, "INSTALL_MARKER", package / ".installation")
    monkeypatch.setattr(paths, "DEFAULT_EXTRAS_ROOT", tmp_path / "PU_AISandbox_data")
    return package


@pytest.fixture
def client_and_result():
    """A client for the setup page, plus whatever folder it settles on."""
    chosen: list[Path] = []
    app = setup_web.create_setup_app(lambda folder: chosen.append(folder))
    return TestClient(app), chosen


def _make_setup(root: Path, *, people: int = 1):
    root.mkdir(parents=True, exist_ok=True)
    tables = "\n".join(
        f'[professors.p{i}]\nname = "Person {i}"\nkey = "sk-{i}"\n' for i in range(people)
    )
    (root / paths.SETTINGS_FILENAME).write_text(tables, encoding="utf-8")
    (root / paths.DATA_DIRNAME).mkdir(exist_ok=True)
    return root


class TestAskingWhereFilesGo:
    def test_offers_the_default_when_nothing_exists(self, client_and_result):
        client, _ = client_and_result
        page = client.get("/").text
        assert "Where should your files be kept" in page
        assert str(paths.DEFAULT_EXTRAS_ROOT) in page

    def test_creating_the_folder_sets_the_sandbox_up(self, client_and_result):
        client, chosen = client_and_result
        target = paths.DEFAULT_EXTRAS_ROOT
        resp = client.post("/", data={"folder": str(target)})
        assert resp.status_code == 200
        assert "All set" in resp.text
        assert (target / paths.SETTINGS_FILENAME).is_file()
        assert (target / paths.DATA_DIRNAME).is_dir()
        assert paths.is_installed() is True

    def test_the_new_settings_file_is_owner_only(self, client_and_result):
        """It holds API keys from the moment it exists, browser route or not."""
        client, _ = client_and_result
        client.post("/", data={"folder": str(paths.DEFAULT_EXTRAS_ROOT)})
        mode = (paths.DEFAULT_EXTRAS_ROOT / paths.SETTINGS_FILENAME).stat().st_mode & 0o777
        assert mode == 0o600

    def test_it_reports_back_which_folder_was_chosen(self, client_and_result):
        client, chosen = client_and_result
        client.post("/", data={"folder": str(paths.DEFAULT_EXTRAS_ROOT)})
        # The callback is fired on a short timer so the page can finish
        # rendering first; wait for it rather than racing it.
        import time
        for _ in range(40):
            if chosen:
                break
            time.sleep(0.05)
        assert chosen == [paths.DEFAULT_EXTRAS_ROOT]


class TestCarryingForwardAnExistingSetup:
    def test_an_existing_folder_is_offered_rather_than_a_blank_field(self, client_and_result):
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=3)
        client, _ = client_and_result
        page = client.get("/").text
        assert "already here" in page
        assert "3 people configured" in page
        assert "Use these files" in page

    def test_using_them_changes_nothing_on_disk(self, client_and_result):
        root = _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=2)
        before = (root / paths.SETTINGS_FILENAME).read_bytes()
        client, _ = client_and_result
        client.post("/", data={"folder": str(root)})
        assert (root / paths.SETTINGS_FILENAME).read_bytes() == before
        assert paths.is_installed() is True

    def test_pointing_at_an_existing_setup_never_overwrites_it(self, client_and_result, tmp_path):
        """The refusal that matters, reached through the browser this time."""
        elsewhere = _make_setup(tmp_path / "somewhere else", people=5)
        before = (elsewhere / paths.SETTINGS_FILENAME).read_bytes()
        client, _ = client_and_result
        resp = client.post("/", data={"folder": str(elsewhere)})
        assert resp.status_code == 200
        assert (elsewhere / paths.SETTINGS_FILENAME).read_bytes() == before


class TestRejectingBadAnswers:
    def test_a_relative_path_is_refused_with_a_reason(self, client_and_result):
        """A browser gives no working directory to resolve against."""
        client, _ = client_and_result
        resp = client.post("/", data={"folder": "my files"})
        assert "isn't a full path" in resp.text
        assert paths.is_installed() is False

    def test_a_file_where_a_folder_should_be(self, client_and_result, tmp_path):
        a_file = tmp_path / "not-a-folder"
        a_file.write_text("x", encoding="utf-8")
        client, _ = client_and_result
        resp = client.post("/", data={"folder": str(a_file)})
        assert "is a file, not a folder" in resp.text
        assert paths.is_installed() is False


class TestCloudSyncWarning:
    def test_a_synced_default_is_called_out(self, client_and_result, tmp_path, monkeypatch):
        monkeypatch.setattr(
            paths, "DEFAULT_EXTRAS_ROOT", tmp_path / "home" / "Dropbox" / "sandbox"
        )
        client, _ = client_and_result
        page = client.get("/").text
        assert "Dropbox" in page
        assert "API keys" in page
