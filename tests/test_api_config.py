"""Tests for src/services/api_config.py."""

from unittest.mock import patch

import pytest

from src.services.api_config import (
    APIConfig,
    credential_path_for_endpoint,
    get_default_api_name,
    list_apis,
    load_api_config,
    parse_model_source,
)

# ---------------------------------------------------------------------------
# credential_path_for_endpoint
# ---------------------------------------------------------------------------

class TestCredentialPathForEndpoint:
    def test_underscore_name(self):
        assert credential_path_for_endpoint("hpc_cluster") == "endpoints.hpc_cluster.key"

    def test_hyphen_name(self):
        assert credential_path_for_endpoint("my-cluster") == "endpoints.my-cluster.key"


# ---------------------------------------------------------------------------
# Helpers — fake merged-settings endpoint data
# ---------------------------------------------------------------------------

_ENDPOINTS_WITH_ENDPOINTS = {
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
}

_ENDPOINTS_EMPTY: dict = {}

_CREDENTIALS = {
    "endpoints.hpc_cluster.key": "test-key-123",
    "endpoints.data_service.key": "rest-key",
}


def _patch_endpoints(data: dict):
    """Context manager that patches settings.ENDPOINTS to *data*."""
    return patch("src.services.api_config.settings.ENDPOINTS", data)


def _patch_credentials(mapping: dict):
    """Context manager that patches settings_store.get_value to look up *mapping*."""
    return patch(
        "src.services.api_config.settings_store.get_value",
        side_effect=lambda path: mapping.get(path),
    )


# ---------------------------------------------------------------------------
# load_api_config — happy paths
# ---------------------------------------------------------------------------

class TestLoadAPIConfig:
    def test_openai_compatible(self):
        with _patch_endpoints(_ENDPOINTS_WITH_ENDPOINTS), _patch_credentials(_CREDENTIALS):
            cfg = load_api_config("hpc_cluster")
        assert cfg.api_name == "hpc_cluster"
        assert cfg.display_name == "HPC Cluster"
        assert cfg.base_url == "https://cluster.example.com/v1"
        assert cfg.api_key == "test-key-123"
        assert cfg.openai_compatible is True
        assert cfg.default_model == "llama-3-70b-instruct"
        assert cfg.timeout == 30
        assert cfg.verify_ssl is True

    def test_generic_rest(self):
        with _patch_endpoints(_ENDPOINTS_WITH_ENDPOINTS), _patch_credentials(_CREDENTIALS):
            cfg = load_api_config("data_service")
        assert cfg.openai_compatible is False
        assert cfg.default_model is None

    def test_defaults_applied(self):
        """Fields not in the endpoint table get sensible defaults."""
        minimal = {"minimal": {"base_url": "http://localhost:8000"}}
        with _patch_endpoints(minimal), _patch_credentials({"endpoints.minimal.key": "k"}):
            cfg = load_api_config("minimal")
        assert cfg.display_name == "minimal"  # falls back to api_name
        assert cfg.openai_compatible is False
        assert cfg.timeout == 30
        assert cfg.verify_ssl is True

    def test_extra_fields_preserved(self):
        """Unknown fields are captured in cfg.extra."""
        data = {
            "fancy": {
                "base_url": "http://x.com",
                "custom_header": "Bearer xyz",
            }
        }
        with _patch_endpoints(data), _patch_credentials({"endpoints.fancy.key": "k"}):
            cfg = load_api_config("fancy")
        assert "custom_header" in cfg.extra

    def test_returns_api_config_instance(self):
        with _patch_endpoints(_ENDPOINTS_WITH_ENDPOINTS), _patch_credentials(_CREDENTIALS):
            cfg = load_api_config("hpc_cluster")
        assert isinstance(cfg, APIConfig)


# ---------------------------------------------------------------------------
# load_api_config — error paths
# ---------------------------------------------------------------------------

class TestLoadAPIConfigErrors:
    def test_unknown_api_name(self):
        with _patch_endpoints(_ENDPOINTS_WITH_ENDPOINTS):
            with pytest.raises(ValueError, match="not configured"):
                load_api_config("nonexistent")

    def test_unknown_api_hints_available(self):
        with _patch_endpoints(_ENDPOINTS_WITH_ENDPOINTS):
            with pytest.raises(ValueError, match="hpc_cluster"):
                load_api_config("nonexistent")

    def test_missing_key_raises(self):
        with _patch_endpoints(_ENDPOINTS_WITH_ENDPOINTS), _patch_credentials({}):
            with pytest.raises(ValueError, match="endpoints.hpc_cluster.key"):
                load_api_config("hpc_cluster")

    def test_missing_base_url_raises(self):
        data = {"bad": {"name": "Bad"}}
        with _patch_endpoints(data), _patch_credentials({"endpoints.bad.key": "k"}):
            with pytest.raises(ValueError, match="base_url"):
                load_api_config("bad")

    def test_no_endpoints_configured(self):
        with _patch_endpoints(_ENDPOINTS_EMPTY):
            with pytest.raises(ValueError, match="not configured"):
                load_api_config("anything")


# ---------------------------------------------------------------------------
# list_apis
# ---------------------------------------------------------------------------

class TestListAPIs:
    def test_returns_names(self):
        with _patch_endpoints(_ENDPOINTS_WITH_ENDPOINTS):
            names = list_apis()
        assert set(names) == {"hpc_cluster", "data_service"}

    def test_empty_when_no_endpoints(self):
        with _patch_endpoints(_ENDPOINTS_EMPTY):
            assert list_apis() == []


# ---------------------------------------------------------------------------
# get_default_api_name
# ---------------------------------------------------------------------------

class TestGetDefaultApiName:
    def test_returns_default(self):
        with patch("src.services.api_config.settings.DEFAULT_ENDPOINT", "hpc_cluster"):
            assert get_default_api_name() == "hpc_cluster"

    def test_returns_none_when_unset(self):
        with patch("src.services.api_config.settings.DEFAULT_ENDPOINT", None):
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
