"""Tests for plugins/webui/src/app.py's routes for installing plugins and updating the sandbox."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from plugins.webui.tests.helpers import (
    _parse_sse,
    _rendered_chat,
    app_module_threading,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestInstallingAPluginOverTheWeb:
    """Installing a plugin puts new program code on the computer and runs it
    as part of the sandbox, with the API keys. What is tested here is mostly
    what the endpoint declines to do."""

    def _install(self, client, **body):
        payload = {"repository": "https://github.com/someone/thing.git",
                   "folder": "thing"}
        payload.update(body)
        return client.post("/api/plugins/install", json=payload)

    def test_it_needs_the_interface_unlocked(self, client):
        assert self._install(client).status_code == 401

    def test_it_is_refused_from_another_computer(self, tmp_path, monkeypatch):
        """A passphrase says somebody may use the sandbox. It does not say
        they may put new code on the machine it runs on."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        conversation = sys.modules["_pu_webui_conversation"]
        jobs = sys.modules["_pu_webui_jobs"]
        monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")
        monkeypatch.setattr(jobs, "_CONVERSATIONS_DIR", tmp_path / "conversations")
        fetched = []
        monkeypatch.setattr(sys.modules["_pu_webui_plugin_install"], "install_from_git",
                            lambda *a, **k: fetched.append(a))

        elsewhere = TestClient(app_module.create_app(), client=("10.0.0.5", 50000))
        elsewhere.post("/unlock", data={"passphrase": ""})
        resp = elsewhere.post("/api/plugins/install",
                              json={"repository": "https://github.com/x/y.git",
                                    "folder": "y"})
        assert resp.status_code == 403
        assert fetched == [], "it went and fetched it anyway"

    def test_the_address_it_judges_by_is_the_connection(self, tmp_path, monkeypatch):
        """Not a header. Anybody can send X-Forwarded-For; nobody can forge
        which socket they connected on."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        conversation = sys.modules["_pu_webui_conversation"]
        jobs = sys.modules["_pu_webui_jobs"]
        monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")
        monkeypatch.setattr(jobs, "_CONVERSATIONS_DIR", tmp_path / "conversations")

        elsewhere = TestClient(app_module.create_app(), client=("10.0.0.5", 50000))
        elsewhere.post("/unlock", data={"passphrase": ""})
        resp = elsewhere.post("/api/plugins/install",
                              json={"repository": "https://github.com/x/y.git",
                                    "folder": "y"},
                              headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 403

    def test_a_folder_name_that_would_escape_is_refused(self, unlocked_client):
        resp = self._install(unlocked_client, folder="../../src")
        assert resp.status_code == 400
        assert "not a folder name" in resp.text

    def test_an_address_that_names_a_program_is_refused(self, unlocked_client):
        resp = self._install(unlocked_client, repository="ext::sh -c whoami")
        assert resp.status_code == 400
        assert "https://" in resp.text

    def test_nothing_restarts_when_nothing_was_installed(self, unlocked_client,
                                                          monkeypatch):
        """A restart on a failed install would throw away every running job to
        load a plugin that is not there.

        What is watched is the scheduling, not the firing. An earlier version
        of this replaced the restart itself and then looked straight away —
        half a second before the timer it was waiting for, so it could not
        have seen anything either way.
        """
        import sys

        app_module = sys.modules["_pu_webui_app"]
        scheduled = []
        monkeypatch.setattr(
            app_module.threading, "Timer",
            lambda delay, fn: type("T", (), {"start": lambda s: scheduled.append(fn)})())
        self._install(unlocked_client, folder="../../src")
        assert scheduled == [], "a failed install asked for a restart"

    def test_a_successful_install_asks_for_a_restart(self, unlocked_client, monkeypatch):
        """Re-scanning in this process would put the plugin in the menu with
        its orchestration methods missing — SandboxProcessor gathers those at
        first import, which has long since happened."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        installed = app_module.sys.modules["_pu_webui_plugin_install"].Installed
        monkeypatch.setattr(
            app_module.sys.modules["_pu_webui_plugin_install"], "install_from_git",
            lambda *a, **k: installed(name="thing", path=Path("/x"), commands=["do"]))
        timers = []
        monkeypatch.setattr(app_module.threading, "Timer",
                            lambda delay, fn: type("T", (), {"start": lambda s: timers.append(fn)})())
        resp = self._install(unlocked_client)
        assert resp.status_code == 200
        assert resp.json()["restarting"] is True
        assert resp.json()["commands"] == ["do"]
        assert timers, "nothing was scheduled to restart the sandbox"

    def test_the_response_goes_before_the_restart(self, unlocked_client, monkeypatch):
        """os.execv replaces this process. Restarting inside the handler would
        mean the browser never hears where to look."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        source = Path(app_module.__file__).read_text()
        handler = source[source.index("def api_install_plugin"):]
        handler = handler[:handler.index("@app.get")]
        assert handler.index("threading.Timer") < handler.index("return {")


class TestUpdatingTheSandboxOverTheWeb:
    """An update replaces the sandbox's own program code and then runs it.
    As with installing a plugin above, most of what is tested here is what the
    endpoint declines to do — and, past that, what it leaves behind when it
    cannot finish."""

    @pytest.fixture
    def upgrade(self, monkeypatch):
        """The upgrade module, with nothing in it that reaches a network."""
        module = sys.modules["_pu_webui_upgrade"]
        monkeypatch.setattr(module, "check_for_updates",
                            lambda: module.Available(checked_at="2026-09-18T00:00:00+00:00"))
        monkeypatch.setattr(module, "an_update_is_running", lambda: False)
        return module

    def _elsewhere(self, tmp_path, monkeypatch):
        """A client that looks like a browser on some other computer."""
        app_module = sys.modules["_pu_webui_app"]
        conversation = sys.modules["_pu_webui_conversation"]
        jobs = sys.modules["_pu_webui_jobs"]
        monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")
        monkeypatch.setattr(jobs, "_CONVERSATIONS_DIR", tmp_path / "conversations")
        client = TestClient(app_module.create_app(), client=("10.0.0.5", 50000))
        client.post("/unlock", data={"passphrase": ""})
        return client

    def test_every_one_of_them_needs_the_interface_unlocked(self, client):
        assert client.get("/api/updates").status_code == 401
        assert client.post("/api/updates/check").status_code == 401
        assert client.post("/api/updates/apply").status_code == 401
        assert client.post("/api/updates/undo",
                           json={"was": "abc1234"}).status_code == 401

    def test_it_is_refused_from_another_computer(self, tmp_path, monkeypatch, upgrade):
        """A passphrase says somebody may use the sandbox. It does not say
        they may replace what it is."""
        applied = []
        monkeypatch.setattr(upgrade, "apply_update",
                            lambda on_progress: applied.append(True))
        elsewhere = self._elsewhere(tmp_path, monkeypatch)
        assert elsewhere.post("/api/updates/apply").status_code == 403
        assert applied == [], "it went and updated anyway"

    def test_and_so_is_merely_looking(self, tmp_path, monkeypatch, upgrade):
        """Showing somebody a newer version they are not allowed to install
        would be telling them about a button that is not there."""
        elsewhere = self._elsewhere(tmp_path, monkeypatch)
        assert elsewhere.get("/api/updates").status_code == 403
        assert elsewhere.post("/api/updates/check").status_code == 403

    def test_the_address_it_judges_by_is_the_connection(self, tmp_path, monkeypatch,
                                                        upgrade):
        """Not a header. Anybody can send X-Forwarded-For; nobody can forge
        which socket they connected on."""
        elsewhere = self._elsewhere(tmp_path, monkeypatch)
        resp = elsewhere.post("/api/updates/apply",
                              headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 403

    def test_the_settings_page_is_told_which_computer_is_asking(self, unlocked_client,
                                                                settings_env):
        """It is what the card hides itself on."""
        assert unlocked_client.get("/api/settings").json()["same_computer"] is True

    def test_nothing_has_been_looked_for_yet_is_not_the_same_as_nothing_found(
            self, unlocked_client, upgrade, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_update_check", None)
        assert unlocked_client.get("/api/updates").json() == {"checked_at": None}

    def test_looking_again_is_remembered(self, unlocked_client, upgrade, monkeypatch):
        found = upgrade.Available(behind=2, changes=["a", "b"], checked_at="then")
        monkeypatch.setattr(upgrade, "check_for_updates", lambda: found)
        assert unlocked_client.post("/api/updates/check").json()["behind"] == 2
        # And the next page to ask is answered without looking again.
        monkeypatch.setattr(upgrade, "check_for_updates",
                            lambda: pytest.fail("it looked a second time"))
        assert unlocked_client.get("/api/updates").json()["behind"] == 2

    def test_an_answer_about_somewhere_this_copy_has_left_is_not_shown(
            self, unlocked_client, upgrade, monkeypatch):
        """Switching branch, or pulling in a terminal, while the sandbox runs
        must not leave the page repeating what was true when it started."""
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_update_check", upgrade.Available(
            behind=3, checked_at="then", position="refs/heads/main aaa"))
        monkeypatch.setattr(upgrade, "where_this_copy_is",
                            lambda: "refs/heads/something-else bbb")
        looked = []
        monkeypatch.setattr(app_module, "_look_for_an_update_in_the_background",
                            lambda: looked.append(True))

        assert unlocked_client.get("/api/updates").json() == {"checked_at": None}
        assert looked, "it did not look again"

    def test_an_answer_about_where_this_copy_still_is_stands(
            self, unlocked_client, upgrade, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_update_check", upgrade.Available(
            behind=3, checked_at="then", position="refs/heads/main aaa"))
        monkeypatch.setattr(upgrade, "where_this_copy_is", lambda: "refs/heads/main aaa")
        monkeypatch.setattr(app_module, "_look_for_an_update_in_the_background",
                            lambda: pytest.fail("it looked again for no reason"))

        assert unlocked_client.get("/api/updates").json()["behind"] == 3

    def test_it_will_not_start_while_something_is_being_worked_on(
            self, unlocked_client, upgrade, monkeypatch):
        """Restarting does not pause a translation. It destroys it — jobs are
        kept in memory only and there is nothing to pick up again."""
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module._job_store, "running", lambda: 1)
        applied = []
        monkeypatch.setattr(upgrade, "apply_update",
                            lambda on_progress: applied.append(True))

        resp = unlocked_client.post("/api/updates/apply")
        assert resp.status_code == 409
        assert applied == []

    def test_a_second_update_is_turned_away(self, unlocked_client, upgrade, monkeypatch):
        monkeypatch.setattr(upgrade, "an_update_is_running", lambda: True)
        assert unlocked_client.post("/api/updates/apply").status_code == 409

    def test_the_stream_says_what_is_happening_and_then_stops(
            self, unlocked_client, upgrade, monkeypatch):
        def fake(on_progress):
            on_progress(1, "Checking this copy can be updated")
            on_progress(2, "Asking for what has changed")
            on_progress(3, "Getting the new files")
            on_progress(4, "Finishing up")
            return upgrade.Applied(was="old1234", now="new5678",
                                   installed_dependencies=False)
        monkeypatch.setattr(upgrade, "apply_update", fake)
        monkeypatch.setattr(app_module_threading(), "Timer",
                            lambda delay, fn: type("T", (), {"start": lambda s: None})())

        events = _parse_sse(unlocked_client.post("/api/updates/apply").text)
        progress = [e for e in events if e["type"] == "progress"]
        assert len(progress) == 4
        for event in progress:
            assert set(event) == {"type", "step", "total", "label"}
            assert 1 <= event["step"] <= event["total"]
        endings = [e for e in events if e["type"] in ("done", "error")]
        assert len(endings) == 1, "a stream must end exactly once"
        assert events[-1] is endings[0], "and the ending must be last"
        assert endings[0]["now"] == "new5678"
        assert endings[0]["restarting"] is True

    def test_a_successful_update_asks_for_a_restart(self, unlocked_client, upgrade,
                                                    monkeypatch):
        monkeypatch.setattr(
            upgrade, "apply_update",
            lambda on_progress: upgrade.Applied("old1234", "new5678", True))
        scheduled = []
        monkeypatch.setattr(
            app_module_threading(), "Timer",
            lambda delay, fn: type("T", (), {"start": lambda s: scheduled.append(fn)})())

        unlocked_client.post("/api/updates/apply")
        assert scheduled, "nothing was scheduled to restart the sandbox"

    def test_nothing_restarts_when_the_update_stopped_partway(
            self, unlocked_client, upgrade, monkeypatch):
        """This is the whole safety net. Restarting into new code whose
        software is not installed is exactly the sandbox that will not start.

        What is watched is the scheduling, not the firing — the same reason
        the install test above gives.
        """
        def fail(on_progress):
            raise upgrade.StoppedPartway("No matching distribution found", "old1234")
        monkeypatch.setattr(upgrade, "apply_update", fail)
        scheduled = []
        monkeypatch.setattr(
            app_module_threading(), "Timer",
            lambda delay, fn: type("T", (), {"start": lambda s: scheduled.append(fn)})())

        events = _parse_sse(unlocked_client.post("/api/updates/apply").text)
        assert scheduled == [], "a failed update asked for a restart"
        assert events[-1]["type"] == "error"

    def test_stopping_partway_offers_both_ways_out(self, unlocked_client, upgrade,
                                                   monkeypatch):
        """Installing fails for passing reasons more often than lasting ones,
        so trying again is worth offering before putting it back."""
        def fail(on_progress):
            raise upgrade.StoppedPartway("No matching distribution found", "old1234")
        monkeypatch.setattr(upgrade, "apply_update", fail)

        last = _parse_sse(unlocked_client.post("/api/updates/apply").text)[-1]
        assert last["can_retry"] is True
        assert last["can_undo"] is True
        assert last["was"] == "old1234"
        assert "No matching distribution" in last["message"]

    def test_any_other_failure_offers_neither(self, unlocked_client, upgrade,
                                              monkeypatch):
        """Nothing moved, so there is nothing to put back and nothing that a
        second attempt would do differently."""
        def fail(on_progress):
            raise upgrade.UpgradeError("Could not reach the published version")
        monkeypatch.setattr(upgrade, "apply_update", fail)

        last = _parse_sse(unlocked_client.post("/api/updates/apply").text)[-1]
        assert last["type"] == "error"
        assert last["can_retry"] is False and last["can_undo"] is False

    def test_putting_it_back_says_where_it_landed(self, unlocked_client, upgrade,
                                                  monkeypatch):
        asked = []
        monkeypatch.setattr(upgrade, "put_the_files_back",
                            lambda was: asked.append(was) or "old1234")
        resp = unlocked_client.post("/api/updates/undo", json={"was": "old1234"})
        assert resp.status_code == 200
        assert resp.json() == {"back_at": "old1234"}
        assert asked == ["old1234"]

    def test_the_last_event_goes_before_the_restart(self, unlocked_client):
        """os.execv replaces this process. Restarting before the stream has
        ended would mean the browser never hears that it worked, and never
        starts waiting for the sandbox to come back."""
        app_module = sys.modules["_pu_webui_app"]
        source = Path(app_module.__file__).read_text()
        handler = source[source.index("def api_apply_update"):]
        handler = handler[:handler.index("@app.post(\"/api/updates/undo\")")]
        assert handler.index('"restarting": True') < handler.index("threading.Timer")


class TestWhereAnInstalledPluginLands:
    """It landed in the repository root rather than plugins/, so the sandbox
    restarted and never found it — and the folder left behind was there for
    the next attempt to collide with."""

    def test_the_endpoint_installs_into_the_plugins_folder(self, unlocked_client,
                                                            monkeypatch):
        import sys

        from src.paths import PACKAGE_ROOT

        app_module = sys.modules["_pu_webui_app"]
        install_module = sys.modules["_pu_webui_plugin_install"]
        asked = {}

        def remember(repository, folder, plugins_dir):
            asked["plugins_dir"] = plugins_dir
            return install_module.Installed(name=folder, path=plugins_dir / folder,
                                            commands=[])

        monkeypatch.setattr(install_module, "install_from_git", remember)
        monkeypatch.setattr(app_module.threading, "Timer",
                            lambda d, fn: type("T", (), {"start": lambda s: None})())
        resp = unlocked_client.post("/api/plugins/install", json={
            "repository": "https://github.com/x/y.git", "folder": "y"})
        assert resp.status_code == 200
        assert asked["plugins_dir"] == PACKAGE_ROOT / "plugins"
        assert asked["plugins_dir"].name == "plugins", "it is not the plugins folder"

    def test_it_is_the_folder_the_loader_reads(self, unlocked_client, monkeypatch):
        """Installing somewhere the loader does not look is the same as not
        installing."""
        import sys

        from src.paths import PACKAGE_ROOT

        app_module = sys.modules["_pu_webui_app"]
        source = Path(app_module.__file__).read_text()
        loader = source[source.index("def _get_plugins"):]
        loader = loader[:loader.index("return _plugins_cache")]
        assert '"plugins"' in loader
        assert (PACKAGE_ROOT / "plugins").is_dir()

    def test_a_refused_install_is_written_down(self, unlocked_client, monkeypatch,
                                               caplog):
        """A browser is not always what is looking, and the page could not show
        this at all until the helper it needed was shared."""
        import logging

        with caplog.at_level(logging.WARNING):
            resp = unlocked_client.post("/api/plugins/install", json={
                "repository": "ext::sh -c whoami", "folder": "y"})
        assert resp.status_code == 400
        assert any("install a plugin" in r.message.lower() or
                   "install a plugin" in r.getMessage().lower()
                   for r in caplog.records), caplog.text


class TestWaitingForTheSandboxToComeBack:
    """After an install the sandbox restarts, and the page waits for it. The
    first version waited on /api/plugin-actions — the very thing it wanted to
    see change — which requires an unlocked session. Restarting mints a new
    session secret unless one is configured, so every session from before it
    is void: the poll got 401 once a second, forever, and the page never
    reloaded."""

    def _wait_block(self):
        import re

        chat = _rendered_chat()
        script = "\n".join(re.findall(r"<script>(.*?)</script>", chat, re.S))
        start = script.index("function waitForTheSandboxToComeBack")
        return script[start:script.index("\n}", start)]

    def test_it_waits_on_something_that_answers_when_locked(self):
        block = self._wait_block()
        assert 'fetch("/"' in block
        assert "plugin-actions" not in block, (
            "waiting on an endpoint that 401s until the session is remade")

    def test_the_path_it_waits_on_really_does_answer_a_new_session(self, client):
        """Not an assumption about the route table: asked of the app itself,
        with a session that has never unlocked — which is what a browser has
        after the restart."""
        assert client.get("/").status_code == 200
        assert client.get("/api/plugin-actions").status_code == 401

    def test_any_answer_counts_as_back(self):
        """Including one asking to unlock again. Only a failure to connect
        means still starting."""
        block = self._wait_block()
        assert "location.reload()" in block
        # Reload sits in .then, not behind a check of what came back.
        then = block[block.index(".then("):block.index(".catch(")]
        assert "location.reload()" in then

    def test_it_gives_up_rather_than_asking_for_ever(self):
        """The comparison, not the names. Checking only that "gaveUpAt"
        appears passes against `if (false)`, which keeps every name and asks
        for ever anyway."""
        block = self._wait_block()
        assert "RESTART_PATIENCE_MS" in block
        assert "Date.now() > gaveUpAt" in block, (
            "nothing compares the clock to the deadline, so it never stops")
        # And stops rather than falling through to another attempt.
        after = block[block.index("Date.now() > gaveUpAt"):]
        assert "return;" in after[:after.index("setTimeout(ask")]

    def test_and_says_what_to_do_when_it_does(self):
        block = self._wait_block()
        assert "Reload this page" in block
