"""Integration tests for the webui plugin's FastAPI routes (plugins/webui/src/app.py).

Uses FastAPI's TestClient (backed by httpx) against a fresh app instance
built by create_app() — no real server is started, and no real AI API calls
are made (the /api/chat route's SandboxProcessor is monkeypatched).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.settings as core_settings_mod
import src.settings_store as settings_store_mod
from src.config import (
    load_professor_config as _real_load_professor_config,
)


@pytest.fixture(autouse=True)
def _configured_professors(monkeypatch):
    """Two fake professors, matching the {safe_name: {...}} shape load_professor_config() returns."""
    fake_config = {
        "heller": {"name": "Heller", "key": "sk-heller", "backup_key": None, "safe_name": "heller"},
        "smith": {"name": "Smith", "key": "sk-smith", "backup_key": None, "safe_name": "smith"},
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


class TestRenameConversation:
    def test_renames_conversation(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.patch(f"/api/conversations/{conv_id}", json={
            "professor": "heller", "title": "My Custom Title",
        })
        assert resp.status_code == 200
        assert resp.json()["title"] == "My Custom Title"

        got = unlocked_client.get(f"/api/conversations/{conv_id}", params={"professor": "heller"})
        assert got.json()["title"] == "My Custom Title"

    def test_rename_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.patch("/api/conversations/c_missing", json={
            "professor": "heller", "title": "Anything",
        })
        assert resp.status_code == 404

    def test_blank_title_rejected(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.patch(f"/api/conversations/{conv_id}", json={
            "professor": "heller", "title": "   ",
        })
        assert resp.status_code == 400

    def test_title_is_trimmed_and_truncated(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.patch(f"/api/conversations/{conv_id}", json={
            "professor": "heller", "title": "  " + ("x" * 100) + "  ",
        })
        assert resp.json()["title"] == "x" * 80

    def test_rename_requires_unlock(self, client):
        resp = client.patch("/api/conversations/c_anything", json={"professor": "heller", "title": "x"})
        assert resp.status_code == 401


def _parse_sse(text: str) -> list[dict]:
    """Turn a raw "data: {...}\\n\\n" SSE response body into a list of parsed event dicts."""
    events = []
    for block in text.strip().split("\n\n"):
        for line in block.strip().split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


class TestChat:
    def test_chat_turn_streams_deltas_then_done_with_usage(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {"type": "delta", "text": "Hello "},
            {"type": "delta", "text": "back!"},
            {
                "type": "done", "content": "Hello back!", "model": "gpt-4o",
                "prompt_tokens": 5, "completion_tokens": 7, "cost": 0.001,
            },
        ])
        # No title-generation call configured -> falls back to the literal
        # opening words, same as generate_title() failing for a real reason.
        # See test_first_turn_uses_generated_title_when_available for the
        # AI-generated-title path.
        fake_sandbox.chat_service.generate_title.return_value = None
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi there", "model": "gpt-4o",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        deltas = [e for e in events if e["type"] == "delta"]
        assert [d["text"] for d in deltas] == ["Hello ", "back!"]

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        conv = done_events[0]["conversation"]
        assert conv["messages"][-2] == {
            "role": "user", "content": "Hi there", "timestamp": conv["messages"][-2]["timestamp"],
            "model": None, "prompt_tokens": None, "completion_tokens": None, "cost": None,
        }
        assert conv["messages"][-1]["content"] == "Hello back!"
        assert conv["messages"][-1]["cost"] == 0.001
        assert conv["title"] == "Hi there"

    def test_first_turn_uses_generated_title_when_available(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {
                "type": "done", "content": "Sure, here's a plan.", "model": "gpt-4o",
                "prompt_tokens": 5, "completion_tokens": 7, "cost": 0.001,
            },
        ])
        fake_sandbox.chat_service.generate_title.return_value = "Weekend Trip Planning"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id,
            "message": "Help me plan a weekend trip", "model": "gpt-4o",
        })
        events = _parse_sse(resp.text)
        conv = [e for e in events if e["type"] == "done"][0]["conversation"]
        assert conv["title"] == "Weekend Trip Planning"

    def test_later_turn_does_not_regenerate_title(self, unlocked_client, monkeypatch):
        """Only the first exchange should trigger title generation — once a
        conversation has a real title (from either the AI or the fallback),
        later turns must not silently overwrite it."""
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        # side_effect (not return_value) so each call gets its own fresh
        # iterator — a shared return_value would be exhausted by the first
        # /api/chat call, leaving the second call's stream_message() loop
        # with nothing left to iterate.
        fake_sandbox.chat_service.stream_message.side_effect = lambda *a, **kw: iter([
            {
                "type": "done", "content": "ok", "model": "gpt-4o",
                "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0001,
            },
        ])
        fake_sandbox.chat_service.generate_title.return_value = "First Title"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "first", "model": "gpt-4o",
        })
        fake_sandbox.chat_service.generate_title.return_value = "Should Not Be Used"
        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "second", "model": "gpt-4o",
        })
        events = _parse_sse(resp.text)
        conv = [e for e in events if e["type"] == "done"][0]["conversation"]
        assert conv["title"] == "First Title"

    def test_chat_on_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": "c_missing", "message": "Hi", "model": "gpt-4o",
        })
        assert resp.status_code == 404

    def test_chat_service_failure_emits_error_event(self, unlocked_client, monkeypatch):
        """Once streaming has begun the HTTP status can't change, so a failure
        partway through the model call surfaces as an in-band SSE error event
        instead of an HTTP error status (unlike the old one-shot /api/chat)."""
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        def _boom(*a, **kw):
            raise RuntimeError("upstream API error")
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _boom)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "upstream API error" in events[0]["message"]

    def test_user_message_is_saved_even_if_the_model_call_fails(self, unlocked_client, monkeypatch):
        """The user's message is persisted before the model is called at all,
        so a failed turn doesn't lose what was actually sent."""
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        def _boom(*a, **kw):
            raise RuntimeError("upstream API error")
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _boom)

        unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
        })

        got = unlocked_client.get(f"/api/conversations/{conv_id}", params={"professor": "heller"})
        assert got.json()["messages"][-1] == {
            "role": "user", "content": "Hi", "timestamp": got.json()["messages"][-1]["timestamp"],
            "model": None, "prompt_tokens": None, "completion_tokens": None, "cost": None,
        }


# ---------------------------------------------------------------------------
# /settings and /api/settings/* — see plugins/webui/src/templates/settings.html
# ---------------------------------------------------------------------------

@pytest.fixture
def settings_env(monkeypatch, tmp_path):
    """Redirect .settings to a tmp file and restore the real, settings_store-backed
    load_professor_config for these tests — undoing the module-level
    _configured_professors fixture's fixed fake dict, since these tests need to
    see data actually persisted through src/settings_store.py, not a stub."""
    monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", tmp_path / ".settings")
    app_module = sys.modules["_pu_webui_app"]
    monkeypatch.setattr(app_module, "load_professor_config", _real_load_professor_config)
    return tmp_path


class TestSettingsPage:

    def test_page_loads_when_unlocked(self, client, settings_env):
        client.post("/unlock", data={"passphrase": ""})
        resp = client.get("/settings")
        assert resp.status_code == 200

    def test_page_requires_unlock(self, client, settings_env):
        resp = client.get("/settings", follow_redirects=False)
        assert "Unlock" in resp.text or "passphrase" in resp.text.lower()

    def test_api_requires_unlock(self, client, settings_env):
        resp = client.get("/api/settings")
        assert resp.status_code == 401

    def test_no_professors_first_run_order(self, unlocked_client, settings_env):
        data = unlocked_client.get("/api/settings").json()
        assert data["has_professors"] is False
        assert data["order"] == ["professors", "external_sources", "webui", "shared"]
        assert data["professors"] == []

    def test_index_redirects_to_settings_with_no_professors(self, unlocked_client, settings_env):
        resp = unlocked_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"


class TestSettingsProfessors:

    def test_add_professor(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={
            "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 200
        assert resp.json()["safe_name"] == "jeff_heller"

        data = unlocked_client.get("/api/settings").json()
        assert data["has_professors"] is True
        assert data["order"] == ["shared", "professors", "webui", "external_sources"]
        prof = data["professors"][0]
        assert prof == {
            "safe_name": "jeff_heller", "name": "Jeff Heller",
            "has_key": True, "has_backup_key": False,
        }

    def test_add_professor_with_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={
            "name": "Jeff Heller", "key": "sk-primary", "backup_key": "sk-backup",
        })
        prof = unlocked_client.get("/api/settings").json()["professors"][0]
        assert prof["has_backup_key"] is True

    def test_add_professor_blank_key_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={"name": "Jeff Heller", "key": "  "})
        assert resp.status_code == 400

    def test_add_duplicate_professor_rejected(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"name": "Jeff Heller", "key": "sk-1"})
        resp = unlocked_client.post("/api/settings/professors", json={"name": "Jeff Heller", "key": "sk-2"})
        assert resp.status_code == 400

    def test_remove_professor(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"name": "Jeff Heller", "key": "sk-1"})
        resp = unlocked_client.delete("/api/settings/professors/jeff_heller")
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["professors"] == []

    def test_remove_unknown_professor_404s(self, unlocked_client, settings_env):
        resp = unlocked_client.delete("/api/settings/professors/nobody")
        assert resp.status_code == 404

    def test_update_professor_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post("/api/settings/professors/jeff_heller/key", json={"key": "sk-new"})
        assert resp.status_code == 200
        assert settings_store_mod.get_professors()["jeff_heller"]["key"] == "sk-new"

    def test_update_professor_key_blank_rejected(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post("/api/settings/professors/jeff_heller/key", json={"key": "  "})
        assert resp.status_code == 400

    def test_set_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post(
            "/api/settings/professors/jeff_heller/backup-key", json={"backup_key": "sk-backup"}
        )
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["professors"][0]["has_backup_key"] is True

    def test_clear_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={
            "name": "Jeff Heller", "key": "sk-old", "backup_key": "sk-backup",
        })
        unlocked_client.post("/api/settings/professors/jeff_heller/backup-key", json={"backup_key": None})
        assert unlocked_client.get("/api/settings").json()["professors"][0]["has_backup_key"] is False


class TestSettingsPassphrase:

    def test_set_passphrase(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/passphrase", json={"passphrase": "hunter2", "confirm": "hunter2"}
        )
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["webui"]["passphrase_configured"] is True

    def test_mismatched_passphrase_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/passphrase", json={"passphrase": "hunter2", "confirm": "different"}
        )
        assert resp.status_code == 400

    def test_empty_passphrase_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/passphrase", json={"passphrase": "", "confirm": ""})
        assert resp.status_code == 400

    def test_stored_value_is_hashed_not_plaintext(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/passphrase", json={"passphrase": "hunter2", "confirm": "hunter2"})
        stored = settings_store_mod.get_value("webui.passphrase_hash")
        assert stored != "hunter2"

    def test_clear_passphrase(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/passphrase", json={"passphrase": "hunter2", "confirm": "hunter2"})
        resp = unlocked_client.delete("/api/settings/passphrase")
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["webui"]["passphrase_configured"] is False


class TestSettingsValues:

    def test_set_shared_settings_path(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "shared_settings.path", "value": "/tmp/shared.toml"}
        )
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["shared"]["shared_settings_path"] == "/tmp/shared.toml"

    def test_generate_session_secret(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/values/generate", json={"path": "webui.session_secret"})
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["webui"]["session_secret_set"] is True

    def test_unset_value(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/values", json={"path": "shared_settings.path", "value": "/tmp/x.toml"})
        resp = unlocked_client.delete("/api/settings/values", params={"path": "shared_settings.path"})
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["shared"]["shared_settings_path"] is None

    def test_unregistered_path_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "professors.heller.key", "value": "sk-sneaky"}
        )
        assert resp.status_code == 400

    def test_passphrase_hash_path_rejected_via_generic_endpoint(self, unlocked_client, settings_env):
        """webui.passphrase_hash must only ever be set pre-hashed, via /api/settings/passphrase."""
        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "webui.passphrase_hash", "value": "not-a-real-hash"}
        )
        assert resp.status_code == 400

    def test_blank_value_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "shared_settings.path", "value": "   "}
        )
        assert resp.status_code == 400

    def test_endpoint_credential_settable(self, unlocked_client, settings_env, monkeypatch):
        fake_endpoints = {"hpc_cluster": {"name": "HPC Cluster", "base_url": "http://x.internal/v1"}}
        # ENDPOINTS is imported by value into both src.settings (real) and app.py's
        # own namespace, so both copies need patching for list_apis() (used inside
        # list_optional_env_fields()) and _settings_snapshot()'s own loop to agree.
        monkeypatch.setattr(core_settings_mod, "ENDPOINTS", fake_endpoints)
        monkeypatch.setattr(sys.modules["_pu_webui_app"], "ENDPOINTS", fake_endpoints)

        data = unlocked_client.get("/api/settings").json()
        ep = data["shared"]["endpoints"][0]
        assert ep == {
            "name": "hpc_cluster", "display_name": "HPC Cluster", "base_url": "http://x.internal/v1",
            "openai_compatible": False, "default_model": None, "timeout": 30,
            "credential_path": "endpoints.hpc_cluster.key", "key_set": False,
        }

        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "endpoints.hpc_cluster.key", "value": "sk-cluster"}
        )
        assert resp.status_code == 200
        ep2 = unlocked_client.get("/api/settings").json()["shared"]["endpoints"][0]
        assert ep2["key_set"] is True


class TestSettingsSources:

    def test_add_and_list_read_only_source(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/sources", json={
            "label": "Prof. Smith", "path": "/tmp/smith-data", "mode": "read-only",
        })
        assert resp.status_code == 200
        sources = unlocked_client.get("/api/settings").json()["sources"]["external"]
        assert sources == [{"label": "Prof. Smith", "path": "/tmp/smith-data", "mode": "read-only", "professor": None}]

    def test_shared_write_without_professor_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/sources", json={
            "label": "Prof. Smith", "path": "/tmp/smith-data", "mode": "shared-write",
        })
        assert resp.status_code == 400

    def test_shared_write_with_professor_accepted(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/sources", json={
            "label": "This installation", "path": "/tmp/shared", "mode": "shared-write", "professor": "smith",
        })
        assert resp.status_code == 200

    def test_remove_source(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/sources", json={
            "label": "Prof. Smith", "path": "/tmp/smith-data", "mode": "read-only",
        })
        resp = unlocked_client.delete("/api/settings/sources", params={"label": "Prof. Smith"})
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["sources"]["external"] == []

    def test_remove_unknown_source_404s(self, unlocked_client, settings_env):
        resp = unlocked_client.delete("/api/settings/sources", params={"label": "Nobody"})
        assert resp.status_code == 404
