"""Shared API error classification utilities for all service modules."""

import logging
import os
from enum import Enum

from ..errors import CLIError
from ..models import (
    is_model_access_error,
    is_sampling_param_deprecated_error,
    remove_model_from_catalog,
    set_model_fixed_parameters,
)


def _log_raw_error_payload(error: Exception) -> None:
    """Log provider error internals when debug mode is explicitly enabled."""
    if os.getenv("PU_SANDBOX_DEBUG_API") != "1":
        return

    pieces: list[str] = [
        f"type={type(error).__name__}",
        f"repr={error!r}",
    ]

    response = getattr(error, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            pieces.append(f"response.status_code={status}")
        text = getattr(response, "text", None)
        if text:
            pieces.append(f"response.text={text[:4000]}")
        try:
            json_payload = response.json()
            pieces.append(f"response.json={json_payload}")
        except Exception:
            pass

    body = getattr(error, "body", None)
    if body is not None:
        pieces.append(f"error.body={body}")

    logging.error("Raw API error payload: " + " | ".join(pieces))


class APISignal(str, Enum):
    """Sentinel values returned by service calls to signal non-content outcomes."""
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    CONTENT_FILTER = "content_filter_triggered"


def is_content_filter_error(error: Exception) -> bool:
    """Return True if the error was caused by a content filter or jailbreak response."""
    msg = str(error).lower()
    return "content_filter" in msg or "jailbreak" in msg


def is_transient_error(error: Exception) -> bool:
    """Return True if the error is a transient server-side failure worth retrying.

    Covers 503 Service Unavailable, gateway timeouts, and provider-side
    deadline / overload responses (e.g. Google 'Deadline expired').
    """
    msg = str(error).lower()
    error_type = type(error).__name__.lower()
    return (
        "503" in msg
        or "unavailable" in msg
        or "deadline expired" in msg
        or "internalservererror" in msg
        or "internalservererror" in error_type
        or "internal server error" in msg
        or "500" in msg
        or "502" in msg
        or "bad gateway" in msg
        or "504" in msg
        or "gateway timeout" in msg
        or "overloaded" in msg
    )


def raise_for_model_access_error(error: Exception, model: str) -> None:
    """Raise a user-friendly CLIError if *error* is a model-access denial.

    Removes the model from the catalog before raising so subsequent calls
    will not attempt to use it again. Does nothing if the error is not a
    model-access denial.

    Raises:
        CLIError: If this was a model-access denial. ``CLIError`` (rather
                  than a general-purpose error type) marks the message as one
                  written for the person using the tool, so both the CLI and
                  the web interface know they can show it as-is.
    """
    if not is_model_access_error(str(error)):
        return
    removed = remove_model_from_catalog(model) if model else False
    removed_note = " It has been removed from the catalog." if removed else ""
    logging.error(f"Model access denied for {model!r}: {error}")
    raise CLIError(
        f"Model '{model}' is not accessible in the Princeton AI Sandbox — "
        f"you do not have access to this model.{removed_note} "
        "Please use a different model or contact your sandbox administrator."
    ) from error


def raise_for_deprecated_sampling_params(error: Exception, model: str) -> None:
    """Raise a user-friendly CLIError if *error* shows this model has dropped
    support for temperature/top-p entirely.

    Marks the model ``fixed_parameters: true`` in the catalog before raising,
    so a retry (whether the user resends the message or a caller retries the
    call) omits those parameters and succeeds. Does nothing if the error does
    not match this pattern.

    Raises:
        CLIError: If this was a deprecated-sampling-parameter rejection. See
                  ``raise_for_model_access_error()`` for why this error type.
    """
    if not is_sampling_param_deprecated_error(str(error)):
        return
    updated = set_model_fixed_parameters(model) if model else False
    updated_note = (
        " It has been marked as a fixed-parameter model in the catalog, so "
        "sending your message again should work now."
        if updated else ""
    )
    logging.error(f"Model '{model}' rejected sampling parameters as deprecated: {error}")
    raise CLIError(
        f"Model '{model}' no longer accepts temperature/top-p adjustments — "
        f"the gateway reported these as deprecated for this model.{updated_note}"
    ) from error


def handle_api_errors(error: Exception, model: str) -> None:
    """Raise a user-friendly exception for PortKey/OpenAI API errors.

    Covers model-access denial, deprecated sampling parameters, rate limits,
    invalid requests, and authentication failures. Content-filter and
    context-length errors are intentionally excluded — callers that need
    signal-based handling should use classify_api_error() instead.

    If none of the known patterns match, this function returns without raising
    so the caller can decide how to handle the remaining error.
    """
    msg = str(error).lower()
    raise_for_model_access_error(error, model)
    raise_for_deprecated_sampling_params(error, model)
    # These are raised as CLIError, not a bare Exception, because each one is
    # a message meant for the person who ran the command — they describe
    # something the user can act on (wait and retry, fix a setting, renew a
    # key) rather than a fault in this program. That distinction is what lets
    # the rest of the application show these directly while keeping genuinely
    # internal failures out of the user's face: the CLI prints a CLIError as
    # a plain message instead of a traceback, and the web interface shows it
    # in the chat window instead of a generic "something went wrong".
    if "rate_limit" in msg:
        logging.error(f"Rate limit exceeded: {error}")
        raise CLIError(f"Rate limit exceeded: {error}") from error
    if "invalid_request" in msg:
        logging.error(f"Invalid request: {error}")
        raise CLIError(f"Invalid request: {error}") from error
    if "authentication" in msg or "unauthorized" in msg:
        logging.error(f"Authentication error: {error}")
        raise CLIError(f"Authentication error: {error}") from error


def classify_api_error(error: Exception, model: str) -> APISignal:
    """Classify an API error into an APISignal or raise.

    Calls handle_api_errors first (covering model-access, rate-limit,
    invalid-request, and authentication), then maps context-length and
    content-filter errors to their respective signals. Any unrecognised error
    is re-raised with a generic message.

    Applicable to any service — translation, OCR, prompt, or otherwise.
    """
    _log_raw_error_payload(error)
    handle_api_errors(error, model)
    msg = str(error).lower()
    if "context_length_exceeded" in msg or "maximum context length" in msg:
        logging.error(f"Context length exceeded: {error}")
        return APISignal.CONTEXT_LENGTH_EXCEEDED
    if is_content_filter_error(error):
        logging.error(f"Content filter triggered: {error}")
        return APISignal.CONTENT_FILTER
    # Deliberately a plain Exception, not CLIError: nothing above recognised
    # this error, so there's no message here worth putting in front of a user
    # — just the raw text the gateway sent back. Leaving it un-marked is what
    # keeps it out of the browser (see the chat route in the webui plugin),
    # while the full detail still reaches the log above.
    logging.error(f"API call failed with {type(error).__name__}: {error}")
    raise Exception(f"API call failed with {type(error).__name__}: {error}") from error
