"""Shared API error classification utilities for all service modules."""

import logging
from enum import Enum

from ..models import is_model_access_error, remove_model_from_catalog


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
    """Raise a user-friendly ValueError if *error* is a model-access denial.

    Removes the model from the catalog before raising so subsequent calls
    will not attempt to use it again. Does nothing if the error is not a
    model-access denial.
    """
    if not is_model_access_error(str(error)):
        return
    removed = remove_model_from_catalog(model) if model else False
    removed_note = " It has been removed from the catalog." if removed else ""
    logging.error(f"Model access denied for {model!r}: {error}")
    raise ValueError(
        f"Model '{model}' is not accessible in the Princeton AI Sandbox — "
        f"you do not have access to this model.{removed_note} "
        "Please use a different model or contact your sandbox administrator."
    ) from error


def handle_api_errors(error: Exception, model: str) -> None:
    """Raise a user-friendly exception for PortKey/OpenAI API errors.

    Covers model-access denial, rate limits, invalid requests, and
    authentication failures. Content-filter and context-length errors are
    intentionally excluded — callers that need signal-based handling
    should use classify_api_error() instead.

    If none of the known patterns match, this function returns without raising
    so the caller can decide how to handle the remaining error.
    """
    msg = str(error).lower()
    raise_for_model_access_error(error, model)
    if "rate_limit" in msg:
        logging.error(f"Rate limit exceeded: {error}")
        raise Exception(f"Rate limit exceeded: {error}") from error
    if "invalid_request" in msg:
        logging.error(f"Invalid request: {error}")
        raise Exception(f"Invalid request: {error}") from error
    if "authentication" in msg or "unauthorized" in msg:
        logging.error(f"Authentication error: {error}")
        raise Exception(f"Authentication error: {error}") from error


def classify_api_error(error: Exception, model: str) -> APISignal:
    """Classify an API error into an APISignal or raise.

    Calls handle_api_errors first (covering model-access, rate-limit,
    invalid-request, and authentication), then maps context-length and
    content-filter errors to their respective signals. Any unrecognised error
    is re-raised with a generic message.

    Applicable to any service — translation, OCR, prompt, or otherwise.
    """
    handle_api_errors(error, model)
    msg = str(error).lower()
    if "context_length_exceeded" in msg or "maximum context length" in msg:
        logging.error(f"Context length exceeded: {error}")
        return APISignal.CONTEXT_LENGTH_EXCEEDED
    if is_content_filter_error(error):
        logging.error(f"Content filter triggered: {error}")
        return APISignal.CONTENT_FILTER
    logging.error(f"API call failed with {type(error).__name__}: {error}")
    raise Exception(f"API call failed with {type(error).__name__}: {error}") from error
