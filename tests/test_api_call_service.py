"""Tests for src/services/api_call_service.py."""

from unittest.mock import MagicMock

import pytest

from src.services.api_config import APIConfig
from src.services.api_call_service import APICallService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> APIConfig:
    defaults = dict(
        api_name="test",
        display_name="Test API",
        base_url="https://api.example.com/v1",
        api_key="key",
        openai_compatible=True,
        default_model="gpt-4o",
        timeout=10,
        verify_ssl=True,
        extra={},
    )
    defaults.update(kwargs)
    return APIConfig(**defaults)


def _make_svc(monkeypatch, **kwargs) -> APICallService:
    monkeypatch.setattr(
        "src.services.api_service.OpenAI",
        lambda **kw: MagicMock(),
    )
    tracker = MagicMock()
    tracker.record_usage.return_value = MagicMock(total_cost=0.001)
    cfg = _make_config(**kwargs)
    return APICallService(cfg, professor="test", token_tracker=tracker)


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_with_system_prompt(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        msgs = svc.build_messages("Hello", system_prompt="You are a bot.")
        assert msgs[0] == {"role": "system", "content": "You are a bot."}
        assert msgs[1] == {"role": "user", "content": "Hello"}

    def test_default_system_prompt(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        msgs = svc.build_messages("Hello")
        assert msgs[0]["role"] == "system"
        assert len(msgs[0]["content"]) > 0
        assert msgs[1]["content"] == "Hello"

    def test_two_messages_returned(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        msgs = svc.build_messages("hi")
        assert len(msgs) == 2


# ---------------------------------------------------------------------------
# send_prompt
# ---------------------------------------------------------------------------

class TestSendPrompt:
    def test_calls_chat_completion(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        svc.svc.chat_completion = MagicMock(return_value="The answer is 42.")

        result = svc.send_prompt("What is the answer?")
        assert result == "The answer is 42."
        svc.svc.chat_completion.assert_called_once()

    def test_passes_messages(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        captured = {}

        def fake_completion(messages, **_):
            captured["messages"] = messages
            return "ok"

        svc.svc.chat_completion = fake_completion
        svc.send_prompt("My question", system_prompt="Custom system")
        assert captured["messages"][0]["content"] == "Custom system"
        assert captured["messages"][1]["content"] == "My question"

    def test_propagates_exception(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        svc.svc.chat_completion = MagicMock(side_effect=RuntimeError("API down"))

        with pytest.raises(RuntimeError, match="API down"):
            svc.send_prompt("test")
