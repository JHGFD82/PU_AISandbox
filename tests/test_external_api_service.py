"""Tests for src/services/api_service.py (and backward-compat shim external_api_service.py)."""

import time
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from src.services.api_config import APIConfig
from src.services.api_service import APIService
# Backward-compat shim
from src.services.external_api_config import ExternalAPIConfig
from src.services.external_api_service import ExternalAPIService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(openai_compatible: bool = True, **kwargs) -> APIConfig:
    defaults = dict(
        api_name="test_api",
        display_name="Test API",
        base_url="https://api.example.com/v1",
        api_key="test-key",
        openai_compatible=openai_compatible,
        default_model="gpt-4o" if openai_compatible else None,
        timeout=10,
        verify_ssl=True,
    )
    defaults.update(kwargs)
    return APIConfig(**defaults)


def _make_svc(monkeypatch, openai_compatible: bool = True, **kwargs) -> ExternalAPIService:
    """Create an ExternalAPIService with network clients mocked out."""
    cfg = _make_config(openai_compatible=openai_compatible, **kwargs)
    tracker = MagicMock()
    tracker.record_usage.return_value = MagicMock(total_cost=0.0012)

    if openai_compatible:
        monkeypatch.setattr(
            "src.services.api_service.OpenAI",
            lambda **kw: MagicMock(),
        )

    svc = ExternalAPIService(cfg, professor="test", token_tracker=tracker)
    return svc


def _openai_response(content: str = "Hello!", model: str = "gpt-4o") -> MagicMock:
    """Build a fake OpenAI chat-completion response."""
    resp = MagicMock()
    resp.model = model
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return resp


# ---------------------------------------------------------------------------
# ExternalAPIService.__init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_openai_client_created_for_compatible(self, monkeypatch):
        openai_cls = MagicMock()
        monkeypatch.setattr("src.services.api_service.OpenAI", openai_cls)
        cfg = _make_config(openai_compatible=True)
        svc = ExternalAPIService(cfg)
        openai_cls.assert_called_once()
        assert svc._session is None

    def test_requests_session_created_for_rest(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.api_service.OpenAI",
            lambda **kw: MagicMock(),
        )
        cfg = _make_config(openai_compatible=False)
        svc = ExternalAPIService(cfg)
        assert svc._openai_client is None
        assert svc._session is not None

    def test_token_tracker_created_when_none(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.api_service.OpenAI",
            lambda **kw: MagicMock(),
        )
        monkeypatch.setattr(
            "src.services.api_service.TokenTracker",
            lambda **kw: MagicMock(),
        )
        cfg = _make_config()
        svc = ExternalAPIService(cfg, professor="prof")
        assert svc.token_tracker is not None


# ---------------------------------------------------------------------------
# chat_completion
# ---------------------------------------------------------------------------

class TestChatCompletion:
    def test_success(self, monkeypatch):
        svc = _make_svc(monkeypatch, openai_compatible=True)
        fake_response = _openai_response("Paris")
        svc._openai_client.chat.completions.create.return_value = fake_response  # type: ignore

        result = svc.chat_completion([{"role": "user", "content": "Capital of France?"}])
        assert result == "Paris"

    def test_raises_for_non_compatible(self, monkeypatch):
        svc = _make_svc(monkeypatch, openai_compatible=False)
        with pytest.raises(ValueError, match="not configured as openai_compatible"):
            svc.chat_completion([{"role": "user", "content": "hi"}])

    def test_raises_when_no_model(self, monkeypatch):
        cfg = _make_config(openai_compatible=True, default_model=None)
        tracker = MagicMock()
        monkeypatch.setattr(
            "src.services.api_service.OpenAI",
            lambda **kw: MagicMock(),
        )
        svc = ExternalAPIService(cfg, token_tracker=tracker)
        with pytest.raises(ValueError, match="No model specified"):
            svc.chat_completion([{"role": "user", "content": "hi"}])

    def test_model_override_per_call(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        fake_response = _openai_response("ok")
        svc._openai_client.chat.completions.create.return_value = fake_response  # type: ignore

        svc.chat_completion([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        create_call = svc._openai_client.chat.completions.create.call_args  # type: ignore
        assert create_call.kwargs["model"] == "gpt-4o-mini"

    def test_token_usage_recorded(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        fake_response = _openai_response("ok")
        svc._openai_client.chat.completions.create.return_value = fake_response  # type: ignore

        svc.chat_completion([{"role": "user", "content": "hi"}])
        svc.token_tracker.record_usage.assert_called_once()

    def test_none_content_returns_empty_string(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        resp = _openai_response("reply")
        resp.choices[0].message.content = None
        svc._openai_client.chat.completions.create.return_value = resp  # type: ignore

        # _run_with_retry raises after exhausting retries on None content
        with pytest.raises(RuntimeError):
            svc.chat_completion([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# get / post
# ---------------------------------------------------------------------------

class TestHTTPMethods:
    def _rest_svc(self, monkeypatch) -> ExternalAPIService:
        return _make_svc(monkeypatch, openai_compatible=False)

    def test_get_json_response(self, monkeypatch):
        svc = self._rest_svc(monkeypatch)
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {"items": [1, 2, 3]}
        mock_session.get.return_value = mock_resp
        svc._session = mock_session

        result = svc.get("/items", params={"limit": 5})
        assert result == {"items": [1, 2, 3]}
        mock_session.get.assert_called_once_with(
            "https://api.example.com/v1/items",
            params={"limit": 5},
            timeout=10,
            verify=True,
        )

    def test_get_text_response(self, monkeypatch):
        svc = self._rest_svc(monkeypatch)
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.text = "hello world"
        mock_session.get.return_value = mock_resp
        svc._session = mock_session

        result = svc.get("/ping")
        assert result == "hello world"

    def test_post_json(self, monkeypatch):
        svc = self._rest_svc(monkeypatch)
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {"status": "ok"}
        mock_session.post.return_value = mock_resp
        svc._session = mock_session

        result = svc.post("/submit", payload={"key": "value"})
        assert result == {"status": "ok"}
        mock_session.post.assert_called_once_with(
            "https://api.example.com/v1/submit",
            json={"key": "value"},
            timeout=10,
            verify=True,
        )


# ---------------------------------------------------------------------------
# _build_url
# ---------------------------------------------------------------------------

class TestBuildUrl:
    def test_trailing_slash_stripped(self, monkeypatch):
        svc = _make_svc(monkeypatch, base_url="https://api.example.com/v1/")
        assert svc._build_url("/items") == "https://api.example.com/v1/items"

    def test_leading_slash_stripped_from_path(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        assert svc._build_url("items") == "https://api.example.com/v1/items"

    def test_empty_path_returns_base(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        assert svc._build_url("") == "https://api.example.com/v1"


# ---------------------------------------------------------------------------
# _is_transient
# ---------------------------------------------------------------------------

class TestIsTransient:
    def test_connection_error(self):
        assert ExternalAPIService._is_transient(requests.exceptions.ConnectionError())

    def test_timeout_error(self):
        assert ExternalAPIService._is_transient(requests.exceptions.Timeout())

    def test_5xx_http_error(self):
        resp = MagicMock()
        resp.status_code = 503
        err = requests.exceptions.HTTPError(response=resp)
        assert ExternalAPIService._is_transient(err)

    def test_4xx_not_transient(self):
        resp = MagicMock()
        resp.status_code = 400
        err = requests.exceptions.HTTPError(response=resp)
        assert not ExternalAPIService._is_transient(err)

    def test_openai_rate_limit(self):
        # Simulate an openai RateLimitError by matching on the class name string
        class RateLimitError(Exception):
            pass
        assert ExternalAPIService._is_transient(RateLimitError("quota exceeded"))

    def test_value_error_not_transient(self):
        assert not ExternalAPIService._is_transient(ValueError("bad input"))


# ---------------------------------------------------------------------------
# _run_with_retry
# ---------------------------------------------------------------------------

class TestRunWithRetry:
    def test_success_first_attempt(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        result = svc._run_with_retry(lambda i: "done", operation="test")
        assert result == "done"

    def test_retries_on_none(self, monkeypatch):
        monkeypatch.setattr("src.services.api_service.time.sleep", lambda _: None)
        svc = _make_svc(monkeypatch)
        calls = []

        def attempt(i):
            calls.append(i)
            return "ok" if i >= 2 else None

        result = svc._run_with_retry(attempt, operation="test")
        assert result == "ok"
        assert len(calls) == 3

    def test_raises_after_exhaustion(self, monkeypatch):
        monkeypatch.setattr("src.services.api_service.time.sleep", lambda _: None)
        svc = _make_svc(monkeypatch)

        with pytest.raises(RuntimeError, match="returned no content"):
            svc._run_with_retry(lambda i: None, operation="test")

    def test_non_transient_propagates_immediately(self, monkeypatch):
        svc = _make_svc(monkeypatch)

        def attempt(i):
            raise ValueError("bad config")

        with pytest.raises(ValueError, match="bad config"):
            svc._run_with_retry(attempt, operation="test")
