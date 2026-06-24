"""Reads and writes the model catalog file, and answers questions about individual models.

The model catalog (``src/model_catalog.json``) is the single source of truth for
which AI models are available, what they cost per token, and what special
capabilities or limitations each one has. Functions in this module load that
file, look up pricing and properties for a given model, and save any changes
back to disk.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

MODEL_CATALOG_FILE = "model_catalog.json"
DEFAULT_FALLBACK_MODEL = "gpt-4o-mini"

# In-memory cache: populated on first load, invalidated whenever the catalog
# is written.  Eliminates repeated file-descriptor opens during parallel
# translation where each worker would otherwise call load_model_catalog() for
# every API call.
#
# The cache is keyed on both the resolved file path and its mtime so that:
#   a) test fixtures that redirect get_model_catalog_path() to a tmp file get
#      a fresh read automatically.
#   b) any writer (including test helpers that bypass save_model_catalog) that
#      modifies the file on disk invalidates the cache via the changed mtime.
_catalog_cache: Optional[Dict[str, Any]] = None
_catalog_cache_path: Optional[Path] = None
_catalog_cache_mtime: Optional[float] = None


def get_model_catalog_path() -> Path:
    """Get the path to the model catalog file (src/model_catalog.json)."""
    # __file__ is src/models/catalog.py; parent.parent is src/
    return Path(__file__).parent.parent / MODEL_CATALOG_FILE


def load_model_catalog() -> Dict[str, Any]:
    """Read the model catalog file and return its contents.

    To avoid re-opening the file on every API call during a long translation
    job, the result is kept in memory after the first read and reused for the
    rest of the session (caching). The cache is automatically discarded if the
    file on disk has been modified since the last read, so edits to
    ``model_catalog.json`` always take effect on the next call.

    Returns:
        A dictionary with two top-level keys: ``'config'`` (global settings
        such as pricing unit and monthly limit) and ``'models'`` (per-model
        pricing and capability flags).

    Raises:
        FileNotFoundError: If ``src/model_catalog.json`` does not exist.
            The error message explains how to create it from the template.
        ValueError: If the file contains invalid JSON or is missing required
            sections.
    """
    global _catalog_cache, _catalog_cache_path, _catalog_cache_mtime

    catalog_file = get_model_catalog_path()
    try:
        current_mtime: Optional[float] = os.path.getmtime(catalog_file)
    except OSError:
        current_mtime = None

    if (
        _catalog_cache is not None
        and _catalog_cache_path == catalog_file
        and _catalog_cache_mtime == current_mtime
    ):
        return _catalog_cache


    if not catalog_file.exists():
        template_file = catalog_file.parent / "model_catalog.template.json"
        error_msg = (
            f"Model catalog file not found at {catalog_file}. "
            "Copy the template to get started:\n"
            f"  cp {template_file} {catalog_file}\n"
            "Then edit src/model_catalog.json to configure your models, "
            "or use 'openai/model-name' or 'provider/model-name' with -m to "
            "auto-register models on first use."
        )
        logging.error(error_msg)
        raise FileNotFoundError(error_msg)

    try:
        with open(catalog_file, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON in model catalog file {catalog_file}: {e}"
        logging.error(error_msg)
        raise ValueError(error_msg)

    if "config" not in config:
        error_msg = f"Model catalog file {catalog_file} missing required 'config' section."
        logging.error(error_msg)
        raise ValueError(error_msg)

    if "models" not in config:
        error_msg = f"Model catalog file {catalog_file} missing required 'models' section."
        logging.error(error_msg)
        raise ValueError(error_msg)

    if not config["models"]:
        error_msg = f"Model catalog file {catalog_file} has no models configured."
        logging.error(error_msg)
        raise ValueError(error_msg)

    _catalog_cache = config
    _catalog_cache_path = catalog_file
    _catalog_cache_mtime = current_mtime
    return config


def save_model_catalog(config: Dict[str, Any]) -> None:
    """Write an updated model catalog to disk and clear the in-memory cache.

    The file is written completely to a temporary location before replacing
    the live catalog, so a crash or power loss mid-write can never leave a
    partially written file behind (atomic write). The in-memory cache is
    cleared so the next read picks up the new contents.

    Args:
        config: The complete catalog dictionary to save, in the same structure
                returned by ``load_model_catalog()`` — a dict with ``'config'``
                and ``'models'`` keys.
    """
    global _catalog_cache
    _catalog_cache = None  # Invalidate before write so any concurrent reader re-reads
    catalog_file = get_model_catalog_path()
    catalog_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=catalog_file.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp_path, catalog_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_available_models() -> List[str]:
    """Get available models from the model catalog."""
    config = load_model_catalog()
    return list(config["models"].keys())


def get_model_pricing(model: str) -> Dict[str, float]:
    """Return the input and output cost rates for a model from the catalog.

    If the requested model is not in the catalog, falls back to
    ``gpt-4o-mini`` pricing and logs a warning. This prevents a missing entry
    from halting a job, though the cost estimate will be approximate.

    Args:
        model: The model name exactly as it appears in ``model_catalog.json``
               (e.g. ``'gpt-4o'``, ``'gpt-4o-mini'``).

    Returns:
        A dictionary with at minimum ``'input'`` and ``'output'`` keys whose
        values are the cost per pricing unit (see ``get_pricing_unit()``).

    Raises:
        ValueError: If the model is not found and the fallback model is also
                    absent from the catalog.
    """
    config = load_model_catalog()
    models = config["models"]

    if model not in models:
        if DEFAULT_FALLBACK_MODEL in models:
            logging.warning(
                f"Model {model} not found in pricing config. Using {DEFAULT_FALLBACK_MODEL} rates."
            )
            return models[DEFAULT_FALLBACK_MODEL]
        available_models = list(models.keys())
        error_msg = (
            f"Model '{model}' not found in model catalog and no fallback model "
            f"'{DEFAULT_FALLBACK_MODEL}' available. "
            f"Available models: {available_models}. "
            "Please update your model catalog file."
        )
        logging.error(error_msg)
        raise ValueError(error_msg)

    return models[model]


def get_pricing_unit() -> int:
    """Get the pricing unit from the model catalog config."""
    config = load_model_catalog()
    return config["config"]["pricing_unit"]


def get_monthly_limit() -> float:
    """Get the monthly spending limit from the model catalog config."""
    config = load_model_catalog()
    return config["config"]["monthly_limit"]


def model_supports_vision(model: str) -> bool:
    """Check whether a model can accept images as part of a request.

    Models that support vision can be used for OCR and image translation.
    The capability is controlled by the ``supports_vision`` flag in
    ``model_catalog.json``. Returns ``False`` for any model not in the
    catalog.

    Args:
        model: The model name to check (e.g. ``'gpt-4o'``).

    Returns:
        ``True`` if the model's catalog entry has ``"supports_vision": true``,
        ``False`` otherwise.
    """
    config = load_model_catalog()
    models = config["models"]

    if model not in models:
        logging.warning(f"Model {model} not found in pricing config. Assuming no vision support.")
        return False

    return models[model].get("supports_vision", False)


def get_vision_capable_models() -> List[str]:
    """Get list of models that support vision/image processing."""
    config = load_model_catalog()
    return [
        model for model, details in config["models"].items()
        if details.get("supports_vision", False)
    ]


def get_model_system_role(model: str) -> str:
    """Return the role label the model expects for system-level instructions.

    Most models accept a ``"system"`` role for the instruction message at the
    start of a conversation. A small number of newer reasoning models
    (such as ``o3-mini``) require the label ``"developer"`` instead and will
    reject requests that use ``"system"``. This function looks up the correct
    label from the catalog so each model receives the format it expects.

    Args:
        model: The model name to look up (e.g. ``'gpt-4o'``, ``'o3-mini'``).

    Returns:
        ``'system'`` for most models, or ``'developer'`` for models that
        require it. Defaults to ``'system'`` if the model is not in the
        catalog.
    """
    config = load_model_catalog()
    models = config["models"]
    return models.get(model, {}).get("system_role", "system")


def model_uses_max_completion_tokens(model: str) -> bool:
    """Check whether a model requires a different parameter name for setting its response length limit.

    Standard models accept ``max_tokens`` to cap how long their response can
    be. Some newer reasoning models (such as ``o3-mini``) reject that
    parameter and require ``max_completion_tokens`` instead. This function
    looks up which parameter name applies so the API call is constructed
    correctly.

    Args:
        model: The model name to check (e.g. ``'gpt-4o'``, ``'o3-mini'``).

    Returns:
        ``True`` if the model requires ``max_completion_tokens``,
        ``False`` if it uses the standard ``max_tokens``.
    """
    config = load_model_catalog()
    models = config["models"]
    return models.get(model, {}).get("use_max_completion_tokens", False)


def model_has_fixed_parameters(model: str) -> bool:
    """Check whether a model ignores temperature, top-p, and other sampling controls.

    Most models accept parameters that shape how creative or focused their
    responses are (temperature, top-p, etc.). Some reasoning models
    (such as ``o3-mini``) only support fixed, default values for those
    parameters and will reject requests that include them. When this returns
    ``True``, sampling parameters are omitted from the API call entirely.

    Args:
        model: The model name to check (e.g. ``'gpt-4o'``, ``'o3-mini'``).

    Returns:
        ``True`` if the model's catalog entry has ``"fixed_parameters": true``,
        ``False`` otherwise.
    """
    config = load_model_catalog()
    models = config["models"]
    return models.get(model, {}).get("fixed_parameters", False)


def model_omit_sampling_params(model: str) -> bool:
    """Check whether sampling parameters should be left out of requests for this model.

    Similar to ``model_has_fixed_parameters``, but applies to models where the
    provider route discourages temperature and top-p even though the model is
    not fully locked down. Setting ``"omit_sampling_params": true`` in the
    catalog for such a model prevents rejected-request errors without treating
    the model as fully fixed-parameter.

    Args:
        model: The model name to check (e.g. ``'gpt-4o'``).

    Returns:
        ``True`` if sampling parameters should be omitted, ``False`` otherwise.
    """
    config = load_model_catalog()
    models = config["models"]
    return models.get(model, {}).get("omit_sampling_params", False)


def get_model_max_completion_tokens(model: str, default: int) -> int:
    """Return the response-length cap to use for a model, applying any per-model override from the catalog.

    Some models (particularly reasoning models that do internal thinking before
    responding) consume part of the token budget on steps that are not visible
    in the final response. Those models therefore need a larger cap than
    standard models. Setting ``max_completion_tokens`` in the catalog entry
    for such a model overrides the value that would otherwise be used.

    Args:
        model: The model name to look up (e.g. ``'gpt-4o'``).
        default: The cap to use if no per-model override is set in the catalog
                 (e.g. ``4096``).

    Returns:
        The per-model override from the catalog if present, otherwise
        ``default``.
    """
    config = load_model_catalog()
    return config["models"].get(model, {}).get("max_completion_tokens", default)


def get_default_model(role: str) -> Optional[str]:
    """Return the default model name configured for a specific role, if one is set.

    Different commands use different default models — OCR uses a vision-capable
    model while translation may use a different one. These defaults are set in
    the ``config.defaults`` section of ``model_catalog.json`` and can be
    changed there without touching any code.

    Args:
        role: The command role to look up. Recognised values are
              ``'translation'``, ``'ocr'``, and ``'image_translation'``.

    Returns:
        The model name string if a default is configured for that role
        (e.g. ``'gpt-4o-mini'``), or ``None`` if the role is absent from the
        catalog. Callers apply their own fallback when ``None`` is returned.
    """
    config = load_model_catalog()
    return config.get("config", {}).get("defaults", {}).get(role)


def remove_model_from_catalog(model_name: str) -> bool:
    """Delete a model entry from the catalog and save the updated file to disk.

    Used automatically when the system detects that a model is no longer
    accessible through the AI gateway. Logs a warning when the model is
    removed so the change is visible in the run log.

    Args:
        model_name: The exact key of the model to remove, as it appears in
                    ``model_catalog.json`` (e.g. ``'gpt-4o-2024-08-06'``).

    Returns:
        ``True`` if the model was found and removed, ``False`` if the model
        was not present in the catalog (no changes are made in that case).
    """
    catalog = load_model_catalog()
    if model_name not in catalog["models"]:
        return False
    del catalog["models"][model_name]
    save_model_catalog(catalog)
    logging.warning(f"Removed inaccessible model '{model_name}' from catalog.")
    return True


def is_model_access_error(error_message: str) -> bool:
    """Return True if the error message indicates the model is not accessible
    in the Princeton AI Sandbox (PortKey router cannot find it).
    """
    return "invalid target name found in the query router" in error_message.lower()
