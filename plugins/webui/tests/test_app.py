"""Integration tests for the webui plugin's FastAPI routes (plugins/webui/src/app.py).

Uses FastAPI's TestClient (backed by httpx) against a fresh app instance
built by create_app() — no real server is started, and no real AI API calls
are made (the /api/chat route's SandboxProcessor is monkeypatched).
"""

from __future__ import annotations

import json
import re
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


class TestModelsEndpoint:
    def test_includes_accepts_sampling_params_flag(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "get_available_models", lambda: ["gpt-4o", "o3-mini"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: m == "gpt-4o")
        monkeypatch.setattr(app_module, "model_has_fixed_parameters", lambda m: m == "o3-mini")
        monkeypatch.setattr(app_module, "model_omit_sampling_params", lambda m: False)

        resp = unlocked_client.get("/api/models", params={"professor": "heller"})
        assert resp.status_code == 200
        by_name = {m["name"]: m for m in resp.json()["models"]}
        assert by_name["gpt-4o"]["accepts_sampling_params"] is True
        assert by_name["o3-mini"]["accepts_sampling_params"] is False

    def test_omit_sampling_params_also_hides_the_controls(self, unlocked_client, monkeypatch):
        # A model can be "not fully fixed" but still on a provider route
        # that rejects temperature/top-p — see model_omit_sampling_params's
        # docstring. Either flag alone should hide the controls.
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "get_available_models", lambda: ["some-model"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: False)
        monkeypatch.setattr(app_module, "model_has_fixed_parameters", lambda m: False)
        monkeypatch.setattr(app_module, "model_omit_sampling_params", lambda m: True)

        resp = unlocked_client.get("/api/models", params={"professor": "heller"})
        assert resp.json()["models"][0]["accepts_sampling_params"] is False


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
            "attachments": [], "api_content": None,
            "kind": "message", "job_id": None, "output_filename": None, "output_path": None,
            "progress_done": None, "progress_total": None, "page_number": None,
        }
        assert conv["messages"][-1]["content"] == "Hello back!"
        assert conv["messages"][-1]["cost"] == 0.001
        assert conv["title"] == "Hi there"

    def test_sampling_overrides_persist_and_are_passed_to_sandbox(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {"type": "done", "content": "ok", "model": "gpt-4o",
             "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0001},
        ])
        fake_sandbox.chat_service.generate_title.return_value = None
        sandbox_cls = MagicMock(return_value=fake_sandbox)
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", sandbox_cls)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
            "temperature": 0.3, "top_p": 0.85, "max_tokens": 1500,
        })
        assert resp.status_code == 200
        sandbox_cls.assert_called_once_with(
            "heller", model="gpt-4o", temperature=0.3, top_p=0.85, max_tokens=1500,
        )

        # And it's saved on the conversation, not just used for this one call.
        conv = unlocked_client.get(
            "/api/conversations/" + conv_id, params={"professor": "heller"}
        ).json()
        assert conv["temperature"] == 0.3
        assert conv["top_p"] == 0.85
        assert conv["max_tokens"] == 1500

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
        instead of an HTTP error status (unlike the old one-shot /api/chat).

        An unexpected error's own text is deliberately *not* forwarded: it's
        written for whoever maintains the installation and can quote internal
        details back to the browser. The professor gets a plain message and a
        reference code instead.
        """
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        def _boom(*a, **kw):
            raise RuntimeError("upstream API error at /etc/secret/path")
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _boom)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        message = events[0]["message"]
        assert "upstream API error" not in message
        assert "/etc/secret/path" not in message
        # A reference code the professor can quote to whoever helps them.
        assert re.search(r"reference [0-9a-f]{8}", message)

    def test_chat_user_facing_error_is_shown_verbatim(self, unlocked_client, monkeypatch):
        """A CLIError is wording meant for the person using the tool (e.g. a
        rate limit or a model they can't access), so it reaches the browser
        unchanged rather than being replaced by the generic message."""
        from src.errors import CLIError

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        def _boom(*a, **kw):
            raise CLIError("Rate limit exceeded: please wait a moment and try again.")
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _boom)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
        })
        events = _parse_sse(resp.text)
        assert events[0]["type"] == "error"
        assert events[0]["message"] == "Rate limit exceeded: please wait a moment and try again."

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
            "attachments": [], "api_content": None,
            "kind": "message", "job_id": None, "output_filename": None, "output_path": None,
            "progress_done": None, "progress_total": None, "page_number": None,
        }

    def test_attachment_becomes_message_attachment_and_api_content(self, unlocked_client, monkeypatch):
        """An attachment sent with a chat turn shows up as a chip (attachments)
        on the saved message, while the actual document text only reaches the
        model via api_content — never the displayed content."""
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {
                "type": "done", "content": "Here's a summary.", "model": "gpt-4o",
                "prompt_tokens": 50, "completion_tokens": 10, "cost": 0.002,
            },
        ])
        fake_sandbox.chat_service.generate_title.return_value = "Report Summary"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id,
            "message": "Summarize this", "model": "gpt-4o",
            "attachment": {"filename": "report.pdf", "text": "Q3 revenue rose 12%.", "char_count": 20},
        })
        events = _parse_sse(resp.text)
        conv = [e for e in events if e["type"] == "done"][0]["conversation"]
        user_msg = conv["messages"][-2]
        assert user_msg["content"] == "Summarize this"
        assert user_msg["attachments"] == [{"filename": "report.pdf", "char_count": 20}]
        assert "Q3 revenue rose 12%." not in user_msg["content"]

        # The model itself must have received the document text — check what
        # was actually passed to stream_message().
        sent_messages = fake_sandbox.chat_service.stream_message.call_args[0][0]
        assert "Q3 revenue rose 12%." in sent_messages[-1]["content"]
        assert "Summarize this" in sent_messages[-1]["content"]

        # Title generation must NOT have received the full document text —
        # only display_messages()'s filename hint (see generate_title()'s
        # docstring on why: no reason to bill a long document just to name
        # the chat).
        # generate_title() is called after the assistant's reply has already
        # been appended, so the user's turn (with the attachment hint) is
        # the second-to-last message, not the last.
        title_messages = fake_sandbox.chat_service.generate_title.call_args[0][0]
        assert not any("Q3 revenue rose 12%." in m["content"] for m in title_messages)
        assert "report.pdf" in title_messages[-2]["content"]

    def test_attachment_only_message_with_blank_text_is_allowed(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {
                "type": "done", "content": "Sure, here's what it says.", "model": "gpt-4o",
                "prompt_tokens": 30, "completion_tokens": 8, "cost": 0.001,
            },
        ])
        fake_sandbox.chat_service.generate_title.return_value = None
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "", "model": "gpt-4o",
            "attachment": {"filename": "notes.txt", "text": "Some notes.", "char_count": 11},
        })
        events = _parse_sse(resp.text)
        conv = [e for e in events if e["type"] == "done"][0]["conversation"]
        assert conv["messages"][-2]["content"] == ""
        assert conv["messages"][-2]["attachments"][0]["filename"] == "notes.txt"
        # Falls back to the attachment's filename when there's no typed text
        # to use as a title source.
        assert conv["title"] == "notes.txt"


class TestUploadAttachment:
    def test_uploads_and_extracts_text(self, unlocked_client):
        resp = unlocked_client.post(
            "/api/attachments",
            data={"professor": "heller"},
            files={"file": ("notes.txt", b"Hello from an uploaded file.", "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "notes.txt"
        assert "Hello from an uploaded file." in body["text"]
        assert body["char_count"] == len(body["text"])

    def test_unsupported_file_type_returns_400(self, unlocked_client):
        resp = unlocked_client.post(
            "/api/attachments",
            data={"professor": "heller"},
            files={"file": ("virus.exe", b"whatever", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "supported file type" in resp.json()["detail"]

    def test_oversized_attachment_returns_400(self, unlocked_client, monkeypatch):
        attachments = sys.modules["_pu_webui_attachments"]
        monkeypatch.setattr(attachments, "MAX_ATTACHMENT_CHARS", 5)
        resp = unlocked_client.post(
            "/api/attachments",
            data={"professor": "heller"},
            files={"file": ("notes.txt", b"This is definitely longer than five characters.", "text/plain")},
        )
        assert resp.status_code == 400
        assert "too long to attach" in resp.json()["detail"]

    def test_requires_unlock(self, client):
        resp = client.post(
            "/api/attachments",
            data={"professor": "heller"},
            files={"file": ("notes.txt", b"hi", "text/plain")},
        )
        assert resp.status_code == 401

    def test_unknown_professor_rejected(self, unlocked_client):
        resp = unlocked_client.post(
            "/api/attachments",
            data={"professor": "nobody"},
            files={"file": ("notes.txt", b"hi", "text/plain")},
        )
        assert resp.status_code == 400


def _fake_plugin(action_id="translate", run_ui_action=None, preview_ui_action=None):
    """A minimal stand-in for a plugin declaring ui_action + run_ui_action —
    mirrors plugins/webui/tests/test_jobs.py's _FakePlugin, duplicated here
    (rather than imported) since this module doesn't otherwise depend on
    that test file.

    ``preview_ui_action`` is only attached as a real method when a callable
    is passed in — matching the optional, ``hasattr``-checked contract
    described in src/runtime/plugin.py, so a test can also exercise the
    "this plugin doesn't implement a preview" path.
    """
    from src.runtime.ui_action import UiAction

    class _Plugin:
        def __init__(self):
            self.ui_action = UiAction(id=action_id, label="Fake action", command=action_id)

        def run_ui_action(self, fields, professor, model, on_progress, output_dir, on_page_text=None):
            return run_ui_action(fields, professor, model, on_progress, output_dir)

    plugin = _Plugin()
    if preview_ui_action is not None:
        plugin.preview_ui_action = lambda fields, professor, model: preview_ui_action(fields, professor, model)
    return plugin


def _wait_for_job_done(client, conversation_id, professor, timeout=2.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        conv = client.get(
            f"/api/conversations/{conversation_id}", params={"professor": professor}
        ).json()
        if conv["active_job_id"] is None:
            return conv
        time.sleep(0.01)
    pytest.fail("Job did not finish within timeout.")


class TestPluginActions:
    def test_lists_actions_from_installed_plugins(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        fake = _fake_plugin(action_id="translate")
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        resp = unlocked_client.get("/api/plugin-actions")
        assert resp.status_code == 200
        actions = resp.json()["actions"]
        assert [a["id"] for a in actions] == ["translate"]
        assert actions[0]["label"] == "Fake action"

    def test_empty_when_no_plugin_declares_one(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {})
        resp = unlocked_client.get("/api/plugin-actions")
        assert resp.json()["actions"] == []

    def test_requires_unlock(self, client):
        resp = client.get("/api/plugin-actions")
        assert resp.status_code == 401


class TestLanguages:
    def test_lists_registered_languages(self, unlocked_client):
        # Real plugins (translation, transcription) register real languages
        # at import time, so this doesn't need a fake — English at least
        # must be present since plugins/translation/plugin.py registers it
        # unconditionally.
        resp = unlocked_client.get("/api/languages")
        assert resp.status_code == 200
        languages = resp.json()["languages"]
        assert {"code": "en", "name": "English"} in languages
        # Sorted by display name, not by code.
        names = [lang["name"] for lang in languages]
        assert names == sorted(names)

    def test_requires_unlock(self, client):
        resp = client.get("/api/languages")
        assert resp.status_code == 401


class TestPluginActionPreview:
    def test_returns_preview_from_plugin(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        from src.runtime.ui_action import UiPromptPreview

        fake = _fake_plugin(
            action_id="translate",
            preview_ui_action=lambda fields, professor, model: UiPromptPreview(
                system_prompt=f"System for {fields.get('target_language')}",
                user_prompt="User prompt text",
                model=model or "default-model",
            ),
        )
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        resp = unlocked_client.post(
            "/api/plugin-actions/translate/preview",
            json={"professor": "heller", "model": "gpt-4o", "fields": {"target_language": "en"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["system_prompt"] == "System for en"
        assert body["user_prompt"] == "User prompt text"
        assert body["model"] == "gpt-4o"

    def test_unavailable_when_plugin_has_no_preview_method(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        fake = _fake_plugin(action_id="translate")  # no preview_ui_action attached
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        resp = unlocked_client.post(
            "/api/plugin-actions/translate/preview",
            json={"professor": "heller", "fields": {}},
        )
        assert resp.status_code == 200
        assert resp.json() == {"available": False}

    def test_unknown_action_id_404s(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {})
        resp = unlocked_client.post(
            "/api/plugin-actions/nope/preview",
            json={"professor": "heller", "fields": {}},
        )
        assert resp.status_code == 404

    def test_preview_exception_reported_as_unavailable_not_500(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]

        def _boom(fields, professor, model):
            raise ValueError("bad field value")

        fake = _fake_plugin(action_id="translate", preview_ui_action=_boom)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        resp = unlocked_client.post(
            "/api/plugin-actions/translate/preview",
            json={"professor": "heller", "fields": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "bad field value" in body["error"]

    def test_requires_unlock(self, client):
        resp = client.post("/api/plugin-actions/translate/preview", json={"professor": "heller", "fields": {}})
        assert resp.status_code == 401


class TestPluginActionExtensionFields:
    """GET /api/plugin-actions/{action_id}/extension-fields — the composer's
    dynamic subsection for whatever a language-extension plugin (e.g.
    translation-ea, transcription-ea) registers via
    register_extension_ui_hooks(), keyed by (action_id, language token) —
    see ExtensionUiHooks's docstring in src/runtime/ui_action.py for why
    action_id is part of the key (two different actions can register the
    same token for unrelated fields)."""

    @pytest.fixture(autouse=True)
    def _isolated_registry(self, monkeypatch):
        from src.runtime import ui_action as ui_action_module
        monkeypatch.setattr(ui_action_module, "_EXTENSION_UI_HOOKS", {})

    def test_returns_empty_list_when_nothing_registered(self, unlocked_client):
        resp = unlocked_client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": "jp"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"fields": []}

    def test_returns_registered_fields_for_matching_token(self, unlocked_client):
        from src.runtime.ui_action import UiField, register_extension_ui_hooks
        register_extension_ui_hooks(
            action_id="translate",
            token="jp",
            fields=[UiField(name="kanbun", label="Use Kanbun conventions", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: None,
        )
        resp = unlocked_client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": "jp"}
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "fields": [{
                "name": "kanbun", "label": "Use Kanbun conventions", "kind": "checkbox",
                "required": False, "choices": None, "group": None, "allow_folder": False,
            }]
        }

    def test_blank_target_language_returns_empty_list(self, unlocked_client):
        from src.runtime.ui_action import UiField, register_extension_ui_hooks
        register_extension_ui_hooks(
            action_id="translate", token="jp",
            fields=[UiField(name="kanbun", label="Kanbun", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: None,
        )
        resp = unlocked_client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": ""}
        )
        assert resp.json() == {"fields": []}

    def test_unmatched_token_returns_empty_list(self, unlocked_client):
        from src.runtime.ui_action import UiField, register_extension_ui_hooks
        register_extension_ui_hooks(
            action_id="translate", token="jp",
            fields=[UiField(name="kanbun", label="Kanbun", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: None,
        )
        resp = unlocked_client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": "zh"}
        )
        assert resp.json() == {"fields": []}

    def test_different_action_with_the_same_token_returns_empty_list(self, unlocked_client):
        # Regression coverage for the collision this key shape fixes:
        # registering "jp" under "translate" must not leak into a request
        # for "transcribe"'s own extension fields for the same token.
        from src.runtime.ui_action import UiField, register_extension_ui_hooks
        register_extension_ui_hooks(
            action_id="translate", token="jp",
            fields=[UiField(name="kanbun", label="Kanbun", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: None,
        )
        resp = unlocked_client.get(
            "/api/plugin-actions/transcribe/extension-fields", params={"target_language": "jp"}
        )
        assert resp.json() == {"fields": []}

    def test_requires_unlock(self, client):
        resp = client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": "jp"}
        )
        assert resp.status_code == 401


def _fake_ui_job_result(output_dir):
    from src.runtime.ui_action import UiJobResult
    out = f"{output_dir}/out.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("done")
    return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")


class TestStartJob:
    def test_start_job_returns_running_status(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        # A real run_ui_action always returns a UiJobResult (never None) —
        # matters here even though this test only checks the immediate
        # response, since the background thread runs to completion inside
        # this same process regardless of whether the test waits for it.
        fake = _fake_plugin(
            action_id="translate",
            run_ui_action=lambda fields, professor, model, on_progress, output_dir: _fake_ui_job_result(output_dir),
        )
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        _wait_for_job_done(unlocked_client, conv_id, "heller")

    def test_uploaded_file_saved_and_passed_as_file_path(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        received = {}

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
            files={"files": ("doc.txt", b"some document text", "text/plain")},
        )
        assert resp.status_code == 200
        _wait_for_job_done(unlocked_client, conv_id, "heller")
        assert received["file_name"] == "doc.txt"
        assert received["file_path"].endswith("doc.txt")
        import os
        assert os.path.exists(received["file_path"])

    def test_multiple_uploaded_files_saved_into_one_folder(self, unlocked_client, monkeypatch):
        # A professor picking several images (or a whole folder, on a
        # browser that supports it) at once for an allow_folder field —
        # see UiField.allow_folder's docstring. All uploads land in one
        # directory, and file_path points at that directory the same way
        # it would for a CLI user pointing -i at a folder directly.
        app_module = sys.modules["_pu_webui_app"]
        received = {}

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="transcribe", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"transcribe": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "transcribe",
                "fields_json": json.dumps({"target_language": "en"}),
            },
            files=[
                ("files", ("page1.jpg", b"fake image bytes 1", "image/jpeg")),
                ("files", ("page2.jpg", b"fake image bytes 2", "image/jpeg")),
            ],
        )
        assert resp.status_code == 200
        _wait_for_job_done(unlocked_client, conv_id, "heller")

        import os
        assert received["file_name"] == "2 images"
        assert os.path.isdir(received["file_path"])
        assert sorted(os.listdir(received["file_path"])) == ["page1.jpg", "page2.jpg"]

    def test_no_files_leaves_fields_untouched(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        received = {}

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
        )
        assert resp.status_code == 200
        _wait_for_job_done(unlocked_client, conv_id, "heller")
        assert "file_path" not in received
        assert "file_name" not in received

    def test_unknown_action_returns_400(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {})
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": conv_id, "action_id": "translate", "fields_json": "{}"},
        )
        assert resp.status_code == 400

    def test_missing_conversation_returns_404(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        fake = _fake_plugin(run_ui_action=lambda *a: None)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})
        resp = unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": "c_missing", "action_id": "translate", "fields_json": "{}"},
        )
        assert resp.status_code == 404

    def test_conversation_already_busy_returns_409(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        conversation = sys.modules["_pu_webui_conversation"]
        fake = _fake_plugin(run_ui_action=lambda *a: None)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        store = conversation.ConversationStore("heller", base_dir=conversation.CONVERSATIONS_DIR)
        conv = store.load(conv_id)
        conv.active_job_id = "job_existing"
        store.save(conv)

        resp = unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": conv_id, "action_id": "translate", "fields_json": "{}"},
        )
        assert resp.status_code == 409

    def test_invalid_fields_json_returns_400(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        fake = _fake_plugin(run_ui_action=lambda *a: None)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": "not json",
            },
        )
        assert resp.status_code == 400

    def test_requires_unlock(self, client):
        resp = client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": "c_1", "action_id": "translate", "fields_json": "{}"},
        )
        assert resp.status_code == 401


class TestChatBlockedWhileJobRunning:
    def test_chat_returns_409_when_conversation_has_active_job(self, unlocked_client):
        conversation = sys.modules["_pu_webui_conversation"]
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        store = conversation.ConversationStore("heller", base_dir=conversation.CONVERSATIONS_DIR)
        conv = store.load(conv_id)
        conv.active_job_id = "job_running"
        store.save(conv)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "hi", "model": "gpt-4o",
        })
        assert resp.status_code == 409


class TestJobOutputDownload:
    def _run_job_to_completion(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/translated.docx"
            with open(out, "w", encoding="utf-8") as f:
                f.write("translated content")
            return UiJobResult(output_path=out, output_filename="translated.docx", summary="Translated it.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
        )
        conv = _wait_for_job_done(unlocked_client, conv_id, "heller")
        job_id = next(m["job_id"] for m in conv["messages"] if m["kind"] == "job_result")
        return conv_id, job_id

    def test_downloads_the_finished_file(self, unlocked_client, monkeypatch):
        conv_id, job_id = self._run_job_to_completion(unlocked_client, monkeypatch)
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/job-outputs/{job_id}", params={"professor": "heller"}
        )
        assert resp.status_code == 200
        assert resp.content == b"translated content"

    def test_unknown_job_id_returns_404(self, unlocked_client, monkeypatch):
        conv_id, _ = self._run_job_to_completion(unlocked_client, monkeypatch)
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/job-outputs/job_bogus", params={"professor": "heller"}
        )
        assert resp.status_code == 404

    def test_requires_unlock(self, client):
        resp = client.get("/api/conversations/c_1/job-outputs/job_1", params={"professor": "heller"})
        assert resp.status_code == 401


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


class TestExportConversation:
    @pytest.fixture
    def conv_id(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        return create.json()["id"]

    @pytest.mark.parametrize(("fmt", "content_type"), [
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf", "application/pdf"),
        ("md", "text/markdown"),
    ])
    def test_exports_each_supported_format(self, unlocked_client, conv_id, fmt, content_type):
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/export",
            params={"professor": "heller", "format": fmt},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(content_type)
        assert len(resp.content) > 0

    def test_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.get(
            "/api/conversations/c_missing/export",
            params={"professor": "heller", "format": "docx"},
        )
        assert resp.status_code == 404

    def test_unsupported_format_400s(self, unlocked_client, conv_id):
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/export",
            params={"professor": "heller", "format": "exe"},
        )
        assert resp.status_code == 400

    def test_requires_unlock(self, client):
        resp = client.get(
            "/api/conversations/c_1/export", params={"professor": "heller", "format": "docx"}
        )
        assert resp.status_code == 401


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
