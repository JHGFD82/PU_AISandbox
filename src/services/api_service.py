"""APIService — base class for plugins that call AI API endpoints.

Supports two modes, configured via ``APIConfig.openai_compatible``:

**OpenAI-compatible endpoints** (``openai_compatible = true``):
    Uses the ``openai`` SDK with a custom ``base_url``.  This works with any
    endpoint that speaks the OpenAI chat-completions protocol — direct OpenAI,
    Anthropic-compatible proxies, vLLM clusters, Ollama, etc.

**Generic REST endpoints** (``openai_compatible = false``):
    Uses a ``requests.Session`` for plain HTTP GET / POST calls.  The response
    is returned as parsed JSON (if the Content-Type is ``application/json``) or
    as raw text.

Both modes share the same retry / backoff logic and emit consistent log messages.
Token tracking is supported for OpenAI-compatible responses.

Usage example (plugin service)::

    from src.services.api_config import load_api_config
    from src.services.api_service import APIService
    from src.tracking.token_tracker import TokenTracker

    config = load_api_config("pu_sandbox")
    tracker = TokenTracker(professor="heller")
    svc = APIService(config, professor="heller", token_tracker=tracker)

    # AI endpoint
    response = svc.chat_completion([
        {"role": "system", "content": "You are a research assistant."},
        {"role": "user",   "content": "Summarise the attached abstract."},
    ])

    # Generic REST
    data = svc.get("/datasets", params={"limit": 10})
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from openai import OpenAI

from .api_config import APIConfig
from ..tracking.token_tracker import TokenTracker
from .constants import MAX_RETRIES, BASE_RETRY_DELAY

logger = logging.getLogger(__name__)

_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_TOP_P = 1.0
_DEFAULT_MAX_TOKENS = 4000


class APIService:
    """Base class for plugins that call AI API endpoints.

    Works with OpenAI-compatible LLM endpoints (model clusters, direct
    providers) and generic REST APIs.  Subclasses provide prompt construction;
    this class handles transport, retry, and token tracking.

    Args:
        config:        Resolved ``APIConfig`` (from ``load_api_config``).
        professor:     Professor name — forwarded to the token tracker.
        token_tracker: Existing tracker to reuse, or ``None`` to create a new one.
        model:         Override the model set in ``config.default_model``.
        temperature:   Sampling temperature (OpenAI-compatible only).
        top_p:         Nucleus-sampling top-p (OpenAI-compatible only).
        max_tokens:    Maximum completion tokens (OpenAI-compatible only).
    """

    def __init__(
        self,
        config: APIConfig,
        professor: Optional[str] = None,
        token_tracker: Optional[TokenTracker] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        self.config = config
        self.professor = professor or ""
        self.custom_model = model
        self.custom_temperature = temperature
        self.custom_top_p = top_p
        self.custom_max_tokens = max_tokens
        self.token_tracker = (
            token_tracker
            if token_tracker is not None
            else TokenTracker(professor=self.professor)
        )

        if config.openai_compatible:
            self._openai_client: Optional[OpenAI] = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.timeout,
            )
            self._session: Optional[requests.Session] = None
        else:
            self._openai_client = None
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            })

    # ── Public: AI endpoint ────────────────────────────────────────────────────

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Send a chat-completion request to an OpenAI-compatible endpoint.

        Args:
            messages:    Full messages list, e.g.
                         ``[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]``
            model:       Override model for this call (falls back to constructor
                         override, then ``config.default_model``).
            temperature: Override temperature for this call.
            top_p:       Override top_p for this call.
            max_tokens:  Override max_tokens for this call.

        Returns:
            The assistant's reply text.

        Raises:
            RuntimeError: If all retries are exhausted with no usable response.
            ValueError:   If this config is not OpenAI-compatible.
        """
        if not self.config.openai_compatible or self._openai_client is None:
            raise ValueError(
                f"API '{self.config.api_name}' is not configured as openai_compatible. "
                "Use get() or post() for generic REST calls."
            )

        effective_model = model or self.custom_model or self.config.default_model
        if not effective_model:
            raise ValueError(
                f"No model specified for API '{self.config.api_name}'. "
                "Set 'default_model' in settings.toml or pass --model on the CLI."
            )

        eff_temperature = temperature if temperature is not None else (
            self.custom_temperature if self.custom_temperature is not None else _DEFAULT_TEMPERATURE
        )
        eff_top_p = top_p if top_p is not None else (
            self.custom_top_p if self.custom_top_p is not None else _DEFAULT_TOP_P
        )
        eff_max_tokens = max_tokens if max_tokens is not None else (
            self.custom_max_tokens if self.custom_max_tokens is not None else _DEFAULT_MAX_TOKENS
        )

        logger.info(
            f"Calling {self.config.display_name} — model: {effective_model}, "
            f"messages: {len(messages)}"
        )

        def _attempt(attempt_idx: int) -> Optional[str]:
            response = self._openai_client.chat.completions.create(  # type: ignore[union-attr]
                model=effective_model,
                messages=messages,  # type: ignore[arg-type]
                temperature=eff_temperature,
                top_p=eff_top_p,
                max_tokens=eff_max_tokens,
            )
            self._record_token_usage(response, effective_model)
            if response.choices and response.choices[0].message:
                content = response.choices[0].message.content
                if isinstance(content, str):
                    return content
            return None

        result = self._run_with_retry(_attempt, operation=f"{self.config.display_name} chat completion")
        return result or ""

    # ── Public: generic REST ───────────────────────────────────────────────────

    def get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Perform an HTTP GET request.

        Args:
            path:   Path relative to ``config.base_url`` (e.g. ``"/datasets"``).
            params: Optional query-string parameters.

        Returns:
            Parsed JSON (dict/list) if the response is JSON, otherwise raw text.

        Raises:
            RuntimeError: All retries exhausted.
            requests.HTTPError: Non-retryable HTTP error.
        """
        url = self._build_url(path)

        def _attempt(attempt_idx: int) -> Any:
            resp = self._session.get(  # type: ignore[union-attr]
                url,
                params=params,
                timeout=self.config.timeout,
                verify=self.config.verify_ssl,
            )
            resp.raise_for_status()
            return self._parse_response(resp)

        return self._run_with_retry(_attempt, operation=f"GET {path}")

    def post(
        self,
        path: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Perform an HTTP POST request.

        Args:
            path:    Path relative to ``config.base_url`` (e.g. ``"/submit"``).
            payload: Optional JSON body.

        Returns:
            Parsed JSON (dict/list) if the response is JSON, otherwise raw text.

        Raises:
            RuntimeError: All retries exhausted.
            requests.HTTPError: Non-retryable HTTP error.
        """
        url = self._build_url(path)

        def _attempt(attempt_idx: int) -> Any:
            resp = self._session.post(  # type: ignore[union-attr]
                url,
                json=payload,
                timeout=self.config.timeout,
                verify=self.config.verify_ssl,
            )
            resp.raise_for_status()
            return self._parse_response(resp)

        return self._run_with_retry(_attempt, operation=f"POST {path}")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _build_url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        path = path.lstrip("/")
        return f"{base}/{path}" if path else base

    @staticmethod
    def _parse_response(resp: requests.Response) -> Any:
        ct = resp.headers.get("Content-Type", "")
        if "application/json" in ct:
            return resp.json()
        return resp.text

    def _record_token_usage(self, response: Any, model: str) -> None:
        """Record token usage from an OpenAI-compatible response."""
        if (
            response.usage
            and response.usage.prompt_tokens is not None
            and response.usage.completion_tokens is not None
            and response.usage.total_tokens is not None
        ):
            usage = self.token_tracker.record_usage(
                model=getattr(response, "model", None) or model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                requested_model=model,
            )
            logger.info(
                f"Tokens used \u2014 prompt: {response.usage.prompt_tokens}, "
                f"completion: {response.usage.completion_tokens}, "
                f"total: {response.usage.total_tokens}, "
                f"cost: ${usage.total_cost:.4f}"
            )
        else:
            logger.warning("No token usage information in response.")

    def _run_with_retry(
        self,
        attempt_fn: Any,
        operation: str = "API call",
    ) -> Any:
        """Run *attempt_fn* with exponential-backoff retry.

        Mirrors the ``_run_with_retry`` pattern in ``BaseService``.
        Retries on transient HTTP errors (5xx, connection errors, timeouts).

        Args:
            attempt_fn: Callable ``(attempt_index: int) -> Any``.  Return a
                        non-``None`` value to signal success.  ``None`` causes
                        an immediate retry.
            operation:  Human-readable label for log messages.

        Returns:
            The first non-``None`` value returned by *attempt_fn*.

        Raises:
            RuntimeError: All ``MAX_RETRIES`` attempts exhausted.
            Exception:    Non-retryable exception propagated from *attempt_fn*.
        """
        for attempt in range(MAX_RETRIES):
            try:
                if attempt > 0:
                    delay = BASE_RETRY_DELAY * (2 ** attempt) + (0.1 * attempt)
                    logger.info(
                        f"Retrying {operation} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES}) after {delay:.1f}s..."
                    )
                    time.sleep(delay)
                result = attempt_fn(attempt)
                if result is not None:
                    return result
            except Exception as e:
                if self._is_transient(e) and attempt < MAX_RETRIES - 1:
                    delay = BASE_RETRY_DELAY * (2 ** attempt) + (0.1 * attempt)
                    logger.warning(
                        f"Transient error on {operation} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES}), "
                        f"retrying in {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
                    continue
                logger.error(f"{operation} error: {e}")
                raise

        raise RuntimeError(
            f"{operation} returned no content after {MAX_RETRIES} retries."
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """Return True for errors worth retrying (connection, timeout, 5xx)."""
        if isinstance(exc, (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        )):
            return True
        if isinstance(exc, requests.exceptions.HTTPError):
            resp = getattr(exc, "response", None)
            if resp is not None and resp.status_code >= 500:
                return True
        # openai SDK exceptions
        exc_name = type(exc).__name__
        if exc_name in ("APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError"):
            return True
        return False


# Backward compatibility alias
ExternalAPIService = APIService
