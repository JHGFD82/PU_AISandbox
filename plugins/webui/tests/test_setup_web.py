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
    # A loopback client address, because that is what a real browser on this
    # computer looks like — and the "Browse…" route refuses anything else.
    return TestClient(app, client=("127.0.0.1", 50000)), chosen


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

    def test_somewhere_else_is_offered_alongside_what_was_found(self, client_and_result):
        """Files found in the usual place aren't proof they're the ones wanted.

        The command line asks "Use these? [Y/n]" and falls through to asking
        where when the answer is no. Without this the browser had only the
        yes — someone whose real files sit on an external drive, with a
        stale folder left in the usual place, was stuck.
        """
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=1)
        client, _ = client_and_result
        page = client.get("/").text
        assert "somewhere else" in page
        assert 'name="folder"' in page
        # Two answers, so two forms — one submitting the found folder, one
        # submitting whatever gets typed.
        assert page.count("<form") == 2

    def test_choosing_a_different_folder_carries_that_one_forward(
        self, client_and_result, tmp_path
    ):
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=1)
        real = _make_setup(tmp_path / "external drive", people=4)
        before = (real / paths.SETTINGS_FILENAME).read_bytes()
        client, _ = client_and_result
        resp = client.post("/", data={"folder": str(real)})
        assert resp.status_code == 200
        assert "All set" in resp.text
        assert paths.read_install_marker() == real
        assert (real / paths.SETTINGS_FILENAME).read_bytes() == before

    def test_an_empty_folder_box_is_not_read_as_the_current_directory(
        self, client_and_result
    ):
        """Submitting the alternative form untouched must not settle on anything."""
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=1)
        client, _ = client_and_result
        resp = client.post("/", data={"folder": "   "})
        assert "No folder was given" in resp.text
        assert paths.is_installed() is False


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

    def test_taking_the_suggestion_does_not_ask_twice(
        self, client_and_result, tmp_path, monkeypatch
    ):
        """The warning was beside the button; pressing it read it."""
        synced = tmp_path / "home" / "Dropbox" / "sandbox"
        monkeypatch.setattr(paths, "DEFAULT_EXTRAS_ROOT", synced)
        client, _ = client_and_result
        resp = client.post("/", data={"folder": str(synced), "acknowledged": str(synced)})
        assert "All set" in resp.text
        assert paths.read_install_marker() == synced

    def test_a_synced_folder_someone_chose_themselves_is_queried(
        self, client_and_result, tmp_path
    ):
        """Nothing warned about this one — it was typed, or reached via Browse."""
        synced = tmp_path / "home" / "Dropbox" / "my documents"
        client, _ = client_and_result
        resp = client.post("/", data={"folder": str(synced)})
        assert resp.status_code == 200
        assert "Dropbox" in resp.text
        assert "Use it anyway" in resp.text
        # Nothing has happened yet — this is a question, not a refusal.
        assert paths.is_installed() is False
        assert not synced.exists()

    def test_saying_use_it_anyway_goes_ahead(self, client_and_result, tmp_path):
        synced = tmp_path / "home" / "Dropbox" / "my documents"
        client, _ = client_and_result
        client.post("/", data={"folder": str(synced)})
        resp = client.post(
            "/", data={"folder": str(synced), "acknowledged": str(synced)}
        )
        assert "All set" in resp.text
        assert paths.read_install_marker() == synced
        assert (synced / paths.SETTINGS_FILENAME).is_file()

    def test_an_acknowledgement_for_a_different_folder_does_not_count(
        self, client_and_result, tmp_path
    ):
        """The hidden field names the folder it was shown for, so it can't be reused."""
        synced = tmp_path / "home" / "Dropbox" / "my documents"
        client, _ = client_and_result
        resp = client.post(
            "/", data={"folder": str(synced), "acknowledged": str(tmp_path / "elsewhere")}
        )
        assert "Use it anyway" in resp.text
        assert paths.is_installed() is False

    def test_an_ordinary_folder_is_never_queried(self, client_and_result, tmp_path):
        plain = tmp_path / "somewhere ordinary"
        client, _ = client_and_result
        resp = client.post("/", data={"folder": str(plain)})
        assert "All set" in resp.text
        assert paths.read_install_marker() == plain


class TestBrowseButton:
    """The button that opens this computer's own folder chooser.

    A browser can't tell a page where a folder is — that is a protection,
    not an oversight. It works here only because the server is running on
    the same computer as the browser, so the chooser can be opened there.
    Nothing below opens a real window; the chooser itself is stood in for.
    """

    def test_the_button_is_offered_when_there_is_a_chooser(self, client_and_result, monkeypatch):
        monkeypatch.setattr(setup_web.file_picker, "available", lambda: True)
        client, _ = client_and_result
        page = client.get("/").text
        assert 'id="browse-btn"' in page
        assert "/pick" in page

    def test_no_button_when_this_computer_has_no_chooser(self, client_and_result, monkeypatch):
        # Better a box you can type in than a button that does nothing.
        monkeypatch.setattr(setup_web.file_picker, "available", lambda: False)
        client, _ = client_and_result
        page = client.get("/").text
        assert 'id="browse-btn"' not in page
        assert 'id="folder"' in page

    def test_choosing_a_folder_hands_back_its_real_path(self, client_and_result, monkeypatch, tmp_path):
        monkeypatch.setattr(setup_web.file_picker, "choose", lambda **kw: tmp_path / "chosen")
        client, _ = client_and_result
        resp = client.post("/pick", json={"start": str(tmp_path)})
        assert resp.status_code == 200
        assert resp.json()["path"] == str(tmp_path / "chosen")

    def test_closing_the_window_leaves_the_box_alone(self, client_and_result, monkeypatch):
        monkeypatch.setattr(setup_web.file_picker, "choose", lambda **kw: None)
        client, _ = client_and_result
        resp = client.post("/pick", json={"start": None})
        assert resp.status_code == 200
        assert resp.json()["path"] is None

    def test_no_chooser_is_explained_rather_than_crashing(self, client_and_result, monkeypatch):
        def unavailable(**kw):
            raise setup_web.file_picker.PickerUnavailable("no chooser here")

        monkeypatch.setattr(setup_web.file_picker, "choose", unavailable)
        client, _ = client_and_result
        resp = client.post("/pick", json={"start": None})
        assert resp.status_code == 503
        assert "no chooser" in resp.json()["error"]

    def test_a_browser_on_another_computer_is_refused(self, monkeypatch):
        # The window would open on the screen of whoever runs the sandbox,
        # and hand back a folder from a disk the clicker has never seen.
        opened = []
        monkeypatch.setattr(setup_web.file_picker, "choose", lambda **kw: opened.append(kw))
        app = setup_web.create_setup_app(lambda folder: None)
        elsewhere = TestClient(app, client=("10.0.0.5", 50000))
        resp = elsewhere.post("/pick", json={"start": None})
        assert resp.status_code == 403
        assert opened == []
