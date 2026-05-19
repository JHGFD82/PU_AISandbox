"""Tests for src/services/api_config.py."""

import os
from unittest.mock import patch
import pytest

from src.services.api_config import (
    APIConfig,
    load_api_config,
    list_apis,
    get_default_api_name,
    parse_model_source,
    _env_key_for,
)

# ---------------------------------------------------------------------------
# _env_key_for
# ---------------------------------------------------------------------------

class TestEnvKeyFor:
    def test_underscore_name(self):
        assert _env_key_for("hpc_cluster") == "API_HPC_CLUSTER_KEY"

    def test_hyphen_name(self):
        assert _env_key_for("my-cluster") == "API_MY_CLUSTER_KEY"

    def test_already_upper(self):
        assert _env_key_for("UPPER") == "API_UPPER_KEY"

    def test_mixed(self):
        assert _env_key_for("My_Service-v2") == "API_MY_SERVICE_V2_KEY"


# ---------------------------------------------------------------------------
# Helpers — fake apis.json data
# ---------------------------------------------------------------------------

_APIS_WITH_ENDPOINTS = {
    "default": None,
    "endpoints": {
        "hpc_cluster": {
            "name": "HPC Cluster",
            "base_url": "https://cluster.example.com/v1",
            "openai_compatible": True,
            "default_model": "llama-3-70b-instruct",
            "timeout": 30,
            "verify_ssl": True,
        },
        "data_service": {
            "name": "Research Data",
            "base_url": "https://data.example.com",
            "openai_compatible": False,
        },
    },
}

_APIS_WITH_DEFAULT = {
    "default": "hpc_cluster",
    "endpoints": {
        "hpc_cluster": {
            "name": "HPC Cluster",
            "base_url": "https://cluster.example.com/v1",
            "openai_compatible": True,
            "default_model": "llama-3-70b-instruct",
        },
    },
}

_APIS_EMPTY = {}


def _patch_apis(data: dict):
    """Context manager that patches _load_apis_json to return *data*."""
    return patch(
        "src.services.api_config._load_apis_json",
        return_value=data,
    )


# ---------------------------------------------------------------------------
# load_api_config — happy paths
# ---------------------------------------------------------------------------

class TestLoadAPIConfig:
    def test_openai_compatible(self, monkeypatch):
        monkeypatch.setenv("API_HPC_CLUSTER_KEY", "test-key-123")
        with _patch_apis(_APIS_WITH_ENDPOINTS):
            cfg = load_api_config("hpc_cluster")
        assert cfg.api_name == "hpc_cluster"
        assert cfg.display_name == "HPC Cluster"
        assert cfg.base_url == "https://cluster.example.com/v1"
        assert cfg.api_key == "test-key-123"
        assert cfg.openai_compatible is True
        assert cfg.default_model == "llama-3-70b-instruct"
        assert cfg.timeout == 30
        assert cfg.verify_ssl is True

    def test_generic_rest(self, monkeypatch):
        monkeypatch.setenv("API_DATA_SERVICE_KEY", "rest-key")
        with _patch_apis(_APIS_WITH_ENDPOINTS):
            cfg = load_api_config("data_service")
        assert cfg.openai_compatible is False
        assert cfg.default_model is None

    def test_defaults_applied(self, monkeypatch):
        """Fields not in apis.json get sensible defaults."""
        minimal = {
            "endpoints": {
                "minimal": {"base_url": "http://localhost:8000"}
            }
        }
        monkeypatch.setenv("API_MINIMAL_KEY", "k")
        with _patch_apis(minimal):
            cfg = load_api_config("minimal")
        assert cfg.display_name == "minimal"  # falls back to api_name
        assert cfg.openai_compatible is False
        assert cfg.timeout == 30
        assert cfg.verify_ssl is True

    def test_extra_fields_preserved(self, monkeypatch):
        """Unknown fields are captured in cfg.extra."""
        data = {
            "endpoints": {
                "fancy": {
                    "base_url": "http://x.com",
                    "custom_header": "Bearer xyz",
                }
            }
        }
        monkeypatch.setenv("API_FANCY_KEY", "k")
        with _patch_apis(data):
            cfg = load_api_config("fancy")
        assert "custom_header" in cfg.extra

    def test_returns_api_config_instance(self, monkeypatch):
        monkeypatch.setenv("API_HPC_CLUSTER_KEY", "k")
        with _patch_apis(_APIS_WITH_ENDPOINTS):
            cfg = load_api_config("hpc_cluster")
        assert isinstance(cfg, APIConfig)


# ---------------------------------------------------------------------------
# load_api_config — error paths
# ---------------------------------------------------------------------------

class TestLoadAPIConfigErrors:
    def test_unknown_api_name(self):
        with _patch_apis(_APIS_WITH_ENDPOINTS):
            with pytest.raises(ValueError, match="not configured"):
                load_api_config("nonexistent")

    def test_unknown_api_hints_available(self):
        with _patch_apis(_APIS_WITH_ENDPOINTS):
            with pytest.raises(ValueError, match="hpc_cluster"):
                load_api_config("nonexistent")

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("API_HPC_CLUSTER_KEY", raising=False)
        with _patch_apis(_APIS_WITH_ENDPOINTS):
            with pytest.raises(ValueError, match="API_HPC_CLUSTER_KEY"):
                load_api_config("hpc_cluster")

    def test_missing_base_url_raises(self, monkeypatch):
        data = {"endpoints": {"bad": {"name": "Bad"}}}
        monkeypatch.setenv("API_BAD_KEY", "k")
        with _patch_apis(data):
            with pytest.raises(ValueError, match="base_url"):
                load_api_config("bad")

    def test_no_endpoints_configured(self):
        with _patch_apis(_APIS_EMPTY):
            with pytest.raises(ValueError, match="not configured"):
                load_api_config("anything")


# ---------------------------------------------------------------------------
# list_apis
# ---------------------------------------------------------------------------

class TestListAPIs:
    def test_returns_names(self):
        with _patch_apis(_APIS_WITH_ENDPOINTS):
            names = list_apis()
        assert set(names) == {"hpc_cluster", "data_service"}

    def test_empty_when_no_endpoints_key(self):
        with _patch_apis(_APIS_EMPTY):
            assert list_apis() == []

    def test_empty_when_endpoints_is_empty(self):
        with _patch_apis({"default": None, "endpoints": {}}):
            assert list_apis() == []


# ---------------------------------------------------------------------------
# get_default_api_name
# ---------------------------------------------------------------------------

class TestGetDefaultApiName:
    def test_returns_default(self):
        with _patch_apis(_APIS_WITH_DEFAULT):
            assert get_default_api_name() == "hpc_cluster"

    def test_returns_none_when_null(self):
        with _patch_apis(_APIS_WITH_ENDPOINTS):
            assert get_default_api_name() is None

    def test_returns_none_when_empty_string(self):
        with _patch_apis({"default": "", "endpoints": {}}):
            assert get_default_api_name() is None

    def test_returns_none_when_no_default_key(self):
        with _patch_apis(_APIS_EMPTY):
            assert get_default_api_name() is None


# ---------------------------------------------------------------------------
# parse_model_source
# ---------------------------------------------------------------------------

class TestParseModelSource:
    def test_colon_splits_api_and_model(self):
        api, model = parse_model_source("hpc_cluster:llama-3-70b")
        assert api == "hpc_cluster"
        assert model == "llama-3-70b"

    def test_colon_with_provider_slash_model(self):
        api, model = parse_model_source("my_cluster:openai/gpt-4o")
        assert api == "my_cluster"
        assert model == "openai/gpt-4o"

    def test_bare_model_returns_none_api(self):
        api, model = parse_model_source("gpt-4o")
        assert api is None
        assert model == "gpt-4o"

    def test_bare_model_with_hyphen_not_colon(self):
        api, model = parse_model_source("gpt-4o-mini")
        assert api is None
        assert model == "gpt-4o-mini"

    def test_model_with_version_numbers(self):
        api, model = parse_model_source("cluster:llama-3-70b-instruct")
        assert api == "cluster"
        assert model == "llama-3-70b-instruct"

    def test_empty_api_part_treated_as_bare_model(self):
        """A leading colon (no api name) falls back to bare model."""
        api, model = parse_model_source(":gpt-4o")
        assert api is None
        assert model == ":gpt-4o"

    def test_empty_model_part_treated_as_bare(self):
        """A trailing colon (no model after) falls back to bare model."""
        api, model = parse_model_source("hpc_cluster:")
        assert api is None
        assert model == "hpc_cluster:"

    def test_whitespace_stripped(self):
        api, model = parse_model_source("  hpc_cluster  :  llama-3-70b  ")
        assert api == "hpc_cluster"
        assert model == "llama-3-70b"
