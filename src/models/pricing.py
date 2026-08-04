"""Looks up and stores AI model pricing from Princeton's PortKey pricing service.

When someone requests a model that isn't yet in the local price list
(``model_catalog.json``), these functions fetch its current price from
PortKey (the gateway service Princeton uses to reach AI providers) and save
it locally, so future requests for that model don't need another network
lookup.
"""

import json
import logging
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from . import catalog as _catalog

logger = logging.getLogger(__name__)

# Keeps track, in memory, of when each model's price was last refreshed —
# this is a temporary record that exists only while the program is running,
# and it prevents every worker thread from separately re-reading the catalog
# file off disk when they all happen to use the same model.
_sync_cache: Dict[str, datetime] = {}

PORTKEY_PRICING_API_BASE = "https://api.portkey.ai/model-configs/pricing"


def _fetch_model_pricing(provider_model: str, pricing_unit: int) -> Dict[str, Any]:
    """Look up a model's current price directly from PortKey's pricing service.

    Args:
        provider_model: The provider and model name together, separated by a
                         slash (e.g. ``'openai/gpt-4o'``).
        pricing_unit: How many tokens the returned prices should be expressed
                      per — tokens are the unit AI providers use to measure
                      text length, roughly one token per word. PortKey always
                      reports prices per 100 tokens, so this function scales
                      that figure up to match the catalog's convention (e.g.
                      multiplying by 10,000 when ``pricing_unit`` is
                      1,000,000, meaning "price per million tokens").

    Returns:
        A dictionary with ``'input'`` (cost per unit of prompt text sent) and
        ``'output'`` (cost per unit of AI-generated text received) keys.

    Raises:
        RuntimeError: If PortKey has no usable pricing for this model, or the
                      network request fails.
    """
    provider, model_key = provider_model.split("/", 1)
    catalog = _catalog.load_model_catalog()
    provider_map = catalog.get("config", {}).get("provider_map", {})
    api_provider = provider_map.get(provider.lower(), provider.lower())
    url = f"{PORTKEY_PRICING_API_BASE}/{api_provider}/{model_key}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as response:  # nosec B310
        data = json.loads(response.read())

    pay = data.get("pay_as_you_go", {})
    input_price = float(pay.get("request_token", {}).get("price", 0))
    output_price = float(pay.get("response_token", {}).get("price", 0))

    if not (input_price > 0 and output_price > 0):
        raise RuntimeError(f"No valid pricing data for '{provider_model}' in PortKey pricing catalog")

    # PortKey stores per-100-token prices; convert to our pricing_unit
    factor = pricing_unit / 100
    return {
        "input": round(input_price * factor, 4),
        "output": round(output_price * factor, 4),
    }


def _test_and_describe(
    model_name: str, entry: Dict[str, Any], api_key: str
) -> Dict[str, Any]:
    """Ask a model what it can do and fold the answers into its catalog entry.

    Kept apart from ``add_model_to_catalog()`` so that a model already in the
    catalog can be re-tested without its price being fetched again, and so that
    a failure to test never stops a model being added: a model with unknown
    quirks is still usable, and the alternative is refusing to add it at all.

    Args:
        model_name: The model to test, as the sandbox names it.
        entry: Its catalog entry so far.
        api_key: A professor's API key, used to make the test requests. They
                 are billed to whoever's key this is — a few tokens in total.

    Returns:
        The entry with whatever was learned applied to it, or unchanged if
        nothing could be.
    """
    from .capabilities import (
        apply_capability_report, client_for_testing, probe_model_capabilities,
    )

    try:
        report = probe_model_capabilities(model_name, client_for_testing(api_key))
    except Exception as error:
        # Never fatal. Adding a model that hasn't been tested is a worse
        # catalog entry, not a broken one, and this runs in the middle of
        # somebody's first request for that model.
        logger.warning("Could not test '%s': %s", model_name, error)
        return entry

    for line in report.settled:
        logger.info("%s: %s", model_name, line)
    for line in report.unsettled:
        logger.warning("%s: %s", model_name, line)

    return apply_capability_report(entry, report)


def add_model_to_catalog(
    provider_model: str, api_key: Optional[str] = None, probe: bool = True
) -> Tuple[str, Dict[str, Any]]:
    """Look up a new model's pricing from PortKey and save it to the local catalog.

    This is what runs automatically the first time someone requests a model
    using ``provider/model-name`` format (e.g. ``-m openai/gpt-4o``) that
    isn't already in ``model_catalog.json``. After this call, the model's
    price is stored locally and future requests for it won't need another
    network lookup.

    Args:
        provider_model: The provider and model name together, separated by a
                         slash — e.g. ``'openai/gpt-4o'``,
                         ``'google/gemini-2.5-pro'``, or
                         ``'azure-ai/Llama-3.3-70B-Instruct'``. The catalog
                         entry is stored under just the part after the slash
                         (e.g. ``'gpt-4o'``).
        api_key: A professor's API key, used to test what the model can do —
                 a handful of very small requests, billed to that key. Without
                 one the model is still added, but its capabilities stay
                 unknown and a warning says so.
        probe: Set ``False`` to add the model without testing it, for when the
               answers are already known or a test would be unwelcome.

    Returns:
        A two-item tuple of ``(model_name, entry)``: the model's catalog key
        (e.g. ``'gpt-4o'``) and the full pricing entry that was saved,
        including whatever testing settled about it: whether it can read
        images (``supports_vision``), and any quirks under ``rejects`` and
        ``prefers``. Where testing couldn't run — no key, or the model
        couldn't be reached — the model is still added, but it is recorded as
        unable to read images and a warning names the command that settles it.

    Raises:
        ValueError: If ``provider_model`` isn't in ``provider/model-name``
                    format (missing the slash).
    """
    if "/" not in provider_model:
        raise ValueError(
            f"Model '{provider_model}' must be in 'provider/model-name' format "
            "(e.g. 'openai/gpt-4o', 'google/gemini-2.5-pro')."
        )

    _provider, model_name = provider_model.split("/", 1)

    # Load or initialize the catalog without requiring models to exist yet
    catalog_file = _catalog.get_model_catalog_path()
    if catalog_file.exists():
        try:
            with open(catalog_file, "r") as f:
                catalog = json.load(f)
            catalog.setdefault("config", {"pricing_unit": 1_000_000, "monthly_limit": 250.0})
            catalog.setdefault("models", {})
        except (json.JSONDecodeError, Exception):
            catalog = {"config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0}, "models": {}}
    else:
        catalog = {"config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0}, "models": {}}

    pricing_unit = catalog["config"]["pricing_unit"]

    # Preserve any existing extra fields (system_role, fixed_parameters, etc.)
    entry: Dict[str, Any] = dict(catalog["models"].get(model_name, {}))
    entry["portkey_id"] = provider_model

    fetched = _fetch_model_pricing(provider_model, pricing_unit)
    entry["input"] = fetched["input"]
    entry["output"] = fetched["output"]
    entry["last_sync"] = datetime.now().isoformat(timespec="seconds")
    # Taken from the response when it says — which the PortKey pricing endpoint
    # currently never does, since it reports prices and nothing else. The branch
    # stays because the answer belongs there if it ever arrives.
    if "supports_vision" not in entry and "supports_vision" in fetched:
        entry["supports_vision"] = fetched["supports_vision"]

    # Then ask the model itself. This is the part that keeps anyone from having
    # to open this file: a few tiny requests settle what the pricing service
    # can't say, and whatever they settle is saved here. Without a key to ask
    # with, or if the model can't be reached, the questions stay unanswered —
    # see below for what that costs and why it is still better than guessing.
    if api_key and probe:
        entry = _test_and_describe(model_name, entry, api_key)

    if "supports_vision" not in entry:
        entry["supports_vision"] = False
        logger.warning(
            "Added '%s' without testing whether it can read images, so the sandbox "
            "has assumed it cannot — that is the safe way to be wrong, but it means "
            "the model cannot be used for chat, which needs to read attached "
            "documents. Run 'python main.py settings test-model %s' to settle it.",
            model_name, model_name,
        )

    catalog["models"][model_name] = entry
    _catalog.save_model_catalog(catalog)
    return model_name, entry


def maybe_sync_model_pricing(model: str) -> None:
    """Refresh a model's price from PortKey if it hasn't been checked in the last hour.

    Called automatically before most API calls to keep prices reasonably
    current without slowing down every single request with a network lookup.
    Does nothing if the model was never auto-registered from PortKey (i.e. it
    has no ``'portkey_id'`` saved), and silently gives up on any network
    problem so that a pricing check never blocks or breaks an API call.

    Args:
        model: The catalog name of the model to check (e.g. ``'gpt-4o'``).
    """
    # Fast in-memory check — avoids disk reads when workers share the same model
    cached_at = _sync_cache.get(model)
    if cached_at is not None and datetime.now() - cached_at < timedelta(hours=1):
        return

    try:
        catalog = _catalog.load_model_catalog()
        model_entry = catalog["models"].get(model, {})
        portkey_id = model_entry.get("portkey_id")
        if not portkey_id:
            _sync_cache[model] = datetime.now()  # no portkey_id — nothing to sync
            return

        last_sync_str = model_entry.get("last_sync", "")
        try:
            last_sync_dt = datetime.fromisoformat(last_sync_str)
            if datetime.now() - last_sync_dt < timedelta(hours=1):
                _sync_cache[model] = last_sync_dt
                return
        except (ValueError, TypeError):
            # Timestamp absent or in old monthly format — treat as stale and update
            pass

        pricing_unit = catalog["config"]["pricing_unit"]
        fetched = _fetch_model_pricing(portkey_id, pricing_unit)
        now_dt = datetime.now()
        now_iso = now_dt.isoformat(timespec="seconds")
        catalog["models"][model]["input"] = fetched["input"]
        catalog["models"][model]["output"] = fetched["output"]
        catalog["models"][model]["last_sync"] = now_iso
        _catalog.save_model_catalog(catalog)
        _sync_cache[model] = now_dt
        logging.info(
            f"Synced pricing for {model}: "
            f"input=${catalog['models'][model]['input']}, "
            f"output=${catalog['models'][model]['output']}"
        )
    except Exception as e:
        logging.warning(f"Could not sync pricing for {model}: {e}")
