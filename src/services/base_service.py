"""Foundation class that all AI service modules build on.

Subclasses (such as ``TranslationService`` and ``ImageProcessorService``) get
a working AI client connection, token tracking, retry handling, and prompt
construction for free by inheriting from ``BaseService``. Plugin developers
do not use this class directly — it is wired up automatically by
``SandboxProcessor``.
"""

import logging
import os
import re
import time
from typing import Any, Callable, Optional

from portkey_ai import Portkey
from collections.abc import Iterator as ABCIterator

from ..models import (
    model_uses_max_completion_tokens, model_has_fixed_parameters, model_omit_sampling_params,
    resolve_model, maybe_sync_model_pricing, get_model_max_completion_tokens,
)
from ..tracking.token_tracker import TokenTracker
from .api_errors import APISignal, classify_api_error, is_transient_error
from .constants import MAX_RETRIES, BASE_RETRY_DELAY


class BaseService:
    """Common foundation for all AI services that route requests through the PortKey gateway.

    Provides a ready-to-use AI client connection, shared token tracking,
    retry handling with automatic waits on failure, and helpers for building
    and sending API requests. All concrete service classes (e.g.
    ``TranslationService``, ``ImageProcessorService``) inherit from this class
    and add their own prompt-construction logic on top.

    Subclasses may override ``_get_model()`` to require a vision-capable model
    or a different default, and are responsible for constructing their own
    prompts before calling ``_run_with_retry()``.
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
        """Connect to the AI gateway and prepare token tracking for a professor.

        Sets up the PortKey AI client using the provided API key, attaches a
        token tracker so every call is logged against the correct professor's
        budget, and stores any custom model or sampling overrides for use in
        later API calls.

        Args:
            api_key: The API key (private credential) used to authenticate
                     with the PortKey gateway. Obtained via ``get_api_key()``
                     in ``src/config.py``.
            professor: The professor's safe-filename identifier
                       (e.g. ``'heller'``), used to write usage to the correct
                       tracking file. ``None`` only in tests — in normal
                       operation this is supplied by ``SandboxProcessor``.
            token_tracker: An existing ``TokenTracker`` instance to share
                           across services in the same session, so that totals
                           are not double-counted. When ``None``, a new tracker
                           is created automatically for this service.
            token_tracker_file: Path to an alternative usage file used by the
                                auto-created tracker. ``None`` in normal
                                operation; redirected to a temporary file in
                                tests.
            model: Override the default model name for this service
                   (e.g. ``'gpt-4o-mini'``). ``None`` means use the catalog
                   default for this service's role.
            temperature: Override how varied or creative the model's responses
                         are (a number from ``0.0`` to ``2.0``). ``None`` means
                         use the service default.
            top_p: Alternative way to control response variety — limits the
                   model to the most probable portion of its output
                   (``0.0`` to ``1.0``). ``None`` means use the service
                   default.
            max_tokens: Cap on how long the model's response can be, measured
                        in tokens (roughly one token per word). ``None`` means
                        use the per-model default from the catalog.
        """
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
        self._suppress_inline_print: bool = False

    def _resolve_sampling_params(
        self,
        model: str,
        default_temperature: float,
        default_top_p: float,
        default_max_tokens: int,
    ) -> tuple[float, float, int]:
        """Return the temperature, top-p, and max-tokens values to use for an API call.

        Applies any custom overrides set at construction time, falling back to
        the service's own defaults when none were provided.

        Args:
            model: The model name being used — needed to look up any per-model
                   token-limit override in the catalog (e.g. ``'gpt-4o'``).
            default_temperature: The service's default temperature to use when
                                 no override was set (e.g. ``0.3``).
            default_top_p: The service's default top-p value (e.g. ``0.95``).
            default_max_tokens: The service's default response-length cap in
                                tokens (e.g. ``4096``).

        Returns:
            A three-item tuple of ``(temperature, top_p, max_tokens)`` with
            overrides applied.
        """
        temperature = self.custom_temperature if self.custom_temperature is not None else default_temperature
        top_p = self.custom_top_p if self.custom_top_p is not None else default_top_p
        max_tokens = self.custom_max_tokens if self.custom_max_tokens is not None else get_model_max_completion_tokens(model, default_max_tokens)
        if self.custom_temperature is not None or self.custom_top_p is not None:
            logging.debug(f"Sampling params: temperature={temperature}, top_p={top_p}")
        return temperature, top_p, max_tokens

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

    @staticmethod
    def _is_claude_family_model(model: str) -> bool:
        """Return True when *model* appears to target Anthropic Claude."""
        lower = model.lower()
        return "claude" in lower or lower.startswith("anthropic/")

    def _build_image_content_block(self, model: str, data_url: str) -> dict[str, Any]:
        """Wrap a base64-encoded image in the format the target model expects.

        Different AI providers require different JSON structures around an
        image. Most models use the OpenAI-style ``image_url`` format. Models
        in the Claude family (made by Anthropic) require their own ``image``
        block format with the image data embedded directly. This function
        detects which family the model belongs to and returns the appropriate
        structure so the API call is accepted without modification.

        Args:
            model: The model name being used (e.g. ``'gpt-4o'``,
                   ``'claude-3-5-sonnet'``).
            data_url: The image encoded as a data URL string — a self-contained
                      text representation that includes the image type and
                      the raw image data encoded in base64
                      (e.g. ``'data:image/png;base64,iVBOR...'``).

        Returns:
            A dictionary ready to include in the ``content`` list of an API
            message, formatted correctly for the given model.
        """
        if self._is_claude_family_model(model):
            m = re.match(r"^data:([^;]+);base64,(.+)$", data_url, re.DOTALL)
            if m:
                media_type, base64_data = m.group(1), m.group(2)
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64_data,
                    },
                }
            logging.warning(
                "Claude model selected but image was not a data URL; "
                "falling back to image_url payload format."
            )

        return {"type": "image_url", "image_url": {"url": data_url}}

    def _build_completion_kwargs(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: Optional[float],
        top_p: Optional[float],
        stream: bool,
        extra_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble the keyword arguments for a chat completion request, streaming or not.

        Shared by ``_create_completion()`` and ``_create_completion_stream()``
        so the model-specific quirks (the ``max_tokens`` vs
        ``max_completion_tokens`` parameter name, which models reject
        ``temperature``/``top_p`` entirely) are handled in exactly one place
        regardless of which one a caller uses.

        Args:
            model: The model to use for this request (e.g. ``'gpt-4o'``).
            messages: The conversation history to send, as a list of
                      ``{'role': ..., 'content': ...}`` dictionaries.
            max_tokens: Maximum number of tokens allowed in the response.
            temperature: How varied or creative the response should be
                         (``0.0``–``2.0``). ``None`` omits this parameter
                         from the API call entirely.
            top_p: Alternative response-variety control (``0.0``–``1.0``).
                   ``None`` omits this parameter.
            stream: Whether this request should stream its response back
                    incrementally. When ``True``, also asks the API for a
                    final usage-only chunk (``stream_options.include_usage``)
                    so billing can still be recorded once the stream ends.
            extra_kwargs: Any additional keyword arguments to forward
                          directly to the API call (e.g. ``frequency_penalty``).

        Returns:
            The complete keyword-argument dictionary ready to pass to
            ``self.client.chat.completions.create(**kwargs)``.
        """
        use_completion_tokens = model_uses_max_completion_tokens(model)
        fixed_params = model_has_fixed_parameters(model)
        omit_sampling_params = model_omit_sampling_params(model)

        kwargs: dict[str, Any] = {
            "model": model,
            "stream": stream,
            "messages": messages,
            **extra_kwargs,
        }
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p

        if fixed_params or omit_sampling_params:
            for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
                kwargs.pop(key, None)
            if omit_sampling_params and not fixed_params:
                logging.debug(
                    f"Omitting sampling params for model '{model}' due to catalog configuration."
                )

        kwargs["max_completion_tokens" if use_completion_tokens else "max_tokens"] = max_tokens
        return kwargs

    def _create_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        **extra_kwargs: Any,
    ) -> Any:
        """Send a chat request to the AI API and return the raw response.

        Automatically uses the correct parameter name for the response-length
        cap (``max_tokens`` or ``max_completion_tokens`` depending on the
        model) and omits temperature and top-p entirely for models that do
        not accept them. Any additional keyword arguments are forwarded
        directly to the API call.

        Args:
            model: The model to use for this request (e.g. ``'gpt-4o'``).
            messages: The conversation history to send, as a list of
                      ``{'role': ..., 'content': ...}`` dictionaries.
            max_tokens: Maximum number of tokens allowed in the response.
            temperature: How varied or creative the response should be
                         (``0.0``–``2.0``). ``None`` causes this parameter to
                         be omitted from the API call entirely.
            top_p: Alternative response-variety control (``0.0``–``1.0``).
                   ``None`` causes this parameter to be omitted.

        Returns:
            The raw API response object, passed internally to
            ``_record_response_usage()`` to log token counts and to
            ``_extract_response_content()`` to extract the text.
        """
        kwargs = self._build_completion_kwargs(
            model, messages, max_tokens, temperature, top_p, stream=False, extra_kwargs=extra_kwargs,
        )
        return self.client.chat.completions.create(**kwargs)  # type: ignore[misc]

    def _create_completion_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        **extra_kwargs: Any,
    ) -> Any:
        """Send a chat request and return a live stream of response chunks instead of one final reply.

        Each yielded chunk follows the OpenAI ``ChatCompletionChunk`` shape
        (Portkey normalizes every provider, including Anthropic/Claude, into
        this same format) — most chunks carry a small piece of new text at
        ``chunk.choices[0].delta.content``; because this method asks for
        ``stream_options.include_usage``, one final chunk (with an empty
        ``choices`` list) carries the completed call's token counts at
        ``chunk.usage`` instead of any text. See ``ChatService.stream_message()``
        for how the two are told apart and turned into billing data.

        Note: unlike ``_create_completion()``, callers of this method handle
        their own errors — ``_run_with_retry()``'s retry-on-transient-error
        behavior doesn't extend cleanly to a stream that fails partway
        through, since visible partial content has already reached the
        person by then; restarting it from scratch would silently duplicate
        or discard what they've already seen. Only the request itself (before
        any chunk is yielded) is safe to retry, so streaming callers are
        expected to treat a mid-stream failure as a hard stop, not a retry.

        Args:
            model: The model to use for this request (e.g. ``'gpt-4o'``).
            messages: The conversation history to send, as a list of
                      ``{'role': ..., 'content': ...}`` dictionaries.
            max_tokens: Maximum number of tokens allowed in the response.
            temperature: How varied or creative the response should be
                         (``0.0``–``2.0``). ``None`` omits this parameter.
            top_p: Alternative response-variety control (``0.0``–``1.0``).
                   ``None`` omits this parameter.

        Returns:
            An iterator of response chunks, consumed as they arrive.
        """
        kwargs = self._build_completion_kwargs(
            model, messages, max_tokens, temperature, top_p, stream=True, extra_kwargs=extra_kwargs,
        )
        return self.client.chat.completions.create(**kwargs)  # type: ignore[misc]

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

        if os.getenv("PU_SANDBOX_DEBUG_API") == "1":
            raw = None
            try:
                if hasattr(response, "model_dump_json"):
                    raw = response.model_dump_json()  # type: ignore[attr-defined]
                elif hasattr(response, "model_dump"):
                    raw = str(response.model_dump())  # type: ignore[attr-defined]
                elif hasattr(response, "to_dict"):
                    raw = str(response.to_dict())  # type: ignore[attr-defined]
                else:
                    raw = repr(response)
            except Exception as raw_err:
                raw = f"<failed to serialize response: {raw_err}>"
            logging.debug(f"Raw API response payload: {raw}")

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
                if self._suppress_inline_print
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
        """Run a request and automatically retry it if the API returns a temporary error.

        On each failure the method waits before trying again, doubling the wait
        time after each attempt (exponential backoff). Errors that are likely
        permanent (such as an invalid API key) are re-raised immediately rather
        than retried.

        Args:
            body_fn: The function containing the actual API call, called once
                     per attempt with the attempt number (starting at ``0``).
                     A non-``None`` return value signals success and exits the
                     loop; a ``None`` return value signals an empty response
                     and triggers another attempt.
            model: The model name being used — needed to classify what kind of
                   error occurred (e.g. ``'gpt-4o'``).
            operation: A short human-readable label shown in log messages to
                       identify what was being attempted (e.g. ``'OCR'``,
                       ``'translation'``).
            timeout_msg: The error message to raise if all retry attempts are
                         exhausted without a successful response. Defaults to a
                         generic message if not provided.
            return_signal_on_error: When ``True``, returns an ``APISignal``
                                    value instead of raising an exception on
                                    unrecoverable errors. The translation
                                    pipeline uses this so a single failed page
                                    does not abort the entire job. When
                                    ``False`` (the default), raises the
                                    exception so the caller can handle it.

        Returns:
            The value returned by ``body_fn`` on success, or an ``APISignal``
            when ``return_signal_on_error=True`` and all retries failed.

        Raises:
            RuntimeError: All retries exhausted and ``return_signal_on_error``
                          is ``False``.
            Exception: Any non-retryable error from the API when
                       ``return_signal_on_error`` is ``False``.
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
