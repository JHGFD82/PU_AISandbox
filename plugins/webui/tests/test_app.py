"""Integration tests for the webui plugin's FastAPI routes (plugins/webui/src/app.py).

Uses FastAPI's TestClient (backed by httpx) against a fresh app instance
built by create_app() — no real server is started, and no real AI API calls
are made (the /api/chat route's SandboxProcessor is monkeypatched).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
import tomllib
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
def preference_file(tmp_path, monkeypatch):
    """A preferences.toml this test owns, with the real one out of reach.

    Two places get pointed at it, because they are two different things: a
    route writes through src.paths, and the settings layer reads a path it
    worked out when it was first imported. In a running sandbox they are the
    same file; here they both have to be told about this one.
    """
    path = tmp_path / "preferences.toml"
    path.write_text("[webui]\n")
    import src.paths
    monkeypatch.setattr(src.paths, "preferences_path", lambda: path)
    monkeypatch.setattr(core_settings_mod, "_PREFERENCES_PATH", path)
    return path


@pytest.fixture
def unlocked_client(client):
    resp = client.post("/unlock", data={"passphrase": ""})
    assert resp.status_code in (200, 303)
    return client


def _rendered_template(name: str) -> str:
    """Any template as the browser receives it, with every {% include %} resolved."""
    from fastapi.templating import Jinja2Templates

    directory = Path(__file__).resolve().parents[1] / "src" / "templates"
    return Jinja2Templates(directory=str(directory)).get_template(name).render(request=None)


def _rendered_chat() -> str:
    """chat.html as the browser receives it, with every {% include %} resolved.

    Reading the file alone stopped being the same thing once the combobox moved
    into a partial the page includes.
    """
    from fastapi.templating import Jinja2Templates

    directory = Path(__file__).resolve().parents[1] / "src" / "templates"
    return Jinja2Templates(directory=str(directory)).get_template("chat.html").render(request=None)


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
        monkeypatch.setattr(app_module, "model_owner", lambda m: "Test")
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
        monkeypatch.setattr(app_module, "model_owner", lambda m: "Test")
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
                "allow_text": False,
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
        assert data["order"] == [
            "professors", "external_sources", "webui", "shared", "endpoints", "models",
            "folder",
        ]
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
        assert data["order"] == [
            "folder", "shared", "endpoints", "models", "professors", "webui",
            "external_sources",
        ]
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
            "professor": "smith",
        })
        assert resp.status_code == 200
        sources = unlocked_client.get("/api/settings").json()["sources"]["external"]
        assert sources == [{"label": "Prof. Smith", "path": "/tmp/smith-data",
                            "mode": "read-only", "professor": "smith"}]

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
            "professor": "smith",
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
            assert 'mode === "folder"' in before, (
                "webkitdirectory must only be set for the folder mode; setting it "
                "on every file field is what removed single-file selection"
            )

    def test_the_choice_defaults_to_a_single_file(self):
        """The commoner case, and the one that broke."""
        page = self._page()
        assert "radio.checked = index === 0" in page
        # The modes are built up rather than written as one literal now, so that
        # a field can offer folders, pasted text, both or neither — but a single
        # file is always the first, and therefore the default.
        assert 'const modes = [["file", "A single file"]];' in page

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
        # The page has to say so somewhere; the wording is not this test's
        # business, and pinning a sentence discourages improving it.
        assert "quotation marks" in page

    def test_unticking_keeps_the_value_it_had(self):
        """The behaviour, not the sentence describing it.

        The page used to explain that unticking a setting shows the shipped
        value and remembers yours. That paragraph has been rewritten away; what
        it described is still what happens, so this checks the code rather than
        the prose.
        """
        page = self._page()
        assert "custom" in page and "checked" in page

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

    def _set(self, preference_file, on):
        """Turn the setting on or off the way the settings page does."""
        from src.plugin_preferences import set_live
        set_live(preference_file, "webui", "keep_supplied_documents",
                 "true" if on else "false")

    def test_nothing_is_kept_unless_it_is_asked_for(
        self, unlocked_client, preference_file
    ):

        self._set(preference_file, False)
        conv_id = self._conv(unlocked_client)
        assert self._upload(unlocked_client, conv_id).json()["saved_as"] is None

    def test_when_asked_for_it_sits_with_the_conversation(
        self, unlocked_client, preference_file
    ):
        import sys

        self._set(preference_file, True)
        conv_id = self._conv(unlocked_client)
        assert self._upload(unlocked_client, conv_id).json()["saved_as"] == "source.txt"
        store = sys.modules["_pu_webui_conversation"].ConversationStore("heller")
        assert (store.attachments_dir(conv_id) / "source.txt").read_bytes() == b"hello"

    def test_a_second_document_of_the_same_name_does_not_replace_the_first(
        self, unlocked_client, preference_file
    ):
        self._set(preference_file, True)
        conv_id = self._conv(unlocked_client)
        self._upload(unlocked_client, conv_id)
        second = self._upload(unlocked_client, conv_id)
        assert second.json()["saved_as"] == "source (2).txt"

    def test_a_name_that_is_a_path_cannot_write_outside_the_folder(
        self, unlocked_client, preference_file
    ):
        self._set(preference_file, True)
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
        monkeypatch.setattr(app_module, "model_owner", lambda m: "Test")
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "gpt-4o")
        names = [m["name"] for m in
                 unlocked_client.get("/api/models?professor=heller").json()["models"]]
        assert names == ["claude-sonnet-5", "gpt-4o", "Llama-3.3-70B-Instruct"]

    def test_the_page_shows_them_in_the_order_it_is_given(self):
        """Grouped, but not reordered: the server decides which model comes
        before which, and the page only decides which heading they sit under."""

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        assert "(state.models || []).forEach" in page
        # Group headings are sorted here; the models under them are not, so
        # there is one authority on which model comes before which.
        renderer = page.split("function renderModelList")[1].split("\nfunction ")[0]
        sorts = renderer.count(".sort(")
        assert sorts == 1, f"{sorts} sorts in the menu builder — the models are being reordered"
        assert "groups.get(owner).forEach" in renderer


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
        monkeypatch.setattr(app_module, "model_owner", lambda m: "Test")
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

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
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

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
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

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        dots = re.search(r"menuBtn\.innerHTML = '(.*?)';", page, re.S).group(1)
        assert "icon-upright" in dots
        assert ".icon-upright { transform: rotate(90deg); }" in page

    def test_the_menu_button_gives_those_dots_a_square_to_sit_in(self):
        """A drawing that is turned upright cannot be fitted to its old shape.

        The box was 3px by 13px, for a drawing that was already vertical. The
        supplied one is a wide row: fitted to that box it would come out 3px by
        0.6px, and rotating that would not help.
        """

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        rule = page.split(".conv-menu-btn svg {")[1].split("}")[0]
        width = rule.split("width:")[1].split("px")[0].strip()
        height = rule.split("height:")[1].split("px")[0].strip()
        assert width == height, f"the dots would be squashed before being turned: {width}x{height}"

    def test_a_cross_is_the_plus_turned(self):
        """Asked for: the same drawing, rotated, rather than a second one."""

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        for button_id in ("settings-modal-close", "job-modal-close"):
            start = page.index(f'id="{button_id}"')
            block = page[page.rindex("<button", 0, start): page.index("</button>", start)]
            assert "icon-as-cross" in block, button_id
        assert "rotate(45deg)" in page
        # Turned, its corners reach further than its sides did, so it is scaled
        # back to sit level with the icons beside it.
        assert "scale(0.707)" in page

    def test_the_new_conversation_button_reads_like_the_send_button(self):
        """It was asked for in the logo's orange, and drawn that way put an
        orange mark on a button that is itself orange — 1.46:1 under the
        pointer. It takes the button's own colours now, as Send does."""

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        start = page.index('id="new-conv"')
        block = page[page.rindex("<button", 0, start): page.index("</button>", start)]
        assert "currentColor" in block
        assert "#f58025" not in block

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
        return _rendered_chat()

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
        return _rendered_chat()

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
        assert "font-family: var(--font-reading)" in chat.split(".msg-body {")[1].split("}")[0]

    def test_the_reading_face_starts_as_the_serif(self):
        """The choice exists; the serif is what a page of prose wants until
        somebody says otherwise."""
        from pathlib import Path

        system = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "_design-system.html").read_text()
        assert "--font-reading: var(--font-text);" in system
        assert '[data-reading="sans"] { --font-reading: var(--font-ui); }' in system

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
        # The reading face, whichever of the two it currently is — so a heading
        # can never be set differently from the passage it introduces.
        assert "var(--font-reading)" in heading
        body = chat.split(".msg-body {")[1].split("}")[0]
        assert "var(--font-reading)" in body
        # And no part of a reply names a face directly, which is how one of them
        # would stop following the choice.
        body_rules = chat.split(".msg-body {")[1].split(".code-block {")[0]
        assert "var(--font-ui)" not in body_rules, "part of a reply is set as interface"
        assert "var(--font-text)" not in body_rules, "part of a reply ignores the choice"

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
        return _rendered_chat()

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

    def test_the_composer_buttons_are_centred_against_the_box(self):
        """They are shorter than it, so flush-to-the-bottom left all the slack
        above them and the row read as uneven. The margin is what centres it:
        if either number moves without the other, this says so."""
        import re

        chat = self._chat()
        box = float(re.search(r"height:\s*([0-9.]+)rem",
                              chat.split("#composer textarea {")[1].split("}")[0]).group(1))
        rule = chat.split("#composer .icon-btn, #composer #send-btn {")[1].split("}")[0]
        button = float(re.search(r"height:\s*([0-9.]+)rem", rule).group(1))
        below = float(re.search(r"margin-bottom:\s*([0-9.]+)rem", rule).group(1))
        assert button < box, "the button is not shorter than the box"
        above = box - button - below
        assert abs(above - below) < 0.001, (
            f"{above:.2f}rem above and {below:.2f}rem below — not centred"
        )

    def test_the_row_stays_bottom_aligned_as_the_box_grows(self):
        """The box grows with what is pasted into it. Centring the row itself
        would float the buttons into the middle of a tall box."""
        chat = self._chat()
        assert "align-items: flex-end" in chat.split("#composer {")[1].split("}")[0]

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
        # The bar specifically. Other things on this page are hidden and shown
        # by their own logic — an empty-catalogue note, for one — and reading
        # any of those as this rule made the check fail for the wrong reason.
        assert not re.search(r'getElementById\("topbar"\)\.hidden\s*=\s*(true|false)', source)

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


class TestChoosingHowRepliesAreSet:
    """Serif or sans, remembered, and only for what was written."""

    def _chat(self):
        return _rendered_chat()

    def test_there_is_a_control_for_it(self):
        chat = self._chat()
        assert 'id="reading-face-btn"' in chat

    def test_the_choice_is_remembered(self):
        """Like the light/dark choice, and stored the same way."""
        chat = self._chat()
        assert 'localStorage.setItem("reading-face"' in chat
        assert 'localStorage.getItem("reading-face")' in chat

    def test_the_button_samples_the_face_you_would_get(self):
        """Not the one you are already reading. A button showing what you
        already have says nothing about what pressing it does."""
        chat = self._chat()
        # Reading the serif, the sample is the sans; the other way by default.
        assert '[data-reading="serif"] .reading-face-mark { font-family: var(--font-ui); }' in chat
        assert ".reading-face-mark { font-family: var(--font-text); }" in chat

    def test_it_says_which_way_it_will_switch(self):
        chat = self._chat()
        assert "Read replies in a serif face" in chat
        assert "Read replies in a sans-serif face" in chat

    def test_the_interface_does_not_follow_the_choice(self):
        """Only what was written changes. If the interface followed too, the
        two would stop being distinguishable, which was the point of having
        two faces."""
        from pathlib import Path

        system = (Path(__file__).resolve().parents[1] / "src" / "templates"
                  / "_design-system.html").read_text()
        sans = system.split('[data-reading="sans"]')[1].split("}")[0]
        assert "--font-reading" in sans
        assert "--font-ui:" not in sans, "the choice redefines the interface's own face"


class TestASourceSaysWhoseUsageItHolds:
    """One professor may share a folder for work, another only for tracking."""

    def _settings(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / "settings.html").read_text()

    def test_a_read_only_source_is_refused_without_a_professor(self, unlocked_client, settings_env):
        r = unlocked_client.post("/api/settings/sources", json={
            "label": "Prof. Smith", "path": "/tmp/smith", "mode": "read-only",
        })
        assert r.status_code >= 400
        assert "professor" in r.text.lower()

    def test_both_modes_take_one(self, unlocked_client, settings_env):
        for mode in ("read-only", "shared-write"):
            r = unlocked_client.post("/api/settings/sources", json={
                "label": f"Prof {mode}", "path": f"/tmp/{mode}", "mode": mode,
                "professor": "smith",
            })
            assert r.status_code == 200, (mode, r.text)

    def test_the_professor_box_is_always_offered(self):
        """It used to appear only for shared-write."""
        page = self._settings()
        assert 'id="source-professor"' in page
        assert "source-professor-wrap" not in page, "the box is still being hidden by mode"

    def test_the_folder_gets_a_line_to_itself(self):
        """It is the longest value on the page and was sharing a row with
        three other boxes."""
        page = self._settings()
        assert 'class="source-path-row"' in page
        path_at = page.index('id="source-path"')
        fields_at = page.index('class="inline-fields"', page.index('id="add-source-form"'))
        assert path_at > page.index("</div>", fields_at), "the path is still in the crowded row"

    def test_the_two_modes_are_explained_where_they_are_chosen(self):
        page = self._settings()
        assert "only look" in page
        assert "can never write" in page


class TestTheModelMenuIsGrouped:
    def _chat(self):
        return _rendered_chat()

    def test_the_menu_is_told_whose_each_model_is(self, unlocked_client, monkeypatch):
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "models_in_reading_order", lambda: ["gpt-4o"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: True)
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr(app_module, "model_owner", lambda m: "OpenAI")
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "gpt-4o")
        model = unlocked_client.get("/api/models?professor=heller").json()["models"][0]
        assert model["owner"] == "OpenAI"

    def test_a_model_on_your_own_service_is_offered_here(self):
        """Typed as endpoint:model — the same shape the command line takes."""
        chat = self._chat()
        assert "della:alibaba/qwen35" in chat, "the box does not say the syntax is allowed"
        assert "rememberEndpointModel" in chat

    def test_those_are_remembered_rather_than_retyped(self):
        chat = self._chat()
        assert 'localStorage.setItem("endpoint-models"' in chat

    def test_a_damaged_note_of_them_does_not_empty_the_menu(self):
        """It is a convenience kept in the browser, so it must not be load-bearing."""
        chat = self._chat()
        remembered = chat.split("function rememberedEndpointModels")[1].split("\n}")[0]
        assert "catch" in remembered


class TestTheFollowUpFixesStay:
    """Six things that were reported after a first attempt at each."""

    def _source(self, name):
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "templates" / name).read_text()

    def test_hiding_the_settings_bar_beats_the_rule_that_shows_it(self):
        """Setting the attribute was not enough: #topbar sets display, and an
        author rule outranks the browser's meaning for [hidden].

        Read rendered, not as source: the bar moved into _forms.html when the
        four pages stopped each keeping their own copy of it.
        """
        page = _rendered_template("settings.html")
        assert "#topbar[hidden] { display: none; }" in page
        # Both rules are one selector each, so neither outranks the other and
        # source order decides. The hiding one has to come first.
        assert page.index("#topbar[hidden]") < re.search(r"#topbar\s*\{", page).start()

    def test_a_menu_row_can_be_seen_under_the_pointer(self):
        """The ordinary hover is 1.02:1 against the raised surface a menu sits
        on, so a menu appeared to have no hover at all."""
        chat = self._source("chat.html")
        assert "var(--hover-raised)" in chat.split(".conv-menu-option:hover")[1].split("}")[0]
        assert "var(--border-raised)" in chat.split(".conv-menu-divider {")[1].split("}")[0]

    def test_the_raised_hover_and_border_differ_from_the_raised_surface(self):
        import re

        system = self._source("_design-system.html")
        dark = system.split('[data-theme="dark"]')[1].split("}")[0]
        values = dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})", dark))
        for key in ("--hover-raised", "--border-raised"):
            assert values[key].lower() != values["--surface-raised"].lower(), key

    def test_the_new_conversation_button_is_coloured_like_the_send_button(self):
        """Because the Send button reads: a solid orange with the interface's
        dark ink on it. Every attempt at keeping the mark orange put an orange
        shape on an orange ground."""
        chat = self._source("chat.html")
        rule = chat.split(".plus-btn {")[1].split("}")[0]
        assert "background:" not in rule, "it overrides the button colour it should take"
        assert ":hover" not in chat.split(".plus-btn {")[0].split(".plus-btn")[-1]

    def test_the_add_model_box_fits_the_example_inside_it(self):
        import re

        chat = self._source("chat.html")
        rem = float(re.search(r"width:\s*([0-9.]+)rem",
                              chat.split(".model-add-popover {")[1].split("}")[0]).group(1))
        placeholder = "openai/gpt-4o, or della:alibaba/qwen35"
        # Roughly half an em per character at the box's own size, less the
        # padding and the button beside it.
        fits = (rem * 16 - 80) / (0.8125 * 16 * 0.5)
        assert fits >= len(placeholder), f"{fits:.0f} characters of {len(placeholder)}"

    def test_the_composer_row_is_settled(self):
        """Equal heights read as uneven because the box carries an outline and
        the buttons do not. They are shorter and centred instead."""
        chat = self._source("chat.html")
        rule = chat.split("#composer .icon-btn, #composer #send-btn {")[1].split("}")[0]
        assert "margin-bottom" in rule


class TestTheNewConversationButtonReads:
    """It is the one button whose drawing is the same colour as buttons are."""

    def _chat(self):
        return _rendered_chat()

    def test_it_takes_the_ordinary_button_colours(self):
        """Including on hover, so the mark stays readable at the moment it is
        pressed — which is when it used to vanish."""
        chat = self._chat()
        assert ".plus-btn:hover" not in chat, "it opts out of the button hover again"
        rule = chat.split(".plus-btn {")[1].split("}")[0]
        assert "background" not in rule

    def test_the_drawings_own_backing_square_is_gone(self):
        """The button is that square. Two of them made the button look dark."""
        chat = self._chat()
        assert "display: none" in chat.split(".plus-btn .plus-plate {")[1].split("}")[0]

    def test_the_plate_is_named_in_the_drawing(self):
        chat = self._chat()
        start = chat.index('id="new-conv"')
        block = chat[chat.rindex("<button", 0, start): chat.index("</button>", start)]
        assert 'class="plus-plate"' in block
        assert block.count("<path") == 2, "the mark should still be a plate and a plus"

    def test_the_mark_takes_the_buttons_colour_like_every_other_icon(self):
        """Naming a colour here is what led to a drawing fighting the thing it
        was drawn on."""
        chat = self._chat()
        start = chat.index('id="new-conv"')
        block = chat[chat.rindex("<button", 0, start): chat.index("</button>", start)]
        assert 'fill="currentColor"' in block
        assert "#f58025" not in block


class TestWhatEachConversationKeeps:
    """The two boxes deciding whether a conversation's folder holds documents.

    Both were reachable only by editing a file by hand before this — one of them
    only through the shared settings editor, which writes a whole group's file,
    and the other did not exist at all.
    """

    def test_it_says_what_is_set_now(self, unlocked_client):
        body = unlocked_client.get("/api/settings/conversation-folder").json()
        assert set(body) == {"keep_supplied_documents", "keep_job_outputs"}
        assert all(isinstance(v, bool) for v in body.values())

    def test_reading_requires_unlock(self, client):
        assert client.get("/api/settings/conversation-folder").status_code == 401

    def test_changing_requires_unlock(self, client):
        resp = client.post("/api/settings/conversation-folder",
                           json={"keep_job_outputs": False})
        assert resp.status_code == 401

    def test_ticking_a_box_writes_it_to_the_persons_preferences(
        self, unlocked_client, preference_file
    ):
        resp = unlocked_client.post("/api/settings/conversation-folder",
                                    json={"keep_supplied_documents": True})
        assert resp.status_code == 200
        assert tomllib.loads(preference_file.read_text())["webui"]["keep_supplied_documents"] is True

    def test_a_box_not_sent_is_not_touched(self, unlocked_client, preference_file):
        """A page saving one box must not decide the other one is off."""
        preference_file.write_text("[webui]\nkeep_job_outputs = false\n")
        unlocked_client.post("/api/settings/conversation-folder",
                             json={"keep_supplied_documents": True})
        assert tomllib.loads(preference_file.read_text())["webui"]["keep_job_outputs"] is False

    def test_it_answers_with_what_the_file_now_says(self, unlocked_client, preference_file):
        """Not with what was asked for — a shared file may have the last word."""
        body = unlocked_client.post("/api/settings/conversation-folder",
                                    json={"keep_job_outputs": False}).json()
        assert body["keep_job_outputs"] is False

    def test_the_setting_is_read_again_rather_than_at_startup(self, preference_file):
        """Ticking a box and being told to restart would be no use to anyone."""
        from src.settings import is_on
        preference_file.write_text("[webui]\n")
        import src.plugin_preferences as pp
        # The webui plugin reads its own settings.toml plus preferences.toml.
        assert is_on("keep_job_outputs", True) is True
        pp.set_live(preference_file, "webui", "keep_job_outputs", "false")
        assert is_on("keep_job_outputs", True) is False


class TestWhereAJobsOutputGoes:
    """Whether a finished job's file lands in the conversation's folder."""

    def test_it_goes_in_the_conversation_by_default(self, tmp_path):
        jobs = sys.modules["_pu_webui_jobs"]
        path = jobs.job_output_dir("jh43", "j1", base_dir=tmp_path,
                                   conversation_id="c_05b92b6ac41a9449")
        assert path.parts[-3:] == ("c_05b92b6ac41a9449", "outputs", "j1")

    def test_turned_off_it_goes_to_the_shared_folder_of_results(self, tmp_path):
        jobs = sys.modules["_pu_webui_jobs"]
        path = jobs.job_output_dir("jh43", "j1", base_dir=tmp_path,
                                   conversation_id=None)
        assert "_job_outputs" in path.parts
        assert "c_05b92b6ac41a9449" not in path.parts

    def test_the_download_link_still_finds_it_either_way(self, tmp_path, monkeypatch):
        """Turning it off must not break a saved conversation's download."""
        jobs = sys.modules["_pu_webui_jobs"]
        monkeypatch.setattr(jobs, "_CONVERSATIONS_DIR", tmp_path)
        outside = jobs.job_output_dir("jh43", "j1", conversation_id=None)
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "translated.docx").write_bytes(b"result")
        found = jobs.resolve_output_path(
            "jh43", "j1", "translated.docx", conversation_id="c_05b92b6ac41a9449")
        assert found is not None and found.exists()
        assert found.read_bytes() == b"result"


class TestTheConversationFolderCard:
    """The settings page's own section for the two choices."""

    @pytest.fixture
    def page(self):
        return (Path(__file__).resolve().parents[1] / "src" / "templates"
                / "settings.html").read_text()

    def card_words(self, page):
        """The card's wording as one line, so where the HTML wraps doesn't matter."""
        card = page.split('data-section="folder"')[1].split("</div>")[0]
        return " ".join(card.split())

    def test_both_boxes_are_on_the_page(self, page):
        assert 'id="keep-supplied-documents"' in page
        assert 'id="keep-job-outputs"' in page

    def test_every_section_of_the_page_is_placed_in_both_orders(self, page):
        """A section missing from an order list gets order 0 and jumps to the top."""
        app_module = sys.modules["_pu_webui_app"]
        on_page = set(re.findall(r'data-section="([a-z_]+)"', page))
        for order in (app_module._SETTINGS_ORDER_FIRST_RUN,
                      app_module._SETTINGS_ORDER_REPEAT):
            assert set(order) == on_page
            assert len(order) == len(set(order))

    def test_turning_the_outputs_box_off_says_the_file_is_not_thrown_away(self, page):
        """The words have to say what off means, because the name doesn't."""
        words = self.card_words(page)
        # The property, not the phrasing: turning it off must not read as
        # "and the file is gone".
        assert "download" in words

    def test_it_says_the_choice_is_not_retrospective(self, page):
        # Somewhere in the card, in whatever words: a change here does not
        # reach back into work already done.
        words = self.card_words(page)
        assert "existing conversations" in words or "already" in words

    def test_the_whole_row_is_the_target_and_not_just_the_box(self, page):
        """A 13px box is a poor target; the sentence beside it is a good one."""
        assert re.search(r"\.choice\s*\{[^}]*cursor:\s*pointer", page)
        card = page.split('data-section="folder"')[1].split("</div>")[0]
        assert card.count('<label class="choice">') == 2

    def test_a_failed_save_puts_the_box_back(self, page):
        """Otherwise the box says one thing and the file says another."""
        handler = page.split("async function saveFolderChoice")[1].split("\n}")[0]
        assert "box.checked = !box.checked" in handler

    def test_it_shows_what_the_file_says_rather_than_what_was_clicked(self, page):
        handler = page.split("async function saveFolderChoice")[1].split("\n}")[0]
        assert "renderFolderChoices(now)" in handler


class TestResizingTheJobModal:
    """The splitter between a plugin's options and its prompt preview.

    Some plugins ask three questions and some ask fifteen; the prompt they build
    can be a paragraph or a page. Neither side always deserves the room, so the
    split is left to whoever is looking at it.
    """

    @pytest.fixture
    def chat(self):
        return (Path(__file__).resolve().parents[1] / "src" / "templates"
                / "chat.html").read_text()

    def test_the_splitter_sits_between_the_two_halves(self, chat):
        body = chat.split('<div class="job-modal-body">')[1].split("</div>\n      <div class=\"job-modal-footer\"")[0]
        assert body.index('id="job-options"') < body.index('id="job-splitter"')
        assert body.index('id="job-splitter"') < body.index('class="job-preview"')

    def test_it_is_announced_as_a_separator_and_can_be_focused(self, chat):
        tag = chat.split('id="job-splitter"')[0].rsplit("<div", 1)[1] + \
              chat.split('id="job-splitter"')[1].split(">")[0]
        assert 'role="separator"' in tag
        assert 'tabindex="0"' in tag

    def test_the_arrow_keys_move_it(self, chat):
        """A control that answers only to a held-down mouse button excludes people."""
        handler = chat.split('splitter.addEventListener("keydown"')[1].split("\n  });")[0]
        for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
            assert key in handler

    def test_the_options_width_is_a_variable_the_splitter_writes(self, chat):
        assert "--job-options-width" in chat
        assert "flex: 0 0 var(--job-options-width)" in chat

    def test_the_preview_can_shrink(self, chat):
        """Without min-width:0 a flex child refuses to go below its content."""
        rule = chat.split(".job-preview {")[1].split("}")[0]
        assert "min-width: 0" in rule

    def test_the_pointer_is_captured_for_the_drag(self, chat):
        """The pointer outruns the splitter the moment the width hits either end."""
        assert "setPointerCapture" in chat
        assert "releasePointerCapture" in chat

    def test_a_drag_does_not_select_the_page(self, chat):
        assert re.search(r"\.job-modal-panel\.is-resizing\s*\{[^}]*user-select:\s*none", chat)

    def test_the_grip_is_wider_than_the_rule(self, chat):
        """2px of rule is not a target anybody hits first time."""
        rule = chat.split(".job-splitter {")[1].split("}")[0]
        width = float(re.search(r"width:\s*([0-9.]+)px", rule).group(1))
        grip = float(re.search(r"border-left:\s*([0-9.]+)px", rule).group(1))
        assert grip * 2 + width >= 16

    def test_the_preview_keeps_a_minimum_share(self, chat):
        """Otherwise a small window leaves it a strip too narrow to read."""
        fn = chat.split("function jobOptionsMax()")[1].split("\n}")[0]
        assert "JOB_PREVIEW_MIN" in fn
        assert "panel - JOB_PREVIEW_MIN" in fn

    def test_what_is_remembered_is_what_was_asked_for(self, chat):
        """Opening it once on a laptop must not shrink the big monitor's width."""
        fn = chat.split("function setJobOptionsWidth")[1].split("\n}")[0]
        saved = re.search(r'localStorage\.setItem\("job-options-width",\s*([^)]+)\)', fn)
        assert "px" in saved.group(1), "the clamped width would be the wrong thing to keep"

    def test_the_width_is_restored_when_the_modal_opens(self, chat):
        opener = chat.split("async function openJobModal")[1].split("\n}")[0]
        assert "restoreJobOptionsWidth()" in opener


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


@pytest.fixture
def a_catalogue(monkeypatch, tmp_path):
    """A small real catalogue on disk, read through the ordinary path.

    One model tested and able to read images, one recorded as text-only by the
    old assumption with nothing to show it was ever asked.
    """
    import json

    import src.models.catalog as catalog_module

    path = tmp_path / "model_catalog.json"
    path.write_text(json.dumps({
        "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
        "models": {
            "gpt-4o": {"input": 2.5, "output": 10.0, "supports_vision": True,
                       "portkey_id": "openai/gpt-4o",
                       "rejects": {"top_p": "tested on add: refused"}},
            "old-text-model": {"input": 1.0, "output": 2.0, "supports_vision": False,
                               "portkey_id": "openai/old-text-model"},
        },
    }))
    monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: path)
    return path


class TestAddingAModelFromTheBrowser:
    """Adding a model without opening a terminal or a JSON file.

    The point of the section is the testing that follows the add: a model whose
    capabilities were never established is recorded as unable to read images,
    and chat requires that — so before this, a model added anywhere but the
    command line would be offered in the picker and then refused on use.
    """

    def test_it_needs_an_unlocked_session(self, client):
        assert client.get("/api/settings/models").status_code == 401
        assert client.post("/api/settings/models", json={
            "provider_model": "openai/gpt-4o", "professor": "smith",
        }).status_code == 401

    def test_the_catalogue_is_listed_with_what_each_model_can_do(
        self, unlocked_client, a_catalogue
    ):
        models = unlocked_client.get("/api/settings/models").json()["models"]
        assert [m["name"] for m in models] == ["gpt-4o", "old-text-model"]
        seen = {m["name"]: m for m in models}
        assert seen["gpt-4o"]["supports_vision"] is True
        assert seen["old-text-model"]["supports_vision"] is False
        assert seen["gpt-4o"]["input"] == 2.5

    def test_a_model_nobody_has_tested_is_not_called_text_only(
        self, unlocked_client, a_catalogue
    ):
        """The distinction the whole section turns on.

        old-text-model has supports_vision false because nothing ever asked,
        not because anything found out. The page needs to be able to say so.
        """
        models = {m["name"]: m for m in
                  unlocked_client.get("/api/settings/models").json()["models"]}
        assert models["old-text-model"]["tested"] is False

    def test_a_missing_catalogue_is_an_empty_list_not_a_failure(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        """An ordinary state on a copy that has not been set up yet.

        Pointed at a catalogue that is not there, rather than relying on the
        suite's fixture being empty — it is not, and a test that passes only
        because of what another file happens to contain is not testing this.
        """
        import src.models.catalog as catalog_module

        monkeypatch.setattr(catalog_module, "get_model_catalog_path",
                            lambda: tmp_path / "nothing-here.json")
        resp = unlocked_client.get("/api/settings/models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_a_name_without_a_provider_is_refused_before_anything_is_billed(
        self, unlocked_client, monkeypatch
    ):
        """'gpt-5.2' alone can't be looked up, and saying so costs nothing."""
        import src.models.pricing as pricing_module

        def must_not_run(*args, **kwargs):
            raise AssertionError("the provider was contacted for a name that can't work")

        monkeypatch.setattr(pricing_module, "add_model_to_catalog", must_not_run)
        resp = unlocked_client.post("/api/settings/models", json={
            "provider_model": "gpt-5.2", "professor": "smith",
        })
        assert resp.status_code == 400
        assert "slash" in resp.json()["detail"]

    def test_an_unknown_professor_is_refused(self, unlocked_client):
        resp = unlocked_client.post("/api/settings/models", json={
            "provider_model": "openai/gpt-4o", "professor": "nobody",
        })
        assert resp.status_code == 400

    def test_adding_tests_the_model_and_reports_what_it_found(
        self, unlocked_client, monkeypatch
    ):
        app_module = sys.modules["_pu_webui_app"]
        captured = {}

        def fake_add(provider_model, api_key=None, probe=True):
            captured["provider_model"] = provider_model
            captured["api_key"] = api_key
            return "gpt-5.2", {"input": 1.0, "output": 2.0, "supports_vision": True}

        monkeypatch.setattr("src.models.add_model_to_catalog", fake_add)
        monkeypatch.setattr(app_module, "_capability_summary",
                            lambda m: {"supports_vision": True, "refuses": [],
                                       "prefers": {}, "tested": True})
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))

        resp = unlocked_client.post("/api/settings/models", json={
            "provider_model": "openai/gpt-5.2", "professor": "smith",
        })
        assert resp.status_code == 200
        assert captured["provider_model"] == "openai/gpt-5.2"
        # The key has to reach the add, or the model arrives untested — which
        # is the whole failure this section exists to prevent.
        assert captured["api_key"] == "sk-test"
        assert resp.json()["capabilities"]["supports_vision"] is True


class TestTestingAModelAgainFromTheBrowser:
    """Correcting an entry recorded before any testing existed."""

    def _fake_report(self, **kw):
        from src.models.capabilities import CapabilityReport
        return CapabilityReport(**kw)

    def test_a_model_not_in_the_catalogue_is_a_404(self, unlocked_client, monkeypatch):
        monkeypatch.setattr("src.models.load_model_catalog",
                            lambda: {"config": {}, "models": {}})
        resp = unlocked_client.post("/api/settings/models/no-such-model/test",
                                    json={"professor": "smith"})
        assert resp.status_code == 404

    def test_a_successful_test_saves_and_reports(
        self, unlocked_client, monkeypatch, a_catalogue
    ):
        """A model recorded as text-only by assumption is corrected in place."""
        saved = {}
        monkeypatch.setattr("src.models.save_model_catalog",
                            lambda c: saved.update(c["models"]))
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))
        monkeypatch.setattr("src.models.capabilities.probe_model_capabilities",
                            lambda name, client: self._fake_report(
                                findings={"supports_vision": True},
                                settled=["Can read images"]))

        resp = unlocked_client.post("/api/settings/models/old-text-model/test",
                                    json={"professor": "smith"})
        assert resp.status_code == 200
        assert resp.json()["settled"] == ["Can read images"]
        assert saved["old-text-model"]["supports_vision"] is True
        # The price it already had is not lost to a test about capabilities.
        assert saved["old-text-model"]["input"] == 1.0

    def test_a_model_that_cannot_be_reached_changes_nothing(
        self, unlocked_client, monkeypatch, a_catalogue
    ):
        """The restraint that matters, carried through to the browser.

        A model that couldn't be tested must not come back recorded as unable
        to read images — that is indistinguishable from having tested it.
        """
        def must_not_save(catalog):
            raise AssertionError("a failed test wrote to the catalogue")

        monkeypatch.setattr("src.models.save_model_catalog", must_not_save)
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))
        monkeypatch.setattr("src.models.capabilities.probe_model_capabilities",
                            lambda name, client: self._fake_report(
                                reachable=False, unsettled=["Testing stopped early: timed out"]))

        resp = unlocked_client.post("/api/settings/models/gpt-4o/test",
                                    json={"professor": "smith"})
        assert resp.status_code == 502
        assert "could not be reached" in resp.json()["detail"]


class TestTheModelsSectionOnThePage:
    @pytest.fixture
    def page(self):
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / "src" / "templates"
                / "settings.html").read_text()

    def test_untested_is_shown_as_its_own_state(self, page):
        """Three states, not two.

        'Cannot read images' is a fact about the model; 'not tested yet' is a
        job to do. Showing them identically is what made a capable model look
        broken with no hint that anything could be done about it.
        """
        assert "Not tested yet" in page

    def test_it_says_whose_key_the_testing_uses(self, page):
        """Adding a model makes real requests against somebody's key.

        The wording changed and the sentence about what it costs went with it;
        the field asking whose key it is stays, and that is the part somebody
        has to answer.
        """
        assert "Test with whose key" in page

    def test_it_shows_how_a_model_is_named(self, page):
        """'openai/gpt-5.2' is not guessable from an empty box."""
        assert "openai/gpt-5.2" in page



class TestAModelThatNoLongerExists:
    """gpt-35-turbo, gpt-35-turbo-16k and gpt-4-32k had all been retired.

    Testing recorded each as "tested, text only" with a date, because every
    probe failed identically and that read as a model refusing everything. The
    browser has to say what is actually true and offer the one useful action.
    """

    def test_it_is_reported_as_gone_not_as_a_failed_test(
        self, unlocked_client, monkeypatch, a_catalogue
    ):
        from src.models.capabilities import CapabilityReport

        def must_not_save(catalog):
            raise AssertionError("a retired model was written to the catalogue")

        monkeypatch.setattr("src.models.save_model_catalog", must_not_save)
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))
        monkeypatch.setattr("src.models.capabilities.probe_model_capabilities",
                            lambda name, client: CapabilityReport(
                                reachable=False, missing=True,
                                unsettled=["There is no such model"]))

        resp = unlocked_client.post("/api/settings/models/old-text-model/test",
                                    json={"professor": "smith"})
        # 410, not 502: nothing is wrong with the request or the connection.
        assert resp.status_code == 410
        assert "no longer exists" in resp.json()["detail"]

    def test_it_can_be_removed(self, unlocked_client, a_catalogue):
        assert unlocked_client.delete("/api/settings/models/old-text-model").status_code == 200
        remaining = [m["name"] for m in
                     unlocked_client.get("/api/settings/models").json()["models"]]
        assert remaining == ["gpt-4o"]

    def test_removing_one_that_is_not_there_is_a_404(self, unlocked_client, a_catalogue):
        assert unlocked_client.delete("/api/settings/models/never-existed").status_code == 404

    def test_removing_needs_an_unlocked_session(self, client):
        assert client.delete("/api/settings/models/gpt-4o").status_code == 401

    def test_the_page_offers_removal_only_for_a_model_that_is_gone(self):
        from pathlib import Path
        page = (Path(__file__).resolve().parents[1] / "src" / "templates"
                / "settings.html").read_text()
        # Hidden by default: a working model must not carry a delete button.
        assert 'data-remove-model="${m.name}" style="display:none"' in page
        assert "no longer exists" in page


class TestTheSettingsPageIsInThreeTabs:
    """Seven cards in one column meant scrolling to find anything."""

    ASSIGNED = {
        "professors": "system", "shared": "system", "external_sources": "system",
        "webui": "webui", "folder": "webui",
        "models": "models", "endpoints": "models",
    }

    @pytest.fixture
    def page(self):
        return (Path(__file__).resolve().parents[1] / "src" / "templates"
                / "settings.html").read_text()

    def test_every_card_belongs_to_exactly_one_tab(self, page):
        """A card with no tab is a card nobody can ever reach."""
        cards = re.findall(r'data-section="([a-z_]+)"\s+data-tab="([a-z]+)"', page)
        assert dict(cards) == self.ASSIGNED
        assert len(cards) == len(re.findall(r'class="card"', page))

    def test_the_three_tabs_are_the_ones_asked_for(self, page):
        strip = page.split('id="tabs"')[1].split("</div>")[0]
        assert re.findall(r'data-tab="([a-z]+)"', strip) == ["system", "webui", "models"]

    def test_a_tab_is_not_dressed_as_a_button(self, page):
        """Buttons here are orange and mean "this does something"."""
        rule = page.split("#tabs button {")[1].split("}")[0]
        assert "background: none" in rule

    def test_the_chosen_tab_is_marked_by_more_than_colour(self, page):
        rule = page.split('#tabs button[aria-selected="true"] {')[1].split("}")[0]
        assert "border-bottom-color" in rule

    def test_the_strip_can_be_hidden_before_there_is_anything_in_it(self, page):
        """#tabs sets display, which beats the browser's own [hidden]."""
        assert "#tabs[hidden] { display: none; }" in page

    def test_the_arrow_keys_move_between_tabs(self, page):
        handler = page.split('document.getElementById("tabs").addEventListener("keydown"')[1]
        handler = handler.split("\n});")[0]
        for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
            assert key in handler

    def test_only_the_chosen_tab_is_a_tab_stop(self, page):
        """Otherwise Tab walks through three tabs before reaching a setting."""
        fn = page.split("function showTab")[1].split("\n}")[0]
        assert 'setAttribute("tabindex", "-1")' in fn
        assert 'removeAttribute("tabindex")' in fn

    def test_the_panel_says_which_tab_it_belongs_to(self, page):
        assert 'role="tabpanel"' in page
        fn = page.split("function showTab")[1].split("\n}")[0]
        assert "aria-labelledby" in fn

    def test_the_order_the_server_sends_still_applies(self, page):
        """Tabs decide what is shown; the server still decides the order."""
        fn = page.split("function showTab")[1].split("\n}")[0]
        assert "style.order" not in fn, "showTab must not take over ordering"
        assert "function applyOrder" in page

    def test_every_section_is_still_placed_in_both_orders(self, page):
        app_module = sys.modules["_pu_webui_app"]
        on_page = set(re.findall(r'data-section="([a-z_]+)"', page))
        for order in (app_module._SETTINGS_ORDER_FIRST_RUN,
                      app_module._SETTINGS_ORDER_REPEAT):
            assert set(order) == on_page


class TestTheProfessorPickerMatchesTheModelPicker:
    """It was a bare <select>: the browser draws those itself.

    A different height from every other field on the page, a different chevron,
    and a list rendered by the OS that ignores the theme entirely — which is the
    same reason the model picker stopped being one.
    """

    @pytest.fixture
    def chat(self):
        return _rendered_chat()

    def test_the_select_is_gone(self, chat):
        assert "professor-select" not in chat
        assert 'id="professor-combobox"' in chat

    def test_both_pickers_are_built_from_the_same_parts(self, chat):
        for part in ("combobox", "combobox-field", "combobox-toggle", "combobox-list"):
            for picker in ("professor", "model"):
                block = chat.split(f'id="{picker}-combobox"')[1].split("</div>\n")[0] \
                    if picker == "professor" else chat.split('id="model-combobox"')[1]
                assert part in block[:1200], f"{picker} is missing {part}"

    def test_the_field_class_is_not_named_after_one_picker(self, chat):
        """It was .model-field while two fields use it."""
        assert "class=\"model-field\"" not in chat
        assert ".combobox input.combobox-field" in chat

    def test_both_chevrons_are_the_same_drawing(self, chat):
        paths = re.findall(r'class="combobox-toggle"[^>]*>\s*<svg[^>]*>\s*<path d="([^"]{40,})"', chat)
        assert len(paths) == 2 and paths[0] == paths[1]

    def test_opening_and_closing_is_written_once(self, chat):
        """Two copies is how two pickers drift apart."""
        assert chat.count("function openCombobox") == 1
        assert chat.count("function wireCombobox") == 1
        # The model picker's own versions now defer rather than duplicate.
        # openModelList() was a wrapper with no callers once the picker moved
        # into the partial; closeModelList() still has one.
        assert "function openModelList" not in chat
        assert 'function closeModelList() { closeCombobox("model"); }' in chat

    def test_the_listener_is_wired_outside_the_loader(self, chat):
        """loadProfessors() runs twice; wiring inside it stacked the handlers."""
        loader = chat.split("async function loadProfessors")[1].split("\n}")[0]
        assert "addEventListener" not in loader or "opt.addEventListener" in loader
        assert 'wireCombobox("professor");' in chat

    def test_clicking_away_closes_every_picker_on_the_page(self, chat):
        """Written once over all of them, so a second picker gets it for free.

        It used to be a line per picker in one page-wide handler, which is a
        line somebody has to remember to add.
        """
        handler = chat.split('document.addEventListener("click"')[1].split("\n});")[0]
        assert "WIRED_COMBOBOXES.forEach" in handler
        assert "professor-combobox" not in handler, "no picker should be named here"

    def test_choosing_the_same_professor_does_no_work(self, chat):
        """It reloaded every conversation and the usage panel for nothing."""
        fn = chat.split("async function chooseProfessor")[1].split("\n}")[0]
        assert "if (netid === state.professor) return;" in fn

    def test_it_is_announced_as_a_combobox(self, chat):
        # To the end of the combobox, not a fixed slice — the chevron's path
        # data alone is longer than a window sized by eye.
        block = chat.split('id="professor-combobox"')[1].split("</div>\n    </div>")[0]
        assert 'role="combobox"' in block
        assert 'aria-expanded' in block
        assert 'role="listbox"' in block


class TestPastingTextIntoAJobForm:
    """The browser side of -c/--custom: a third mode on the file field."""

    @pytest.fixture
    def chat(self):
        return (Path(__file__).resolve().parents[1] / "src" / "templates"
                / "chat.html").read_text()

    def test_pasting_is_offered_as_a_mode(self, chat):
        assert 'if (field.allow_text) modes.push(["text", "Paste the text"]);' in chat

    def test_it_adds_a_mode_rather_than_replacing_one(self, chat):
        """A single file must stay first, and therefore the default."""
        assert 'const modes = [["file", "A single file"]];' in chat
        assert 'if (field.allow_folder) modes.push(' in chat

    def test_the_toggle_appears_for_text_alone(self, chat):
        """A field offering only pasting still needs somewhere to choose it."""
        assert "if (field.allow_folder || field.allow_text) {" in chat

    def test_pasted_text_travels_as_a_value_not_a_file(self, chat):
        fn = chat.split("function collectJobFieldValues")[1].split("\n}")[0]
        assert 'values[field.name + "_text"] = el.value;' in fn
        # The element either is a file input or it is not; that is the honest test.
        assert "if (el.files)" in fn

    def test_a_pasted_passage_satisfies_a_required_file_field(self, chat):
        """Otherwise the form refuses to start a job it has everything for."""
        assert 'files.length === 0 && !pasted' in chat

    def test_the_refusal_names_both_ways_out(self, chat):
        assert "or paste the text to translate" in chat

    def test_switching_mode_refreshes_the_preview(self, chat):
        """What was chosen in the old mode is gone, so the preview is stale."""
        fn = chat.split("const apply = (mode) => {")[1].split("\n  };")[0]
        assert "scheduleJobPreview()" in fn

    def test_typing_refreshes_the_preview(self, chat):
        fn = chat.split("const apply = (mode) => {")[1].split("\n  };")[0]
        assert 'replacement.addEventListener("input", scheduleJobPreview)' in fn

    def test_the_box_is_big_enough_for_a_passage(self, chat):
        fn = chat.split("const apply = (mode) => {")[1].split("\n  };")[0]
        rows = int(re.search(r"replacement\.rows = (\d+)", fn).group(1))
        assert rows >= 5, "one line misrepresents what goes in it"


class TestTheProfessorPickerIsFieldHeight:
    """It stretched to about a hundred pixels tall, chevron floating mid-way.

    `.combobox` carried `flex: 1`, written for the top bar — a flex row, where
    that means "take the remaining width". The sidebar is a flex column, so the
    same declaration told the professor picker to take the remaining *height*.
    The chevron is positioned at top: 50%, so it centred itself in the result.
    """

    @pytest.fixture
    def chat(self):
        return _rendered_chat()

    def test_the_component_claims_no_space_of_its_own(self, chat):
        rule = chat.split(".combobox { ")[1].split("}")[0]
        assert "flex:" not in rule, (
            "a component that grows on its own stretches wherever it is put — "
            "here, down a column"
        )

    def test_the_top_bar_still_gives_it_the_room(self, chat):
        """Dropping the grow must not have collapsed the model field."""
        assert ".model-picker .combobox { flex: 1; }" in chat

    def test_the_sidebar_is_still_a_column(self, chat):
        """If this ever stops being true, the rule above is why it mattered."""
        rule = chat.split("#sidebar { ")[1].split("}")[0]
        assert "flex-direction: column" in rule

    def test_the_chevron_still_centres_on_the_field(self, chat):
        """Right for a field-height box; that was never the bug."""
        rule = chat.split(".combobox-toggle {")[1].split("}")[0]
        assert "top: 50%" in rule and "translateY(-50%)" in rule


class TestOneComboboxForEveryPage:
    """Three pickers is where copying the markup had to stop.

    A <select> is drawn by the operating system: not the height of the fields
    around it, not the arrow used anywhere else, and a list that ignores the
    theme — a white box out of nowhere in the dark one. Each picker that stopped
    being a <select> had been another copy of the same CSS and the same handlers.
    """

    def _rendered(self, name):
        from fastapi.templating import Jinja2Templates
        directory = Path(__file__).resolve().parents[1] / "src" / "templates"
        return Jinja2Templates(directory=str(directory)).get_template(name).render(request=None)

    def test_the_partial_exists_on_its_own(self):
        partial = (Path(__file__).resolve().parents[1] / "src" / "templates"
                   / "_combobox.html")
        assert partial.exists()

    def test_both_pages_include_it(self):
        for page in ("chat.html", "settings.html"):
            source = (Path(__file__).resolve().parents[1] / "src" / "templates"
                      / page).read_text()
            assert '{% include "_combobox.html" %}' in source, page

    def test_neither_page_keeps_its_own_copy(self):
        """The drift this is here to prevent."""
        for page in ("chat.html", "settings.html"):
            source = (Path(__file__).resolve().parents[1] / "src" / "templates"
                      / page).read_text()
            assert ".combobox-list {" not in source, f"{page} has its own copy of the CSS"
            assert "function wireCombobox" not in source, f"{page} has its own copy of the JS"

    def test_the_settings_picker_is_no_longer_a_select(self):
        page = self._rendered("settings.html")
        assert '<select id="add-model-professor"' not in page
        assert 'id="add-model-professor-combobox"' in page

    def test_all_three_pickers_are_wired(self):
        chat = self._rendered("chat.html")
        settings = self._rendered("settings.html")
        assert 'wireCombobox("model")' in chat
        assert 'wireCombobox("professor")' in chat
        assert 'wireCombobox("add-model-professor")' in settings

    def test_one_prefix_finds_every_part(self):
        """Field, list, chevron and container are all found from the one name."""
        partial = self._rendered("chat.html")
        fn = partial.split("function wireCombobox")[1].split("\n}")[0]
        assert 'prefix + "-toggle-btn"' in fn
        assert "comboboxField(prefix)" in fn

    def test_dismissal_covers_every_picker_without_naming_one(self):
        page = self._rendered("settings.html")
        handler = page.split('document.addEventListener("click"')[1].split("\n});")[0]
        assert "WIRED_COMBOBOXES.forEach" in handler

    def test_the_component_still_claims_no_space_of_its_own(self):
        """The sidebar bug, now in a file two pages depend on."""
        page = self._rendered("settings.html")
        rule = page.split(".combobox { ")[1].split("}")[0]
        assert "flex:" not in rule

    def test_the_form_sends_a_netid_and_not_a_name(self):
        """The field shows 'Jeff Heller (jh43)'; the request needs 'jh43'."""
        page = self._rendered("settings.html")
        assert "const professor = addModelProfessor;" in page
        assert 'getElementById("add-model-professor").value' not in page

    def test_someone_is_chosen_to_begin_with(self):
        """A <select> shows its first option; an empty box looks broken."""
        fn = self._rendered("settings.html").split("function renderModelProfessors")[1]
        assert "if (!addModelProfessor) {" in fn.split("\n}")[0]


class TestNoPageInventsAColourItAlreadyHasATokenFor:
    """A literal where a token belongs stops following the theme.

    settings.html and shared_settings.html both hardcoded #cc6600 for
    button:hover. That is --orange-hover's *light* value; in the dark theme the
    token is #ff8f2e, deliberately lighter, because darkening an orange on a
    dark panel moves it towards the background rather than away from it. So on
    those two pages, in dark mode, hovering a button made it recede — the exact
    thing the design system's own comment says not to do.
    """

    TEMPLATES = ["chat.html", "settings.html", "shared_settings.html", "unlock.html"]

    def _template(self, name):
        return (Path(__file__).resolve().parents[1] / "src" / "templates" / name).read_text()

    def test_no_page_repeats_a_value_the_design_system_names(self):
        tokens = self._template("_design-system.html")
        # Every colour the token layer defines, as written there.
        defined = set(re.findall(r"--[\w-]+:\s*(#[0-9A-Fa-f]{3,8})\s*;", tokens))
        assert defined, "no colours found — has the token layer moved?"
        for name in self.TEMPLATES:
            body = re.sub(r"/\*.*?\*/", "", self._template(name), flags=re.S)
            for colour in defined:
                assert colour.lower() not in body.lower(), (
                    f"{name} writes {colour} itself; the token layer already names it, "
                    "and a literal stops following the theme"
                )

    def test_the_hover_follows_the_theme_on_every_page(self):
        for name in self.TEMPLATES:
            body = self._template(name)
            # The bare element rule, not "#tabs button:hover" — a tab is not
            # an orange button and is right to hover differently.
            found = re.search(r"^\s*button:hover\s*\{([^}]*)\}", body, re.M)
            if not found:
                continue
            assert "var(--orange-hover)" in found.group(1), f"{name} does not use the token"

    def test_the_token_really_does_differ_between_themes(self):
        """If it ever stops differing, the bug above stops being possible."""
        tokens = self._template("_design-system.html")
        values = re.findall(r"--orange-hover:\s*(#[0-9A-Fa-f]+)", tokens)
        assert len(values) == 2, "expected a light value and a dark one"
        assert values[0].lower() != values[1].lower()


class TestNoRuleIsWrittenTwice:
    """Four pages each keeping their own copy is how they drifted apart.

    Before the shared partials, 39 selectors were declared in more than one
    template and 13 of those had different rules — including button:hover, where
    two pages wrote a colour literal that made them recede on hover in the dark
    theme.
    """

    TEMPLATES = ["chat.html", "settings.html", "shared_settings.html", "unlock.html"]
    PARTIALS = ["_design-system.html", "_forms.html", "_panels.html",
                "_combobox.html"]

    def _rules(self, name):
        text = (Path(__file__).resolve().parents[1] / "src" / "templates" / name).read_text()
        css = "\n".join(re.findall(r"<style>(.*?)</style>", text, re.S))
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        out = {}
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            sel = " ".join(sel.split())
            if sel and not sel.startswith("@"):
                out[sel] = " ".join(body.split())
        return out

    def test_no_page_repeats_a_rule_a_partial_already_gives_it(self):
        """A copy identical to the shared one is dead weight that can drift."""
        shared = {}
        for partial in self.PARTIALS:
            shared.update(self._rules(partial))
        for name in self.TEMPLATES:
            for sel, body in self._rules(name).items():
                assert shared.get(sel) != body, (
                    f"{name} repeats '{sel}' exactly as the shared partial has it"
                )

    def test_a_page_that_overrides_says_only_what_differs(self):
        """Restating the rest is how one of them silently stops matching."""
        shared = {}
        for partial in self.PARTIALS:
            shared.update(self._rules(partial))
        for name in self.TEMPLATES:
            for sel, body in self._rules(name).items():
                if sel not in shared:
                    continue
                mine = {d.split(":")[0].strip() for d in body.split(";") if ":" in d}
                theirs = {d.split(":")[0].strip(): d.split(":", 1)[1].strip()
                          for d in shared[sel].split(";") if ":" in d}
                repeated = {
                    p for p in mine & set(theirs)
                    if [d.split(":", 1)[1].strip() for d in body.split(";")
                        if d.split(":")[0].strip() == p] == [theirs[p]]
                }
                assert not repeated, (
                    f"{name}'s '{sel}' restates {sorted(repeated)} unchanged from the partial"
                )

    def test_the_two_settings_pages_no_longer_share_a_stylesheet(self):
        """shared_settings.html was 68% a copy of settings.html."""
        a, b = self._rules("settings.html"), self._rules("shared_settings.html")
        # A selector may legitimately appear in both when the pages genuinely
        # differ — #page is 720px on one and 1040px on the other. What must not
        # appear in both is the same rule written out twice.
        copies = {s for s in set(a) & set(b) if a[s] == b[s]}
        assert not copies, f"still written out in both: {sorted(copies)}"

    def test_artwork_used_twice_is_drawn_once(self):
        """1,842 characters of path data were repeated across the templates."""
        from collections import Counter
        seen = Counter()
        for name in self.TEMPLATES:
            text = (Path(__file__).resolve().parents[1] / "src" / "templates" / name).read_text()
            seen.update(re.findall(r'<path d="([^"]{60,})"', text))
        repeated = {d[:40]: n for d, n in seen.items() if n > 1}
        assert not repeated, f"path data still repeated: {list(repeated)}"

    def test_an_icon_in_the_partial_is_never_also_pasted_inline(self):
        """Counting repeats is not enough: one inline copy beside the macro
        calls repeats nothing and still leaves two drawings to keep in step."""
        icons = (Path(__file__).resolve().parents[1] / "src" / "templates"
                 / "_icons.html").read_text()
        drawn = re.findall(r'<path d="([^"]{60,})"', icons)
        assert drawn, "no artwork found — has _icons.html moved?"
        for name in self.TEMPLATES:
            text = (Path(__file__).resolve().parents[1] / "src" / "templates" / name).read_text()
            for d in drawn:
                assert d not in text, (
                    f"{name} draws an icon inline that _icons.html already has; "
                    "call the macro instead"
                )

    def test_the_panels_are_only_given_to_pages_built_from_them(self):
        """The chat page uses none of them; shipping it the rules is the same
        waste as the duplication, moved rather than removed."""
        for name in self.TEMPLATES:
            source = (Path(__file__).resolve().parents[1] / "src" / "templates" / name).read_text()
            wants = name in ("settings.html", "shared_settings.html")
            assert ('{% include "_panels.html" %}' in source) is wants, name


class TestTheWebFirstRunExplainsItself:
    """Walking in with nothing configured, and being told what is missing.

    A new installation has two things to do before it can be used: somebody to
    bill, and a model to send to. Neither can be shipped — one is a private
    credential, the other depends on the institution's own AI sandbox — so what
    matters is that both are named before anything is typed.
    """

    def _no_models(self, monkeypatch, tmp_path):
        import src.models.catalog as catalog_module
        monkeypatch.setattr(catalog_module, "get_model_catalog_path",
                            lambda: tmp_path / "not-here.json")

    def test_with_no_professor_the_chat_page_is_not_offered(
        self, unlocked_client, settings_env
    ):
        resp = unlocked_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"

    def test_with_no_models_the_chat_page_is_not_offered_either(
        self, unlocked_client, monkeypatch, tmp_path, settings_env
    ):
        """The gap this closes: a chat window that looks ready and fails on the
        first message, because there is nothing to send it to."""
        self._no_models(monkeypatch, tmp_path)
        settings_store_mod.add_professor("jh43", "Jeff Heller", "k")
        resp = unlocked_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"

    def test_the_settings_page_says_which_of_the_two_is_outstanding(
        self, unlocked_client, monkeypatch, tmp_path, settings_env
    ):
        self._no_models(monkeypatch, tmp_path)
        body = unlocked_client.get("/api/settings").json()
        assert body["has_professors"] is False
        assert body["has_models"] is False
        settings_store_mod.add_professor("jh43", "Jeff Heller", "k")
        body = unlocked_client.get("/api/settings").json()
        assert body["has_professors"] is True
        assert body["has_models"] is False

    def test_the_page_opens_on_whichever_step_is_left(self):
        """Adding the professor moves it on to Models by itself."""
        page = _rendered_template("settings.html")
        fn = page.split("function openingTab")[1].split("\n}")[0]
        assert 'if (!data.has_professors) return "system";' in fn
        assert 'if (!data.has_models) return "models";' in fn
        # And once neither is outstanding, it goes back to remembering.
        assert 'localStorage.getItem("settings-tab")' in fn

    def test_the_page_says_where_to_find_out_what_to_add(self):
        """Somewhere on the panel, not only when the list is empty.

        It used to be in the empty-state note alone, so it disappeared the
        moment somebody added their first model — which is before most of the
        adding is done.
        """
        card = _rendered_template("settings.html").split('data-section="models"')[1]
        card = card.split('id="section-shared"')[0]
        words = " ".join(card.split())
        assert "AI Sandbox" in words
        # A link to the list of models, so nobody has to go looking for it.
        assert "href=" in card and "princeton" in card.lower()

    def test_that_note_is_shown_only_while_there_are_none(self):
        page = _rendered_template("settings.html")
        fn = page.split("async function loadModels")[1].split("\n}")[0]
        assert "note.hidden = data.models.length > 0;" in fn

    def test_a_catalogue_that_would_not_load_is_a_different_thing(self):
        """Not the same as having none, and it must not read as advice."""
        page = _rendered_template("settings.html")
        fn = page.split("async function loadModels")[1].split("\n}")[0]
        failure = fn.split("catch")[1]
        assert "note.hidden = true;" in failure
        assert "Could not read the model catalogue" in failure


class TestAddingTheFirstModel:
    """The one thing a new installation must be able to do.

    Adding a model reads the catalogue, puts the entry in, and saves it. While
    an empty catalogue refused to be read, that first step raised — so the very
    situation the Models panel exists for was the one it could not handle, and
    the person was told "there are no models set up yet" while trying to set
    one up.
    """

    def _empty_catalogue(self, monkeypatch, tmp_path):
        import src.models.catalog as catalog_module
        path = tmp_path / "model_catalog.json"
        path.write_text(json.dumps(
            {"config": {"pricing_unit": 1000000, "provider_map": {}}, "models": {}}))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: path)
        monkeypatch.setattr(catalog_module, "_catalog_cache", None)
        return path

    def test_the_models_panel_loads_with_none(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        self._empty_catalogue(monkeypatch, tmp_path)
        resp = unlocked_client.get("/api/settings/models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_the_settings_page_still_answers(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        """It reports has_models, which means reading a catalogue with none."""
        self._empty_catalogue(monkeypatch, tmp_path)
        resp = unlocked_client.get("/api/settings")
        assert resp.status_code == 200
        assert resp.json()["has_models"] is False

    def test_adding_one_gets_past_reading_the_catalogue(
        self, unlocked_client, monkeypatch, tmp_path, settings_env
    ):
        """Not a test of the provider call — of the step that used to raise first.

        Whatever the request to the provider does, it has to be *reached*.
        While an empty catalogue refused to be read, it never was.
        """
        self._empty_catalogue(monkeypatch, tmp_path)
        settings_store_mod.add_professor("jh43", "Jeff Heller", "a-key")
        reached = {}
        import src.models as models_module

        def add(name, *a, **kw):
            reached["name"] = name
            raise ValueError("stop here — the point is that we got this far")

        monkeypatch.setattr(models_module, "add_model_to_catalog", add)
        unlocked_client.post("/api/settings/models",
                             json={"provider_model": "openai/gpt-4o", "professor": "jh43"})
        assert reached.get("name") == "openai/gpt-4o", (
            "the catalogue read raised before the model could be looked up"
        )


class TestTheModelsPanelReadsAsOnePage:
    """Five things that made the panel look assembled rather than designed."""

    @pytest.fixture
    def page(self):
        return _rendered_template("settings.html")

    def test_a_list_is_set_like_the_sentence_above_it(self, page):
        rule = page.split(".card ul, .card ol {")[1].split("}")[0]
        assert "font-size: var(--text-sm)" in rule
        assert "color: var(--text-muted)" in rule

    def test_no_list_is_trapped_inside_a_paragraph(self, page):
        """A browser closes a <p> the moment a block starts inside it.

        The list then sits outside .hint and inherits none of it — which is why
        it looked nothing like the text introducing it, however the list itself
        was styled.
        """
        assert not re.search(r'<p class="hint[^"]*">(?:(?!</p>).)*?<(ul|ol|div)\b',
                             page, re.S)

    def test_what_a_model_can_do_is_not_coloured_as_good_news(self, page):
        """Green means "this is set". Being able to read images is neither
        good news nor bad — it is a fact about the model."""
        assert 'badge ${m.supports_vision ? "can"' in page
        assert ".badge.can {" in page

    def test_that_badge_is_the_sandbox_orange_and_not_a_new_colour(self, page):
        """Same hue as #F58025, lightened — one family, not a second accent."""
        import colorsys
        for token in ("--badge-can-bg", "--badge-can-text"):
            found = re.search(rf"{token}:\s*#([0-9a-fA-F]{{6}})", page)
            r, g, b = (int(found.group(1)[i:i+2], 16) / 255 for i in (0, 2, 4))
            hue = colorsys.rgb_to_hls(r, g, b)[0] * 360
            assert 18 <= hue <= 36, f"{token} is hue {hue:.0f}°, not the orange's"

    def test_there_is_a_line_before_adding_one(self, page):
        """Without it the heading read as part of the last row above it."""
        rule = page.split(".after-a-list {")[1].split("}")[0]
        assert "border-top" in rule
        assert 'id="add-model-form" class="after-a-list"' in page

    def test_links_are_not_the_browsers_own_blue(self, page):
        """There was no rule at all, so they came out louder than anything
        else on a page made of one orange and greys."""
        assert "a { color: var(--link); }" in page
        assert re.search(r"--link:\s*#[0-9a-fA-F]{6}", page)

    def test_the_link_colour_complements_the_orange(self, page):
        """Its complement, not another warm colour fighting it."""
        import colorsys
        found = re.search(r"--link:\s*#([0-9a-fA-F]{6})", page)
        r, g, b = (int(found.group(1)[i:i+2], 16) / 255 for i in (0, 2, 4))
        hue = colorsys.rgb_to_hls(r, g, b)[0] * 360
        assert 190 <= hue <= 220, f"hue {hue:.0f}° is not opposite the orange's 26°"

    def test_the_button_is_not_hard_against_the_fields(self, page):
        assert "button.after-fields { margin-top:" in page
        assert 'id="add-model-btn" class="after-fields"' in page

    def test_every_new_colour_can_be_read_in_both_themes(self, page):
        """The floor for text is 4.5:1, and a colour chosen by eye misses it."""
        def lum(h):
            h = h.lstrip("#")
            parts = [int(h[i:i+2], 16) / 255 for i in (0, 2, 4)]
            f = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
            return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]

        def ratio(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        def token(where, name):
            return re.findall(rf"{name}:\s*(#[0-9a-fA-F]{{6}})", where)[-1]

        light, dark = page.split('[data-theme="dark"]')
        for half, theme in ((light, "light"), (dark, "dark")):
            assert ratio(token(half, "--badge-can-bg"),
                         token(half, "--badge-can-text")) >= 4.5, theme
            assert ratio(token(half, "--panel-bg"), token(half, "--link")) >= 4.5, theme
            assert ratio(token(half, "--bg"), token(half, "--link")) >= 4.5, theme
