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

import src.models.catalog as catalog_module
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
    monkeypatch.setattr("src.services.base_service.model_rejected_fields", lambda m: {})
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
    model_max_tokens_field()        sampling params in `rejects`   branch
    max_tokens (default)            no                             max_tokens=
    max_completion_tokens           no                             max_completion_tokens= + temp/top_p
    max_completion_tokens           yes                            max_completion_tokens= without temp/top_p
    """

    def _messages(self):
        return [{"role": "user", "content": "hi"}]

    def test_standard_model_uses_max_tokens(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_tokens")
        svc._create_completion("gpt-4o", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "max_tokens" in call_kwargs.kwargs
        assert "max_completion_tokens" not in call_kwargs.kwargs

    def test_reasoning_model_gets_the_max_completion_tokens_name(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_completion_tokens")
        svc._create_completion("o1", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "max_completion_tokens" in call_kwargs.kwargs
        assert "max_tokens" not in call_kwargs.kwargs
        assert call_kwargs.kwargs.get("temperature") == 0.5

    def test_fixed_params_model_strips_temperature_and_top_p(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_completion_tokens")
        # All four, because that is what the catalog records as a group — a
        # model that refuses one of these refuses the rest.
        monkeypatch.setattr("src.services.base_service.model_rejected_fields",
                            lambda m: {f: "refused" for f in catalog_module._SAMPLING_FIELDS})
        svc._create_completion("o1-mini", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "max_completion_tokens" in call_kwargs.kwargs
        assert "temperature" not in call_kwargs.kwargs
        assert "top_p" not in call_kwargs.kwargs

    def test_no_temperature_not_passed_when_none(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_tokens")
        svc._create_completion("gpt-4o", self._messages(), 512)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "temperature" not in call_kwargs.kwargs
        assert "top_p" not in call_kwargs.kwargs

    def test_refused_sampling_params_are_all_left_out(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_tokens")
        # All four, because that is what the catalog records as a group — a
        # model that refuses one of these refuses the rest.
        monkeypatch.setattr("src.services.base_service.model_rejected_fields",
                            lambda m: {f: "refused" for f in catalog_module._SAMPLING_FIELDS})

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
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_tokens")
        svc._create_completion_stream("gpt-4o", self._messages(), 512)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert call_kwargs.kwargs["stream"] is True

    def test_requests_usage_in_final_chunk(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_tokens")
        svc._create_completion_stream("gpt-4o", self._messages(), 512)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert call_kwargs.kwargs["stream_options"] == {"include_usage": True}

    def test_non_streaming_call_has_no_stream_options(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_tokens")
        svc._create_completion("gpt-4o", self._messages(), 512)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "stream_options" not in call_kwargs.kwargs
        assert call_kwargs.kwargs["stream"] is False

    def test_reasoning_model_gets_the_max_completion_tokens_name_when_streaming(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_completion_tokens")
        svc._create_completion_stream("o1", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "max_completion_tokens" in call_kwargs.kwargs
        assert "max_tokens" not in call_kwargs.kwargs
        assert call_kwargs.kwargs.get("temperature") == 0.5

    def test_fixed_params_model_strips_temperature_and_top_p_when_streaming(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_completion_tokens")
        # All four, because that is what the catalog records as a group — a
        # model that refuses one of these refuses the rest.
        monkeypatch.setattr("src.services.base_service.model_rejected_fields",
                            lambda m: {f: "refused" for f in catalog_module._SAMPLING_FIELDS})
        svc._create_completion_stream("o1-mini", self._messages(), 512, temperature=0.5, top_p=0.9)
        call_kwargs = svc.client.chat.completions.create.call_args
        assert "temperature" not in call_kwargs.kwargs
        assert "top_p" not in call_kwargs.kwargs

    def test_returns_client_create_return_value(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_tokens")
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
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        body = MagicMock(return_value=None)
        with pytest.raises(RuntimeError, match="returned no content"):
            svc._run_with_retry(body, "gpt-4o")
        assert body.call_count == 2

    def test_retries_on_transient_error(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 3)
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 0)
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

    def test_waits_once_per_retry_at_a_flat_delay(self, monkeypatch):
        """Each retry waits exactly once, for the same length of time.

        Two things used to go wrong together here. The wait doubled after
        every attempt, and the transient-error handler slept before
        ``continue`` on top of the sleep at the top of the loop — so a
        transient failure paid the wait twice per attempt. At the shipped ten
        retries that came to about 77 minutes before the call gave up, 26 of
        them purely the double count. Someone watching a command run is
        better served by a clear failure in under a minute.
        """
        svc = _make_svc(monkeypatch)
        slept: list[float] = []
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 4)
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 5.0)
        monkeypatch.setattr("src.services.base_service.time.sleep", slept.append)
        monkeypatch.setattr("src.services.base_service.is_transient_error", lambda e: True)

        def body(attempt):
            raise Exception("503 unavailable")

        with pytest.raises(Exception, match="503 unavailable"):
            svc._run_with_retry(body, "gpt-4o")

        # Four attempts means three gaps between them, not six.
        assert slept == [5.0, 5.0, 5.0]

    def test_flat_delay_also_applies_to_empty_responses(self, monkeypatch):
        """The other retry path — a response with no content — waits the same way."""
        svc = _make_svc(monkeypatch)
        slept: list[float] = []
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 3)
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 5.0)
        monkeypatch.setattr("src.services.base_service.time.sleep", slept.append)

        with pytest.raises(RuntimeError, match="returned no content"):
            svc._run_with_retry(MagicMock(return_value=None), "gpt-4o")

        assert slept == [5.0, 5.0]

    def test_raises_non_retryable_error(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 3)
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 0)
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
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 0)
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
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        body = MagicMock(return_value=None)
        result = svc._run_with_retry(body, "gpt-4o", return_signal_on_error=True)
        assert result == APISignal.CONTENT_FILTER

    def test_custom_timeout_msg(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 1)
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 0)
        monkeypatch.setattr("src.services.base_service.time.sleep", lambda _: None)
        body = MagicMock(return_value=None)
        with pytest.raises(RuntimeError, match="my custom msg"):
            svc._run_with_retry(body, "gpt-4o", timeout_msg="my custom msg")

    def test_content_filter_retries_then_exhausts(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.MAX_RETRIES", 3)
        monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 0)
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



# ---------------------------------------------------------------------------
# Learning which request fields a model refuses
# ---------------------------------------------------------------------------

# The error that prompted all of this: mistral-small-2503 routed through
# azure-ai, which rejects unknown keys in the request body outright. The web
# interface always asks for streamed usage totals, so every chat turn with
# that model failed — with a generic "something went wrong", because nothing
# recognised the error.
AZURE_AI_REFUSES_STREAM_OPTIONS = (
    "Error code: 422 - {'error': {'message': 'azure-ai error: "
    '{"detail":[{"type":"extra_forbidden","loc":["body","stream_options",'
    '"include_usage"],"msg":"Extra inputs are not permitted"}]}\', '
    "'code': 'Invalid input'}, 'provider': 'azure-ai'}"
)


class TestLearningRefusedFields:

    def _svc(self, monkeypatch, *, already_rejected=None):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr("src.services.base_service.model_max_tokens_field", lambda m: "max_tokens")
        monkeypatch.setattr(
            "src.services.base_service.model_rejected_fields",
            lambda m: dict(already_rejected or {}),
        )
        return svc

    def test_refused_field_is_recorded_dropped_and_the_request_retried(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "src.services.base_service.record_rejected_field",
            lambda model, field, reason: recorded.append((model, field)) or True,
        )
        svc = self._svc(monkeypatch)
        svc.client.chat.completions.create.side_effect = [
            Exception(AZURE_AI_REFUSES_STREAM_OPTIONS),
            "the reply",
        ]

        result = svc._create_completion_stream("mistral-small-2503", [{"role": "user", "content": "hi"}], 100)

        assert result == "the reply"
        assert recorded == [("mistral-small-2503", "stream_options")]
        # First attempt carried the field; the retry did not.
        first, second = svc.client.chat.completions.create.call_args_list
        assert "stream_options" in first.kwargs
        assert "stream_options" not in second.kwargs

    def test_a_field_already_known_to_be_refused_is_never_sent(self, monkeypatch):
        svc = self._svc(monkeypatch, already_rejected={"stream_options": "2026-07-29: nope"})
        svc.client.chat.completions.create.return_value = "the reply"

        svc._create_completion_stream("mistral-small-2503", [{"role": "user", "content": "hi"}], 100)

        kwargs = svc.client.chat.completions.create.call_args.kwargs
        assert "stream_options" not in kwargs
        assert svc.client.chat.completions.create.call_count == 1  # no failed first try

    def test_an_unrelated_error_is_raised_unchanged(self, monkeypatch):
        """A failure that isn't a field refusal must reach the caller untouched.

        Everything downstream — the retry loop, the model-access cleanup, the
        user-facing messages — depends on seeing the original error.
        """
        svc = self._svc(monkeypatch)
        svc.client.chat.completions.create.side_effect = Exception("rate_limit exceeded")
        with pytest.raises(Exception, match="rate_limit"):
            svc._create_completion("gpt-4o", [{"role": "user", "content": "hi"}], 100)
        assert svc.client.chat.completions.create.call_count == 1

    def test_a_field_the_request_never_sent_is_not_recorded(self, monkeypatch):
        """Guards against a pattern reading the wrong word out of a new error.

        Recording a field that wasn't in the request would put nonsense in the
        catalog for good, and dropping it would change nothing — so the real
        failure would look handled while still happening.
        """
        recorded = []
        monkeypatch.setattr(
            "src.services.base_service.record_rejected_field",
            lambda model, field, reason: recorded.append(field) or True,
        )
        svc = self._svc(monkeypatch)
        svc.client.chat.completions.create.side_effect = Exception(
            "Error code: 400 - Unrecognized request argument supplied: nonesuch"
        )
        with pytest.raises(Exception, match="nonesuch"):
            svc._create_completion("gpt-4o", [{"role": "user", "content": "hi"}], 100)
        assert recorded == []

    @pytest.mark.parametrize("field", ["model", "messages", "stream"])
    def test_the_request_essentials_are_never_dropped(self, monkeypatch, field):
        """However a provider phrases it, a request still needs these.

        Both halves are checked: a catalog that already names one of them, and
        an error asking for one to be dropped.
        """
        svc = self._svc(monkeypatch, already_rejected={field: "somehow recorded"})
        svc.client.chat.completions.create.return_value = "the reply"
        svc._create_completion_stream("m", [{"role": "user", "content": "hi"}], 100)
        assert field in svc.client.chat.completions.create.call_args.kwargs

        svc2 = self._svc(monkeypatch)
        svc2.client.chat.completions.create.side_effect = Exception(
            f"Error code: 400 - {{'error': {{'message': 'Unknown parameter.', 'param': '{field}'}}}}"
        )
        with pytest.raises(Exception, match="Unknown parameter"):
            svc2._create_completion("m", [{"role": "user", "content": "hi"}], 100)

    def test_two_refused_fields_are_learned_one_after_the_other(self, monkeypatch):
        """Providers report one objection at a time, so one turn may need two passes."""
        monkeypatch.setattr("src.services.base_service.record_rejected_field", lambda *a: True)
        svc = self._svc(monkeypatch)
        svc.client.chat.completions.create.side_effect = [
            Exception(AZURE_AI_REFUSES_STREAM_OPTIONS),
            Exception("Error code: 400 - {'error': {'param': 'presence_penalty'}, "
                      "'message': 'unsupported parameter'}"),
            "the reply",
        ]
        result = svc._create_completion_stream(
            "m", [{"role": "user", "content": "hi"}], 100, presence_penalty=0.3,
        )
        assert result == "the reply"
        final = svc.client.chat.completions.create.call_args.kwargs
        assert "stream_options" not in final and "presence_penalty" not in final

    def test_a_provider_that_objects_endlessly_does_not_loop_forever(self, monkeypatch):
        monkeypatch.setattr("src.services.base_service.record_rejected_field", lambda *a: True)
        svc = self._svc(monkeypatch)
        svc.client.chat.completions.create.side_effect = Exception(AZURE_AI_REFUSES_STREAM_OPTIONS)
        with pytest.raises(Exception, match="Extra inputs"):
            svc._create_completion_stream("m", [{"role": "user", "content": "hi"}], 100)
        assert svc.client.chat.completions.create.call_count <= 4
