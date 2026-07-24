"""Tests for ChatService (plugins/webui/src/services/chat_service.py, registered as
src.services.chat_service). Mirrors the mocking conventions in
plugins/prompt/tests/test_prompt_service.py — no real API calls are made.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.chat_service import ChatService


class _Usage:
    def __init__(self, prompt_tokens=10, completion_tokens=20, total_tokens=30):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _FakeResponse:
    def __init__(self, content: object = "hello", model: str = "gpt-4o", usage=True):
        self.id = "resp-1"
        self.model = model
        self.usage = _Usage() if usage else None
        self.choices = [_Choice(content)]


@pytest.fixture
def svc():
    tracker = MagicMock()
    tracker.record_usage.return_value = MagicMock(total_cost=0.0042)
    return ChatService(api_key="fake-key", token_tracker=tracker)


@pytest.fixture(autouse=True)
def patch_model(monkeypatch):
    monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
    monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
    monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
    monkeypatch.setattr("src.services.chat_service.get_model_system_role", lambda m: "system")


class TestSendMessage:
    def test_returns_reply_content(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("Hi there")):
            result = svc.send_message([{"role": "user", "content": "hello"}])
        assert result["content"] == "Hi there"
        assert result["model"] == "gpt-4o"

    def test_records_usage_and_returns_cost(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("ok")):
            result = svc.send_message([{"role": "user", "content": "hello"}])
        svc.token_tracker.record_usage.assert_called_once()
        assert result["prompt_tokens"] == 10
        assert result["completion_tokens"] == 20
        assert result["cost"] == 0.0042

    def test_no_usage_in_response_leaves_billing_fields_none(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("ok", usage=False)):
            result = svc.send_message([{"role": "user", "content": "hello"}])
        svc.token_tracker.record_usage.assert_not_called()
        assert result["prompt_tokens"] is None
        assert result["cost"] is None

    def test_system_prompt_is_prepended(self, svc):
        captured = []

        def fake_create(model, messages, max_tokens, **kw):
            captured.extend(messages)
            return _FakeResponse("ok")

        with patch.object(svc, "_create_completion", side_effect=fake_create):
            svc.send_message([{"role": "user", "content": "hello"}], system_prompt="Be concise.")
        assert captured[0] == {"role": "system", "content": "Be concise."}
        assert captured[1] == {"role": "user", "content": "hello"}

    def test_no_system_prompt_sends_only_conversation(self, svc):
        captured = []

        def fake_create(model, messages, max_tokens, **kw):
            captured.extend(messages)
            return _FakeResponse("ok")

        with patch.object(svc, "_create_completion", side_effect=fake_create):
            svc.send_message([{"role": "user", "content": "hello"}])
        assert captured == [{"role": "user", "content": "hello"}]

    def test_full_conversation_history_forwarded(self, svc):
        captured = []

        def fake_create(model, messages, max_tokens, **kw):
            captured.extend(messages)
            return _FakeResponse("ok")

        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        with patch.object(svc, "_create_completion", side_effect=fake_create):
            svc.send_message(history)
        assert captured == history

    def test_uses_response_model_over_requested_when_different(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("ok", model="gpt-4o-2024-08-06")):
            result = svc.send_message([{"role": "user", "content": "hi"}])
        assert result["model"] == "gpt-4o-2024-08-06"

    def test_retries_until_content_returned(self, svc, monkeypatch):
        """_create_completion returning empty content should trigger a retry, not a crash."""
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        calls = {"n": 0}

        def flaky(model, messages, max_tokens, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResponse(content=None)
            return _FakeResponse("recovered")

        with patch.object(svc, "_create_completion", side_effect=flaky):
            result = svc.send_message([{"role": "user", "content": "hi"}])
        assert result["content"] == "recovered"
        assert calls["n"] == 2
