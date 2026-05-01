"""Shared utilities for parallel processing across services."""

import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator


def cap_worker_count(
    workers: int,
    item_count: int,
    max_workers: int,
    item_label: str = "item",
    container_label: str = "collection",
) -> int:
    """Return the effective worker count, capped at item_count and max_workers.

    Logs an INFO message when the requested worker count is reduced, explaining
    which limit was hit (item count or the settings.toml cap).

    Args:
        workers: Requested number of workers.
        item_count: Number of items to process (e.g. pages, images).
        max_workers: Upper bound from settings (MAX_PARALLEL_WORKERS).
        item_label: Singular label for the items (e.g. \"page\", \"image\").
        container_label: Label for the containing collection (e.g. \"document\", \"folder\").

    Returns:
        The effective worker count to use.
    """
    actual = min(workers, item_count, max_workers)
    if actual < workers:
        if actual == item_count:
            reason = f"{container_label} has {item_count} {item_label}(s)"
        else:
            reason = f"max_parallel_workers={max_workers} in settings.toml"
        logging.info(f"workers capped at {actual} ({reason})")
    return actual

from tqdm import tqdm


class _TqdmLoggingHandler(logging.Handler):
    """Logging handler that routes messages through tqdm.write() to avoid corrupting progress bars."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record))
        except Exception:
            self.handleError(record)


_TQDM_LOG_FORMATTER = logging.Formatter(
    fmt="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


@contextmanager
def tqdm_logging() -> Generator[None, None, None]:
    """Context manager that redirects root-logger output through tqdm.write().

    Use this around any block that runs a tqdm progress bar to prevent
    logging output from corrupting the bar display.

    Also silences INFO-level chatter from third-party libraries (httpx HTTP
    request lines) and internal loggers (token_tracker model-substitution
    notes) that are redundant when tqdm already shows running totals.
    """
    # Loggers to quieten to WARNING for the duration of the parallel run.
    _QUIET_LOGGERS = (
        "httpx",                                      # "HTTP Request: POST … 200 OK"
        "httpcore",                                   # lower-level httpx transport
        "src.tracking.token_tracker",                 # "Using requested model … for pricing"
        "openai._base_client",                        # "Retrying request to /chat/completions …"
        "portkey_ai._vendor.openai._base_client",     # same, via portkey vendor copy
    )

    root_logger = logging.getLogger()
    handler = _TqdmLoggingHandler()
    handler.setFormatter(_TQDM_LOG_FORMATTER)
    existing_handlers = root_logger.handlers[:]
    for h in existing_handlers:
        root_logger.removeHandler(h)
    root_logger.addHandler(handler)

    # Save and raise levels for chatty loggers.
    _saved_levels: dict[str, int] = {}
    for name in _QUIET_LOGGERS:
        lg = logging.getLogger(name)
        _saved_levels[name] = lg.level
        lg.setLevel(logging.WARNING)

    try:
        yield
    finally:
        root_logger.removeHandler(handler)
        for h in existing_handlers:
            root_logger.addHandler(h)
        # Restore logger levels.
        for name, level in _saved_levels.items():
            logging.getLogger(name).setLevel(level)


def update_pbar_postfix(
    pbar: tqdm,
    usage_data: Dict[str, Any],
    baseline_tokens: Any,
    baseline_cost: Any,
) -> None:
    """Update a tqdm progress bar postfix with run-so-far token/cost counts.

    Silently does nothing if the values cannot be converted (e.g. None on
    first call before any usage has been recorded).
    """
    try:
        run_tokens = int(usage_data["total_usage"].get("total_tokens", 0)) - int(baseline_tokens)
        run_cost = float(usage_data["total_usage"].get("total_cost", 0.0)) - float(baseline_cost)
        pbar.set_postfix(tokens=f"{run_tokens:,}", cost=f"${run_cost:.4f}")
    except (TypeError, ValueError):
        pass
