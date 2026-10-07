"""Work out which company made a model, from OpenRouter's public list of models.

A list of models is easier to read grouped by company — Anthropic's together,
Google's together — but nothing in a model's name says whose it is, and the
catalog doesn't record it either. OpenRouter, a service that resells most of
the world's AI models, publishes the list of every model it carries, free and
without an account, each filed under the company that made it:
``qwen/qwen3-235b``, ``google/gemma-3-27b-it``. That list is what this module
reads, so a company the sandbox has never heard of is named correctly without
anybody editing the code.

Two ways of matching a model to its maker, tried in order:

1. **By its exact name**, for a model OpenRouter lists under the same name the
   sandbox uses (``gpt-4o``, ``claude-sonnet-4-6``).
2. **By its family** — the first word of its name, such as ``qwen``, ``gemma``
   or ``llama``. OpenRouter's list is used to learn which company each family
   belongs to, so a model it doesn't list at all, like ``qwen3.8:27b-mlx``
   running on somebody's own computer, is still placed under the company whose
   family it belongs to.

The list is asked for at most once a month and kept in the ``data`` folder of
the settings location (``model_makers.json``), in a compact form holding only
what the matching needs. Nothing here asks OpenRouter while a list is being
drawn: ``refresh_model_makers()`` does the asking and is called beforehand,
off to one side, and ``model_maker()`` only ever reads what is already saved.
Without a saved copy — offline on first use, say — it answers ``None`` and the
caller falls back to what the catalog itself records.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

MAKERS_FILENAME = "model_makers.json"

# How long a saved copy of the list is trusted before it is asked for again.
# Companies release families rarely; a month keeps the list current without
# asking every time the sandbox starts.
_KEEP_FOR = timedelta(days=30)

# After a failed attempt to reach OpenRouter, how long before trying again in
# the same running process. Without it, a computer that is offline would wait
# out the timeout every time a list of models was drawn.
_RETRY_AFTER = timedelta(hours=1)

# How long to wait for OpenRouter to answer. Short, because somebody may be
# waiting for a list of models to appear while it is asked.
_TIMEOUT_SECONDS = 8

# Names in OpenRouter's list that belong to OpenRouter itself (its own routing
# models), which make nothing and so are never anybody's maker.
_NOT_A_MAKER = {"openrouter"}

_last_failed: Optional[datetime] = None
_lock = threading.Lock()


def makers_path() -> Path:
    """Return where the saved copy of the list of makers is kept."""
    from ..paths import data_root

    return data_root() / MAKERS_FILENAME


def family_of(name: str) -> str:
    """Return the first word of a model's name, which names the family it belongs to.

    Args:
        name: A model's name with nothing in front of it (``'qwen3.8:27b-mlx'``,
              ``'Llama-3.3-70B-Instruct'``).

    Returns:
        The leading letters, in lower case (``'qwen'``, ``'llama'``), or an
        empty string if the name begins with anything else.
    """
    match = re.match(r"[a-z]+", name.lower())
    return match.group() if match else ""


def summarise(models: list[dict[str, Any]]) -> Dict[str, Any]:
    """Boil OpenRouter's full list down to what matching a model to its maker needs.

    Args:
        models: The ``data`` list OpenRouter returns, one entry per model, each
                with an ``id`` such as ``'qwen/qwen3-235b'`` and a ``name``
                such as ``'Qwen: Qwen3 235B'``.

    Returns:
        A dictionary with three parts, each keyed in lower case:

        - ``makers``: each company's short name in the list (``'meta-llama'``)
          and the name it goes by (``'Meta'``), read from the part of each
          model's display name before the colon.
        - ``models``: each model's own name (``'gemma-3-27b-it'``) and its
          company's short name.
        - ``families``: each family (``'gemma'``) and the company with the
          most models in it, so that a company's own family is not handed to
          somebody who merely offers an adjusted copy of one.
    """
    display: Dict[str, Counter] = defaultdict(Counter)
    by_family: Dict[str, Counter] = defaultdict(Counter)
    exact: Dict[str, str] = {}
    for model in models:
        full_id = str(model.get("id", ""))
        if "/" not in full_id:
            continue
        # A leading ~ marks OpenRouter's alias for "the newest of these";
        # the company is the same.
        maker, own_name = full_id.lstrip("~").split("/", 1)
        maker = maker.lower()
        if maker in _NOT_A_MAKER:
            continue
        own_name = own_name.split(":", 1)[0].lower()
        label = str(model.get("name", ""))
        if ":" in label:
            display[maker][label.split(":", 1)[0].strip()] += 1
        exact.setdefault(own_name, maker)
        family = family_of(own_name)
        if family:
            by_family[family][maker] += 1
    return {
        "makers": {maker: counts.most_common(1)[0][0] for maker, counts in display.items()},
        "models": exact,
        # Most models first, then alphabetically, so a tie always goes the
        # same way.
        "families": {family: sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                     for family, counts in by_family.items()},
    }


def _fetch() -> Dict[str, Any]:
    """Ask OpenRouter for its list of models and return it boiled down.

    Raises:
        RuntimeError: If OpenRouter can't be reached or sends back something
                      that isn't the list.
    """
    request = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # nosec B310
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise RuntimeError(f"Could not read OpenRouter's list of models: {error}") from error
    models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models:
        raise RuntimeError("OpenRouter's list of models came back empty.")
    return summarise(models)


def load_makers() -> Optional[Dict[str, Any]]:
    """Return the saved copy of the list of makers, or ``None`` if there isn't a usable one."""
    try:
        saved = json.loads(makers_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or not isinstance(saved.get("families"), dict):
        return None
    return saved


def _is_fresh(saved: Optional[Dict[str, Any]]) -> bool:
    if not saved:
        return False
    try:
        fetched = datetime.fromisoformat(str(saved.get("fetched", "")))
    except ValueError:
        return False
    return datetime.now() - fetched < _KEEP_FOR


def refresh_model_makers() -> None:
    """Ask OpenRouter for its list again if the saved copy is missing or a month old.

    Safe to call as often as a list of models is drawn: it asks at most once a
    month, and after a failure waits an hour before trying again. A failure is
    logged and otherwise ignored — an older saved copy goes on being used, and
    with none at all the caller falls back to what the catalog records.
    """
    global _last_failed
    with _lock:
        if _is_fresh(load_makers()):
            return
        if _last_failed and datetime.now() - _last_failed < _RETRY_AFTER:
            return
        try:
            summary = _fetch()
        except RuntimeError as error:
            _last_failed = datetime.now()
            logging.info("%s Companies will be read from the model catalog instead.", error)
            return
        summary["fetched"] = datetime.now().isoformat(timespec="seconds")
        _save(summary)


def _save(summary: Dict[str, Any]) -> None:
    """Write the boiled-down list, all at once, so a reader never sees half of it."""
    path = makers_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".model_makers.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as error:
        logging.info("Could not save the list of model makers to %s: %s", path, error)


def maker_named(short_name: str) -> Optional[str]:
    """Return the name a company goes by, given its short name in OpenRouter's list.

    Args:
        short_name: Such as ``'openai'`` or ``'meta-llama'``, in any case.

    Returns:
        Such as ``'OpenAI'`` or ``'Meta'``, or ``None`` if OpenRouter doesn't
        list a company by that short name or there is no saved list.
    """
    saved = load_makers()
    if not saved:
        return None
    return saved.get("makers", {}).get(short_name.strip().lower())


def model_maker(name: str) -> Optional[str]:
    """Return the company that made a model, going by OpenRouter's list.

    Args:
        name: The model's name as the service running it knows it, with no
              endpoint in front: ``'claude-sonnet-4-6'``, ``'qwen3.8:27b-mlx'``,
              or with its company in front, ``'meta-llama/Llama-3-70B'``.

    Returns:
        The company's name (``'Anthropic'``, ``'Qwen'``, ``'Meta'``), or
        ``None`` if the list doesn't place it or there is no saved list yet.
    """
    saved = load_makers()
    if not saved:
        return None
    makers = saved.get("makers", {})

    def named(short_name: Optional[str]) -> Optional[str]:
        return makers.get(short_name) or short_name if short_name else None

    lowered = name.strip().lower()
    if "/" in lowered:
        in_front, lowered = lowered.split("/", 1)
        if in_front in makers:
            return makers[in_front]
    exact = saved.get("models", {}).get(lowered.split(":", 1)[0])
    if exact:
        return named(exact)
    return named(saved.get("families", {}).get(family_of(lowered)))
