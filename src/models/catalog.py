"""Model catalog file I/O and model property queries."""

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
    """Load model catalog from file with comprehensive validation.

    The result is cached in memory after the first successful read and
    re-used as long as the file's mtime has not changed.  This eliminates
    repeated file-descriptor opens during parallel translation where each
    worker would otherwise open the catalog for every API call.
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
    """Save model catalog to file atomically.

    Writes to a temp file in the same directory, then replaces the target
    with os.replace() so readers never see a partially written file.
    Invalidates the in-memory cache so the next read reflects the new data.
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
    """Get pricing for a specific model."""
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
    """Check if a model supports vision/image processing."""
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
    """Get the appropriate system message role for a model.

    Newer reasoning models (e.g. o3-mini, gpt-5) require 'developer' instead of 'system'.
    Defaults to 'system' for all other models.
    """
    config = load_model_catalog()
    models = config["models"]
    return models.get(model, {}).get("system_role", "system")


def model_uses_max_completion_tokens(model: str) -> bool:
    """Check if a model requires 'max_completion_tokens' instead of 'max_tokens'.

    Newer reasoning models (e.g. o3-mini, gpt-5) reject 'max_tokens'.
    """
    config = load_model_catalog()
    models = config["models"]
    return models.get(model, {}).get("use_max_completion_tokens", False)


def model_has_fixed_parameters(model: str) -> bool:
    """Check if a model only accepts default sampling parameters.

    Reasoning models (e.g. o3-mini, gpt-5) reject temperature, top_p,
    frequency_penalty, and presence_penalty — only the default values are supported.
    """
    config = load_model_catalog()
    models = config["models"]
    return models.get(model, {}).get("fixed_parameters", False)


def model_omit_sampling_params(model: str) -> bool:
    """Check if model should omit optional sampling parameters entirely.

    Some provider routes deprecate temperature/top_p for specific models even
    when they are not marked as fully fixed-parameter models.
    """
    config = load_model_catalog()
    models = config["models"]
    return models.get(model, {}).get("omit_sampling_params", False)


def get_model_max_completion_tokens(model: str, default: int) -> int:
    """Get per-model max completion tokens override, falling back to the given default.

    Reasoning models (e.g. gpt-5) consume hidden reasoning tokens from the same
    completion budget, so they need a larger cap than standard models.
    Set 'max_completion_tokens' in model_catalog.json to override the default.
    """
    config = load_model_catalog()
    return config["models"].get(model, {}).get("max_completion_tokens", default)


def get_default_model(role: str) -> Optional[str]:
    """Get the default model name for a given role from config.defaults.

    Roles: 'translation', 'ocr', 'image_translation'.
    Returns None if the defaults section or the specific role is absent from
    the catalog, allowing callers to apply their own fallback logic.
    """
    config = load_model_catalog()
    return config.get("config", {}).get("defaults", {}).get(role)


def remove_model_from_catalog(model_name: str) -> bool:
    """Remove a model from the catalog by key name.

    Returns True if the model was removed, False if it was not present.
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
