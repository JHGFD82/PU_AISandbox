"""
Tests for src/services/api_errors.py.

All external dependencies (is_model_access_error, remove_model_from_catalog)
are patched so no real catalog I/O or network calls occur.
"""

import pytest
from unittest.mock import patch

from src.services.api_errors import (
    APISignal,
    classify_api_error,
    handle_api_errors,
    is_content_filter_error,
    is_transient_error,
    raise_for_deprecated_sampling_params,
    raise_for_model_access_error,
)


# ---------------------------------------------------------------------------
# APISignal
# ---------------------------------------------------------------------------

class TestAPISignal:
    def test_context_length_value(self):
        assert APISignal.CONTEXT_LENGTH_EXCEEDED == "context_length_exceeded"

    def test_content_filter_value(self):
        assert APISignal.CONTENT_FILTER == "content_filter_triggered"

    def test_is_str_subclass(self):
        assert isinstance(APISignal.CONTEXT_LENGTH_EXCEEDED, str)


# ---------------------------------------------------------------------------
# is_content_filter_error
# ---------------------------------------------------------------------------

class TestIsContentFilterError:
    def test_content_filter_keyword(self):
        assert is_content_filter_error(Exception("content_filter blocked")) is True

    def test_jailbreak_keyword(self):
        assert is_content_filter_error(Exception("jailbreak detected")) is True

    def test_case_insensitive(self):
        assert is_content_filter_error(Exception("CONTENT_FILTER")) is True

    def test_unrelated_error(self):
        assert is_content_filter_error(Exception("some other error")) is False

    def test_empty_message(self):
        assert is_content_filter_error(Exception("")) is False


# ---------------------------------------------------------------------------
# is_transient_error
# ---------------------------------------------------------------------------

class TestIsTransientError:
    @pytest.mark.parametrize("msg", [
        "503 service unavailable",
        "unavailable right now",
        "Deadline expired",
        "InternalServerError occurred",
        "502 Bad Gateway",
        "bad gateway response",
        "504 Gateway Timeout",
        "gateway timeout",
        "model overloaded",
    ])
    def test_transient_messages(self, msg):
        assert is_transient_error(Exception(msg)) is True

    def test_non_transient_error(self):
        assert is_transient_error(Exception("invalid_request")) is False

    def test_empty_message(self):
        assert is_transient_error(Exception("")) is False

    def test_case_insensitive(self):
        assert is_transient_error(Exception("INTERNALSERVERERROR")) is True


# ---------------------------------------------------------------------------
# raise_for_model_access_error
# ---------------------------------------------------------------------------

class TestRaiseForModelAccessError:

    def test_no_raise_when_not_access_error(self):
        """Should return silently when the error is not a model-access denial."""
        with patch("src.services.api_errors.is_model_access_error", return_value=False):
            # Must not raise
            raise_for_model_access_error(Exception("some error"), "gpt-4o")

    def test_raises_value_error_on_access_denied(self):
        with patch("src.services.api_errors.is_model_access_error", return_value=True), \
             patch("src.services.api_errors.remove_model_from_catalog", return_value=True):
            with pytest.raises(ValueError, match="not accessible"):
                raise_for_model_access_error(Exception("access denied"), "bad-model")

    def test_removed_note_included_when_catalog_updated(self):
        with patch("src.services.api_errors.is_model_access_error", return_value=True), \
             patch("src.services.api_errors.remove_model_from_catalog", return_value=True):
            with pytest.raises(ValueError, match="removed from the catalog"):
                raise_for_model_access_error(Exception("access denied"), "bad-model")

    def test_no_removed_note_when_not_in_catalog(self):
        with patch("src.services.api_errors.is_model_access_error", return_value=True), \
             patch("src.services.api_errors.remove_model_from_catalog", return_value=False):
            with pytest.raises(ValueError) as exc_info:
                raise_for_model_access_error(Exception("access denied"), "bad-model")
            assert "removed from the catalog" not in str(exc_info.value)

    def test_empty_model_name_skips_catalog_removal(self):
        """An empty model string should not call remove_model_from_catalog."""
        with patch("src.services.api_errors.is_model_access_error", return_value=True), \
             patch("src.services.api_errors.remove_model_from_catalog") as mock_remove:
            with pytest.raises(ValueError):
                raise_for_model_access_error(Exception("access denied"), "")
            mock_remove.assert_not_called()

    def test_chained_exception(self):
        original = Exception("original cause")
        with patch("src.services.api_errors.is_model_access_error", return_value=True), \
             patch("src.services.api_errors.remove_model_from_catalog", return_value=False):
            with pytest.raises(ValueError) as exc_info:
                raise_for_model_access_error(original, "m")
            assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# raise_for_deprecated_sampling_params
# ---------------------------------------------------------------------------

class TestRaiseForDeprecatedSamplingParams:

    def test_no_raise_when_not_deprecated_error(self):
        """Should return silently when the error isn't this specific pattern."""
        with patch("src.services.api_errors.is_sampling_param_deprecated_error", return_value=False):
            # Must not raise
            raise_for_deprecated_sampling_params(Exception("some error"), "gpt-4o")

    def test_raises_value_error_and_mentions_temperature(self):
        with patch("src.services.api_errors.is_sampling_param_deprecated_error", return_value=True), \
             patch("src.services.api_errors.set_model_fixed_parameters", return_value=True):
            with pytest.raises(ValueError, match="temperature/top-p"):
                raise_for_deprecated_sampling_params(
                    Exception("`temperature` is deprecated for this model."), "claude-fable-5",
                )

    def test_updated_note_included_when_catalog_updated(self):
        with patch("src.services.api_errors.is_sampling_param_deprecated_error", return_value=True), \
             patch("src.services.api_errors.set_model_fixed_parameters", return_value=True):
            with pytest.raises(ValueError, match="marked as a fixed-parameter model"):
                raise_for_deprecated_sampling_params(Exception("deprecated"), "claude-fable-5")

    def test_no_updated_note_when_already_fixed_or_absent(self):
        with patch("src.services.api_errors.is_sampling_param_deprecated_error", return_value=True), \
             patch("src.services.api_errors.set_model_fixed_parameters", return_value=False):
            with pytest.raises(ValueError) as exc_info:
                raise_for_deprecated_sampling_params(Exception("deprecated"), "claude-fable-5")
            assert "marked as a fixed-parameter model" not in str(exc_info.value)

    def test_empty_model_name_skips_catalog_update(self):
        with patch("src.services.api_errors.is_sampling_param_deprecated_error", return_value=True), \
             patch("src.services.api_errors.set_model_fixed_parameters") as mock_set:
            with pytest.raises(ValueError):
                raise_for_deprecated_sampling_params(Exception("deprecated"), "")
            mock_set.assert_not_called()

    def test_chained_exception(self):
        original = Exception("original cause")
        with patch("src.services.api_errors.is_sampling_param_deprecated_error", return_value=True), \
             patch("src.services.api_errors.set_model_fixed_parameters", return_value=False):
            with pytest.raises(ValueError) as exc_info:
                raise_for_deprecated_sampling_params(original, "m")
            assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# handle_api_errors
# ---------------------------------------------------------------------------

class TestHandleApiErrors:

    def _no_access(self):
        return patch("src.services.api_errors.is_model_access_error", return_value=False)

    def test_rate_limit_raises(self):
        with self._no_access():
            with pytest.raises(Exception, match="Rate limit exceeded"):
                handle_api_errors(Exception("rate_limit hit"), "gpt-4o")

    def test_invalid_request_raises(self):
        with self._no_access():
            with pytest.raises(Exception, match="Invalid request"):
                handle_api_errors(Exception("invalid_request error"), "gpt-4o")

    def test_authentication_error_raises(self):
        with self._no_access():
            with pytest.raises(Exception, match="Authentication error"):
                handle_api_errors(Exception("authentication failed"), "gpt-4o")

    def test_unauthorized_raises(self):
        with self._no_access():
            with pytest.raises(Exception, match="Authentication error"):
                handle_api_errors(Exception("unauthorized access"), "gpt-4o")

    def test_unknown_error_returns_silently(self):
        """Unrecognised errors should not be raised — caller decides."""
        with self._no_access():
            result = handle_api_errors(Exception("some weird error"), "gpt-4o")
            assert result is None

    def test_model_access_error_delegates(self):
        with patch("src.services.api_errors.is_model_access_error", return_value=True), \
             patch("src.services.api_errors.remove_model_from_catalog", return_value=False):
            with pytest.raises(ValueError, match="not accessible"):
                handle_api_errors(Exception("invalid target name found in the query router"), "bad-model")

    def test_deprecated_sampling_params_delegates(self):
        with self._no_access(), \
             patch("src.services.api_errors.set_model_fixed_parameters", return_value=True):
            with pytest.raises(ValueError, match="no longer accepts temperature/top-p"):
                handle_api_errors(Exception("`temperature` is deprecated for this model."), "claude-fable-5")

    def test_deprecated_sampling_params_takes_priority_over_invalid_request(self):
        """The real-world PortKey error for this case is wrapped as
        'invalid_request_error', so the specific check must run first or the
        generic invalid-request branch would swallow it instead."""
        with self._no_access(), \
             patch("src.services.api_errors.set_model_fixed_parameters", return_value=True):
            real_error = Exception(
                "Error code: 400 - {'error': {'message': \"azure-ai error: `temperature` is "
                "deprecated for this model.\", 'type': 'invalid_request_error', 'param': None, "
                "'code': None}, 'provider': 'azure-ai'}"
            )
            with pytest.raises(ValueError, match="no longer accepts temperature/top-p"):
                handle_api_errors(real_error, "claude-fable-5")


# ---------------------------------------------------------------------------
# classify_api_error
# ---------------------------------------------------------------------------

class TestClassifyApiError:

    def _no_access(self):
        return patch("src.services.api_errors.is_model_access_error", return_value=False)

    def test_context_length_exceeded(self):
        with self._no_access():
            result = classify_api_error(Exception("context_length_exceeded"), "gpt-4o")
            assert result is APISignal.CONTEXT_LENGTH_EXCEEDED

    def test_maximum_context_length_phrase(self):
        with self._no_access():
            result = classify_api_error(Exception("maximum context length is 128000"), "gpt-4o")
            assert result is APISignal.CONTEXT_LENGTH_EXCEEDED

    def test_content_filter(self):
        with self._no_access():
            result = classify_api_error(Exception("content_filter blocked this"), "gpt-4o")
            assert result is APISignal.CONTENT_FILTER

    def test_jailbreak_maps_to_content_filter(self):
        with self._no_access():
            result = classify_api_error(Exception("jailbreak attempt detected"), "gpt-4o")
            assert result is APISignal.CONTENT_FILTER

    def test_rate_limit_raises(self):
        with self._no_access():
            with pytest.raises(Exception, match="Rate limit exceeded"):
                classify_api_error(Exception("rate_limit hit"), "gpt-4o")

    def test_authentication_raises(self):
        with self._no_access():
            with pytest.raises(Exception, match="Authentication error"):
                classify_api_error(Exception("authentication failed"), "gpt-4o")

    def test_unrecognised_error_raises_generic(self):
        with self._no_access():
            with pytest.raises(Exception, match="API call failed"):
                classify_api_error(Exception("something totally unexpected"), "gpt-4o")

    def test_model_access_denied_raises_value_error(self):
        with patch("src.services.api_errors.is_model_access_error", return_value=True), \
             patch("src.services.api_errors.remove_model_from_catalog", return_value=False):
            with pytest.raises(ValueError, match="not accessible"):
                classify_api_error(Exception("invalid target name found in the query router"), "bad-model")

    def test_context_length_case_insensitive(self):
        with self._no_access():
            result = classify_api_error(Exception("CONTEXT_LENGTH_EXCEEDED"), "gpt-4o")
            assert result is APISignal.CONTEXT_LENGTH_EXCEEDED
