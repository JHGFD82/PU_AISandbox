"""Tests for src/services/base_service.py — BaseService shared methods.

Covers:
  - _create_completion() all three API branches
  - _record_response_usage() with/without usage data, parallel mode, critical flag
  - _run_with_retry() success, None-result exhaustion, transient retry,
    content-filter retry, non-retryable error, and return_signal_on_error paths
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.services.base_service import BaseService
from src.services.api_errors import APISignal


# ---------------------------------------------------------------------------
# Helpers — build a BaseService with all I/O patched out
# ---------------------------------------------------------------------------

def _make_svc(monkeypatch, **kwargs) -> BaseService:
    """Construct a BaseService instance without any real disk/network I/O."""
    monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
    monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
    monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
    monkeypatch.setattr("src.services.base_service.model_omit_sampling_params", lambda m: False)
    tracker = MagicMock()
    tracker.record_usage.return_value = MagicMock(total_cost=0.001)
    svc = BaseService.__new__(BaseService)
    svc.api_key = "fake"
    svc.professor = "test"
    svc.custom_model = kwargs.get("custom_model", None)
    svc.custom_temperature = kwargs.get("custom_temperature", None)
    svc.custom_top_p = kwargs.get("custom_top_p", None)
    svc.custom_max_tokens = kwargs.get("custom_max_tokens", None)
    svc.token_tracker = tracker
    svc.client = MagicMock()
    svc.system_note = None
    svc.user_note = None
    svc._suppress_inline_print = False
    return svc


# ---------------------------------------------------------------------------
# _create_completion — three API branches
# ---------------------------------------------------------------------------

class TestCreateCompletion:
    """
    model_uses_max_completion_tokens  model_has_fixed_parameters  branch
    False                             *                            max_tokens=
    True                              False                        max_completion_tokens= + temp/top_p
    True                              True                         max_completion_tokens= without temp/top_p
    """

    def _messages(self):
        return [{"role": "user", "content": "hi"}]

    def test_standard_model_uses_max_tokens(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: False)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        svc._create_completion("gpt-4o", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "max_tokens" in call_kwargs.kwargs
        assert "max_completion_tokens" not in call_kwargs.kwargs

    def test_reasoning_model_uses_max_completion_tokens(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: True)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        svc._create_completion("o1", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "max_completion_tokens" in call_kwargs.kwargs
        assert "max_tokens" not in call_kwargs.kwargs
        assert call_kwargs.kwargs.get("temperature") == 0.5

    def test_fixed_params_model_strips_temperature_and_top_p(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: True)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: True)
        svc._create_completion("o1-mini", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "max_completion_tokens" in call_kwargs.kwargs
        assert "temperature" not in call_kwargs.kwargs
        assert "top_p" not in call_kwargs.kwargs

    def test_no_temperature_not_passed_when_none(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: False)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        svc._create_completion("gpt-4o", self._messages(), 512)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "temperature" not in call_kwargs.kwargs
        assert "top_p" not in call_kwargs.kwargs

    def test_catalog_flag_omits_sampling_params(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: False)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        monkeypatch.setattr("src.services.base_service.model_omit_sampling_params", lambda m: True)

        svc._create_completion(
            "custom-model",
            self._messages(),
            512,
            temperature=0.2,
            top_p=0.9,
            frequency_penalty=0.3,
            presence_penalty=0.4,
        )
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "temperature" not in call_kwargs.kwargs
        assert "top_p" not in call_kwargs.kwargs
        assert "frequency_penalty" not in call_kwargs.kwargs
        assert "presence_penalty" not in call_kwargs.kwargs


class TestCreateCompletionStream:
    """_create_completion_stream() shares _build_completion_kwargs() with
    _create_completion(), so the model-specific quirks only need re-checking
    for the two stream-specific additions (stream=True, stream_options)."""

    def _messages(self):
        return [{"role": "user", "content": "hi"}]

    def test_sets_stream_true(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: False)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        svc._create_completion_stream("gpt-4o", self._messages(), 512)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert call_kwargs.kwargs["stream"] is True

    def test_requests_usage_in_final_chunk(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: False)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        svc._create_completion_stream("gpt-4o", self._messages(), 512)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert call_kwargs.kwargs["stream_options"] == {"include_usage": True}

    def test_non_streaming_call_has_no_stream_options(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: False)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        svc._create_completion("gpt-4o", self._messages(), 512)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "stream_options" not in call_kwargs.kwargs
        assert call_kwargs.kwargs["stream"] is False

    def test_reasoning_model_uses_max_completion_tokens_when_streaming(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: True)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        svc._create_completion_stream("o1", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "max_completion_tokens" in call_kwargs.kwargs
        assert "max_tokens" not in call_kwargs.kwargs
        assert call_kwargs.kwargs.get("temperature") == 0.5

    def test_fixed_params_model_strips_temperature_and_top_p_when_streaming(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: True)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: True)
        svc._create_completion_stream("o1-mini", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "temperature" not in call_kwargs.kwargs
        assert "top_p" not in call_kwargs.kwargs

    def test_returns_client_create_return_value(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_uses_max_completion_tokens", lambda m: False)
        monkeypatch.setattr("src.services.base_service.model_has_fixed_parameters", lambda m: False)
        fake_stream = iter([MagicMock()])
        svc.client.chat.completions.create.return_value = fake_stream
        result = svc._create_completion_stream("gpt-4o", self._messages(), 512)
        assert result is fake_stream


# ---------------------------------------------------------------------------
# _record_response_usage
# ---------------------------------------------------------------------------

class _Usage:
    prompt_tokens = 10
    completion_tokens = 20
    total_tokens = 30


class _Response:
    def __init__(self, with_usage=True, model="gpt-4o"):
        self.id = "resp-1"
        self.model = model
        self.usage = _Usage() if with_usage else None
        self.choices = []


class TestRecordResponseUsage:

    def test_records_usage_when_present(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        resp = _Response(with_usage=True)
        svc._record_response_usage(resp, "gpt-4o")
        svc.token_tracker.record_usage.assert_called_once()

    def test_warning_when_no_usage(self, monkeypatch, caplog):
        svc = _make_svc(monkeypatch)
        resp = _Response(with_usage=False)
        with caplog.at_level(logging.WARNING, logger="root"):
            svc._record_response_usage(resp, "gpt-4o")
        assert "No token usage" in caplog.text
        svc.token_tracker.record_usage.assert_not_called()

    def test_critical_flag_logs_error(self, monkeypatch, caplog):
        svc = _make_svc(monkeypatch)
        resp = _Response(with_usage=False)
        with caplog.at_level(logging.ERROR, logger="root"):
            svc._record_response_usage(resp, "gpt-4o", critical=True)
        assert "CRITICAL" in caplog.text

    def test_parallel_mode_logs_at_debug(self, monkeypatch, caplog):
        svc = _make_svc(monkeypatch)
        svc._suppress_inline_print = True
        resp = _Response(with_usage=True)
        with caplog.at_level(logging.DEBUG, logger="root"):
            svc._record_response_usage(resp, "gpt-4o")
        # Token info should appear at DEBUG, not INFO
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG and "Tokens used" in r.message]
        assert debug_records

    def test_raises_on_stream_response(self, monkeypatch):
        from collections.abc import Iterator
        svc = _make_svc(monkeypatch)

        class _FakeStream:
            def __iter__(self):
                return iter([])

        _FakeStream()
        # Make it look like an ABCIterator instance
        with patch("src.services.base_service.ABCIterator", Iterator):
            with pytest.raises(AssertionError):
                svc._record_response_usage(iter([]), "gpt-4o")


# ---------------------------------------------------------------------------
# _run_with_retry
# ---------------------------------------------------------------------------

class TestRunWithRetry:

    def test_returns_on_first_success(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        body = MagicMock(return_value="result")
        result = svc._run_with_retry(body, "gpt-4o")
        assert result == "result"
        body.assert_called_once_with(0)

    def test_retries_on_none_result_then_raises(self, monkeypatch):
        """body_fn returning None on every attempt → RuntimeError after MAX_RETRIES."""
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 2)
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        body = MagicMock(return_value=None)
        with pytest.raises(RuntimeError, match="returned no content"):
            svc._run_with_retry(body, "gpt-4o")
        assert body.call_count == 2

    def test_retries_on_transient_error(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 3)
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        monkeypatch.setattr("src.services.base_service.is_transient_error", lambda e: True)
        calls = []
        def body(attempt):
            calls.append(attempt)
            if attempt < 2:
                raise Exception("503 unavailable")
            return "ok"
        result = svc._run_with_retry(body, "gpt-4o")
        assert result == "ok"
        assert len(calls) == 3

    def test_raises_non_retryable_error(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 3)
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        monkeypatch.setattr("src.services.base_service.is_transient_error", lambda e: False)
        monkeypatch.setattr("src.services.base_service.classify_api_error",
                            lambda e, m: (_ for _ in ()).throw(e))
        def body(attempt):
            raise ValueError("invalid_request")
        with pytest.raises(ValueError, match="invalid_request"):
            svc._run_with_retry(body, "gpt-4o")

    def test_return_signal_on_error_true_returns_signal(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 2)
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        monkeypatch.setattr("src.services.base_service.is_transient_error", lambda e: False)
        monkeypatch.setattr("src.services.base_service.classify_api_error",
                            lambda e, m: APISignal.CONTENT_FILTER)
        # content filter on attempt 0 < MAX_RETRIES-1, then exhausted → returns signal
        def body(attempt):
            raise Exception("content_filter")
        result = svc._run_with_retry(body, "gpt-4o", return_signal_on_error=True)
        assert result == APISignal.CONTENT_FILTER


# ---------------------------------------------------------------------------
# _build_image_content_block
# ---------------------------------------------------------------------------

class TestImageContentBlock:

    def test_claude_model_uses_anthropic_base64_block(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        data_url = "data:image/jpeg;base64,QUJDRA=="

        block = svc._build_image_content_block("claude-3-7-sonnet", data_url)

        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/jpeg"
        assert block["source"]["data"] == "QUJDRA=="

    def test_non_claude_model_uses_image_url_block(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        data_url = "data:image/png;base64,AAA="

        block = svc._build_image_content_block("gpt-4o", data_url)

        assert block == {"type": "image_url", "image_url": {"url": data_url}}

    def test_claude_model_falls_back_when_not_data_url(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        not_data_url = "https://example.com/image.png"

        block = svc._build_image_content_block("claude-3-opus", not_data_url)

        assert block == {"type": "image_url", "image_url": {"url": not_data_url}}

    def test_return_signal_on_none_exhaustion(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 2)
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        body = MagicMock(return_value=None)
        result = svc._run_with_retry(body, "gpt-4o", return_signal_on_error=True)
        assert result == APISignal.CONTENT_FILTER

    def test_custom_timeout_msg(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 1)
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        body = MagicMock(return_value=None)
        with pytest.raises(RuntimeError, match="my custom msg"):
            svc._run_with_retry(body, "gpt-4o", timeout_msg="my custom msg")

    def test_content_filter_retries_then_exhausts(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 3)
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        monkeypatch.setattr("src.services.base_service.is_transient_error", lambda e: False)
        monkeypatch.setattr("src.services.base_service.classify_api_error",
                            lambda e, m: APISignal.CONTENT_FILTER)
        calls = []
        def body(attempt):
            calls.append(attempt)
            raise Exception("content_filter")
        with pytest.raises(Exception, match="content_filter"):
            svc._run_with_retry(body, "gpt-4o", return_signal_on_error=False)
        # Should have retried (content filter retries up to MAX_RETRIES-1)
        assert len(calls) >= 2


# ---------------------------------------------------------------------------
# _resolve_sampling_params — debug logging branch (line 76)
# ---------------------------------------------------------------------------

class TestResolveSamplingParams:

    def test_debug_logged_when_custom_temperature_set(self, monkeypatch, caplog):
        svc = _make_svc(monkeypatch, custom_temperature=0.3)
        with caplog.at_level(logging.DEBUG):
            svc._resolve_sampling_params("gpt-4o", 1.0, 1.0, 1000)
        assert any("Sampling params" in r.message for r in caplog.records)

    def test_debug_logged_when_custom_top_p_set(self, monkeypatch, caplog):
        svc = _make_svc(monkeypatch, custom_top_p=0.8)
        with caplog.at_level(logging.DEBUG):
            svc._resolve_sampling_params("gpt-4o", 1.0, 1.0, 1000)
        assert any("Sampling params" in r.message for r in caplog.records)

    def test_no_debug_when_no_custom_sampling_params(self, monkeypatch, caplog):
        svc = _make_svc(monkeypatch)
        with caplog.at_level(logging.DEBUG):
            svc._resolve_sampling_params("gpt-4o", 1.0, 1.0, 1000)
        assert not any("Sampling params" in r.message for r in caplog.records)

    def test_returns_custom_values_when_set(self, monkeypatch):
        svc = _make_svc(monkeypatch, custom_temperature=0.5, custom_top_p=0.7, custom_max_tokens=256)
        temp, top_p, max_tok = svc._resolve_sampling_params("gpt-4o", 1.0, 1.0, 1000)
        assert temp == 0.5
        assert top_p == 0.7
        assert max_tok == 256

    def test_falls_back_to_defaults_when_none(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        temp, top_p, max_tok = svc._resolve_sampling_params("gpt-4o", 0.9, 0.95, 500)
        assert temp == 0.9
        assert top_p == 0.95
        assert max_tok == 500  # _make_svc patches get_model_max_completion_tokens to return default

