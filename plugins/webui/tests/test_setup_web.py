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
    # Shaped like the real template: a catalog is a config section and a
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
        # Read as "it asks where, and suggests somewhere", not as one exact
        # sentence — the wording of these pages is still being worked on.
        assert "Where should" in page and "be kept" in page
        assert str(paths.DEFAULT_EXTRAS_ROOT) in page

    def test_creating_the_folder_sets_the_sandbox_up(self, client_and_result):
        client, chosen = client_and_result
        target = paths.DEFAULT_EXTRAS_ROOT
        resp = client.post("/", data={"folder": str(target)})
        assert resp.status_code == 200
        # Setup carries on to step 2 rather than ending here.
        assert "Step 2 of 3" in resp.text
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
        assert str(paths.DEFAULT_EXTRAS_ROOT) in page, "it did not offer what it found"
        assert "3 people configured" in page
        # One button that takes what was found, whatever it is called.
        assert 'name="acknowledged"' in page

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
        assert "<summary>" in page, "the second answer is not offered at all"
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
        # Setup carries on to step 2 rather than ending here.
        assert "Step 2 of 3" in resp.text
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
        # Setup carries on to step 2 rather than ending here.
        assert "Step 2 of 3" in resp.text
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
        # Setup carries on to step 2 rather than ending here.
        assert "Step 2 of 3" in resp.text
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
        # Setup carries on to step 2 rather than ending here.
        assert "Step 2 of 3" in resp.text
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

    def test_the_panel_starts_marked_as_required(self, client_and_result):
        _client, _chosen, page = self._at_step_two(client_and_result)
        assert 'class="needed"' in page
        # Colour is a reminder, not the message: it says so in words as well.
        assert 'class="required-flag"' in page
        assert "Required" in page

    def test_each_step_says_where_it_is(self, client_and_result):
        client, _chosen, page = self._at_step_two(client_and_result)
        assert "Step 2 of 3" in page
        assert 'class="progress-bar"' in page
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        assert "Step 3 of 3" in client.get("/models").text

    def test_the_button_says_what_it_leads_to(self, client_and_result):
        _client, _chosen, page = self._at_step_two(client_and_result)
        assert "Continue to step 3" in page

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
        # The catalog setup just made, not the suite's fixture one — this is
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
        # The model is put in the catalog directly: adding one through the
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

    def test_step_three_is_not_reachable_before_step_two_is_answered(
        self, client_and_result
    ):
        """A model is tested with somebody's key, so there has to be a somebody.

        Separating the questions makes this a matter of which page you are on
        rather than a control to keep disabled, which is one fewer thing that
        can be wrong.
        """
        client, _chosen, _page = self._at_step_two(client_and_result)
        sent_back = client.get("/models", follow_redirects=False)
        assert sent_back.status_code == 303
        assert sent_back.headers["location"] == "/people"

    def test_the_state_sits_at_the_right_hand_edge_of_the_panel(self, client_and_result):
        """Not trailing the words. It describes the box, so it belongs on it."""
        _client, _chosen, page = self._at_step_two(client_and_result)
        rule = page.split("fieldset.needed > legend")[1].split("}")[0]
        assert "width: calc(100% - " in rule

    def test_the_box_line_carries_on_between_the_two(self, client_and_result):
        """Otherwise they are two labels floating in a gap in the border."""
        _client, _chosen, page = self._at_step_two(client_and_result)
        assert '<span class="rule"></span>' in page
        rule = page.split("legend .rule")[1].split("}")[0]
        assert "flex: 1" in rule
        assert "border-top" in rule

    def test_the_picker_is_built_from_what_is_on_disk(self, client_and_result):
        """The reported fault, and why it cannot happen this way.

        The two questions used to share a page, so the picker had to be kept in
        step with the list beside it in the browser — and it was that keeping in
        step that emptied it of the name just added. A page asking one thing is
        rendered from the settings file each time it is asked for, so there is
        nothing to keep in step.
        """
        client, _chosen, _page = self._at_step_two(client_and_result)
        client.post("/people", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-t"})
        client.post("/people", json={"netid": "tconlan", "name": "T Conlan", "key": "sk-2"})
        page = client.get("/models").text
        assert 'value="jh43"' in page
        assert 'value="tconlan"' in page
        # Whoever comes first is what a browser selects; nothing has to say so.
        assert page.index('value="jh43"') < page.index('value="tconlan"')

    def test_adding_someone_shows_them_on_the_page(self, client_and_result):
        client, _chosen, _page = self._at_step_two(client_and_result)
        client.post("/people", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-t"})
        page = client.get("/people").text
        assert "Jeff Heller (jh43)" in page
        # And the panel stops asking for one.
        assert 'class="satisfied"' in page
        assert "1 added" in page

    def test_whoever_was_chosen_stays_chosen(self, client_and_result):
        """Adding a model reloads the page. Without carrying the choice, the
        browser picks whichever name sorts first — quietly moving whose key the
        next test is billed to, which is not a thing to change for somebody."""
        client, _chosen, _page = self._at_step_two(client_and_result)
        for netid, name in (("jh43", "Jeff Heller"), ("tconlan", "T Conlan")):
            client.post("/people", json={"netid": netid, "name": name, "key": "sk-" + netid})

        page = client.get("/models", params={"billed_to": "tconlan"}).text
        assert '<option value="tconlan" selected>' in page
        assert '<option value="jh43">' in page

    def test_with_nobody_named_the_browser_decides_as_it_always_did(self, client_and_result):
        client, _chosen, _page = self._at_step_two(client_and_result)
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        assert "selected" not in client.get("/models").text

    def test_a_name_that_is_not_there_reaches_the_page_as_nothing(self, client_and_result):
        """It arrives in the address bar, so it is not to be trusted.

        What keeps it harmless is that it is only ever compared against the
        netIDs already configured — never written into the page. This pins that
        property rather than a check that could be removed without effect.
        """
        client, _chosen, _page = self._at_step_two(client_and_result)
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        page = client.get("/models", params={"billed_to": "<script>x</script>"}).text
        assert "<script>x</script>" not in page
        assert "selected" not in page

    def test_the_reload_carries_the_choice(self, client_and_result):
        client, _chosen, _page = self._at_step_two(client_and_result)
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        page = client.get("/models").text
        assert 'billed_to=" + encodeURIComponent(professor)' in page

    def test_no_line_explains_that_the_button_may_be_pressed(self, client_and_result):
        """Once it works, pressing it is the answer."""
        client, _chosen, empty = self._at_step_two(client_and_result)
        # While it is still needed, it says what is missing.
        assert "Add at least one person first" in empty
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        page = client.get("/people").text
        assert "Add more if you need to" not in page
        assert "or carry on" not in page

    def test_a_field_is_as_wide_as_it_says_it_is(self, client_and_result):
        """The model box overhung its panel while the picker below it fitted.

        Both said width: 100%. A browser measures a text input's width inside
        its padding and a select's around it, so the same declaration produced
        two different widths — and the wider one ran past the edge of the box.
        """
        _client, _chosen, page = self._at_step_two(client_and_result)
        # Set on everything, not patched onto the one field that showed it —
        # and it comes from the shared design system now, along with everything
        # else about how this page looks.
        assert "box-sizing: border-box" in page

    def test_the_two_fields_are_declared_the_same_width(self, client_and_result):
        """One rule for all three, so they cannot come to differ."""
        import re

        client, _chosen, _page = self._at_step_two(client_and_result)
        client.post("/people", json={"netid": "jh43", "name": "J", "key": "sk-t"})
        page = client.get("/models").text
        rule = re.search(r"input\[type=text\][^{]*\{([^}]*)\}", page)
        assert rule, "the text field has no rule at all"
        assert "input[type=password]" in rule.group(0)
        assert "select" in rule.group(0)
        assert "width: 100%" in rule.group(1)


class TestEveryPageLooksLikeTheSamePiece:
    """First-time setup used to have a palette of its own.

    Twenty-eight colours, not one of them the sandbox's: a different blue, a
    different set of greys, a different dark background. The first thing anyone
    saw of this software looked like a different piece of software from the one
    it was installing.
    """

    def _rendered(self, name, **values):
        from jinja2 import Environment, FileSystemLoader, select_autoescape
        directory = Path(__file__).resolve().parents[1] / "src" / "templates"
        environment = Environment(loader=FileSystemLoader(str(directory)),
                                  autoescape=select_autoescape(["html"]))
        return environment.get_template(name).render(**values)

    def _tokens(self):
        import re
        directory = Path(__file__).resolve().parents[1] / "src" / "templates"
        text = (directory / "_design-system.html").read_text()
        return {m.group(1).lower()
                for m in re.finditer(r"--[\w-]+:\s*(#[0-9a-fA-F]{3,8})", text)}

    def test_setup_uses_the_shared_design_system(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "setup.html").read_text()
        assert '{% include "_design-system.html" %}' in source

    def test_it_invents_no_colours_of_its_own(self):
        """The test that would have failed loudest before this."""
        import re

        page = self._rendered("setup.html", body="", lede="", script="", error="")
        css = "\n".join(re.findall(r"<style>(.*?)</style>", page, re.S))
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        declared = set(re.findall(r"--[\w-]+:\s*(#[0-9a-fA-F]{3,8})", css))
        used = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css)) - declared
        stray = sorted(c for c in used if c.lower() not in self._tokens())
        assert not stray, f"setup draws its own colours: {stray}"

    def test_it_keeps_its_own_shape(self):
        """Sharing the colours is not the same as pretending to be the app.

        An installer is one narrow column with nothing to navigate. It should
        not grow a sidebar and a spend panel because the chat page has them.
        """
        source = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "setup.html").read_text()
        assert "max-width: 40rem" in source
        assert "topbar" not in source
        assert '{% include "_forms.html" %}' not in source

    def test_the_page_is_no_longer_built_by_string_formatting(self):
        """58 doubled braces were there only to survive .format()."""
        source = (Path(__file__).resolve().parents[1] / "src"
                  / "setup_web.py").read_text()
        assert "_PAGE.format(" not in source
        assert "def _page(" in source


class TestSetupIsBuiltLikeTheSettingsPage:
    """Somebody who has just been through setup meets the settings page the
    first time they change anything. Both ask the same shape of question — here
    is what you have, and here is how to add another — so both are arranged the
    same way: a heading over a ruled-off block holding the boxes."""

    def _fields_by_where_they_sit(self, page):
        """Return the ids of the boxes inside the ruled-off block, and outside."""
        from html.parser import HTMLParser

        class Read(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack, self.inside, self.outside = [], [], []
                self.heading_is_ruled_off = None
                self.headings = []

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag in ("div", "fieldset", "ul"):
                    self.stack.append("ruled" if "after-a-list" in (a.get("class") or "")
                                      else tag)
                if tag in ("input", "select"):
                    (self.inside if "ruled" in self.stack else self.outside).append(a.get("id"))
                if tag == "h3":
                    self.heading_is_ruled_off = "ruled" in self.stack
                    self.reading_heading = True

            def handle_endtag(self, tag):
                if tag in ("div", "fieldset", "ul") and self.stack:
                    self.stack.pop()
                if tag == "h3":
                    self.reading_heading = False

            def handle_data(self, data):
                if getattr(self, "reading_heading", False) and data.strip():
                    self.headings.append(data.strip())

        reader = Read()
        reader.feed(page)
        return reader

    def _page(self, which, tmp_path):
        import sys

        setup_web = sys.modules["_pu_webui_setup_web"]
        return (setup_web._render_people(tmp_path) if which == "people"
                else setup_web._render_models(tmp_path))

    @pytest.mark.parametrize("which,heading,expected", [
        ("people", "Add a person",
         ["netid", "fullname", "apikey", "backupkey", "usagepath", "usagemode"]),
        ("models", "Add a model", ["modelname", "modelprof"]),
    ])
    def test_the_boxes_sit_under_a_heading_in_a_ruled_off_block(
        self, which, heading, expected, tmp_path
    ):
        read = self._fields_by_where_they_sit(self._page(which, tmp_path))
        assert read.headings == [heading]
        assert read.heading_is_ruled_off is True
        assert read.inside == expected
        assert read.outside == [], "a box was left above the rule"

    @pytest.mark.parametrize("which", ["people", "models"])
    def test_what_is_already_there_is_shown_above_the_rule(self, which, tmp_path):
        """The list answers "what do I have"; the block below answers "how do I
        add another". Running them together was what the rule fixed."""
        page = self._page(which, tmp_path)
        assert page.index('class="added"') < page.index('class="after-a-list"')

    def test_it_uses_the_same_three_rules_the_settings_page_uses(self):
        """Written out rather than shared, because this page keeps its own
        layout — so the values have to be the same tokens, not lookalikes."""
        directory = Path(__file__).resolve().parents[1] / "src" / "templates"
        setup = (directory / "setup.html").read_text()
        forms = (directory / "_forms.html").read_text()
        panels = (directory / "_panels.html").read_text()

        assert "h3 { font-size: var(--text-md); margin: 0 0 var(--space-1); }" in setup
        assert "font-size: var(--text-md)" in forms, "settings changed its heading size"
        assert "* + h3 { margin-top: var(--space-6); }" in setup
        assert ".card * + h3 { margin-top: var(--space-6); }" in forms
        for rule in ("margin-top: var(--space-4); padding-top: var(--space-4)",):
            assert rule in setup.replace("\n    ", " ")
            assert rule in panels.replace("\n    ", " ")

    def test_a_paragraph_after_a_field_is_given_room_here_too(self):
        setup = (Path(__file__).resolve().parents[1] / "src" / "templates"
                 / "setup.html").read_text()
        assert ":is(input, select, .field-row) + p { margin-top: var(--space-6); }" in setup

    def test_step_one_has_such_a_paragraph_for_that_rule_to_reach(self, monkeypatch):
        """The line about the suggested folder sits directly under the box.

        Only on a machine with nothing set up yet — the other branch of that
        page offers a folder it already found and has no such line, which is
        what a first check of this was misled by.
        """
        import re

        monkeypatch.setattr(setup_web.first_run, "find_existing", lambda: [])
        body = re.sub(r"<style>.*?</style>|<!--.*?-->", "", setup_web._render(), flags=re.S)
        assert re.search(r'<div class="field-row">.*?</div>\s*<p', body, re.S), (
            "nothing follows the folder box, so the rule above has no work to do")

    def test_the_heading_opening_a_block_is_not_pushed_off_it(self):
        setup = (Path(__file__).resolve().parents[1] / "src" / "templates"
                 / "setup.html").read_text()
        assert ".after-a-list > h3:first-child { margin-top: 0; }" in setup


class TestTheTwoLocationsAreNamedSeparately:
    """"Your files are here" was one sentence covering two places. The settings
    are in the folder that was found; each person's work is wherever their
    settings say, which is often somewhere else entirely — and reading that one
    sentence, somebody would take it to cover both."""

    def _found_with(self, tmp_path, monkeypatch, *, shared=True, conversations=0):
        """A settings location whose people keep their work here or elsewhere."""
        import sys

        from src import first_run, paths as core_paths

        root = paths.DEFAULT_EXTRAS_ROOT
        (root / core_paths.DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
        (root / core_paths.MODEL_CATALOG_FILENAME).write_text('{"models": {}}')
        where = tmp_path / "Dropbox" / "heller"
        if shared:
            (root / core_paths.SETTINGS_FILENAME).write_text(
                '[professors.jh43]\nname = "Heller"\nkey = "sk"\n'
                f'usage_path = "{where}"\nusage_mode = "shared-write"\n')
            (where / "archives").mkdir(parents=True)
            (where / "archives" / "2026-05.json").write_text("{}")
            for i in range(conversations):
                (where / core_paths.CONVERSATIONS_DIRNAME / f"c_{i:016x}").mkdir(parents=True)
        else:
            (root / core_paths.SETTINGS_FILENAME).write_text(
                '[professors.jh43]\nname = "Heller"\nkey = "sk"\n')
        module = sys.modules["_pu_webui_setup_web"]
        monkeypatch.setattr(module.first_run, "find_existing",
                            lambda: [first_run.inspect_extras(root)])
        return where

    def _text(self, page):
        import re

        stripped = re.sub(r"<script>.*?</script>|<style>.*?</style>|<!--.*?-->", "",
                          page, flags=re.S)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped))

    def test_the_settings_location_is_named_as_that(self, client_and_result,
                                                    tmp_path, monkeypatch):
        self._found_with(tmp_path, monkeypatch)
        client, _ = client_and_result
        assert "Settings location" in self._text(client.get("/").text)

    def test_a_shared_folder_is_named_as_a_data_location(self, client_and_result,
                                                         tmp_path, monkeypatch):
        where = self._found_with(tmp_path, monkeypatch, conversations=28)
        client, _ = client_and_result
        text = self._text(client.get("/").text)
        assert "Data location" in text
        assert str(where) in text, "the folder holding their work is not shown"
        assert "28 conversations" in text

    def test_it_says_plainly_that_some_of_it_is_elsewhere(self, client_and_result,
                                                          tmp_path, monkeypatch):
        self._found_with(tmp_path, monkeypatch)
        client, _ = client_and_result
        assert "outside the folder above" in self._text(client.get("/").text)

    def test_and_says_the_opposite_when_it_is_all_here(self, client_and_result,
                                                       tmp_path, monkeypatch):
        self._found_with(tmp_path, monkeypatch, shared=False)
        client, _ = client_and_result
        text = self._text(client.get("/").text)
        assert "All of it is kept inside the folder above" in text
        assert "outside the folder above" not in text

    def test_usage_history_is_not_listed_as_if_it_were_in_the_settings_folder(
        self, client_and_result, tmp_path, monkeypatch
    ):
        """The first list covers the settings location only. History belongs in
        the second, next to the folder actually holding it."""
        self._found_with(tmp_path, monkeypatch)
        client, _ = client_and_result
        text = self._text(client.get("/").text)
        settings_part = text[text.index("Settings location"):text.index("Data location")]
        assert "month" not in settings_part
        assert "conversation" not in settings_part

    def test_the_page_names_itself_the_same_way_twice(self):
        """The title said one name and the heading said another."""
        source = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "setup.html").read_text()
        import re

        title = re.search(r"<title>(.*?)</title>", source).group(1)
        heading = re.search(r"<h1>(.*?)</h1>", source).group(1)
        assert title == heading


class TestASharedFolderCanBeGivenDuringSetup:
    """Somebody setting up a second computer already knows where their work
    lives. Asking only afterwards, on the settings page, meant setting it up
    twice — and the wording on both pages now says it can be given here."""

    def _client(self, client_and_result):
        """Past step one, which is what tells the sandbox where it lives."""
        from src import settings_store

        client, _ = client_and_result
        assert client.post("/", data={"folder": str(paths.DEFAULT_EXTRAS_ROOT)}
                           ).status_code == 200
        return client, settings_store

    def test_a_folder_given_here_is_recorded_on_the_person(self, client_and_result, tmp_path):
        client, store = self._client(client_and_result)
        resp = client.post("/people", json={
            "netid": "smith", "name": "Prof. Smith", "key": "sk-test",
            "usage_path": str(tmp_path / "Dropbox"), "usage_mode": "shared-write"})
        assert resp.status_code == 200
        source = store.get_shared_write_source("smith")
        assert source is not None and source.path == str(tmp_path / "Dropbox")

    def test_leaving_it_blank_records_nothing(self, client_and_result):
        """Most people have none, and an empty box must not become a folder."""
        client, store = self._client(client_and_result)
        assert client.post("/people", json={
            "netid": "jones", "name": "Prof. Jones", "key": "sk"}).status_code == 200
        assert store.get_shared_write_source("jones") is None
        assert store.get_configured_sources() == []

    def test_a_mode_that_is_not_one_is_refused(self, client_and_result):
        client, _ = self._client(client_and_result)
        assert client.post("/people", json={
            "netid": "brown", "name": "Prof. Brown", "key": "sk",
            "usage_path": "/x", "usage_mode": "nonsense"}).status_code == 400

    def test_the_person_is_added_before_the_folder_is_set_on_them(self, client_and_result,
                                                                  tmp_path):
        """A folder is recorded on somebody, so there has to be a somebody."""
        client, store = self._client(client_and_result)
        client.post("/people", json={
            "netid": "smith", "name": "Prof. Smith", "key": "sk-test",
            "usage_path": str(tmp_path / "Dropbox"), "usage_mode": "shared-write"})
        assert "smith" in store.get_professors()

    def test_both_pages_explain_it_the_same_way(self):
        """The settings page and setup ask this in the same words, because it
        is the same question."""
        directory = Path(__file__).resolve().parents[1] / "src"
        settings = (directory / "templates" / "settings.html").read_text()
        setup = (directory / "setup_web.py").read_text()
        for phrase in ("use the sandbox on multiple computers",
                       "research team or lab", "shared folder"):
            assert phrase in settings, phrase
            assert phrase in setup, phrase

    def test_the_browse_button_is_hidden_where_there_is_no_chooser(
        self, tmp_path, monkeypatch
    ):
        import sys

        module = sys.modules["_pu_webui_setup_web"]
        monkeypatch.setattr(module.file_picker, "available", lambda: False)
        page = module._render_people(tmp_path)
        at = page.index('id="browse-usage-btn"')
        assert "display:none" in page[at:at + 120]
