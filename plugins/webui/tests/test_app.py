"""Integration tests for the webui plugin's FastAPI routes (plugins/webui/src/app.py).

Uses FastAPI's TestClient (backed by httpx) against a fresh app instance
built by create_app() — no real server is started, and no real AI API calls
are made (the /api/chat route's SandboxProcessor is monkeypatched).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(autouse=True)
def _configured_professors(monkeypatch):
    """Two fake professors, matching the {safe_name: {...}} shape load_professor_config() returns."""
    fake_config = {
        "heller": {"name": "Heller", "primary_key": "PROF_1_KEY", "backup_key": "PROF_1_BACKUP_KEY", "id": "1", "safe_name": "heller"},
        "smith": {"name": "Smith", "primary_key": "PROF_2_KEY", "backup_key": "PROF_2_BACKUP_KEY", "id": "2", "safe_name": "smith"},
    }
    # app.py imported load_professor_config into its own namespace at import
    # time, so it must be patched there (on the actual registered module
    # object, "_pu_webui_app" — see conftest.py) rather than on src.config,
    # which every other route would still see the real version of.
    monkeypatch.setattr(sys.modules["_pu_webui_app"], "load_professor_config", lambda: fake_config)
    return fake_config


@pytest.fixture(autouse=True)
def _no_passphrase(monkeypatch):
    """Default every test to the open-access (no passphrase configured) case."""
    monkeypatch.delenv("WEBUI_PASSPHRASE_HASH", raising=False)
    auth = sys.modules["_pu_webui_auth"]
    app_module = sys.modules["_pu_webui_app"]
    monkeypatch.setattr(app_module, "_auth_backend", auth.PassphraseBackend(passphrase_hash=""))


@pytest.fixture
def client(tmp_path, monkeypatch):
    app_module = sys.modules["_pu_webui_app"]
    conversation = sys.modules["_pu_webui_conversation"]
    # Redirect conversation storage to a temp dir so tests never touch real data/.
    monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")

    app = app_module.create_app()
    return TestClient(app)


@pytest.fixture
def unlocked_client(client):
    resp = client.post("/unlock", data={"passphrase": ""})
    assert resp.status_code in (200, 303)
    return client


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

    def test_api_route_requires_unlock(self, client):
        resp = client.get("/api/professors")
        assert resp.status_code == 401

    def test_lock_clears_session(self, unlocked_client):
        resp = unlocked_client.post("/lock")
        assert resp.status_code == 200
        resp2 = unlocked_client.get("/api/professors")
        assert resp2.status_code == 401


class TestProfessors:
    def test_lists_configured_professors(self, unlocked_client):
        resp = unlocked_client.get("/api/professors")
        assert resp.status_code == 200
        names = {p["safe_name"] for p in resp.json()["professors"]}
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


class TestConversations:
    def test_new_conversation_then_list(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        assert create.status_code == 200
        conv_id = create.json()["id"]

        listed = unlocked_client.get("/api/conversations", params={"professor": "heller"})
        assert conv_id in {c["id"] for c in listed.json()["conversations"]}

    def test_get_single_conversation(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        got = unlocked_client.get(f"/api/conversations/{conv_id}", params={"professor": "heller"})
        assert got.status_code == 200
        assert got.json()["id"] == conv_id

    def test_get_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.get("/api/conversations/c_missing", params={"professor": "heller"})
        assert resp.status_code == 404

    def test_delete_conversation(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.delete(f"/api/conversations/{conv_id}", params={"professor": "heller"})
        assert resp.status_code == 200
        assert unlocked_client.get(f"/api/conversations/{conv_id}", params={"professor": "heller"}).status_code == 404

    def test_conversations_isolated_per_professor(self, unlocked_client):
        unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        smith_list = unlocked_client.get("/api/conversations", params={"professor": "smith"})
        assert smith_list.json()["conversations"] == []


class TestChat:
    def test_chat_turn_appends_messages_and_returns_usage(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.send_message.return_value = {
            "content": "Hello back!", "model": "gpt-4o",
            "prompt_tokens": 5, "completion_tokens": 7, "cost": 0.001,
        }
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi there", "model": "gpt-4o",
        })
        assert resp.status_code == 200
        conv = resp.json()["conversation"]
        assert conv["messages"][-2] == {
            "role": "user", "content": "Hi there", "timestamp": conv["messages"][-2]["timestamp"],
            "model": None, "prompt_tokens": None, "completion_tokens": None, "cost": None,
        }
        assert conv["messages"][-1]["content"] == "Hello back!"
        assert conv["messages"][-1]["cost"] == 0.001
        assert conv["title"] == "Hi there"

    def test_chat_on_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": "c_missing", "message": "Hi", "model": "gpt-4o",
        })
        assert resp.status_code == 404

    def test_chat_service_failure_becomes_502(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        def _boom(*a, **kw):
            raise RuntimeError("upstream API error")
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _boom)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
        })
        assert resp.status_code == 502
