"""Base class shared by all AI service modules."""

import logging
import time
from typing import Any, Callable, Optional

from portkey_ai import Portkey
from collections.abc import Iterator as ABCIterator

from ..models import (
    model_uses_max_completion_tokens, model_has_fixed_parameters,
    resolve_model, maybe_sync_model_pricing,
)
from ..tracking.token_tracker import TokenTracker
from .api_errors import APISignal, classify_api_error, is_transient_error
from .constants import MAX_RETRIES, BASE_RETRY_DELAY


class BaseService:
    """Common foundation for all PortKey-backed AI services.

    Subclasses inherit:
      - Portkey client initialisation
      - Shared TokenTracker setup (accept existing or create new)
      - system_note / user_note attributes for runtime --notes injection
      - _create_completion() for the 3-branch max_tokens API call
      - _record_response_usage() for token tracking + logging
      - _run_with_retry() for exponential-backoff retry with classify_api_error
      - _get_model() default (resolve_model + maybe_sync_model_pricing)
      - _extract_response_content() for safely pulling text from API responses

    Subclasses may override _get_model() for custom model selection
    (e.g. vision-only models) and are responsible for their own prompt
    construction.
    """

    def __init__(
        self,
        api_key: str,
        professor: Optional[str] = None,
        token_tracker: Optional[TokenTracker] = None,
        token_tracker_file: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        self.api_key = api_key
        self.professor = professor
        self.custom_model = model
        self.custom_temperature = temperature
        self.custom_top_p = top_p
        self.custom_max_tokens = max_tokens
        self.client = Portkey(api_key=api_key)
        self.token_tracker = (
            token_tracker
            if token_tracker is not None
            else TokenTracker(professor=professor or "", data_file=token_tracker_file)
        )
        self.system_note: Optional[str] = None
        self.user_note: Optional[str] = None

    def _get_model(self) -> str:
        """Resolve and return the model to use, syncing pricing if needed.

        Subclasses may override this to require vision support or a specific
        default (e.g. ImageProcessorService, ImageTranslationService).
        """
        model = resolve_model(requested_model=self.custom_model)
        maybe_sync_model_pricing(model)
        return model

    def _extract_response_content(self, response: Any) -> Optional[str]:
        """Safely extract the text content from an API response choice.

        Returns the content string if present and a valid str, otherwise None.
        """
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content
            if isinstance(content, str):
                return content
        return None

    def _create_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        **extra_kwargs: Any,
    ) -> Any:
        """Call the chat completions API using the correct token-limit parameter for the model.

        Uses max_completion_tokens for reasoning/o-series models and max_tokens
        for all others. When the model has fixed parameters (e.g. o1), temperature
        and top_p are omitted entirely. Any additional keyword arguments are
        forwarded directly to the API call.
        """
        use_completion_tokens = model_uses_max_completion_tokens(model)
        fixed_params = model_has_fixed_parameters(model)

        base_kwargs: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": messages,
            **extra_kwargs,
        }
        if temperature is not None:
            base_kwargs["temperature"] = temperature
        if top_p is not None:
            base_kwargs["top_p"] = top_p

        if use_completion_tokens and fixed_params:
            return self.client.chat.completions.create(  # type: ignore[misc]
                max_completion_tokens=max_tokens,
                **{k: v for k, v in base_kwargs.items() if k not in ("temperature", "top_p")},
            )
        if use_completion_tokens:
            return self.client.chat.completions.create(  # type: ignore[misc]
                max_completion_tokens=max_tokens,
                **base_kwargs,
            )
        return self.client.chat.completions.create(  # type: ignore[misc]
            max_tokens=max_tokens,
            **base_kwargs,
        )

    def _record_response_usage(self, response: Any, model: str, critical: bool = False) -> None:
        """Record token usage from an API response and log a summary.

        Args:
            response: The raw API response object.
            model: The model name used for the request (fallback if response.model is absent).
            critical: When True, logs a CRITICAL error instead of a warning when usage is missing.
                      Set this for operations (e.g. OCR, image translation) where missing billing
                      data indicates a serious configuration problem.
        """
        assert not isinstance(response, ABCIterator), "Unexpected stream response received."

        if response.id:
            logging.debug(f"API call successful. Response ID: {response.id}")
        if response.model:
            logging.debug(f"Model used: {response.model}")

        if (
            response.usage
            and response.usage.prompt_tokens is not None
            and response.usage.completion_tokens is not None
            and response.usage.total_tokens is not None
        ):
            usage = self.token_tracker.record_usage(
                model=response.model or model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                requested_model=model,
            )
            # In parallel mode (_suppress_inline_print=True) the tqdm postfix already
            # shows running totals, so demote per-call token info to DEBUG.
            _token_level = (
                logging.DEBUG
                if getattr(self, "_suppress_inline_print", False)
                else logging.INFO
            )
            logging.log(
                _token_level,
                f"Tokens used \u2014 prompt: {response.usage.prompt_tokens}, "
                f"completion: {response.usage.completion_tokens}, "
                f"total: {response.usage.total_tokens}, "
                f"cost: ${usage.total_cost:.4f}"
            )
        else:
            if critical:
                logging.error("CRITICAL: No token usage in response. Token tracking failed!")
            else:
                logging.warning("No token usage information available in response.")

    def _run_with_retry(
        self,
        body_fn: Callable[[int], Any],
        model: str,
        operation: str = "API call",
        timeout_msg: Optional[str] = None,
        return_signal_on_error: bool = False,
    ) -> Any:
        """Run a request in a retry loop with exponential backoff and error classification.

        Args:
            body_fn: Called on each attempt with the attempt index (0-based).
                     Return any non-None value to signal success and exit the loop.
                     Return None to signal \"no usable content — retry\".
            model: Model name forwarded to classify_api_error.
            operation: Human-readable label used in log messages (e.g. \"OCR\", \"translation\").
            timeout_msg: RuntimeError message when all retries are exhausted without
                         an exception. Defaults to a generic message.
            return_signal_on_error: When True, return the APISignal on error or retry
                                    exhaustion instead of raising (translation pattern).
                                    When False (default), raise (OCR / image pattern).
        Returns:
            The non-None value returned by body_fn, or an APISignal when
            return_signal_on_error=True and an unresolvable error occurred.
        Raises:
            RuntimeError: All retries exhausted with return_signal_on_error=False.
            Exception: Propagated from classify_api_error for non-retryable errors
                       when return_signal_on_error=False.
        """
        if timeout_msg is None:
            timeout_msg = f"{operation} returned no content after {MAX_RETRIES} retries."

        for attempt in range(MAX_RETRIES):
            try:
                if attempt > 0:
                    delay = BASE_RETRY_DELAY * (2 ** attempt) + (0.1 * attempt)
                    logging.info(
                        f"Retrying {operation} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES}) after {delay:.1f}s..."
                    )
                    time.sleep(delay)
                result = body_fn(attempt)
                if result is not None:
                    return result
            except Exception as e:
                if is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = BASE_RETRY_DELAY * (2 ** attempt) + (0.1 * attempt)
                    logging.warning(
                        f"Transient server error on {operation} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES}), retrying in {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
                    continue
                signal = classify_api_error(e, model)
                if signal == APISignal.CONTENT_FILTER and attempt < MAX_RETRIES - 1:
                    logging.warning(
                        f"Content filter triggered on {operation} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES}). Retrying..."
                    )
                    continue
                logging.error(f"{operation} error: {e}")
                if return_signal_on_error:
                    return signal
                raise

        if return_signal_on_error:
            logging.error(
                f"Content filter triggered on {operation} after {MAX_RETRIES} attempts, skipping."
            )
            return APISignal.CONTENT_FILTER
        raise RuntimeError(timeout_msg)
