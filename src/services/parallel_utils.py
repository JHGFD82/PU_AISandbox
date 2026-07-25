"""Shared utilities for parallel processing across services."""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed as futures_as_completed
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, List, Tuple

from tqdm import tqdm

from ..processors.constants import IMAGE_EXTENSIONS

_NATURAL_SPLIT_RE = re.compile(r"(\d+)")


def natural_sort_key(name: str) -> List[Any]:
    """Return a key that sorts embedded digit runs numerically.

    Example: page_2.jpg comes before page_10.jpg.
    """
    parts = _NATURAL_SPLIT_RE.split(name)
    return [int(part) if part.isdigit() else part.casefold() for part in parts]


def collect_image_files(folder_path: str) -> List[str]:
    """Return a sorted list of absolute paths to all image files in a folder.

    Files are sorted so that names with embedded numbers appear in the natural
    reading order (e.g. ``page_2.jpg`` before ``page_10.jpg``) rather than
    alphabetical order (which would put ``page_10.jpg`` before ``page_2.jpg``).
    """
    return [
        os.path.join(folder_path, name)
        for name in sorted(os.listdir(folder_path), key=natural_sort_key)
        if name.lower().endswith(IMAGE_EXTENSIONS)
        and os.path.isfile(os.path.join(folder_path, name))
    ]


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


def run_folder_parallel(
    image_files: List[str],
    worker_fn: Callable[[int, str], Tuple],
    make_error_result: Callable[[str, Exception], Tuple],
    usage_data: Dict[str, Any],
    actual_workers: int,
    desc: str,
    on_progress: "Callable[[int, int], None] | None" = None,
) -> Dict[int, Tuple]:
    """Run *worker_fn* over *image_files* in parallel with a tqdm progress bar.

    Each call to *worker_fn(index, file_path)* must return a tuple whose first
    element is the original *index* (so results can be reassembled in order):
    ``(index, filename, *payload)``.

    *make_error_result(filename, exc)* is called when a worker raises; it
    should return a ``(filename, *fallback_payload)`` tuple that matches the
    shape of a successful payload (minus the leading index).

    Args:
        image_files: Absolute paths to every file to process, in the order
                     results should eventually be reassembled (workers may
                     finish in any order — only the returned dict's keys
                     preserve the original position).
        worker_fn: Called as ``worker_fn(index, file_path)`` on a worker
                   thread for each file.
        make_error_result: Called as ``make_error_result(filename, exc)``
                           when a worker raises, to build a fallback result
                           in the same shape a successful call would return.
        usage_data: The running token/cost totals dict (``TokenTracker.usage_data``),
                    read here only to show a live cost/token readout in the
                    console progress bar's postfix — never written to.
        actual_workers: How many threads to run at once (already capped by
                        ``cap_worker_count``).
        desc: The label shown next to the console ``tqdm`` progress bar.
        on_progress: Called with ``(completed_count, total_count)`` after
                     each file finishes, in *completion* order (which may
                     differ from *image_files*'s order — unlike per-item
                     text streaming, a plain count is safe to report as
                     soon as any item finishes, regardless of which one).
                     ``None`` (the default, and what every CLI call passes)
                     means no such reporting — only the webui's background
                     job runner passes one. This was previously silently
                     unsupported here: the only progress signal was the
                     local console ``tqdm`` bar below, invisible to a caller
                     running this in a background thread (see
                     docs/webui-plugin-plan.md section 10's "progress bar
                     frozen with workers > 1" fix).

    Returns:
        A ``dict[index → (filename, *payload)]``.
    """
    results: Dict[int, Tuple] = {}
    baseline_tokens = usage_data["total_usage"].get("total_tokens", 0)
    baseline_cost = usage_data["total_usage"].get("total_cost", 0.0)
    completed = 0

    with tqdm_logging():
        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            future_map = {
                executor.submit(worker_fn, i, path): i
                for i, path in enumerate(image_files)
            }
            with tqdm(total=len(image_files), desc=desc, ascii=True) as pbar:
                for future in futures_as_completed(future_map):
                    orig_idx = future_map[future]
                    try:
                        idx, *rest = future.result()
                        results[idx] = tuple(rest)
                    except Exception as e:
                        filename = os.path.basename(image_files[orig_idx])
                        logging.error(f"Error processing '{filename}': {e}", exc_info=True)
                        results[orig_idx] = make_error_result(filename, e)
                    update_pbar_postfix(pbar, usage_data, baseline_tokens, baseline_cost)
                    pbar.update(1)
                    completed += 1
                    if on_progress is not None:
                        on_progress(completed, len(image_files))
    return results


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
