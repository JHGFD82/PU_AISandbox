"""Keep the models on this installation's own endpoints listed in the model catalog.

An endpoint defined in ``preferences.toml`` or a shared settings file (see
``src/services/api_config.py``) runs models of its own, which the sandbox cannot
know about unless it asks. This module asks: nearly every OpenAI-compatible
server — vLLM, Ollama, LM Studio and the like — answers a request for the list
of models it runs, and each one it names is written into ``model_catalog.json``
so that it can be picked from the same lists as every other model.

Each is recorded under the name ``-m`` already takes for it — the endpoint, a
colon, then the model, as in ``my_cluster:llama-3-70b`` — so choosing one from a
list sends the work to that endpoint without anything else having to know it
came from one. The entry says which endpoint it belongs to and carries no
price, because calls to an endpoint are counted but never costed; with no price
it is also never picked as "the cheapest model" for work meant for the sandbox.

The endpoint's ``default_model`` is recorded whether or not the server lists
it. So is a model somebody names with the colon syntax, which matters for a
server that will not say what it runs; on one that does, a name it does not
list is taken out again the next time it is asked, since sending work there
would only fail.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, Optional

from . import catalog as _catalog

if TYPE_CHECKING:
    from ..services.api_config import APIConfig

# How long to wait for an endpoint to say which models it runs. Short, because
# the question is asked while somebody is waiting for a list of models to
# appear, and a cluster that is switched off should cost them seconds, not the
# minute a real request is allowed.
_LISTING_TIMEOUT_SECONDS = 5.0

# How often the same running process asks an endpoint again. The web interface
# asks each time a list of models is shown, and a list rarely changes from one
# minute to the next.
_ASK_AGAIN_AFTER = timedelta(hours=1)

# When each endpoint was last asked, by name, in this process.
_last_asked: Dict[str, datetime] = {}

# One update to the catalog at a time from this module, so two browser tabs
# opening at once cannot each add the same models and write over each other.
_lock = threading.Lock()


def endpoint_model_name(api_name: str, model: str) -> str:
    """Return the name an endpoint's model is known by in the catalog.

    The same spelling ``-m`` takes, so a name picked from a list and a name
    typed on the command line are one and the same.

    Examples::

        endpoint_model_name("my_cluster", "llama-3-70b")  -> "my_cluster:llama-3-70b"
    """
    return f"{api_name}:{model}"


def endpoint_of(model: str) -> Optional[str]:
    """Return which endpoint a catalog model belongs to, or ``None`` for a sandbox model.

    Read from the entry itself rather than from the colon in its name, because
    the entry is what this module wrote and a colon could, in principle, turn up
    in somebody's hand-written model name.

    Args:
        model: The model's name as the catalog holds it.

    Returns:
        The endpoint's name (e.g. ``'my_cluster'``), or ``None`` if the model
        is not in the catalog or is not one of an endpoint's.
    """
    try:
        entry = _catalog.load_model_catalog()["models"].get(model)
    except (FileNotFoundError, ValueError):
        return None
    if isinstance(entry, dict) and entry.get("endpoint"):
        return str(entry["endpoint"])
    return None


def _new_entry(api_name: str, model: str) -> Dict[str, Any]:
    """Return the catalog entry recorded for a model found on an endpoint."""
    return {
        "endpoint": api_name,
        # The name the endpoint itself knows the model by, which is what a
        # request has to send. Kept here so nothing has to take the catalog's
        # name apart to find it.
        "model": model,
        "added": datetime.now().isoformat(timespec="seconds"),
    }


def models_on_endpoint(api_config: "APIConfig") -> list[str]:
    """Ask an endpoint which models it runs.

    Args:
        api_config: The endpoint to ask, from ``load_api_config()``.

    Returns:
        The names the endpoint gave, in alphabetical order.

    Raises:
        Exception: Whatever went wrong in asking — the endpoint is switched
                   off, refused the key, or does not answer this question.
                   Callers decide what that means for them.
    """
    from ..services.api_config import endpoint_client

    client = endpoint_client(
        api_config, timeout=min(float(api_config.timeout), _LISTING_TIMEOUT_SECONDS)
    )
    return sorted({m.id for m in client.models.list() if getattr(m, "id", None)})


def remember_endpoint_model(api_name: str, model: str) -> bool:
    """Record one model on an endpoint in the catalog, if it is not there already.

    Called whenever work is sent to an endpoint, so a model somebody names with
    the colon syntax is in the lists from then on, even on a server that will
    not say which models it runs. Nothing is asked of the endpoint.

    Args:
        api_name: The endpoint's name (e.g. ``'my_cluster'``).
        model: The model's name as the endpoint knows it (e.g.
               ``'llama-3-70b'``).

    Returns:
        ``True`` if the model was newly added. ``False`` if it was already
        there, or if it could not be added — no catalog yet on an installation
        that has not been set up, or a catalog that could not be written. None
        of those is a reason to stop the work itself, so nothing is raised.
    """
    name = endpoint_model_name(api_name, model)
    with _lock:
        try:
            catalog = _catalog.load_model_catalog()
        except (FileNotFoundError, ValueError):
            return False
        if name in catalog["models"]:
            return False
        catalog["models"][name] = _new_entry(api_name, model)
        try:
            _catalog.save_model_catalog(catalog)
        except OSError as error:
            logging.warning("Could not add '%s' to model_catalog.json: %s", name, error)
            return False
    logging.info("Added '%s' to model_catalog.json.", name)
    return True


def sync_endpoint_models(force: bool = False) -> None:
    """Bring the catalog's list of endpoint models up to date with the endpoints themselves.

    For every endpoint defined in the settings, asks it which models it runs
    and adds any the catalog does not have, along with its ``default_model``.
    A model that belongs to an endpoint which is no longer defined is taken
    out, and so is one the endpoint has stopped listing, since picking either
    would only fail.

    Nothing is ever taken out on the strength of a failure: an endpoint that
    is switched off or does not answer keeps every model it had, because a
    cluster that is down for the afternoon has not stopped running them. What
    was added by hand to an entry, such as ``"supports_vision": true``, stays
    for as long as the model does.

    Never raises. A list of models failing to update is never a reason for the
    page or command showing that list to fail.

    Args:
        force: Ask every endpoint now, even one asked within the last hour by
               this same running process. The command line passes ``True``,
               since each command is a fresh start anyway.
    """
    from .. import settings
    from ..services.api_config import load_api_config

    try:
        with _lock:
            _sync(settings.ENDPOINTS, load_api_config, force)
    except Exception as error:
        logging.warning("Could not bring the endpoint models in the catalog up to date: %s", error)


def _sync(endpoints: Dict[str, Any], load_api_config: Any, force: bool) -> None:
    """Do the work of ``sync_endpoint_models()``, with the catalog already locked."""
    try:
        catalog = _catalog.load_model_catalog()
    except (FileNotFoundError, ValueError):
        # Not set up yet, or a catalog somebody needs to repair by hand. Either
        # way there is nothing here to bring up to date.
        return
    models: Dict[str, Any] = catalog["models"]
    changed = False

    # An endpoint that is no longer defined anywhere cannot be sent anything,
    # so nothing recorded against it can be used.
    for name, entry in list(models.items()):
        if isinstance(entry, dict) and entry.get("endpoint") and entry["endpoint"] not in endpoints:
            del models[name]
            changed = True

    now = datetime.now()
    for api_name in endpoints:
        try:
            config = load_api_config(api_name)
        except ValueError as error:
            logging.warning("Skipped the endpoint '%s' when listing its models: %s", api_name, error)
            continue
        if not config.openai_compatible:
            continue

        wanted: set[str] = {config.default_model} if config.default_model else set()
        listed: Optional[list[str]] = None
        last = _last_asked.get(api_name)
        if force or last is None or now - last >= _ASK_AGAIN_AFTER:
            # Recorded before asking, so an endpoint that is switched off is
            # waited on once an hour rather than every time a list is shown.
            _last_asked[api_name] = now
            try:
                listed = models_on_endpoint(config)
            except Exception as error:
                logging.warning(
                    "Could not ask the endpoint '%s' which models it runs, so its "
                    "models in the catalog were left as they were: %s", api_name, error,
                )
        if listed:
            wanted.update(listed)

        for model in sorted(wanted):
            name = endpoint_model_name(api_name, model)
            if name not in models:
                models[name] = _new_entry(api_name, model)
                changed = True
                logging.info("Added '%s' to model_catalog.json.", name)

        # Only an answer takes anything out. An empty list is treated as no
        # answer, since a server with nothing loaded at this moment is more
        # likely between models than finished with them.
        if listed:
            for name, entry in list(models.items()):
                if (
                    isinstance(entry, dict)
                    and entry.get("endpoint") == api_name
                    and entry.get("model") not in wanted
                ):
                    del models[name]
                    changed = True
                    logging.info(
                        "Took '%s' out of model_catalog.json: the endpoint no longer lists it.",
                        name,
                    )

    if changed:
        _catalog.save_model_catalog(catalog)
