"""Backward-compatibility shim — import from api_config instead."""
# This file is retained for backward compatibility only.
# All symbols have moved to src/services/api_config.py.
from .api_config import (  # noqa: F401
    APIConfig as ExternalAPIConfig,
    load_api_config as load_external_api_config,
    list_apis as list_external_apis,
    _env_key_for,
    _load_raw_settings,
    _get_apis_dict,
)
