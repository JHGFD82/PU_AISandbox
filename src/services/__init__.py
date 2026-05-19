"""Service layer — AI API services."""

from .api_call_service import APICallService
from .api_config import (
    APIConfig,
    load_api_config,
    list_apis,
    get_default_api_name,
    parse_model_source,
)
from .api_service import APIService

__all__ = [
    "APICallService",
    "APIConfig",
    "APIService",
    "load_api_config",
    "list_apis",
    "get_default_api_name",
    "parse_model_source",
]

# portkey_ai and the services that depend on it are optional — present in
# production environments but may be absent in lightweight test installs.
try:
    from .api_errors import APISignal
    from .base_service import BaseService
    __all__ += ["APISignal", "BaseService"]
except ImportError:
    pass
