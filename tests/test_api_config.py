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
        assert _env_key_for("pu_sandbox") == "EXTERNAL_API_PU_SANDBOX_KEY"

    def test_hyphen_name(self):
        assert _env_key_for("my-cluster") == "EXTERNAL_API_MY_CLUSTER_KEY"

    def test_already_upper(self):
        assert _env_key_for("UPPER") == "EXTERNAL_API_UPPER_KEY"

    def test_mixed(self):
        assert _env_key_for("My_Service-v2") == "EXTERNAL_API_MY_SERVICE_V2_KEY"


# ---------------------------------------------------------------------------
# Helpers — fake settings dict
# ---------------------------------------------------------------------------

_SETTINGS_WITH_APIS = {
    "apis": {
        "pu_sandbox": {
            "name": "PU AI Sandbox",
            "base_url": "https://api.example.com/v1",
            "openai_compatible": True,
            "default_model": "gpt-4o",
            "timeout": 30,
            "verify_ssl": True,
        },
        "data_service": {
            "name": "Research Data",
            "base_url": "https://data.example.com",
            "openai_compatible": False,
        },
    }
}

_SETTINGS_WITH_DEFAULT = {
    "apis": {
        "default": "pu_sandbox",
        "pu_sandbox": {
            "name": "PU AI Sandbox",
            "base_url": "https://api.example.com/v1",
            "openai_compatible": True,
            "default_model": "gpt-4o",
        },
    }
}

_SETTINGS_EMPTY = {}


def _patch_settings(settings: dict):
    """Context manager that patches _load_raw_settings to return *settings*."""
    return patch(
        "src.services.api_config._load_raw_settings",
        return_value=settings,
    )


# ---------------------------------------------------------------------------
# load_api_config — happy paths
# ---------------------------------------------------------------------------

class TestLoadAPIConfig:
    def test_openai_compatible(self, monkeypatch):
        monkeypatch.setenv("EXTERNAL_API_PU_SANDBOX_KEY", "test-key-123")
        with _patch_settings(_SETTINGS_WITH_APIS):
            cfg = load_api_config("pu_sandbox")
        assert cfg.api_name == "pu_sandbox"
        assert cfg.display_name == "PU AI Sandbox"
        assert cfg.base_url == "https://api.example.com/v1"
        assert cfg.api_key == "test-key-123"
        assert cfg.openai_compatible is True
        assert cfg.default_model == "gpt-4o"
        assert cfg.timeout == 30
        assert cfg.verify_ssl is True

    def test_generic_rest(self, monkeypatch):
        monkeypatch.setenv("EXTERNAL_API_DATA_SERVICE_KEY", "rest-key")
        with _patch_settings(_SETTINGS_WITH_APIS):
            cfg = load_api_config("data_service")
        assert cfg.openai_compatible is False
        assert cfg.default_model is None

    def test_defaults_applied(self, monkeypatch):
        """Fields not in settings.toml get sensible defaults."""
        minimal = {
            "apis": {
                "minimal": {"base_url": "http://localhost:8000"}
            }
        }
        monkeypatch.setenv("EXTERNAL_API_MINIMAL_KEY", "k")
        with _patch_settings(minimal):
            cfg = load_api_config("minimal")
        assert cfg.display_name == "minimal"  # falls back to api_name
        assert cfg.openai_compatible is False
        assert cfg.timeout == 30
        assert cfg.verify_ssl is True

    def test_extra_fields_preserved(self, monkeypatch):
        """Unknown fields are captured in cfg.extra."""
        settings = {
            "apis": {
                "fancy": {
                    "base_url": "http://x.com",
                    "custom_header": "Bearer xyz",
                }
            }
        }
        monkeypatch.setenv("EXTERNAL_API_FANCY_KEY", "k")
        with _patch_settings(settings):
            cfg = load_api_config("fancy")
        assert "custom_header" in cfg.extra

    def test_returns_api_config_instance(self, monkeypatch):
        monkeypatch.setenv("EXTERNAL_API_PU_SANDBOX_KEY", "k")
        with _patch_settings(_SETTINGS_WITH_APIS):
            cfg = load_api_config("pu_sandbox")
        assert isinstance(cfg, APIConfig)


# ---------------------------------------------------------------------------
# load_api_config — error paths
# ---------------------------------------------------------------------------

class TestLoadAPIConfigErrors:
    def test_unknown_api_name(self):
        with _patch_settings(_SETTINGS_WITH_APIS):
            with pytest.raises(ValueError, match="not configured"):
                load_api_config("nonexistent")

    def test_unknown_api_hints_available(self):
        with _patch_settings(_SETTINGS_WITH_APIS):
            with pytest.raises(ValueError, match="pu_sandbox"):
                load_api_config("nonexistent")

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("EXTERNAL_API_PU_SANDBOX_KEY", raising=False)
        with _patch_settings(_SETTINGS_WITH_APIS):
            with pytest.raises(ValueError, match="EXTERNAL_API_PU_SANDBOX_KEY"):
                load_api_config("pu_sandbox")

    def test_missing_base_url_raises(self, monkeypatch):
        settings = {"apis": {"bad": {"name": "Bad"}}}
        monkeypatch.setenv("EXTERNAL_API_BAD_KEY", "k")
        with _patch_settings(settings):
            with pytest.raises(ValueError, match="base_url"):
                load_api_config("bad")

    def test_no_apis_configured(self):
        with _patch_settings(_SETTINGS_EMPTY):
            with pytest.raises(ValueError, match="not configured"):
                load_api_config("anything")


# ---------------------------------------------------------------------------
# list_apis
# ---------------------------------------------------------------------------

class TestListAPIs:
    def test_returns_names(self):
        with _patch_settings(_SETTINGS_WITH_APIS):
            names = list_apis()
        assert set(names) == {"pu_sandbox", "data_service"}

    def test_excludes_scalar_default_key(self):
        """The 'default' scalar value must not appear in the API list."""
        with _patch_settings(_SETTINGS_WITH_DEFAULT):
            names = list_apis()
        assert "default" not in names
        assert "pu_sandbox" in names

    def test_empty_when_no_section(self):
        with _patch_settings(_SETTINGS_EMPTY):
            assert list_apis() == []

    def test_empty_when_section_has_only_scalars(self):
        with _patch_settings({"apis": {"default": "missing"}}):
            assert list_apis() == []


# ---------------------------------------------------------------------------
# get_default_api_name
# ---------------------------------------------------------------------------

class TestGetDefaultApiName:
    def test_returns_default(self):
        with _patch_settings(_SETTINGS_WITH_DEFAULT):
            assert get_default_api_name() == "pu_sandbox"

    def test_returns_none_when_not_set(self):
        with _patch_settings(_SETTINGS_WITH_APIS):
            assert get_default_api_name() is None

    def test_returns_none_when_empty_string(self):
        with _patch_settings({"apis": {"default": ""}}):
            assert get_default_api_name() is None

    def test_returns_none_when_no_section(self):
        with _patch_settings(_SETTINGS_EMPTY):
            assert get_default_api_name() is None


# ---------------------------------------------------------------------------
# parse_model_source
# ---------------------------------------------------------------------------

class TestParseModelSource:
    def test_colon_splits_api_and_model(self):
        api, model = parse_model_source("della:qwen-preview")
        assert api == "della"
        assert model == "qwen-preview"

    def test_colon_with_provider_slash_model(self):
        api, model = parse_model_source("pu_sandbox:openai/gpt-4o")
        assert api == "pu_sandbox"
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
        api, model = parse_model_source("della:")
        assert api is None
        assert model == "della:"

    def test_whitespace_stripped(self):
        api, model = parse_model_source("  della  :  qwen  ")
        assert api == "della"
        assert model == "qwen"


