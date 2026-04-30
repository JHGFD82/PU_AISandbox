"""Shared constants for all service modules (rate limiting, retry behaviour)."""

from ..settings import (
    PAGE_DELAY_SECONDS,
    MAX_RETRIES,
    BASE_RETRY_DELAY,
    DEFAULT_PARALLEL_WORKERS,
    MAX_PARALLEL_WORKERS,
)

__all__ = ["PAGE_DELAY_SECONDS", "MAX_RETRIES", "BASE_RETRY_DELAY", "DEFAULT_PARALLEL_WORKERS", "MAX_PARALLEL_WORKERS"]
