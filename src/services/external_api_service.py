"""Backward-compatibility shim — import from api_service instead."""
# This file is retained for backward compatibility only.
# All symbols have moved to src/services/api_service.py.
from .api_service import APIService as ExternalAPIService  # noqa: F401
