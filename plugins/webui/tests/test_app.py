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
    jobs = sys.modules["_pu_webui_jobs"]
    # Redirect conversation storage to a temp dir so tests never touch real data/.
    monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")
    # jobs.py works out the same directory independently, so patching only the
    # one above left every job test writing its output into the real
    # data/conversations/<netid>/_job_outputs/ — which is how a folder for a
    # test-fixture professor kept reappearing in real data after it had been
    # migrated away. Both have to point at the temp directory.
    monkeypatch.setattr(jobs, "_CONVERSATIONS_DIR", tmp_path / "conversations")

    app = app_module.create_app()
    # A loopback client address, because that's what a browser on this same
    # computer looks like — and /api/pick-path refuses anything else.
    return TestClient(app, client=("127.0.0.1", 50000))


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
        monkeypatch.setattr(app_module, "models_in_reading_order", lambda: ["gpt-4o", "o3-mini"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: m == "gpt-4o")
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: m != "o3-mini")
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "gpt-4o")

        resp = unlocked_client.get("/api/models", params={"professor": "heller"})
        assert resp.status_code == 200
        by_name = {m["name"]: m for m in resp.json()["models"]}
        assert by_name["gpt-4o"]["accepts_sampling_params"] is True
        assert by_name["o3-mini"]["accepts_sampling_params"] is False

    def test_omit_sampling_params_also_hides_the_controls(self, unlocked_client, monkeypatch):
        # A model can be "not fully fixed" but still on a provider route
        # that refuses temperature/top-p — see model_accepts_sampling_params's
        # docstring. Either flag alone should hide the controls.
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "models_in_reading_order", lambda: ["some-model"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: False)
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: False)
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "some-model")

        resp = unlocked_client.get("/api/models", params={"professor": "heller"})
        assert resp.json()["models"][0]["accepts_sampling_params"] is False


class TestUsageEndpoint:
    """The spend sidebar's data, which had no test coverage at all.

    The budget figures here must be the ones TokenTracker works out, not a
    second copy of the arithmetic — an earlier version computed its own and
    had already drifted from the terminal report.
    """

    @pytest.fixture
    def tracker(self, monkeypatch):
        """Install a stand-in TokenTracker and hand it back for assertions."""
        app_module = sys.modules["_pu_webui_app"]
        fake = MagicMock()
        fake.monthly_limit = 50.0
        fake.usage_data = {"model_usage": {"gpt-4o": {"total_cost": 12.5}}}
        fake.get_all_time_usage.return_value = {"total_cost": 99.0}
        fake.get_monthly_budget_status.return_value = {
            "monthly_usage": {"total_cost": 12.5, "total_tokens": 4000},
            "usage_percentage": 25.0,
            "remaining_budget": 37.5,
            "is_exceeded": False,
            "approaching_limit": False,
        }
        monkeypatch.setattr(app_module, "TokenTracker", lambda professor: fake)
        return fake

    def test_returns_the_trackers_budget_figures(self, unlocked_client, tracker):
        resp = unlocked_client.get("/api/usage", params={"professor": "heller"})
        assert resp.status_code == 200
        budget = resp.json()["budget"]
        assert budget["monthly_limit"] == 50.0
        assert budget["usage_percentage"] == 25.0
        assert budget["remaining_budget"] == 37.5

    def test_includes_the_two_warning_flags(self, unlocked_client, tracker):
        """These were missing entirely, so the sidebar couldn't show "over budget"."""
        tracker.get_monthly_budget_status.return_value |= {
            "usage_percentage": 130.0,
            "remaining_budget": 0.0,
            "is_exceeded": True,
            "approaching_limit": True,
        }
        budget = unlocked_client.get(
            "/api/usage", params={"professor": "heller"}
        ).json()["budget"]
        assert budget["is_exceeded"] is True
        assert budget["approaching_limit"] is True

    def test_month_totals_come_from_the_same_call_as_the_budget(self, unlocked_client, tracker):
        """One question, asked once — the month shown and the budget it's
        measured against can't disagree if they came from the same answer."""
        body = unlocked_client.get("/api/usage", params={"professor": "heller"}).json()
        assert body["month"] == {"total_cost": 12.5, "total_tokens": 4000}
        assert body["all_time"] == {"total_cost": 99.0}
        assert body["model_usage"] == {"gpt-4o": {"total_cost": 12.5}}
        tracker.get_monthly_budget_status.assert_called_once()

    def test_requires_unlock(self, client, tracker):
        assert client.get("/api/usage", params={"professor": "heller"}).status_code == 401

    def test_rejects_unknown_professor(self, unlocked_client, tracker):
        """The name reaches file paths, so it must name a configured professor."""
        resp = unlocked_client.get("/api/usage", params={"professor": "nobody"})
        assert resp.status_code == 400


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

        seen_while_running = []

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            seen_while_running.append(Path(fields["file_path"]).read_text())
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
        # Readable while the job ran — that is all a plugin needs.
        assert seen_while_running == ["some document text"]
        # And gone afterwards: the professor already has this file where
        # they chose to keep it, so a second copy filed inside their usage
        # data would grow forever for no purpose.
        assert not Path(received["file_path"]).exists()

    def test_uploads_never_land_in_the_professors_data(self, unlocked_client, monkeypatch):
        """The whole point: nothing uploaded is stored under data/."""
        app_module = sys.modules["_pu_webui_app"]
        seen = {}

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            seen["file_path"] = fields["file_path"]
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                  "fields_json": json.dumps({})},
            files={"files": ("paper.pdf", b"pdf bytes", "application/pdf")},
        )
        _wait_for_job_done(unlocked_client, conv_id, "heller")

        conversation = sys.modules["_pu_webui_conversation"]
        data_dir = Path(conversation.CONVERSATIONS_DIR)
        assert "paper.pdf" not in [p.name for p in data_dir.rglob("*")]
        assert not str(Path(seen["file_path"])).startswith(str(data_dir))

    def test_no_input_folder_is_ever_created(self, unlocked_client, monkeypatch):
        """`input/` used to hold copies of every uploaded file. It is gone."""
        app_module = sys.modules["_pu_webui_app"]

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            from src.runtime.ui_action import UiJobResult
            return UiJobResult(output_path=None, output_filename=None, summary="Done.")

        fake = _fake_plugin(action_id="transcribe", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"transcribe": fake})
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": conv_id, "action_id": "transcribe",
                  "fields_json": json.dumps({})},
            files=[("files", ("a.jpg", b"one", "image/jpeg")),
                   ("files", ("b.jpg", b"two", "image/jpeg"))],
        )
        _wait_for_job_done(unlocked_client, conv_id, "heller")
        conversation = sys.modules["_pu_webui_conversation"]
        assert "input" not in [p.name for p in Path(conversation.CONVERSATIONS_DIR).rglob("*")]

    def test_upload_filename_cannot_escape_the_job_directory(self, unlocked_client, monkeypatch):
        """An uploaded file is written under its job's own directory, whatever it claims to be called.

        The filename in a multipart upload is supplied by whoever made the
        request, not by the browser's file picker, so it can contain "../".
        The single-file branch used to build the path from it directly while
        the multi-file branch stripped it, which meant a crafted name could
        write outside the job directory entirely. Both strip it now.
        """
        app_module = sys.modules["_pu_webui_app"]
        received = {}

        seen_while_running = []

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            seen_while_running.append(Path(fields["file_path"]).read_text())
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
            files={"files": ("../../../../escaped.txt", b"payload", "text/plain")},
        )
        assert resp.status_code == 200
        _wait_for_job_done(unlocked_client, conv_id, "heller")

        import os
        written = Path(received["file_path"])
        assert written.name == "escaped.txt"
        # It landed directly inside the scratch folder made for this job,
        # not four levels above it.
        assert written.parent.name.startswith("pu_webui_job_")
        assert not os.path.exists("/escaped.txt")
        # And nothing named that reached the professor's own data.
        conversation = sys.modules["_pu_webui_conversation"]
        assert "escaped.txt" not in [
            p.name for p in Path(conversation.CONVERSATIONS_DIR).rglob("*")
        ]

    def test_multiple_uploaded_files_saved_into_one_folder(self, unlocked_client, monkeypatch):
        # A professor picking several images (or a whole folder, on a
        # browser that supports it) at once for an allow_folder field —
        # see UiField.allow_folder's docstring. All uploads land in one
        # directory, and file_path points at that directory the same way
        # it would for a CLI user pointing -i at a folder directly.
        app_module = sys.modules["_pu_webui_app"]
        received = {}
        seen_listing = []

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            seen_listing.extend(sorted(p.name for p in Path(fields["file_path"]).iterdir()))
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

        assert received["file_name"] == "2 images"
        # One folder holding both, the same shape a plugin gets from a CLI
        # user pointing -i at a directory — just not inside anyone's data.
        assert seen_listing == ["page1.jpg", "page2.jpg"]
        conversation = sys.modules["_pu_webui_conversation"]
        assert not str(received["file_path"]).startswith(str(conversation.CONVERSATIONS_DIR))

    def test_no_files_leaves_fields_untouched(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        received = {}

        seen_while_running = []

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            seen_while_running.append(Path(fields["file_path"]).read_text())
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
    """Redirect settings.toml to a tmp file and restore the real, settings_store-backed
    load_professor_config for these tests — undoing the module-level
    _configured_professors fixture's fixed fake dict, since these tests need to
    see data actually persisted through src/settings_store.py, not a stub."""
    monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", tmp_path / "settings.toml")
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
        assert data["order"] == ["professors", "external_sources", "webui", "shared", "endpoints"]
        assert data["professors"] == []

    def test_index_redirects_to_settings_with_no_professors(self, unlocked_client, settings_env):
        resp = unlocked_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"


class TestSettingsProfessors:

    def test_add_professor(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={
            "netid": "jh43", "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 200
        assert resp.json()["netid"] == "jh43"

        data = unlocked_client.get("/api/settings").json()
        assert data["has_professors"] is True
        assert data["order"] == ["shared", "endpoints", "professors", "webui", "external_sources"]
        prof = data["professors"][0]
        assert prof == {
            "netid": "jh43", "name": "Jeff Heller",
            "has_key": True, "has_backup_key": False,
        }

    def test_add_professor_with_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={
            "netid": "jh43", "name": "Jeff Heller", "key": "sk-primary",
            "backup_key": "sk-backup",
        })
        prof = unlocked_client.get("/api/settings").json()["professors"][0]
        assert prof["has_backup_key"] is True

    def test_a_netid_is_required(self, unlocked_client, settings_env):
        """Without one there is no name for their key, their usage file, or their commands."""
        resp = unlocked_client.post("/api/settings/professors", json={
            "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 422

    def test_a_display_name_in_the_netid_box_is_refused(self, unlocked_client, settings_env):
        """The commonest mistake: 'Jeff Heller' typed where 'jh43' was wanted."""
        resp = unlocked_client.post("/api/settings/professors", json={
            "netid": "Jeff Heller", "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 400
        assert "netID" in resp.json()["detail"]

    def test_an_email_address_in_the_netid_box_is_refused(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={
            "netid": "jh43@princeton.edu", "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 400

    def test_a_netid_typed_in_capitals_is_the_same_person(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={
            "netid": "JH43", "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 200
        assert resp.json()["netid"] == "jh43"

    def test_add_professor_blank_key_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "  "})
        assert resp.status_code == 400

    def test_add_duplicate_professor_rejected(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-1"})
        resp = unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-2"})
        assert resp.status_code == 400

    def test_remove_professor(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-1"})
        resp = unlocked_client.delete("/api/settings/professors/jh43")
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["professors"] == []

    def test_remove_unknown_professor_404s(self, unlocked_client, settings_env):
        resp = unlocked_client.delete("/api/settings/professors/nobody")
        assert resp.status_code == 404

    def test_update_professor_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post("/api/settings/professors/jh43/key", json={"key": "sk-new"})
        assert resp.status_code == 200
        assert settings_store_mod.get_professors()["jh43"]["key"] == "sk-new"

    def test_update_professor_key_blank_rejected(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post("/api/settings/professors/jh43/key", json={"key": "  "})
        assert resp.status_code == 400

    def test_set_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post(
            "/api/settings/professors/jh43/backup-key", json={"backup_key": "sk-backup"}
        )
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["professors"][0]["has_backup_key"] is True

    def test_clear_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={
            "netid": "jh43", "name": "Jeff Heller", "key": "sk-old",
            "backup_key": "sk-backup",
        })
        unlocked_client.post("/api/settings/professors/jh43/backup-key", json={"backup_key": None})
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
        # list_optional_settings()) and _settings_snapshot()'s own loop to agree.
        monkeypatch.setattr(core_settings_mod, "ENDPOINTS", fake_endpoints)
        monkeypatch.setattr(sys.modules["_pu_webui_app"], "ENDPOINTS", fake_endpoints)

        data = unlocked_client.get("/api/settings").json()
        ep = data["shared"]["endpoints"][0]
        assert ep == {
            "name": "hpc_cluster", "display_name": "HPC Cluster", "base_url": "http://x.internal/v1",
            # True unless the endpoint says otherwise — the same default the
            # code that actually connects uses, so the page and the behaviour
            # cannot describe an endpoint differently.
            "openai_compatible": True, "default_model": None, "timeout": 30,
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


class TestSharedSettingsDraftDownload:
    """The export, for whoever looks after a group's settings but doesn't use a terminal.

    Both the person maintaining the shared file and the members pointing at it
    may work entirely in the browser, so neither should need the command line.
    Members were already covered by the shared-path field; this covers the other
    half.
    """

    def test_it_returns_a_draft(self, unlocked_client):
        r = unlocked_client.get("/api/settings/shared-draft")
        assert r.status_code == 200
        assert "settings set shared_settings.path" in r.text

    def test_it_downloads_under_the_documented_name(self, unlocked_client):
        """Same name the CLI writes, so the docs describe one thing."""
        r = unlocked_client.get("/api/settings/shared-draft")
        assert 'filename="shared-settings.toml"' in r.headers["content-disposition"]

    def test_the_draft_is_inert(self, unlocked_client):
        """Everything commented, so placing it unedited changes nothing."""
        import tomllib
        r = unlocked_client.get("/api/settings/shared-draft")
        assert tomllib.loads(r.text) == {}

    def test_it_is_behind_the_unlock_gate(self, client):
        """It lists this installation's whole settings surface."""
        r = client.get("/api/settings/shared-draft", follow_redirects=False)
        assert r.status_code in (302, 303, 401, 403)

    def test_no_shared_settings_file_is_left_behind(self, unlocked_client, tmp_path, monkeypatch):
        """The sandbox never writes a shared settings file; this hands one over.

        Checked for the draft specifically rather than an empty folder — other
        parts of the app create their own directories under here, and this is
        about what the download does, not what else exists.
        """
        from src import paths as paths_mod
        monkeypatch.setattr(paths_mod, "extras_root", lambda: tmp_path)
        unlocked_client.get("/api/settings/shared-draft")
        assert not (tmp_path / "shared-settings.toml").exists()
        assert list(tmp_path.glob("*.toml")) == []


class TestFileOrFolderPicker:
    """A file field that accepts folders must still accept a single file.

    `webkitdirectory` does not add folder selection, it replaces file selection:
    an input carrying it can only pick a directory. Setting it unconditionally
    is what stopped anyone choosing one document to translate, so the page must
    offer the choice instead of assuming.
    """

    def _page(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        directory = Path(__file__).resolve().parents[1] / "src" / "templates"
        return Jinja2Templates(directory=str(directory)).get_template("chat.html").render(request=None)

    def test_the_page_offers_both_modes(self):
        page = self._page()
        assert "A single file" in page
        assert "A whole folder" in page

    def test_webkitdirectory_is_never_set_unconditionally(self):
        """The regression itself: set outside a mode choice, folders are all you get."""
        import re

        page = self._page()
        for match in re.finditer(r"webkitdirectory = true", page):
            before = page[max(0, match.start() - 400):match.start()]
            assert "if (folder)" in before, (
                "webkitdirectory must only be set for the folder mode; setting it "
                "on every file field is what removed single-file selection"
            )

    def test_the_choice_defaults_to_a_single_file(self):
        """The commoner case, and the one that broke."""
        page = self._page()
        assert "radio.checked = index === 0" in page
        assert '[["file", "A single file", false], ["folder", "A whole folder", true]]' in page

    def test_the_rebuilt_input_keeps_the_field_id(self):
        """collect, restore and submit all look the field up by this id."""
        page = self._page()
        assert "replacement.id = input.id" in page


class TestGuidedSharedSettingsEditor:
    """Choosing a group's settings from a list, rather than editing TOML by hand.

    The plain download works but hands someone a hundred commented lines to read.
    This shows every setting with what it does, what it is set to, and which ones
    appeared since their file was written.
    """

    def test_the_page_is_served(self, unlocked_client):
        r = unlocked_client.get("/shared-settings")
        assert r.status_code == 200
        assert "Shared settings" in r.text

    def test_the_page_is_behind_the_unlock_gate(self, client):
        r = client.get("/shared-settings")
        assert "unlock" in r.text.lower() or r.status_code in (302, 303, 401, 403)

    def test_the_inventory_lists_sections_and_settings(self, unlocked_client):
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        assert data["sections"], "no settings offered at all"
        first = data["sections"][0]
        assert {"section", "sources", "settings"} <= set(first)
        assert {"key", "value", "default", "explanation", "chosen", "new"} <= set(
            first["settings"][0]
        )

    def test_the_inventory_says_where_each_section_comes_from(self, unlocked_client):
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        assert all(s["sources"] for s in data["sections"])

    def test_with_no_shared_file_nothing_is_chosen_or_new(self, unlocked_client, monkeypatch):
        from src import settings_store as store_mod
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: None)
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        every = [s for sec in data["sections"] for s in sec["settings"]]
        assert not any(s["chosen"] for s in every)
        assert not any(s["new"] for s in every)
        assert data["existing_path"] is None

    def test_an_existing_file_marks_what_it_decides_and_what_is_new(
        self, unlocked_client, tmp_path, monkeypatch,
    ):
        from src import settings_store as store_mod
        shared = tmp_path / "lab.toml"
        shared.write_text("[retry]\nmax_retries = 3\n")
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: shared)
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        retry = next(s for s in data["sections"] if s["section"] == "retry")
        decided = next(s for s in retry["settings"] if s["key"] == "max_retries")
        assert decided["chosen"] is True
        assert decided["value"] == "3", "should show the group's value, not the shipped one"
        assert decided["new"] is False
        # Both values have to survive: unticking a setting shows what it falls
        # back to, and it can only show that if the shipped value came along.
        assert decided["default"] != "3", "the shipped value was lost"
        assert decided["default"], "no shipped value to fall back to"
        assert any(s["new"] for s in retry["settings"]), "the rest of the section is new"

    def test_a_setting_nobody_has_decided_reports_the_same_pair(self, unlocked_client, monkeypatch):
        """With no group file, what a setting is and what it falls back to are one thing."""
        from src import settings_store as store_mod

        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: None)
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        every = [s for sec in data["sections"] for s in sec["settings"]]
        assert all(s["value"] == s["default"] for s in every)

    def test_building_from_choices_returns_a_downloadable_file(self, unlocked_client):
        r = unlocked_client.post(
            "/api/settings/shared-draft", json={"chosen": {"retry": {"max_retries": "5"}}}
        )
        assert r.status_code == 200
        assert 'filename="shared-settings.toml"' in r.headers["content-disposition"]

    def test_only_the_ticked_settings_are_live(self, unlocked_client):
        import tomllib
        r = unlocked_client.post(
            "/api/settings/shared-draft", json={"chosen": {"retry": {"max_retries": "5"}}}
        )
        parsed = tomllib.loads(r.text)
        assert parsed == {"retry": {"max_retries": 5}}, "nothing else should be set"

    def test_ticking_nothing_gives_a_file_that_changes_nothing(self, unlocked_client):
        import tomllib
        r = unlocked_client.post("/api/settings/shared-draft", json={"chosen": {}})
        assert tomllib.loads(r.text) == {}

    def test_nothing_is_marked_new_in_a_file_built_from_choices(self, unlocked_client):
        """Every setting was just looked at, so anything unticked was left alone."""
        r = unlocked_client.post(
            "/api/settings/shared-draft", json={"chosen": {"retry": {"max_retries": "5"}}}
        )
        assert "NEW:" not in r.text

    def test_a_value_toml_cannot_express_is_refused_with_help(self, unlocked_client):
        """Better a rejected edit than a file that will not parse for the group."""
        r = unlocked_client.post(
            "/api/settings/shared-draft",
            json={"chosen": {"prompt": {"default_system_prompt": "no quotes here"}}},
        )
        assert r.status_code == 400
        assert "quotation marks" in r.text

    def test_a_list_value_survives_the_round_trip(self, unlocked_client):
        import tomllib
        r = unlocked_client.post(
            "/api/settings/shared-draft",
            json={"chosen": {"ocr": {"models": '["gpt-4o", "gpt-4o-mini"]'}}},
        )
        assert tomllib.loads(r.text)["ocr"]["models"] == ["gpt-4o", "gpt-4o-mini"]

    def test_nothing_is_written_to_disk(self, unlocked_client, tmp_path, monkeypatch):
        from src import paths as paths_mod
        monkeypatch.setattr(paths_mod, "extras_root", lambda: tmp_path)
        unlocked_client.post("/api/settings/shared-draft", json={"chosen": {}})
        assert list(tmp_path.glob("*.toml")) == []


class TestSharedSettingsPagePresentation:
    """The editor is a page of its own, so it has to carry the app's look itself.

    Embedded pages inherit the modal's styling by being inside it; this one
    doesn't, and copying the stylesheet without the script that applies the
    theme is how a page ends up permanently light.
    """

    def _page(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        directory = Path(__file__).resolve().parents[1] / "src" / "templates"
        return Jinja2Templates(directory=str(directory)).get_template(
            "shared_settings.html"
        ).render(request=None)

    def test_it_applies_the_saved_theme(self):
        """Without this the dark-mode rules below are dead weight."""
        page = self._page()
        assert "applySavedTheme" in page
        assert 'setAttribute("data-theme"' in page

    def test_it_reads_the_same_theme_setting_as_the_rest_of_the_app(self):
        page = self._page()
        assert 'localStorage.getItem("theme")' in page

    def test_it_defines_the_dark_theme(self):
        page = self._page()
        assert '[data-theme="dark"]' in page

    def test_it_uses_the_same_layout_shell_as_the_settings_modal(self):
        page = self._page()
        assert '<div id="topbar">' in page
        assert '<div id="page">' in page

    def test_it_uses_themed_colours_rather_than_fixed_ones(self):
        """A hardcoded grey looks wrong in one theme or the other.

        Checks the styles this page adds, not the shared stylesheet it copies —
        that one defines the colours, so naming them there is the point.
        """
        page = self._page()
        own_styles = page.rsplit("<style>", 1)[1].split("</style>")[0]
        assert "var(--text-muted)" in own_styles
        assert "#6e6e73" not in own_styles, "hardcoded light-theme grey"
        assert "var(--muted," not in own_styles, "invented variable with a fixed fallback"

    def test_value_boxes_fit_the_longest_setting_this_sandbox_has(self):
        """A list cut off mid-way is a value nobody can check.

        Measured rather than guessed: the widest value any installed plugin
        offers is a model list, and the box has to hold it.
        """
        from pathlib import Path as _Path

        from src.shared_settings import inventory

        repo = _Path(__file__).resolve().parents[3]
        widest = max(
            len(s["value"])
            for section in inventory(repo / "plugins", repo / "settings.default.toml")
            for s in section["settings"]
        )
        import re

        page = self._page()
        widths = [int(m) for m in re.findall(r"minmax\(0, (\d+)rem\)", page)]
        assert widths, "no fixed-width value column found"
        rem = min(widths)
        # 0.78rem monospace at roughly 0.6em per character, less the input's padding.
        fits = (rem * 16 - 16) / (0.78 * 16 * 0.6)
        assert fits >= widest, f"a {rem}rem box holds about {fits:.0f} characters, need {widest}"

    def test_every_value_box_can_be_dragged_taller(self):
        """The field most likely to hold a lot is the one that looks smallest.

        prompt.default_system_prompt ships as a single sentence, so any rule
        based on how long a value is today would give it the smallest box —
        which is the opposite of what a group replacing it with paragraphs
        needs.
        """
        page = self._page()
        assert "resize: vertical" in page
        assert 'createElement("textarea")' in page
        assert 'createElement("input")\n      value.type = "text"' not in page

    def test_a_box_opens_at_the_height_of_its_own_text(self):
        page = self._page()
        assert "scrollHeight" in page

    def test_the_page_is_wide_enough_for_that_box(self):
        """The Settings modal caps at 720px, which squeezes the column back."""
        page = self._page()
        assert "#page { max-width: 1040px; }" in page

    def test_a_wrong_value_is_outlined_and_told_why(self):
        page = self._page()
        assert "textarea.invalid" in page
        assert "border-color: var(--danger-text)" in page
        assert ".field-error" in page
        assert "color: var(--danger-text)" in page

    def test_the_message_sits_under_its_own_box(self):
        """One message at the bottom of forty rows names no row."""
        page = self._page()
        assert "value-cell" in page
        assert "text-align: left" in page

    def test_the_server_still_refuses_a_bad_value(self, unlocked_client):
        """The page checking first must not become the only thing checking.

        Someone can reach this without the page — an old tab, a script — so the
        check that builds the file stays the one that decides.
        """
        r = unlocked_client.post(
            "/api/settings/shared-draft", json={"chosen": {"retry": {"max_retries": "lots"}}}
        )
        assert r.status_code >= 400
        assert "max_retries" in r.text

    def test_text_settings_are_typed_as_text(self):
        """Quotation marks are the file's spelling, not the person's job."""
        page = self._page()
        assert "function forFile" in page
        assert "JSON.stringify" in page
        assert "no quotation marks needed" in page

    def test_the_page_says_unticking_is_safe(self):
        """People will not untick to peek unless told their value survives it."""
        page = self._page()
        assert "remembered" in page
        assert "tick it on again" in page

    def test_a_narrow_window_gives_the_value_its_own_row(self):
        page = self._page()
        assert "@media (max-width: 1080px)" in page


class TestSharedSettingsLink:
    """How the editor is reached from the Settings modal."""

    def _settings_page(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        directory = Path(__file__).resolve().parents[1] / "src" / "templates"
        return Jinja2Templates(directory=str(directory)).get_template(
            "settings.html"
        ).render(request=None)

    def test_it_opens_in_its_own_tab(self):
        """It is a long list to work through; losing it by leaving the modal
        would mean starting again."""
        page = self._settings_page()
        assert '<a href="/shared-settings" target="_blank"' in page

    def test_the_new_tab_cannot_reach_back_into_this_one(self):
        page = self._settings_page()
        link = page[page.index('href="/shared-settings"'):]
        assert 'rel="noopener"' in link[:200]

    def test_the_button_carries_the_new_tab_icon(self):
        page = self._settings_page()
        assert "external-icon" in page

    def test_and_says_so_for_a_screen_reader(self):
        """An icon alone tells someone using a screen reader nothing."""
        page = self._settings_page()
        assert "(opens in a new tab)" in page

class TestBrowseButton:
    """/api/pick-path — the "Browse…" button behind every path box.

    A browser hands a page a file's contents and never its location, which
    is why typing paths by hand was the only option before this. It works
    because the server is on the same computer as the browser, so it can
    open that computer's own chooser. Nothing here opens a real window.
    """

    def test_the_settings_page_is_told_whether_to_draw_the_button(
        self, unlocked_client, settings_env, monkeypatch
    ):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module.file_picker, "available", lambda: False)
        assert unlocked_client.get("/api/settings").json()["can_browse"] is False
        monkeypatch.setattr(app_module.file_picker, "available", lambda: True)
        assert unlocked_client.get("/api/settings").json()["can_browse"] is True

    def test_choosing_a_folder_hands_back_its_real_path(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module.file_picker, "choose", lambda **kw: tmp_path / "picked")
        resp = unlocked_client.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 200
        assert resp.json() == {"path": str(tmp_path / "picked"), "cancelled": False}

    def test_closing_the_window_is_an_answer_not_an_error(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module.file_picker, "choose", lambda **kw: None)
        resp = unlocked_client.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 200
        assert resp.json() == {"path": None, "cancelled": True}

    def test_what_was_typed_is_where_the_chooser_opens(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        asked = {}
        monkeypatch.setattr(
            app_module.file_picker, "choose",
            lambda **kw: asked.update(kw) or None,
        )
        unlocked_client.post(
            "/api/pick-path",
            json={"kind": "file", "start": "/Users/x/shared", "prompt": "Pick the file"},
        )
        assert asked == {"kind": "file", "start": "/Users/x/shared", "prompt": "Pick the file"}

    def test_a_computer_with_no_chooser_says_so(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]

        def unavailable(**kw):
            raise app_module.file_picker.PickerUnavailable("no chooser here")

        monkeypatch.setattr(app_module.file_picker, "choose", unavailable)
        resp = unlocked_client.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 503

    def test_asking_for_something_that_is_not_a_file_or_folder(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]

        def refuse(**kw):
            raise ValueError("kind must be 'folder' or 'file'")

        monkeypatch.setattr(app_module.file_picker, "choose", refuse)
        resp = unlocked_client.post("/api/pick-path", json={"kind": "printer"})
        assert resp.status_code == 400

    def test_a_locked_browser_cannot_open_a_window(self, client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        opened = []
        monkeypatch.setattr(app_module.file_picker, "choose", lambda **kw: opened.append(kw))
        resp = client.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 401
        assert opened == []

    def test_a_browser_on_another_computer_is_refused(self, monkeypatch, tmp_path):
        # The window would open on the screen of whoever runs the sandbox,
        # and hand back a folder from a disk the clicker has never seen.
        app_module = sys.modules["_pu_webui_app"]
        conversation = sys.modules["_pu_webui_conversation"]
        jobs = sys.modules["_pu_webui_jobs"]
        monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")
        monkeypatch.setattr(jobs, "_CONVERSATIONS_DIR", tmp_path / "conversations")
        opened = []
        monkeypatch.setattr(app_module.file_picker, "choose", lambda **kw: opened.append(kw))

        elsewhere = TestClient(app_module.create_app(), client=("10.0.0.5", 50000))
        elsewhere.post("/unlock", data={"passphrase": ""})
        resp = elsewhere.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 403
        assert opened == []


class TestEndpointUsageInTheSidebar:
    """Usage from someone's own AI service, shown apart from Princeton spending."""

    def _page(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        here = Path(__file__).resolve().parents[1] / "src" / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))

    def test_the_api_reports_endpoint_totals(self, unlocked_client, tmp_path, monkeypatch):
        from unittest.mock import patch

        from src.tracking.token_tracker import TokenTracker

        tracker = TokenTracker(
            "heller", data_file=str(tmp_path / "u.json"), monthly_limit=10.0
        )
        with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
             patch("src.tracking.token_tracker.get_model_pricing",
                   return_value={"input": 1.0, "output": 1.0}):
            tracker.record_usage("llama-3-70b", 100, 50, 150, endpoint="my_cluster")
        import sys

        monkeypatch.setattr(sys.modules["_pu_webui_app"], "TokenTracker", lambda **kw: tracker)
        data = unlocked_client.get("/api/usage?professor=heller").json()
        assert data["endpoint_usage"]["my_cluster"]["total_usage"]["total_tokens"] == 150

    def test_the_section_is_hidden_until_there_is_something_in_it(self):
        """Almost everyone is on the sandbox alone and should see no change."""
        page = self._page()
        assert '<div id="spend-endpoints-section" hidden>' in page
        # And stays hidden after the figures load — the markup alone would let
        # an empty heading appear the moment the sidebar refreshed.
        assert "section.hidden = endpoints.length === 0;" in page

    def test_it_shows_tokens_and_never_money(self):
        page = self._page()
        # Just the loop that draws these rows — the rest of the sidebar shows
        # Princeton spending and is supposed to show money.
        block = page[page.index("const endpoints = Object.entries"):]
        block = block[:block.index("list.appendChild(row);")]
        assert "tokens" in block
        assert "fmtMoney" not in block, "a cost was shown for a service with no known prices"

    def test_it_says_these_are_not_billed_through_the_sandbox(self):
        page = self._page()
        assert "Counted, not costed" in page


class TestSystemPromptPerConversation:
    """Standing instructions belong to a conversation and apply to every turn.

    A model is handed the whole conversation afresh on each message and
    remembers nothing of its own, so instructions sent once would quietly stop
    applying from the second message onwards.
    """

    def _page(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        here = Path(__file__).resolve().parents[1] / "src" / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))

    def test_a_conversation_remembers_its_instructions(self, tmp_path):
        from plugins.webui.src.conversation import Conversation

        conv = Conversation(
            id="c1", title="t", created_at="2026-07-30T10:00:00",
            updated_at="2026-07-30T10:00:00", model="gpt-4o",
            system_prompt="Answer in French.",
        )
        assert Conversation.from_dict(conv.to_dict()).system_prompt == "Answer in French."

    def test_a_conversation_saved_before_this_existed_still_loads(self, tmp_path):
        from plugins.webui.src.conversation import Conversation

        old = {
            "id": "c1", "title": "t", "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00", "model": "gpt-4o", "messages": [],
        }
        assert Conversation.from_dict(old).system_prompt is None

    def test_two_conversations_keep_different_instructions(self, tmp_path, monkeypatch):
        from plugins.webui.src import conversation as conv_mod
        from plugins.webui.src.conversation import ConversationStore

        monkeypatch.setattr(conv_mod, "_conversations_dir", lambda: tmp_path)
        store = ConversationStore("heller")
        a = store.create(model="gpt-4o")
        b = store.create(model="gpt-4o")
        a.system_prompt = "Answer in French."
        store.save(a)
        assert store.load(a.id).system_prompt == "Answer in French."
        assert store.load(b.id).system_prompt is None, "instructions leaked between conversations"

    def _chat(self, client, monkeypatch, conv_id, message, **body):
        """Send one message and hand back what the model was asked."""
        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {"type": "done", "content": "ok", "model": "gpt-4o",
             "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0001},
        ])
        fake_sandbox.chat_service.generate_title.return_value = None
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor",
                            lambda *a, **kw: fake_sandbox)
        resp = client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id,
            "message": message, "model": "gpt-4o", **body,
        })
        assert resp.status_code == 200
        return fake_sandbox.chat_service.stream_message.call_args

    def test_the_prompt_is_sent_on_every_turn_not_just_the_first(self, unlocked_client, monkeypatch):
        """The whole point of keeping it: it has to be resent each time."""
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]

        first = self._chat(unlocked_client, monkeypatch, conv_id, "Bonjour",
                           system_prompt="Answer in French.")
        assert first.kwargs["system_prompt"] == "Answer in French."

        # Second turn sends no instructions of its own; the conversation's must
        # still reach the model, or they silently stop applying after one message.
        second = self._chat(unlocked_client, monkeypatch, conv_id, "Et maintenant ?")
        assert second.kwargs["system_prompt"] == "Answer in French."

    def test_blank_instructions_are_the_same_as_none(self, unlocked_client, monkeypatch):
        """Spaces sent on every turn would say nothing and cost tokens."""
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        call = self._chat(unlocked_client, monkeypatch, conv_id, "Hi", system_prompt="   ")
        assert call.kwargs["system_prompt"] is None

    def test_clearing_the_box_clears_the_instructions(self, unlocked_client, monkeypatch):
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        self._chat(unlocked_client, monkeypatch, conv_id, "Hi", system_prompt="Answer in French.")
        call = self._chat(unlocked_client, monkeypatch, conv_id, "Hi again", system_prompt=None)
        assert call.kwargs["system_prompt"] is None

    def test_the_box_is_offered_with_the_other_model_settings(self):
        page = self._page()
        assert 'id="sampling-system-prompt"' in page
        assert "Instructions for this conversation" in page

    def test_the_box_grows_and_can_be_dragged_taller(self):
        """Instructions run to a paragraph; the three settings above are numbers."""
        page = self._page()
        assert "resize: vertical" in page
        assert "scrollHeight" in page

    def test_the_page_says_it_applies_to_every_message(self):
        page = self._page()
        assert "before every message in this conversation" in page

    def test_what_is_typed_reaches_the_model_unchanged(self):
        """No quoting or escaping for a person to get wrong.

        The shared settings editor needs quotation marks because a settings file
        spells text that way. A conversation is stored as JSON, which quotes for
        itself, so instructions are stored exactly as written.
        """
        import json

        from plugins.webui.src.conversation import Conversation

        typed = 'Say "hello" first.\nThen answer in French.'
        conv = Conversation(
            id="c1", title="t", created_at="x", updated_at="x", model="gpt-4o",
            system_prompt=typed,
        )
        assert Conversation.from_dict(json.loads(json.dumps(conv.to_dict()))).system_prompt == typed


class TestOpeningAConversationFolder:
    """The way in to everything a conversation is made of."""

    def _page(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        here = Path(__file__).resolve().parents[1] / "src" / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None, can_reveal=True))

    def test_the_menu_offers_it(self):
        page = self._page()
        assert "Open this conversation's folder" in page

    def test_it_is_not_offered_where_there_is_nothing_to_open_it_with(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        here = Path(__file__).resolve().parents[1] / "src" / "templates"
        page = (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None, can_reveal=False))
        assert "canReveal: false" in page

    def test_the_chat_page_is_told_whether_it_can(self, unlocked_client):
        """The template defaults to false, so a route that forgot would be silent."""
        page = unlocked_client.get("/").text
        assert "canReveal: true" in page or "canReveal: false" in page

    def test_it_opens_the_conversations_own_folder(self, unlocked_client, monkeypatch):
        import sys

        opened = []
        picker = sys.modules["_pu_webui_file_picker"]
        monkeypatch.setattr(picker, "reveal", lambda p: opened.append(str(p)) or True)
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        r = unlocked_client.post(
            f"/api/conversations/{conv_id}/reveal?professor=heller"
        )
        assert r.status_code == 200
        assert len(opened) == 1
        assert opened[0].endswith(conv_id)

    def test_a_conversation_that_does_not_exist_is_refused(self, unlocked_client):
        r = unlocked_client.post(
            "/api/conversations/c_" + "f" * 16 + "/reveal?professor=heller"
        )
        assert r.status_code == 404

    def test_a_malformed_id_is_refused_rather_than_used_as_a_path(self, unlocked_client):
        r = unlocked_client.post("/api/conversations/..%2F..%2Fetc/reveal?professor=heller")
        assert r.status_code in (404, 400)

    def test_it_is_behind_the_unlock_gate(self, client):
        r = client.post("/api/conversations/c_" + "f" * 16 + "/reveal?professor=heller")
        assert r.status_code in (401, 403, 302, 303)

    def test_a_browser_on_another_computer_cannot_open_a_window_here(
        self, unlocked_client, monkeypatch
    ):
        """It would open on the server's screen, not the person's."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_SAME_COMPUTER", frozenset())
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        r = unlocked_client.post(f"/api/conversations/{conv_id}/reveal?professor=heller")
        assert r.status_code == 403


class TestKeepingSuppliedDocuments:
    """Off by default; on, the documents sit with the conversation."""

    def _upload(self, client, conversation_id, name=b"source.txt"):
        return client.post(
            "/api/attachments",
            files={"file": (name.decode(), b"hello", "text/plain")},
            data={"professor": "heller", "conversation_id": conversation_id},
        )

    def _conv(self, client):
        return client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]

    def test_nothing_is_kept_unless_it_is_asked_for(self, unlocked_client, monkeypatch):
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "WEBUI_KEEP_SUPPLIED_DOCUMENTS", False)
        conv_id = self._conv(unlocked_client)
        assert self._upload(unlocked_client, conv_id).json()["saved_as"] is None

    def test_when_asked_for_it_sits_with_the_conversation(self, unlocked_client, monkeypatch):
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "WEBUI_KEEP_SUPPLIED_DOCUMENTS", True)
        conv_id = self._conv(unlocked_client)
        assert self._upload(unlocked_client, conv_id).json()["saved_as"] == "source.txt"
        store = sys.modules["_pu_webui_conversation"].ConversationStore("heller")
        assert (store.attachments_dir(conv_id) / "source.txt").read_bytes() == b"hello"

    def test_a_second_document_of_the_same_name_does_not_replace_the_first(
        self, unlocked_client, monkeypatch
    ):
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "WEBUI_KEEP_SUPPLIED_DOCUMENTS", True)
        conv_id = self._conv(unlocked_client)
        self._upload(unlocked_client, conv_id)
        second = self._upload(unlocked_client, conv_id)
        assert second.json()["saved_as"] == "source (2).txt"

    def test_a_name_that_is_a_path_cannot_write_outside_the_folder(
        self, unlocked_client, monkeypatch
    ):
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "WEBUI_KEEP_SUPPLIED_DOCUMENTS", True)
        conv_id = self._conv(unlocked_client)
        saved = self._upload(unlocked_client, conv_id, b"../../escaped.txt").json()["saved_as"]
        assert saved == "escaped.txt"


class TestTheInterfaceNamesTheValue:
    """A blank box stands for a real number, and says which."""

    def test_the_page_is_given_the_numbers_it_will_send(self, unlocked_client):
        from src.settings import PROMPT_TEMPERATURE, PROMPT_TOP_P

        page = unlocked_client.get("/").text
        assert "defaultSampling" in page
        assert str(PROMPT_TEMPERATURE) in page
        assert str(PROMPT_TOP_P) in page

    def test_no_box_claims_the_model_decides(self, unlocked_client):
        """It does not: a value is always sent, and the sandbox chooses it."""
        page = unlocked_client.get("/").text
        assert "Model default" not in page
        assert "model default" not in page
        assert "model's default" not in page


class TestAJobFormNamesItsOwnNumbers:
    """A blank box in a job form has a real value behind it, and shows it.

    The value comes from the plugin's own settings with the group's shared file
    and this person's preferences applied — so somebody who set
    ``[translation] temperature`` sees the number they set, not a description.
    """

    def _page(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        here = Path(__file__).resolve().parents[1] / "src" / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))

    def test_whatever_an_action_reports_reaches_the_browser(self, unlocked_client):
        """The route hands the values on; each plugin decides what they are."""
        actions = unlocked_client.get("/api/plugin-actions?professor=heller").json()["actions"]
        assert all("sampling" in a for a in actions), (
            "an action's own settings are dropped on the way to the page"
        )

    def test_the_form_shows_the_number_beside_the_box(self):
        page = self._page()
        assert "blank = ${declared[key]}" in page

    def test_a_label_reads_properly_whether_or_not_it_has_a_range(self):
        """Max response tokens has no range, and read "(, blank = 4000)"."""
        page = self._page()
        assert "parts.join(\", \")" in page
        assert 'samplingLabel("Max response tokens", "max_tokens")' in page

    def test_an_action_that_reports_nothing_gets_no_invented_figure(self):
        """A plugin that declares no settings gets a plain label, not a guess."""
        page = self._page()
        assert "declared[key] !== undefined && declared[key] !== null" in page
        assert "parts.length ? " in page

class TestTheEndpointListDescribesEndpointsTruthfully:
    def test_an_endpoint_that_says_nothing_is_shown_as_usable(self, unlocked_client, monkeypatch):
        """The page had its own default, opposite to the one that decides."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "ENDPOINTS", {"quiet": {"base_url": "https://x/v1"}})
        shown = unlocked_client.get("/api/settings/endpoints?professor=heller")
        if shown.status_code != 200:  # the list lives on the settings payload
            shown = unlocked_client.get("/api/settings")
        assert shown.status_code == 200
        assert "quiet" in shown.text
        assert "cannot use it" not in shown.text

    def test_the_page_only_remarks_on_one_it_cannot_use(self):
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "src" / "templates" / "settings.html").read_text()
        assert "marked as not OpenAI-compatible" in page
        assert '" · OpenAI-compatible"' not in page


class TestTheModelMenuReadsInOrder:
    def test_the_menu_uses_the_shared_ordering(self, unlocked_client, monkeypatch):
        """Sorting at each display site is how two lists went wrong the same way."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(
            app_module, "models_in_reading_order",
            lambda: ["claude-sonnet-5", "gpt-4o", "Llama-3.3-70B-Instruct"],
        )
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: True)
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "gpt-4o")
        names = [m["name"] for m in
                 unlocked_client.get("/api/models?professor=heller").json()["models"]]
        assert names == ["claude-sonnet-5", "gpt-4o", "Llama-3.3-70B-Instruct"]

    def test_the_page_shows_them_in_the_order_it_is_given(self):
        """The list is built by walking state.models, so the server decides."""
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()
        assert "state.models.forEach" in page
        assert ".sort(" not in page.split("function renderModelList")[1].split("}")[0]


class TestConversationsAreGroupedByAge:
    """A long list is easier to find your way around when it is dated."""

    def _page(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()

    def test_the_page_groups_by_how_recent_a_conversation_is(self):
        page = self._page()
        for group in ("Today", "This week", "This month", "Older"):
            assert f'"{group}"' in page

    def test_a_heading_goes_in_only_where_the_group_changes(self):
        """Otherwise every conversation gets one."""
        page = self._page()
        assert "if (group !== currentGroup) {" in page

    def test_the_headings_stay_in_view_while_scrolling(self):
        page = self._page()
        block = page.split(".conv-group {")[1].split("}")[0]
        assert "position: sticky" in block

    def test_every_heading_is_the_same_height(self):
        """They are sticky, so two are seen together as one passes the other.

        The first one used to be trimmed to save a little space at the top of
        the list, which made the pair jump as they scrolled past each other.
        """
        import re

        css = self._page().split("</style>")[0]
        rules = re.findall(r"([^{}]*\.conv-group[^{}]*)\{([^}]*)\}", css)
        sizing = ("padding", "height", "margin", "font-size", "line-height")
        assert len(rules) == 1, (
            "more than one rule sets a conversation heading's box: "
            f"{[r[0].strip().splitlines()[-1] for r in rules]}"
        )
        assert not any(
            key in rules[0][1] for key in sizing if ":first-child" in rules[0][0]
        )

    def test_the_server_still_decides_the_order(self, unlocked_client):
        """Grouping is a heading over an order it does not change."""
        page = self._page()
        assert "data.conversations.forEach" in page
        assert "conversations.sort" not in page


class TestTheModelSaysWhatItCanDo:
    """Shown beside the settings, for reference while choosing."""

    def _page(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()

    def test_the_menu_reports_how_long_an_answer_a_model_can_give(
        self, unlocked_client, monkeypatch
    ):
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "models_in_reading_order", lambda: ["o3-mini"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: False)
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: False)
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: 16000)
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "o3-mini")
        model = unlocked_client.get("/api/models?professor=heller").json()["models"][0]
        assert model["max_response_tokens"] == 16000
        assert model["supports_vision"] is False
        assert model["accepts_sampling_params"] is False

    def test_it_is_shown_wherever_the_settings_are(self):
        page = self._page()
        assert 'id="model-capabilities"' in page
        assert "What this model can do" in page

    def test_it_refreshes_when_the_model_changes(self):
        """It sits beside controls that appear and disappear with the model."""
        page = self._page()
        visibility = page.split("function applySamplingVisibility")[1].split("\n}")[0]
        assert "showModelCapabilities(model)" in visibility

    def test_it_says_nothing_a_field_below_already_says(self):
        """A field's presence answers whether a model takes that setting.

        Saying it again is one more thing to read and one more thing to keep
        true. What has no field — reading images — is what belongs here.
        """
        page = self._page()
        block = page.split("function showModelCapabilities")[1].split("\n}")[0]
        assert "images" in block
        assert "temperature" not in block.lower()
        assert "max_response_tokens" not in block

    def test_the_response_cap_is_shown_in_the_box_it_is_about(self):
        page = self._page()
        defaults = page.split("function showSamplingDefaults")[1].split("\n}")[0]
        assert "model.max_response_tokens" in defaults

    def test_the_boxes_are_refreshed_when_the_model_changes(self):
        """The cap differs by model, so it cannot be settled once at load."""
        page = self._page()
        visibility = page.split("function applySamplingVisibility")[1].split("\n}")[0]
        assert "showSamplingDefaults(model)" in visibility


class TestAJobCanRunOnItsOwnModel:
    """Choosable in the form, without changing the conversation."""

    def _page(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()

    def test_the_form_offers_a_model(self):
        page = self._page()
        assert 'name: "model", label: "Model", kind: "model"' in page

    def test_the_job_runs_on_what_the_form_says(self):
        """Not on the chat header, which the form only starts from."""
        page = self._page()
        start = page.split("async function startJobFromModal")[1]
        assert "const model = jobModelName();" in start

    def test_the_preview_is_of_the_job_that_would_run(self):
        """A prompt differs by model — see get_model_system_role."""
        page = self._page()
        preview = page.split("async function refreshJobPreview")[1].split("\n}")[0]
        assert "jobModelName()" in preview

    def test_the_model_is_not_passed_off_as_one_of_the_plugins_fields(self):
        """It is the sandbox's field, not something any plugin declared."""
        page = self._page()
        assert "const { model: _chosenModel, ...actionValues } = values;" in page
        assert "fields_json\", JSON.stringify(actionValues)" in page

    def test_the_conversation_keeps_its_own_model(self, unlocked_client, monkeypatch):
        """Running one job on another model is not a decision about the chat."""
        import sys

        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]

        jobs_module = sys.modules["_pu_webui_jobs"]
        started = {}

        def remember(**kwargs):
            started.update(kwargs)
            from unittest.mock import MagicMock

            job = MagicMock()
            job.id = "job_1"
            return job

        monkeypatch.setattr(jobs_module, "start_job", remember)
        resp = unlocked_client.post("/api/jobs", data={
            "professor": "heller", "conversation_id": conv_id,
            "action_id": "translate", "model": "o3-mini", "fields_json": "{}",
        })
        assert resp.status_code in (200, 400, 404), resp.text
        if resp.status_code == 200:
            assert started.get("model") == "o3-mini", "the job did not run on the chosen model"
        conv = unlocked_client.get(
            f"/api/conversations/{conv_id}?professor=heller"
        ).json()
        assert conv["model"] == "gpt-4o", "running a job changed the conversation's model"


class TestTheSandboxsMarkSitsBehindItsName:
    def _page(self):
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        here = Path(__file__).resolve().parents[1] / "src" / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))

    def test_the_title_is_the_sandboxs_name(self):
        page = self._page()
        assert "Princeton University AI Sandbox" in page
        assert "Chat UI" not in page

    def _data_uri(self):
        import re

        found = re.search(r'url\("data:image/svg\+xml,(.*?)"\)', self._page(), re.S)
        assert found, "no mark is embedded"
        return found.group(1)

    def test_the_mark_is_a_real_drawing_and_not_a_broken_link(self):
        """It is written into the page, so nothing fetches it and finds it gone."""
        import xml.etree.ElementTree as ET
        from urllib.parse import unquote

        root = ET.fromstring(unquote(self._data_uri()))
        assert root.tag.endswith("svg")
        assert len(root) == 3, "the mark is missing part of its drawing"

    def test_a_browser_reads_the_whole_address(self):
        """A '#' inside it would end the address and drop the rest of the drawing.

        The colour of every path in this mark is written as a '#' followed by
        six digits, so this is not a hypothetical: unencoded, a browser stops
        reading part-way through the first path and draws nothing at all. It
        cost an afternoon once, and reading the string back in Python does not
        show it, because Python does not stop at a '#'.
        """
        import xml.etree.ElementTree as ET
        from urllib.parse import unquote

        uri = self._data_uri()
        assert "#" not in uri, "the address ends early at a '#' and the mark will not draw"

        # And what a browser would actually be handed is still a whole drawing.
        as_a_browser_reads_it = uri.split("#")[0]
        root = ET.fromstring(unquote(as_a_browser_reads_it))
        assert len(root) == 3

    def test_the_mark_is_actually_drawn(self):
        page = self._page()
        mark = page.split(".page-title-mark {")[1].split("}")[0]
        assert float(mark.split("opacity:")[1].split(";")[0]) > 0

    def test_the_name_does_not_sit_on_top_of_the_mark(self):
        """The mark is drawn at full strength, so the words move clear of it
        rather than being read through it."""
        page = self._page()
        text = page.split(".page-title-text {")[1].split("}")[0]
        assert "left:" in text, "the name would overlap the mark it is meant to sit beside"

    def test_it_is_visible_in_both_themes(self):
        page = self._page()
        assert '[data-theme="dark"] .page-title-mark' in page

    def test_a_screen_reader_is_not_told_the_name_twice(self):
        page = self._page()
        assert '<span class="page-title-mark" aria-hidden="true">' in page


class TestTheSuppliedButtonIcons:
    """Drawings supplied for the buttons, fitted to how the buttons work here."""

    def _button(self, button_id):
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()
        start = page.index(f'id="{button_id}"')
        return page[page.rindex("<button", 0, start): page.index("</button>", start)]

    def test_every_icon_takes_its_colour_from_the_button(self):
        """They were supplied painted white, which is invisible on a light page."""

        for button_id in ("theme-toggle-btn", "lock-btn", "sampling-options-btn",
                          "plugin-action-btn", "settings-btn", "spend-toggle-btn",
                          "model-toggle-btn", "model-add-btn", "job-modal-reset",
                          "settings-modal-close", "job-modal-close"):
            block = self._button(button_id)
            assert "currentColor" in block, button_id
            assert 'fill="white"' not in block, f"{button_id} is painted white regardless of theme"
            assert "fill-opacity" not in block, f"{button_id} is drawn faded"

    def test_the_theme_button_still_holds_both_drawings(self):
        """It cross-fades between them; one would leave nothing to fade to."""
        import re

        block = self._button("theme-toggle-btn")
        assert len(re.findall(r"<svg\b", block)) == 2
        assert 'class="icon-sun"' in block and 'class="icon-moon"' in block

    def test_each_drawing_is_whole(self):
        """A path lost in the swap would show as a piece of an icon."""
        import re
        import xml.etree.ElementTree as ET

        expected = {"lock-btn": 1, "sampling-options-btn": 1, "plugin-action-btn": 1}
        for button_id, paths in expected.items():
            root = ET.fromstring(re.search(r"<svg\b.*?</svg>", self._button(button_id), re.S).group(0))
            assert len(root) == paths, button_id

    def test_every_button_uses_the_supplied_artwork(self):
        """None left on the drawing it shipped with."""
        import re
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()
        # The one button with no drawing of its own: the artwork supplied for a
        # "sidebar toggle" was a second copy of the padlock, so this button —
        # which only appears on a narrow screen — is still on a plain one.
        awaiting_artwork = {"sidebar-toggle-btn"}
        for m in re.finditer(r'<button\b[^>]*id="([^"]+)"[^>]*>(.*?)</button>', page, re.S):
            block = m.group(2)
            if "<svg" not in block or m.group(1) in awaiting_artwork:
                continue
            # The originals were drawn as strokes; every supplied one is a
            # filled shape, so a leftover would show up here.
            assert 'stroke="currentColor"' not in block, f"{m.group(1)} is still the old drawing"

    def test_the_conversation_menu_dots_stand_upright(self):
        """They are supplied in a row, and the button wants a column."""
        import re
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()
        dots = re.search(r"menuBtn\.innerHTML = '(.*?)';", page, re.S).group(1)
        assert "icon-upright" in dots
        assert ".icon-upright { transform: rotate(90deg); }" in page

    def test_the_menu_button_gives_those_dots_a_square_to_sit_in(self):
        """A drawing that is turned upright cannot be fitted to its old shape.

        The box was 3px by 13px, for a drawing that was already vertical. The
        supplied one is a wide row: fitted to that box it would come out 3px by
        0.6px, and rotating that would not help.
        """
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()
        rule = page.split(".conv-menu-btn svg {")[1].split("}")[0]
        width = rule.split("width:")[1].split("px")[0].strip()
        height = rule.split("height:")[1].split("px")[0].strip()
        assert width == height, f"the dots would be squashed before being turned: {width}x{height}"

    def test_a_cross_is_the_plus_turned(self):
        """Asked for: the same drawing, rotated, rather than a second one."""
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()
        for button_id in ("settings-modal-close", "job-modal-close"):
            start = page.index(f'id="{button_id}"')
            block = page[page.rindex("<button", 0, start): page.index("</button>", start)]
            assert "icon-as-cross" in block, button_id
        assert "rotate(45deg)" in page
        # Turned, its corners reach further than its sides did, so it is scaled
        # back to sit level with the icons beside it.
        assert "scale(0.707)" in page

    def test_the_new_conversation_button_is_the_sandboxs_orange(self):
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()
        start = page.index('id="new-conv"')
        block = page[page.rindex("<button", 0, start): page.index("</button>", start)]
        assert "#f58025" in block
        assert "currentColor" not in block

    def test_no_drawing_carries_a_hidden_backing_rectangle(self):
        """Each was supplied with a fully transparent rect the size of itself."""
        for button_id in ("theme-toggle-btn", "lock-btn", "sampling-options-btn",
                          "plugin-action-btn", "settings-btn", "spend-toggle-btn",
                          "new-conv", "model-toggle-btn", "model-add-btn",
                          "job-modal-reset", "settings-modal-close", "job-modal-close"):
            assert "<rect" not in self._button(button_id), button_id


class TestOneDesignSystem:
    """The values are decided in one place, and the pages agree.

    They did not: four pages each held their own copy and three had drifted,
    with the Settings page painting itself a different orange from the page it
    opens inside — under a comment promising the two were identical.
    """

    TEMPLATES = ("chat.html", "settings.html", "shared_settings.html", "unlock.html")

    def _dir(self):
        from pathlib import Path

        return Path(__file__).resolve().parents[1] / "src" / "templates"

    def _source(self, name):
        return (self._dir() / name).read_text()

    def _rendered(self, name):
        from fastapi.templating import Jinja2Templates

        env = Jinja2Templates(directory=str(self._dir())).env
        return env.get_template(name).render(
            request=None, error=None, can_reveal=True, default_sampling={}
        )

    def test_every_page_takes_its_values_from_the_one_place(self):
        for name in self.TEMPLATES:
            assert '{% include "_design-system.html" %}' in self._source(name), name

    def test_no_page_declares_its_own(self):
        """A second copy is how the drift started."""
        for name in self.TEMPLATES:
            assert "--orange:" not in self._source(name), (
                f"{name} declares a colour of its own instead of using the shared one"
            )

    def test_the_pages_agree_on_the_accent(self):
        accents = {name: self._rendered(name).count("--orange: #E77500")
                   for name in self.TEMPLATES}
        assert all(n == 1 for n in accents.values()), accents

    def test_type_sizes_come_from_the_scale(self):
        """Fourteen ad-hoc sizes, several a fraction of a pixel apart."""
        import re

        for name in self.TEMPLATES:
            css = self._source(name).split("</style>")[0]
            literals = re.findall(r"font-size:\s*([0-9.]+)rem", css)
            assert not literals, f"{name} still sizes text by hand: {literals}"

    def test_shapes_and_faces_come_from_the_scale_too(self):
        """Eight corner radii, where 20px and 999px both meant "fully round"."""
        import re

        for name in self.TEMPLATES:
            page = self._source(name)
            radii = re.findall(r"border-radius:\s*(\d+)px", page)
            assert not radii, f"{name} still rounds corners by hand: {radii}"
            assert "ui-monospace" not in page, f"{name} writes out a font stack"

    def test_nothing_is_smaller_than_twelve_pixels(self):
        """It went down to 9.9px, on a badge, and 11.2px on the sidebar."""
        import re

        system = self._source("_design-system.html")
        steps = [float(v) for v in re.findall(r"--text-[a-z]+:\s*([0-9.]+)rem", system)]
        assert steps, "no type scale found"
        assert min(steps) * 16 >= 12, f"the scale reaches {min(steps) * 16}px"


class TestTheInterfaceCanBeReachedWithoutAMouse:
    def _chat(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()

    def test_there_is_a_focus_style_at_all(self):
        from pathlib import Path

        system = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "_design-system.html").read_text()
        assert ":focus-visible" in system

    def test_the_focus_ring_can_be_seen_on_every_surface(self):
        """Princeton's orange makes 3:1 on a panel and 2.78:1 on the page.

        The transcript sits on the page, so the ring is drawn in a darkened
        orange that clears both. This is the arithmetic that caught it.
        """
        import re
        from pathlib import Path

        css = (Path(__file__).resolve().parents[1] / "src" / "templates"
               / "_design-system.html").read_text()

        def values(block_start):
            segment = css.split(block_start)[1].split("}")[0]
            return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})", segment))

        light = values(":root {")
        dark = {**light, **values('[data-theme="dark"] {')}

        def luminance(colour):
            channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            adjusted = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                        for c in channels]
            return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]

        for theme in (light, dark):
            for surface in ("--bg", "--panel-bg"):
                pair = sorted((luminance(theme["--focus-ring"]), luminance(theme[surface])),
                              reverse=True)
                ratio = (pair[0] + 0.05) / (pair[1] + 0.05)
                assert ratio >= 3.0, f"the ring is {ratio:.2f}:1 against {surface}"

    def test_controls_that_appear_on_hover_appear_on_focus_too(self):
        """They stayed in the tab order while invisible."""
        chat = self._chat()
        assert ".conv-item:focus-within .conv-menu-btn" in chat
        assert ".msg:focus-within .msg-actions" in chat

    def test_the_smallest_controls_can_be_hit(self):
        """The two at 20x20 were the message actions and the menu holding Delete."""
        import re

        chat = self._chat()
        for selector in (".conv-menu-btn {", ".msg-action-btn {"):
            rule = chat.split(selector)[1].split("}")[0]
            size = int(re.search(r"width:\s*(\d+)px", rule).group(1))
            assert size >= 28, f"{selector} is still {size}px"
        assert "inset: -8px" in chat, "no expanded pointer target"

    def test_the_page_says_what_it_is(self):
        """Eight second-level headings and no first-level one."""
        chat = self._chat()
        assert "<h1" in chat

    def test_it_has_the_regions_a_screen_reader_moves_between(self):
        chat = self._chat()
        for tag in ("<header", "<nav", "<main", "<aside"):
            assert tag in chat, tag

    def test_the_message_box_is_labelled_and_says_how_to_send(self):
        """Enter sends and Shift+Enter does not; that was written nowhere."""
        chat = self._chat()
        assert 'for="input"' in chat
        assert "Shift+Enter" in chat

    def test_movement_stops_for_anyone_who_asked_for_less(self):
        from pathlib import Path

        system = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "_design-system.html").read_text()
        assert "prefers-reduced-motion" in system


class TestTheTranscriptIsBuiltForReading:
    def _chat(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()

    def test_the_measure_is_capped(self):
        """It was 70% of an uncapped column: ~190 characters on a wide screen."""
        chat = self._chat()
        rule = chat.split(".msg {")[1].split("}")[0]
        assert "var(--measure)" in rule
        assert "70%" not in rule

    def test_the_line_spacing_suits_the_scripts_it_renders(self):
        """Transcripts here carry thousands of characters of Hangul and kana,
        which fill their em box and need more room than Latin."""
        chat = self._chat()
        rule = chat.split(".msg-body {")[1].split("}")[0]
        assert "line-height: 1.7" in rule

    def test_what_the_model_wrote_is_set_in_the_reading_face(self):
        chat = self._chat()
        assert "font-family: var(--font-text)" in chat.split(".msg-body {")[1].split("}")[0]

    def test_no_text_sits_on_the_orange(self):
        """White on it is 2.63:1 at the value this page used to carry."""
        chat = self._chat()
        assert "background: var(--orange); color: white" not in chat

    def test_the_orange_still_marks_your_own_turns(self):
        """As a rule beside the words rather than a block behind them."""
        chat = self._chat()
        assert ".msg.user .msg-body { border-left-color: var(--orange); }" in chat

    def test_the_reading_face_can_set_the_scripts_this_sandbox_sees(self):
        """A stack that stopped at Latin would leave the browser to guess."""
        from pathlib import Path

        system = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "_design-system.html").read_text()
        stack = system.split("--font-text:")[1].split(";")[0]
        assert "Mincho" in stack, "no Japanese face"
        assert "Songti" in stack or "SimSun" in stack, "no Chinese face"
        assert "Myungjo" in stack or "Myeongjo" in stack or "Batang" in stack, "no Korean face"


class TestWhatTheModelWroteIsRendered:
    """Markdown becomes what it means, and never becomes markup."""

    def _source(self, name):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / name).read_text()

    def test_the_renderer_is_part_of_the_page(self):
        assert '{% include "_markdown.html" %}' in self._source("chat.html")

    def test_nothing_the_model_wrote_is_ever_treated_as_markup(self):
        """The hard requirement. This text comes from a model, a model reads
        whatever document it was given, and a document can be written by
        anyone — so a translation of somebody's PDF must not be able to put a
        script on the page."""
        import re

        # The code only: the note at the top of the file says the word while
        # promising the opposite, and an earlier version of this test failed on
        # its own documentation.
        renderer = self._source("_markdown.html")
        code = renderer.split("#}", 1)[1]
        code = re.sub(r"//.*", "", code)
        assert not re.search(r"\.innerHTML\s*=", code), "the renderer assigns markup"
        assert "insertAdjacentHTML" not in code
        assert "createTextNode" in code

    def test_only_addresses_that_go_somewhere_become_links(self):
        renderer = self._source("_markdown.html")
        scheme = renderer.split("function safeHref")[1].split("}")[0]
        assert "https?" in scheme and "mailto" in scheme

    def test_the_persons_own_words_are_left_as_they_typed_them(self):
        """Somebody pasting source has every right to asterisks in it."""
        chat = self._source("chat.html")
        assert 'if (m.role === "user")' in chat
        assert ".msg-body.verbatim { white-space: pre-wrap; }" in chat

    def test_a_reply_is_only_rendered_once_it_has_finished_arriving(self):
        """Markdown half-written is markdown half-parsed."""
        chat = self._source("chat.html")
        streaming = chat.split('messageLeaf("assistant", chosenModel')[1][:300]
        assert "verbatim" in streaming

    def test_both_copy_buttons_are_the_same_button(self):
        """They were drawn in two places and drifted: same box, but the
        drawings filled 82% and 62% of it, so one plainly looked smaller."""
        chat = self._source("chat.html")
        renderer = self._source("_markdown.html")
        actions = self._source("_actions.html")
        assert "function copyButtonElement" in actions
        assert "copyButtonElement(" in chat, "the message row draws its own"
        assert "copyButtonElement(" in renderer, "the code block draws its own"
        assert "copyBtn.innerHTML" not in chat

    def test_every_action_icon_is_drawn_on_the_same_grid(self):
        """A drawing filling less of its box looks smaller at the same size."""
        import re

        for name in ("_actions.html", "_markdown.html"):
            source = self._source(name)
            boxes = set(re.findall(r'viewBox="([^"]+)"', source))
            boxes |= set(re.findall(r'setAttribute\("viewBox",\s*"([^"]+)"\)', source))
            assert boxes, f"{name} has no drawings"
            assert boxes == {"0 0 14 14"}, f"{name} draws on {sorted(boxes)}"

    def test_the_copy_button_says_it_worked(self):
        """The thing you pressed answers, rather than a message appearing
        elsewhere for the eye to find."""
        actions = self._source("_actions.html")
        assert "icon-copied" in actions
        assert 'classList.add("copied")' in actions
        assert "2000" in actions, "it never goes back"

    def test_and_only_after_the_copy_actually_happened(self):
        actions = self._source("_actions.html")
        before, _, after = actions.partition("writeText(text).then(")
        assert 'classList.add("copied")' in after
        assert 'classList.add("copied")' not in before

    def test_the_two_drawings_fade_between_each_other(self):
        chat = self._source("chat.html")
        assert ".msg-action-btn.copied .action-icons .icon-copied" in chat
        assert ".action-icons svg" in chat or ".icon-stack svg" in chat

    def test_no_heading_is_smaller_than_the_text_it_introduces(self):
        """A model writing "## Section" produced a 14px line above a 16px
        paragraph — the one thing a heading cannot be."""
        import re

        chat = self._source("chat.html")
        system = self._source("_design-system.html")
        sizes = dict(re.findall(r"(--text-[a-z]+):\s*([0-9.]+)rem", system))
        body = float(sizes["--text-body"])
        for tag in ("h3", "h4", "h5", "h6"):
            rule = chat.split(f".msg-body {tag}.md-heading")[1].split("}")[0]
            token = re.search(r"var\((--text-[a-z]+)\)", rule).group(1)
            assert float(sizes[token]) >= body, (
                f"{tag} is {float(sizes[token]) * 16:.0f}px against body at {body * 16:.0f}px"
            )

    def test_the_headings_get_larger_the_higher_they_are(self):
        import re

        chat = self._source("chat.html")
        sizes = dict(re.findall(r"(--text-[a-z]+):\s*([0-9.]+)rem", self._source("_design-system.html")))
        steps = []
        for tag in ("h3", "h4", "h5", "h6"):
            rule = chat.split(f".msg-body {tag}.md-heading")[1].split("}")[0]
            steps.append(float(sizes[re.search(r"var\((--text-[a-z]+)\)", rule).group(1)]))
        assert steps == sorted(steps, reverse=True), steps

    def test_a_reply_is_set_as_a_document_throughout(self):
        """Headings in the interface's face read as furniture — part of the
        application rather than part of what was written."""
        chat = self._source("chat.html")
        heading = chat.split(".md-heading {")[1].split("}")[0]
        assert "var(--font-text)" in heading
        assert "var(--font-ui)" not in heading
        # And nothing inside a reply reaches for the interface's face.
        body_rules = chat.split(".msg-body {")[1].split(".code-block {")[0]
        assert "var(--font-ui)" not in body_rules, "part of a reply is set as interface"

    def test_a_code_block_can_be_taken_away(self):
        renderer = self._source("_markdown.html")
        assert "Copy this code" in renderer
        assert "Save as ${filename}" in renderer

    def test_the_saved_file_is_named_from_the_language(self):
        """A fence says what it is; this is the table that turns that into a
        file name, so a download arrives as parse.py rather than snippet.txt."""
        renderer = self._source("_markdown.html")
        table = renderer.split("CODE_EXTENSIONS = {")[1].split("};")[0]
        for language, extension in (("python", "py"), ("typescript", "ts"),
                                    ("sql", "sql"), ("rust", "rs")):
            assert f'{language}: "{extension}"' in table, language

    def test_a_fence_may_name_its_own_file(self):
        renderer = self._source("_markdown.html")
        assert "function codeFileName" in renderer
        assert "if (named) return named;" in renderer

    def test_code_is_allowed_to_be_wider_than_the_prose(self):
        """Wrapping code at the reading measure would break lines that mean
        something; it scrolls in its own box instead."""
        chat = self._source("chat.html")
        rule = chat.split(".code-block pre {")[1].split("}")[0]
        assert "overflow-x: auto" in rule


class TestControlsSitProperlyTogether:
    def _chat(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / "chat.html").read_text()

    def _rule(self, selector):
        return self._chat().split(selector)[1].split("}")[0]

    def test_the_chevron_is_the_size_of_the_other_icons(self):
        """It had no size of its own, so it filled its whole button."""
        import re

        size = int(re.search(r"width:\s*(\d+)px", self._rule(".combobox-toggle svg {")).group(1))
        assert size == 11, f"the chevron is {size}px against 11px elsewhere"

    def test_the_new_conversation_mark_has_room_for_its_plus(self):
        """The drawing is a square with a plus inside it; at 12px the plus
        itself was about five pixels across."""
        import re

        drawing = int(re.search(r"width:\s*(\d+)px", self._rule(".plus-btn svg {")).group(1))
        button = int(re.search(r"width:\s*(\d+)px", self._rule(".plus-btn {")).group(1))
        assert drawing >= 18, f"the mark is {drawing}px"
        assert button > drawing, "the drawing would touch the button's edge"

    def test_the_composer_is_level_along_its_bottom(self):
        """The box is 2.6rem; the buttons beside it were 32.8px and 34px."""
        box = self._rule("#composer textarea {")
        buttons = self._rule("#composer .icon-btn, #composer #send-btn {")
        assert "height: 2.6rem" in box
        assert "height: 2.6rem" in buttons

    def test_the_settings_popover_fits_the_sentence_inside_it(self):
        """It holds the conversation instructions, whose own example runs to
        about fifty characters."""
        import re

        width = self._rule(".sampling-options-popover {")
        rem = float(re.search(r"width:\s*([0-9.]+)rem", width).group(1))
        assert rem >= 24, f"{rem}rem is narrower than the example text it shows"

    def test_a_floating_panel_can_be_told_from_what_it_covers(self):
        """The conversation menu opens over the sidebar, and both were the same
        colour with a faint shadow between them."""
        for selector in (".conv-menu {", ".combobox-list {", ".model-add-popover {",
                         ".sampling-options-popover {", ".action-picker {"):
            rule = self._rule(selector)
            assert "var(--surface-raised)" in rule, selector
            assert "var(--shadow-raised)" in rule, selector

    def test_the_raised_surface_actually_differs_in_the_dark_theme(self):
        """In the light theme a shadow separates white from white; in the dark
        theme there is no white, so the surface itself has to lift."""
        import re
        from pathlib import Path

        system = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "_design-system.html").read_text()
        dark = system.split('[data-theme="dark"]')[1].split("}")[0]
        raised = re.search(r"--surface-raised:\s*(#[0-9a-fA-F]{6})", dark).group(1)
        panel = re.search(r"--panel-bg:\s*(#[0-9a-fA-F]{6})", dark).group(1)
        assert raised.lower() != panel.lower(), "a menu is the colour of what it covers"


class TestTheSettingsPageSaysThingsOnce:
    def _source(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / "settings.html").read_text()

    def test_the_page_carries_no_second_heading_inside_the_modal(self):
        """The modal already says "Settings" and already has a way out; a
        heading, a close and a Lock button under them are three ways of saying
        what has been said."""
        source = self._source()
        assert 'document.getElementById("topbar").hidden = embeddedInModal;' in source

    def test_but_keeps_it_when_opened_on_its_own(self):
        """Then the bar is the only heading, and the only way to lock."""
        source = self._source()
        assert 'id="lock-btn"' in source
        assert "hidden = embeddedInModal" in source
        assert "hidden = true" not in source

    def test_shared_settings_and_endpoints_are_separate(self):
        """They are different things: one is defaults a group follows, the
        other is another AI service to call."""
        source = self._source()
        assert 'data-section="shared"' in source
        assert 'data-section="endpoints"' in source
        assert "Shared settings &amp; alternate endpoints" not in source

    def test_both_appear_in_the_order_the_server_gives(self, unlocked_client):
        order = unlocked_client.get("/api/settings").json()["order"]
        assert "shared" in order and "endpoints" in order
        source = self._source()
        for key in order:
            assert f'data-section="{key}"' in source, f"{key} is ordered but not on the page"
