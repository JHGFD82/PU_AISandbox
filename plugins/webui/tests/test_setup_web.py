"""Tests for first-time setup in a browser (plugins/webui/src/setup_web.py).

The same questions the command line asks, asked as a form. What matters is
that both routes end up in the same place — ``src/first_run.py`` — so
neither can drift from the other about what an existing setup is, or about
what must never be overwritten.
"""

from __future__ import annotations

import json
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
    # Shaped like the real template: a catalogue is a config section and a
    # models section, and code that reads one is entitled to expect both.
    (templates / "model_catalog.template.json").write_text(
        '{"config": {"pricing_unit": 1000000}, "models": {}}\n', encoding="utf-8")
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

    def test_choosing_the_folder_does_not_end_setup(self, client_and_result):
        """It used to. The folder is the first of three things, not the last.

        Ending here handed somebody three files and nothing that worked: no key
        to bill and no model to send to. Setup carries on and asks.
        """
        import time

        client, chosen = client_and_result
        page = client.post("/", data={"folder": str(paths.DEFAULT_EXTRAS_ROOT)})
        assert page.status_code == 200
        assert "Who will be using this" in page.text
        # Long enough that the old timer would have fired by now.
        time.sleep(0.8)
        assert chosen == [], "setup ended before asking who is using this"


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


class TestAskingWhoIsUsingThisAndWhatTheyMaySendTo:
    """The second page: the two things setup cannot guess.

    An API key is a private credential, and which models exist depends on the
    institution's own AI sandbox. Neither can be shipped or invented, so both
    have to be asked for — and setup is not finished until both are answered.
    """

    def _at_step_two(self, client_and_result):
        client, chosen = client_and_result
        page = client.post("/", data={"folder": str(paths.DEFAULT_EXTRAS_ROOT)})
        assert page.status_code == 200
        return client, chosen, page.text

    def test_both_panels_start_marked_as_required(self, client_and_result):
        _client, _chosen, page = self._at_step_two(client_and_result)
        assert page.count('class="needed"') == 2
        # Colour is a reminder, not the message: it says so in words as well.
        assert page.count('class="required-flag"') == 2
        assert "Required" in page

    def test_the_border_is_told_to_fade_rather_than_snap(self, client_and_result):
        _client, _chosen, page = self._at_step_two(client_and_result)
        assert "transition: border-color" in page

    def test_a_person_can_be_added(self, client_and_result):
        client, _chosen, _page = self._at_step_two(client_and_result)
        resp = client.post("/people", json={
            "netid": "jh43", "name": "Jeff Heller", "key": "sk-test", "backup_key": "",
        })
        assert resp.status_code == 200
        assert resp.json()["netid"] == "jh43"
        assert "jh43" in resp.json()["label"]

    def test_more_than_one_person_can_be_added(self, client_and_result):
        """One is required; a shared installation may have several."""
        client, _chosen, _page = self._at_step_two(client_and_result)
        for netid in ("jh43", "tconlan"):
            resp = client.post("/people", json={
                "netid": netid, "name": netid.upper(), "key": "sk-" + netid,
            })
            assert resp.status_code == 200
        from src.config import load_professor_config
        assert set(load_professor_config()) == {"jh43", "tconlan"}

    def test_a_bad_netid_is_explained_rather_than_crashing(self, client_and_result):
        client, _chosen, _page = self._at_step_two(client_and_result)
        resp = client.post("/people", json={
            "netid": "not a netid!", "name": "Someone", "key": "sk-test",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]

    def test_a_model_needs_a_provider_in_its_name(self, client_and_result):
        client, _chosen, _page = self._at_step_two(client_and_result)
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        resp = client.post("/models", json={"provider_model": "gpt-4o", "professor": "jh43"})
        assert resp.status_code == 400
        assert "slash" in resp.json()["detail"]

    def test_finishing_is_refused_with_nobody_added(self, client_and_result):
        """Checked here, not only in the browser: a disabled button is not a rule."""
        client, chosen, _page = self._at_step_two(client_and_result)
        resp = client.post("/finish", json={})
        assert resp.status_code == 400
        assert "person" in resp.json()["detail"]
        assert chosen == []

    def test_finishing_is_refused_with_no_models(self, client_and_result, monkeypatch):
        import src.models.catalog as catalog_module

        client, chosen, _page = self._at_step_two(client_and_result)
        # The catalogue setup just made, not the suite's fixture one — this is
        # about what a brand-new installation holds.
        monkeypatch.setattr(
            catalog_module, "get_model_catalog_path",
            lambda: paths.DEFAULT_EXTRAS_ROOT / "model_catalog.json")
        monkeypatch.setattr(catalog_module, "_catalog_cache", None)
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        resp = client.post("/finish", json={})
        assert resp.status_code == 400
        assert "model" in resp.json()["detail"]
        assert chosen == []

    def test_finishing_works_once_both_are_there(self, client_and_result, monkeypatch):
        import time

        client, chosen, _page = self._at_step_two(client_and_result)
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        # The model is put in the catalogue directly: adding one through the
        # route would call a provider, and what is being tested here is the
        # ending, not the looking-up.
        import src.models.catalog as catalog_module
        catalog = paths.DEFAULT_EXTRAS_ROOT / "model_catalog.json"
        catalog.write_text(json.dumps(
            {"config": {"pricing_unit": 1000000},
             "models": {"gpt-4o": {"input": 1.0, "output": 2.0, "supports_vision": True}}}
        ))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog)
        monkeypatch.setattr(catalog_module, "_catalog_cache", None)

        resp = client.post("/finish", json={})
        assert resp.status_code == 200
        for _ in range(40):
            if chosen:
                break
            time.sleep(0.05)
        assert chosen == [paths.DEFAULT_EXTRAS_ROOT]

    def test_a_model_cannot_be_added_before_anyone_is(self, client_and_result):
        """Its few test requests are billed to somebody's key, so there has to
        be a somebody. The page disables the button; this is the same rule."""
        _client, _chosen, page = self._at_step_two(client_and_result)
        assert 'id="add-model" disabled' in page
        assert "Add someone above first" in page

    def test_the_state_sits_at_the_right_hand_edge_of_the_panel(self, client_and_result):
        """Not trailing the words. It describes the box, so it belongs on it."""
        _client, _chosen, page = self._at_step_two(client_and_result)
        rule = page.split("fieldset.needed > legend")[1].split("}}")[0]
        assert "justify-content: space-between" in rule
        assert "width: calc(100% - " in rule

    def test_the_picker_is_not_emptied_by_the_repaint(self, client_and_result):
        """The reported fault: nobody could be chosen to test a model with.

        The handler appended the new name and then repainted; the repaint saw
        the placeholder still first in the list and cleared the whole thing —
        taking the name with it. The clearing belongs before the first name
        arrives, not after.
        """
        _client, _chosen, page = self._at_step_two(client_and_result)
        paint = page.split("function paint()")[1].split("\n}")[0]
        assert "modelprof" not in paint, "the repaint must not touch the picker's contents"
        handler = page.split('document.getElementById("add-person")')[1].split("\n});")[0]
        assert "picker.replaceChildren()" in handler
        assert handler.index("replaceChildren") < handler.index("appendChild")

    def test_the_first_person_added_is_the_one_it_bills(self, client_and_result):
        """Otherwise pressing Add is answered by "choose somebody" when there
        is exactly one somebody to choose."""
        _client, _chosen, page = self._at_step_two(client_and_result)
        handler = page.split('document.getElementById("add-person")')[1].split("\n});")[0]
        assert "picker.value = added.netid" in handler
