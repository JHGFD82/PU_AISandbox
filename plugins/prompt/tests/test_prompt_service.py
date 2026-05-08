"""
Tests for PromptService.

Covers _get_model, build_prompts, and send_prompt.
No real API calls are made.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.services.prompt_service import PromptService
from src.settings import DEFAULT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Minimal response stand-in (non-iterable, passes ABCIterator guard)
# ---------------------------------------------------------------------------

class _Usage:
    def __init__(self):
        self.prompt_tokens = 10
        self.completion_tokens = 20
        self.total_tokens = 30


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _FakeResponse:
    def __init__(self, content: object = "hello", model: str = "gpt-4o"):
        self.id = "resp-1"
        self.model = model
        self.usage = _Usage()
        self.choices = [_Choice(content)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc():
    tracker = MagicMock()
    tracker.record_usage.return_value = MagicMock(total_cost=0.001)
    return PromptService(api_key="fake-key", token_tracker=tracker)


@pytest.fixture(autouse=True)
def patch_model(monkeypatch):
    """Patch catalog/model helpers so no disk I/O occurs."""
    monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
    monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
    monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
    monkeypatch.setattr("src.services.prompt_service.get_model_system_role", lambda m: "system")


# ---------------------------------------------------------------------------
# _get_model
# ---------------------------------------------------------------------------

class TestGetModel:
    def test_returns_resolved_model(self, svc):
        assert svc._get_model() == "gpt-4o"


# ---------------------------------------------------------------------------
# build_prompts
# ---------------------------------------------------------------------------

class TestBuildPrompts:
    def test_with_explicit_system_prompt(self, svc):
        sys_p, usr_p = svc.build_prompts("my question", system_prompt="custom system")
        assert sys_p == "custom system"
        assert usr_p == "my question"

    def test_without_system_prompt_uses_default(self, svc):
        sys_p, usr_p = svc.build_prompts("my question")
        assert sys_p == DEFAULT_SYSTEM_PROMPT
        assert usr_p == "my question"

    def test_none_system_prompt_uses_default(self, svc):
        sys_p, usr_p = svc.build_prompts("question", system_prompt=None)
        assert sys_p == DEFAULT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# send_prompt
# ---------------------------------------------------------------------------

class TestSendPrompt:
    def test_returns_response_content(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("The answer")):
            result = svc.send_prompt("What is 2+2?")
        assert result == "The answer"

    def test_uses_custom_system_prompt(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("Custom")):
            result = svc.send_prompt("question", system_prompt="You are a math tutor")
        assert result == "Custom"

    def test_uses_default_system_prompt_when_none(self, svc):
        captured_msgs = []

        def fake_create(model, messages, max_tokens, **kw):
            captured_msgs.extend(messages)
            return _FakeResponse("ok")

        with patch.object(svc, "_create_completion", side_effect=fake_create):
            svc.send_prompt("question")
        assert any(DEFAULT_SYSTEM_PROMPT in m.get("content", "") for m in captured_msgs)

    def test_empty_choices_returns_empty_string(self, svc):
        resp = _FakeResponse()
        resp.choices = []
        with patch.object(svc, "_create_completion", return_value=resp):
            assert svc.send_prompt("q") == ""

    def test_none_content_returns_empty_string(self, svc):
        resp = _FakeResponse(content=None)
        with patch.object(svc, "_create_completion", return_value=resp):
            assert svc.send_prompt("q") == ""

    def test_non_string_content_returns_empty_string(self, svc):
        resp = _FakeResponse(content=42)
        with patch.object(svc, "_create_completion", return_value=resp):
            assert svc.send_prompt("q") == ""

    def test_api_error_is_raised(self, svc):
        """Any API exception should propagate after handle_api_errors."""
        with patch.object(svc, "_create_completion", side_effect=Exception("network fail")), \
             patch("src.services.prompt_service.handle_api_errors"):
            with pytest.raises(Exception, match="network fail"):
                svc.send_prompt("q")

    def test_handle_api_errors_called_on_exception(self, svc):
        exc = Exception("rate_limit")
        with patch.object(svc, "_create_completion", side_effect=exc), \
             patch("src.services.prompt_service.handle_api_errors") as mock_handle:
            with pytest.raises(Exception):
                svc.send_prompt("q")
        mock_handle.assert_called_once_with(exc, "gpt-4o")
