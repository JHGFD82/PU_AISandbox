"""Service layer — AI API services."""

from .api_config import (
    APIConfig,
    load_api_config,
    list_apis,
    get_default_api_name,
    parse_model_source,
    # Backward compatibility aliases
    APIConfig as ExternalAPIConfig,
    load_api_config as load_external_api_config,
    list_apis as list_external_apis,
)
from .api_service import APIService, APIService as ExternalAPIService

__all__ = [
    # New names
    "APIConfig",
    "APIService",
    "load_api_config",
    "list_apis",
    "get_default_api_name",
    "parse_model_source",
    # Backward compatibility aliases
    "ExternalAPIConfig",
    "ExternalAPIService",
    "load_external_api_config",
    "list_external_apis",
]

# portkey_ai and the services that depend on it are optional — present in
# production environments but may be absent in lightweight test installs.
try:
    from .api_errors import APISignal
    from .base_service import BaseService
    __all__ += ["APISignal", "BaseService"]
except ImportError:
    pass
