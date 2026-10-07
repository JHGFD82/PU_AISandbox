"""Tests for plugins/webui/src/app.py: getting in, stopping, and what the app does as it starts.

Covers the passphrase and unlocking, stopping the server, choosing whose
sandbox it is, the sweep at start-up, refusing paths that leave a person's
own folders, and the reference code an error gives. The app's other routes are
tested by area in test_app_<area>.py beside this file, and its pages in
templates/.

Every file uses FastAPI's TestClient against a fresh app from create_app():
no real server is started and no real AI call is made.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

import pytest

from plugins.webui.tests.helpers import _rendered_template

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestUnlock:
    def test_index_shows_unlock_page_when_locked(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Unlock" in resp.text or "passphrase" in resp.text.lower()

    def test_open_access_unlocks_without_passphrase(self, client):
        resp = client.post("/unlock", data={})
        assert resp.status_code in (200, 303)

    def test_wrong_passphrase_rejected_when_configured(self, client, monkeypatch):
        auth = sys.modules["_pu_webui_auth"]
        app_module = sys.modules["_pu_webui_app"]
        hashed = auth.hash_passphrase("correct")
        monkeypatch.setattr(app_module, "_auth_backend", auth.PassphraseBackend(passphrase_hash=hashed))
        resp = client.post("/unlock", data={"passphrase": "wrong"})
        assert resp.status_code == 401

    def test_correct_passphrase_accepted_when_configured(self, client, monkeypatch):
        auth = sys.modules["_pu_webui_auth"]
        app_module = sys.modules["_pu_webui_app"]
        hashed = auth.hash_passphrase("correct")
        monkeypatch.setattr(app_module, "_auth_backend", auth.PassphraseBackend(passphrase_hash=hashed))
        resp = client.post("/unlock", data={"passphrase": "correct"}, follow_redirects=False)
        assert resp.status_code == 303

    def test_repeated_wrong_passphrases_are_rate_limited(self, client, monkeypatch):
        """After a few wrong guesses the route stops checking and asks the
        caller to wait, so the passphrase can't be worked through one guess
        at a time."""
        auth = sys.modules["_pu_webui_auth"]
        app_module = sys.modules["_pu_webui_app"]
        hashed = auth.hash_passphrase("correct")
        monkeypatch.setattr(app_module, "_auth_backend", auth.PassphraseBackend(passphrase_hash=hashed))
        monkeypatch.setattr(app_module, "_attempt_limiter", auth.AttemptLimiter(max_attempts=3, lockout_seconds=60))

        for _ in range(3):
            assert client.post("/unlock", data={"passphrase": "wrong"}).status_code == 401
        blocked = client.post("/unlock", data={"passphrase": "wrong"})
        assert blocked.status_code == 429
        assert "Too many incorrect attempts" in blocked.text

        # Even the *correct* passphrase waits out the cooling-off period —
        # otherwise the limit would leak whether a guess was right.
        assert client.post("/unlock", data={"passphrase": "correct"}).status_code == 429

    def test_successful_unlock_clears_the_attempt_count(self, client, monkeypatch):
        auth = sys.modules["_pu_webui_auth"]
        app_module = sys.modules["_pu_webui_app"]
        hashed = auth.hash_passphrase("correct")
        monkeypatch.setattr(app_module, "_auth_backend", auth.PassphraseBackend(passphrase_hash=hashed))
        monkeypatch.setattr(app_module, "_attempt_limiter", auth.AttemptLimiter(max_attempts=3, lockout_seconds=60))

        client.post("/unlock", data={"passphrase": "wrong"})
        client.post("/unlock", data={"passphrase": "wrong"})
        assert client.post("/unlock", data={"passphrase": "correct"}, follow_redirects=False).status_code == 303
        # Allowance restored — a later typo doesn't immediately lock them out.
        for _ in range(3):
            assert client.post("/unlock", data={"passphrase": "wrong"}).status_code == 401

    def test_api_route_requires_unlock(self, client):
        resp = client.get("/api/professors")
        assert resp.status_code == 401

    def test_quit_clears_session(self, unlocked_client):
        resp = unlocked_client.post("/quit")
        assert resp.status_code == 200
        resp2 = unlocked_client.get("/api/professors")
        assert resp2.status_code == 401


class TestStopping:
    """Quit, from the page, and /__stop, from the launcher.

    The sandbox usually runs with no window of its own, so these are the only
    two ways it stops. Both leave a server they were given standing when
    stopping would throw work away.
    """

    @pytest.fixture
    def server(self, client):
        from types import SimpleNamespace

        stand_in = SimpleNamespace(should_exit=False)
        client.app.state.server = stand_in
        return stand_in

    @pytest.fixture
    def busy(self, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module._job_store, "running", lambda: 1)

    def test_quit_needs_the_passphrase_like_everything_else(self, client, server):
        assert client.post("/quit").status_code == 401
        assert server.should_exit is False

    def test_quit_stops_the_server(self, unlocked_client, server):
        assert unlocked_client.post("/quit").status_code == 200
        assert server.should_exit is True

    def test_quit_refuses_while_something_is_being_worked_on(self, unlocked_client, server, busy):
        resp = unlocked_client.post("/quit")
        assert resp.status_code == 409
        assert "still being worked on" in resp.json()["detail"]
        assert server.should_exit is False

    def test_quit_refuses_during_an_update(self, unlocked_client, server, monkeypatch):
        upgrade = sys.modules["_pu_webui_upgrade"]
        monkeypatch.setattr(upgrade, "an_update_is_running", lambda: True)
        assert unlocked_client.post("/quit").status_code == 409
        assert server.should_exit is False

    def test_stop_is_not_there_when_no_token_was_given(self, client, server, monkeypatch):
        """Started some other way than start.py — nothing can prove it is the launcher."""
        monkeypatch.delenv("PU_SANDBOX_STOP_TOKEN", raising=False)
        resp = client.post("/__stop", headers={"X-Sandbox-Stop-Token": ""})
        assert resp.status_code == 404
        assert server.should_exit is False

    def test_stop_refuses_the_wrong_token(self, client, server, monkeypatch):
        monkeypatch.setenv("PU_SANDBOX_STOP_TOKEN", "right")
        resp = client.post("/__stop", headers={"X-Sandbox-Stop-Token": "wrong"})
        assert resp.status_code == 403
        assert server.should_exit is False

    def test_stop_refuses_a_request_with_no_token(self, client, server, monkeypatch):
        """What a web page elsewhere sending a request here looks like."""
        monkeypatch.setenv("PU_SANDBOX_STOP_TOKEN", "right")
        assert client.post("/__stop").status_code == 403
        assert server.should_exit is False

    def test_stop_with_the_right_token_needs_no_passphrase(self, client, server, monkeypatch):
        monkeypatch.setenv("PU_SANDBOX_STOP_TOKEN", "right")
        resp = client.post("/__stop", headers={"X-Sandbox-Stop-Token": "right"})
        assert resp.status_code == 200
        assert server.should_exit is True

    def test_stop_waits_for_work_in_progress(self, client, server, monkeypatch, busy):
        """The launcher opens the running copy instead, and the work carries on."""
        monkeypatch.setenv("PU_SANDBOX_STOP_TOKEN", "right")
        resp = client.post("/__stop", headers={"X-Sandbox-Stop-Token": "right"})
        assert resp.status_code == 409
        assert server.should_exit is False

    def test_every_page_offers_quit_and_none_offers_lock(self):
        for name in ("chat.html", "settings.html", "shared_settings.html"):
            page = _rendered_template(name)
            assert 'id="quit-btn"' in page, name
            assert "quitTheSandbox" in page, name
            assert '"/lock"' not in page, name


class TestProfessors:
    def test_lists_configured_professors(self, unlocked_client):
        resp = unlocked_client.get("/api/professors")
        assert resp.status_code == 200
        names = {p["netid"] for p in resp.json()["professors"]}
        assert names == {"heller", "smith"}

    def test_active_defaults_to_first_professor(self, unlocked_client):
        resp = unlocked_client.get("/api/professors")
        assert resp.json()["active"] in ("heller", "smith")

    def test_set_active_professor(self, unlocked_client):
        resp = unlocked_client.post("/api/active-professor", json={"professor": "smith"})
        assert resp.status_code == 200
        resp2 = unlocked_client.get("/api/professors")
        assert resp2.json()["active"] == "smith"

    def test_set_unknown_professor_rejected(self, unlocked_client):
        resp = unlocked_client.post("/api/active-professor", json={"professor": "nobody"})
        assert resp.status_code == 400


class TestStartupSweep:
    def test_create_app_clears_stale_active_job_id(self, tmp_path, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        conversation = sys.modules["_pu_webui_conversation"]
        auth = sys.modules["_pu_webui_auth"]
        monkeypatch.setattr(app_module, "load_professor_config", lambda: {
            "heller": {"name": "Heller", "key": "sk-heller", "backup_key": None, "safe_name": "heller"},
        })
        monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")
        monkeypatch.setattr(app_module, "_auth_backend", auth.PassphraseBackend(passphrase_hash=""))

        store = conversation.ConversationStore("heller")
        conv = store.create(model="gpt-4o")
        conv.active_job_id = "job_orphaned"
        store.save(conv)

        # create_app() itself runs the sweep, before any request is made.
        app_module.create_app()

        reloaded = store.load(conv.id)
        assert reloaded.active_job_id is None
        assert any(m.kind == "job_error" for m in reloaded.messages)


class TestConversationIdTraversalOverHttp:
    """The two routes that take a conversation id from a request *body* are
    the ones with no incidental protection from URL path matching — see the
    store-level tests in test_conversation.py for the underlying guard."""

    def test_chat_rejects_a_traversal_id(self, unlocked_client, tmp_path):
        victim = tmp_path / "victim.json"
        victim.write_text('{"id": "victim", "title": "SECRET", "created_at": "t", '
                          '"updated_at": "t", "model": "gpt-4o", "messages": []}')
        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller",
            "conversation_id": f"../../{victim.stem}",
            "message": "Hi",
            "model": "gpt-4o",
        })
        assert resp.status_code == 404
        assert victim.exists()

    def test_start_job_rejects_a_traversal_id(self, unlocked_client, tmp_path):
        victim = tmp_path / "victim2.json"
        victim.write_text('{"id": "victim2", "title": "SECRET", "created_at": "t", '
                          '"updated_at": "t", "model": "gpt-4o", "messages": []}')
        resp = unlocked_client.post("/api/jobs", data={
            "professor": "heller",
            "conversation_id": f"../../{victim.stem}",
            "action_id": "translate",
            "fields_json": "{}",
        })
        assert resp.status_code in (400, 404)
        assert victim.exists()


class TestTheReferenceCodeCanActuallyBeLookedUp:
    """The browser tells a professor to quote a code. Someone has to find it.

    The message promised the details were "in the server log". There was no
    server log: everything went to the terminal the sandbox was started from,
    so starting it from an icon, or closing that window, lost the only copy.
    """

    def test_the_traceback_and_the_code_land_in_the_file(self, tmp_path, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        import src.paths
        monkeypatch.setattr(src.paths, "data_root", lambda: tmp_path)
        path = app_module.start_logging_to_a_file()
        assert path is not None
        try:
            # Raised and caught, because that is how it is called (app.py's
            # streaming path, inside `except Exception as e`). logging.exception
            # only records a traceback from inside an except block — calling it
            # bare writes the line and no traceback at all.
            try:
                raise RuntimeError("the provider said no")
            except RuntimeError as e:
                message = app_module._chat_error_message(e)
        finally:
            logging.getLogger().handlers = [
                h for h in logging.getLogger().handlers
                if not isinstance(h, logging.handlers.RotatingFileHandler)
            ]
        reference = re.search(r"reference ([0-9a-f]{8})", message).group(1)
        written = path.read_text(encoding="utf-8")
        # The code the professor quotes, and the error behind it.
        assert reference in written
        assert "the provider said no" in written
        assert "Traceback" in written

    def test_the_message_says_where_to_look(self, tmp_path, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        import src.paths
        monkeypatch.setattr(src.paths, "data_root", lambda: tmp_path)
        message = app_module._chat_error_message(RuntimeError("boom"))
        assert str(tmp_path / "webui.log") in message

    def test_it_keeps_what_led_up_to_the_error_too(self, tmp_path, monkeypatch):
        """A log that starts at the exception explains nothing."""
        app_module = sys.modules["_pu_webui_app"]
        import src.paths
        monkeypatch.setattr(src.paths, "data_root", lambda: tmp_path)
        monkeypatch.setattr(logging.getLogger(), "level", logging.WARNING)
        path = app_module.start_logging_to_a_file()
        try:
            logging.getLogger("x").info("asked gpt-4o to translate page 12")
        finally:
            logging.getLogger().handlers = [
                h for h in logging.getLogger().handlers
                if not isinstance(h, logging.handlers.RotatingFileHandler)
            ]
        assert "asked gpt-4o to translate page 12" in path.read_text(encoding="utf-8")

    def test_a_log_that_cannot_be_opened_does_not_stop_the_sandbox(
        self, tmp_path, monkeypatch
    ):
        """Refusing to start because of the log would be worse than the bug."""
        app_module = sys.modules["_pu_webui_app"]
        import src.paths
        monkeypatch.setattr(src.paths, "data_root", lambda: tmp_path / "nope")
        monkeypatch.setattr(Path, "mkdir",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only")))
        assert app_module.start_logging_to_a_file() is None

    def test_it_is_rolled_over_rather_than_left_to_grow(self, tmp_path, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        import src.paths
        monkeypatch.setattr(src.paths, "data_root", lambda: tmp_path)
        app_module.start_logging_to_a_file()
        handlers = [h for h in logging.getLogger().handlers
                    if isinstance(h, logging.handlers.RotatingFileHandler)]
        try:
            assert handlers and handlers[-1].maxBytes > 0
            assert handlers[-1].backupCount >= 1
        finally:
            logging.getLogger().handlers = [
                h for h in logging.getLogger().handlers if h not in handlers
            ]
