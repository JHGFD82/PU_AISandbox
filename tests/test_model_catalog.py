"""Tests for model-catalog functions in src/models/."""

import json
import logging

import pytest
from unittest.mock import patch

import src.models.catalog as catalog_module
import src.models.pricing as pricing_module
from src.runtime.model_role import ModelRole
from src.models import (
    cheapest_model,
    get_available_models,
    get_model_catalog_path,
    get_model_max_completion_tokens,
    get_model_pricing,
    get_model_system_role,
    get_monthly_limit,
    get_pricing_unit,
    get_vision_capable_models,
    is_sampling_param_deprecated_error,
    load_model_catalog,
    model_accepts_sampling_params,
    model_supports_vision,
    model_preferences,
    model_rejected_fields,
    model_max_tokens_field,
    record_rejected_field,
    remove_model_from_catalog,
    resolve_model,
    save_model_catalog,
    record_sampling_params_rejected,
)

# ---------------------------------------------------------------------------
# Shared test catalog — used by every test that mocks load_model_catalog
# ---------------------------------------------------------------------------

SAMPLE_CATALOG = {
    "config": {
        "pricing_unit": 1_000_000,
        "monthly_limit": 250.0,
        "defaults": {
            "translation": "gpt-4o",
            "ocr": "gpt-4o",
            "image_translation": "gpt-5",
        },
    },
    "models": {
        "gpt-5": {
            "input": 1.38,
            "output": 11.0,
            "supports_vision": True,
            "system_role": "developer",
            "use_max_completion_tokens": True,
            "fixed_parameters": True,
            "max_completion_tokens": 16000,
        },
        "gpt-4o": {
            "input": 2.75,
            "output": 11.0,
            "supports_vision": True,
        },
        "gpt-4o-mini": {
            "input": 0.165,
            "output": 0.66,
            "supports_vision": True,
        },
        "text-only-model": {
            "input": 0.10,
            "output": 0.30,
            "supports_vision": False,
        },
    },
}


@pytest.fixture()
def mock_catalog(monkeypatch):
    """Patch load_model_catalog to return SAMPLE_CATALOG without hitting disk."""
    monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: SAMPLE_CATALOG)


# ---------------------------------------------------------------------------
# get_model_catalog_path
# ---------------------------------------------------------------------------

class TestGetModelCatalogPath:

    def test_returns_path_ending_in_catalog_filename(self):
        path = get_model_catalog_path()
        assert path.name == "model_catalog.json"

    def test_path_is_in_the_folder_this_installation_keeps_its_files_in(self):
        """The catalogue belongs to the person, not the package.

        It used to live under src/, which meant replacing the package
        replaced their pricing and model list too.
        """
        from src import paths
        assert get_model_catalog_path() == paths.extras_root() / "model_catalog.json"


# ---------------------------------------------------------------------------
# load_model_catalog
# ---------------------------------------------------------------------------

class TestLoadModelCatalog:

    def test_missing_file_raises_file_not_found(self, monkeypatch, tmp_path):
        missing = tmp_path / "nonexistent.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: missing)
        with pytest.raises(FileNotFoundError):
            load_model_catalog()

    def test_invalid_json_raises_value_error(self, monkeypatch, tmp_path):
        bad_file = tmp_path / "model_catalog.json"
        bad_file.write_text("{ not valid json }")
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: bad_file)
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_model_catalog()

    def test_missing_config_section_raises_value_error(self, monkeypatch, tmp_path):
        catalog = tmp_path / "model_catalog.json"
        catalog.write_text(json.dumps({"models": {"gpt-4o": {}}}))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog)
        with pytest.raises(ValueError, match="'config' section"):
            load_model_catalog()

    def test_missing_models_section_raises_value_error(self, monkeypatch, tmp_path):
        catalog = tmp_path / "model_catalog.json"
        catalog.write_text(json.dumps({"config": {"pricing_unit": 1000000, "monthly_limit": 250.0}}))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog)
        with pytest.raises(ValueError, match="'models' section"):
            load_model_catalog()

    def test_empty_models_section_raises_value_error(self, monkeypatch, tmp_path):
        catalog = tmp_path / "model_catalog.json"
        catalog.write_text(json.dumps({"config": {}, "models": {}}))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog)
        with pytest.raises(ValueError, match="no models"):
            load_model_catalog()

    def test_valid_catalog_returns_dict(self, monkeypatch, tmp_path):
        catalog = tmp_path / "model_catalog.json"
        catalog.write_text(json.dumps(SAMPLE_CATALOG))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog)
        result = load_model_catalog()
        assert result["config"]["pricing_unit"] == 1_000_000
        assert "gpt-4o" in result["models"]


# ---------------------------------------------------------------------------
# get_available_models
# ---------------------------------------------------------------------------

class TestGetAvailableModels:

    def test_returns_all_model_keys(self, mock_catalog):
        models = get_available_models()
        assert set(models) == {"gpt-5", "gpt-4o", "gpt-4o-mini", "text-only-model"}

    def test_returns_list(self, mock_catalog):
        assert isinstance(get_available_models(), list)


# ---------------------------------------------------------------------------
# get_model_pricing
# ---------------------------------------------------------------------------

class TestGetModelPricing:

    def test_known_model_returns_pricing(self, mock_catalog):
        pricing = get_model_pricing("gpt-4o")
        assert pricing["input"] == 2.75
        assert pricing["output"] == 11.0

    def test_unknown_model_is_priced_at_the_cheapest_models_rates(self, mock_catalog):
        """An uncatalogued model is recorded rather than lost, at the cheapest rates.

        Deliberately not a named stand-in: whichever model were named here
        would one day be retired, and then this path would raise instead of
        recording anything.
        """
        pricing = get_model_pricing("unknown-model")
        assert pricing["input"] == pytest.approx(0.1)   # text-only-model, cheapest in SAMPLE_CATALOG

    def test_unknown_model_raises_when_nothing_has_a_price(self, monkeypatch):
        """With no priced model to stand in, there is nothing honest to return."""
        catalog_unpriced = {
            "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
            "models": {"placeholder": {"input": 0.0, "output": 0.0, "supports_vision": True}},
        }
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: catalog_unpriced)
        with pytest.raises(ValueError, match="not in the model catalog"):
            get_model_pricing("mystery-model")


# ---------------------------------------------------------------------------
# get_pricing_unit / get_monthly_limit
# ---------------------------------------------------------------------------

class TestConfigValues:

    def test_get_pricing_unit(self, mock_catalog):
        assert get_pricing_unit() == 1_000_000

    def test_get_monthly_limit(self, mock_catalog):
        assert get_monthly_limit() == pytest.approx(250.0)



class TestModelSupportsVision:

    def test_vision_model_returns_true(self, mock_catalog):
        assert model_supports_vision("gpt-4o") is True

    def test_non_vision_model_returns_false(self, mock_catalog):
        assert model_supports_vision("text-only-model") is False

    def test_unknown_model_returns_false(self, mock_catalog):
        assert model_supports_vision("ghost-model") is False

    def test_gpt5_supports_vision(self, mock_catalog):
        assert model_supports_vision("gpt-5") is True


# ---------------------------------------------------------------------------
# get_vision_capable_models
# ---------------------------------------------------------------------------

class TestGetVisionCapableModels:

    def test_returns_only_vision_models(self, mock_catalog):
        vision_models = get_vision_capable_models()
        assert "text-only-model" not in vision_models
        assert set(vision_models) == {"gpt-5", "gpt-4o", "gpt-4o-mini"}

    def test_returns_list(self, mock_catalog):
        assert isinstance(get_vision_capable_models(), list)


# ---------------------------------------------------------------------------
# get_model_system_role
# ---------------------------------------------------------------------------

class TestGetModelSystemRole:

    def test_reasoning_model_returns_developer(self, mock_catalog):
        assert get_model_system_role("gpt-5") == "developer"

    def test_standard_model_defaults_to_system(self, mock_catalog):
        # gpt-4o has no system_role key in SAMPLE_CATALOG → defaults to "system"
        assert get_model_system_role("gpt-4o") == "system"

    def test_unknown_model_defaults_to_system(self, mock_catalog):
        assert get_model_system_role("nonexistent-model") == "system"


# ---------------------------------------------------------------------------
# model_max_tokens_field
# ---------------------------------------------------------------------------

class TestModelMaxTokensField:

    def test_reasoning_model_returns_true(self, mock_catalog):
        assert model_max_tokens_field("gpt-5") == "max_completion_tokens"

    def test_standard_model_returns_false(self, mock_catalog):
        assert model_max_tokens_field("gpt-4o") == "max_tokens"

    def test_unknown_model_returns_false(self, mock_catalog):
        assert model_max_tokens_field("mystery") == "max_tokens"


# ---------------------------------------------------------------------------
# model_accepts_sampling_params
# ---------------------------------------------------------------------------

class TestModelHasFixedParameters:

    def test_reasoning_model_returns_true(self, mock_catalog):
        assert model_accepts_sampling_params("gpt-5") is False

    def test_standard_model_returns_false(self, mock_catalog):
        assert model_accepts_sampling_params("gpt-4o") is True

    def test_unknown_model_returns_false(self, mock_catalog):
        assert model_accepts_sampling_params("mystery") is True


# ---------------------------------------------------------------------------
# get_model_max_completion_tokens
# ---------------------------------------------------------------------------

class TestGetModelMaxCompletionTokens:

    def test_model_with_override_returns_override(self, mock_catalog):
        assert get_model_max_completion_tokens("gpt-5", default=4096) == 16000

    def test_model_without_override_returns_default(self, mock_catalog):
        # gpt-4o has no max_completion_tokens in SAMPLE_CATALOG
        assert get_model_max_completion_tokens("gpt-4o", default=4096) == 4096

    def test_unknown_model_returns_default(self, mock_catalog):
        assert get_model_max_completion_tokens("ghost", default=2048) == 2048

    def test_default_value_is_respected(self, mock_catalog):
        assert get_model_max_completion_tokens("gpt-4o", default=8192) == 8192


# ---------------------------------------------------------------------------
# resolve_model
# ---------------------------------------------------------------------------

class TestResolveModel:

    def test_no_args_returns_the_cheapest_model(self, mock_catalog):
        """What models to prefer is the calling plugin's business, passed as a role.

        With none given, resolution lands on the cheapest model rather than a
        model named in code — nothing here goes stale when a provider retires
        something.
        """
        assert resolve_model() == "text-only-model"   # 0.4 vs gpt-4o-mini's 0.825

    def test_requested_model_returned(self, mock_catalog):
        assert resolve_model(requested_model="gpt-5") == "gpt-5"

    def test_requested_model_not_in_catalog_raises(self, mock_catalog):
        with pytest.raises(ValueError, match="not in the catalog"):
            resolve_model(requested_model="unknown-model")

    def test_requested_model_not_vision_capable_raises(self, mock_catalog):
        with pytest.raises(ValueError, match="not vision-capable"):
            resolve_model(requested_model="text-only-model", require_vision=True)

    def test_the_role_is_used_before_the_price_ranked_fallback(self, mock_catalog):
        assert resolve_model(role=ModelRole(["gpt-5"])) == "gpt-5"

    def test_a_role_model_without_vision_is_skipped_when_vision_is_needed(self, mock_catalog):
        """Skipped for lacking vision, so the cheapest model that HAS it wins.

        Note this is not the cheapest model overall — text-only-model is — so
        the capability filter is being applied before the price ranking.
        """
        assert resolve_model(role=ModelRole(["text-only-model"], requires_vision=True)) == "gpt-4o-mini"

    def test_require_vision_skips_non_vision_models(self, mock_catalog):
        result = resolve_model(require_vision=True)
        assert result in {"gpt-5", "gpt-4o", "gpt-4o-mini"}

    def test_falls_through_to_anything_usable_when_nothing_is_priced(self, monkeypatch):
        """Reached only when no model carries a price, so the ranking has nothing to sort."""
        catalog_unpriced = {
            **SAMPLE_CATALOG,
            "models": {k: {**v, "input": 0.0, "output": 0.0} for k, v in SAMPLE_CATALOG["models"].items()},
        }
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: catalog_unpriced)
        assert resolve_model() in set(catalog_unpriced["models"])

    def test_no_compatible_models_raises(self, monkeypatch):
        all_text_catalog = {
            "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
            "models": {
                "text-a": {"input": 0.1, "output": 0.1, "supports_vision": False},
                "text-b": {"input": 0.2, "output": 0.2, "supports_vision": False},
            },
        }
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: all_text_catalog)
        with pytest.raises(ValueError, match="No vision-capable models"):
            resolve_model(require_vision=True)

    def test_requested_model_takes_precedence_over_prefer(self, mock_catalog):
        assert resolve_model(requested_model="gpt-5", role=ModelRole(["gpt-4o-mini"])) == "gpt-5"

    # --- provider/model format ---

    def test_provider_model_already_in_catalog_resolved_without_api(self, mock_catalog):
        # e.g. "openai/gpt-4o" where "gpt-4o" is already in the catalog
        result = resolve_model(requested_model="openai/gpt-4o")
        assert result == "gpt-4o"

    def test_provider_model_not_in_catalog_auto_registers(self, monkeypatch, tmp_path):
        # Use a real tmp catalog (not mock_catalog) so get_available_models() reflects
        # changes made by fake_add.
        import json
        catalog_file = tmp_path / "model_catalog.json"
        initial_catalog = {
            "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
            "models": {"gpt-4o": {"input": 2.5, "output": 10.0, "supports_vision": True}},
        }
        catalog_file.write_text(json.dumps(initial_catalog))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        def fake_add(provider_model):
            cat = json.loads(catalog_file.read_text())
            cat["models"]["new-gpt"] = {"input": 1.0, "output": 3.0, "supports_vision": False}
            catalog_file.write_text(json.dumps(cat))
            return "new-gpt", cat["models"]["new-gpt"]

        monkeypatch.setattr(pricing_module, "add_model_to_catalog", fake_add)
        result = resolve_model(requested_model="openai/new-gpt")
        assert result == "new-gpt"

    def test_provider_model_auto_register_failure_raises(self, mock_catalog, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        def fake_add_fail(provider_model):
            raise RuntimeError("API down")

        monkeypatch.setattr(pricing_module, "add_model_to_catalog", fake_add_fail)
        with pytest.raises(ValueError, match="Could not auto-register"):
            resolve_model(requested_model="openai/ghost-model")

    def test_priority_candidate_skipped_in_fallback_loop_when_incompatible(
        self, monkeypatch
    ):
        ordered_catalog = {
            "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
            "models": {
                "text-only-model": {"input": 0.1, "output": 0.3, "supports_vision": False},
                "vision-model": {"input": 2.0, "output": 8.0, "supports_vision": True},
            },
        }
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: ordered_catalog)
        result = resolve_model(role=ModelRole(["text-only-model"], requires_vision=True))
        assert result == "vision-model"


# ---------------------------------------------------------------------------
# save_model_catalog
# ---------------------------------------------------------------------------

class TestSaveModelCatalog:

    def test_saves_json_to_catalog_path(self, monkeypatch, tmp_path):
        output_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: output_file)
        save_model_catalog(SAMPLE_CATALOG)
        assert output_file.exists()
        loaded = json.loads(output_file.read_text())
        assert loaded["config"]["pricing_unit"] == 1_000_000
        assert "gpt-4o" in loaded["models"]

    def test_saved_json_is_valid_and_round_trips(self, monkeypatch, tmp_path):
        output_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: output_file)
        save_model_catalog(SAMPLE_CATALOG)
        round_tripped = json.loads(output_file.read_text())
        assert round_tripped == SAMPLE_CATALOG


# ---------------------------------------------------------------------------
# load_model_catalog — missing file error mentions template
# ---------------------------------------------------------------------------

class TestLoadModelCatalogMissingFileError:

    def test_missing_file_error_mentions_template(self, monkeypatch, tmp_path):
        missing = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: missing)
        with pytest.raises(FileNotFoundError, match="model_catalog.template.json"):
            from src.models.catalog import load_model_catalog as lmc
            lmc()

    def test_missing_file_error_names_the_one_command_that_fixes_it(self, monkeypatch, tmp_path):
        """Setup creates the catalogue, so that is the whole answer.

        This used to also suggest passing 'openai/model-name' to -m to
        auto-register a model. That advice is unusable at this exact moment:
        auto-registering writes *into* the catalogue, which is the thing
        that isn't there. Offering someone a second option that cannot work
        makes the first one harder to find.
        """
        missing = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: missing)
        with pytest.raises(FileNotFoundError, match="settings setup"):
            from src.models.catalog import load_model_catalog as lmc
            lmc()


# ---------------------------------------------------------------------------
# add_model_to_catalog
# ---------------------------------------------------------------------------

from src.models import add_model_to_catalog  # noqa: E402


def _make_fake_fetch(input_price=2.5, output_price=10.0, supports_vision=None):
    """Return a fake _fetch_model_pricing that returns fixed prices."""
    def fake_fetch(provider_model, pricing_unit):
        result = {"input": input_price, "output": output_price}
        if supports_vision is not None:
            result["supports_vision"] = supports_vision
        return result
    return fake_fetch


class TestAddModelToCatalog:

    def test_missing_slash_raises_value_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: tmp_path / "model_catalog.json")
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", _make_fake_fetch())
        with pytest.raises(ValueError, match="provider/model-name"):
            add_model_to_catalog("gpt-4o")

    def test_creates_new_catalog_when_missing(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", _make_fake_fetch(2.5, 10.0))
        model_name, entry = add_model_to_catalog("openai/gpt-4o")
        assert model_name == "gpt-4o"
        assert entry["input"] == 2.5
        assert entry["output"] == 10.0
        assert entry["portkey_id"] == "openai/gpt-4o"
        assert catalog_file.exists()

    def test_updates_existing_catalog(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps(SAMPLE_CATALOG))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", _make_fake_fetch(1.0, 3.0))
        model_name, entry = add_model_to_catalog("openai/new-model")
        assert model_name == "new-model"
        loaded = json.loads(catalog_file.read_text())
        assert "new-model" in loaded["models"]
        assert "gpt-4o" in loaded["models"]  # existing model preserved

    def test_vision_defaults_to_false_when_not_in_api_response(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", _make_fake_fetch())
        _, entry = add_model_to_catalog("openai/text-only")
        assert entry["supports_vision"] is False

    def test_vision_populated_from_api_response(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", _make_fake_fetch(supports_vision=True))
        _, entry = add_model_to_catalog("openai/gpt-4o")
        assert entry["supports_vision"] is True

    def test_supports_vision_preserved_from_existing_entry(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        existing = {
            "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
            "models": {
                "gpt-4o": {
                    "input": 2.5, "output": 10.0,
                    "supports_vision": True, "portkey_id": "openai/gpt-4o",
                }
            },
        }
        catalog_file.write_text(json.dumps(existing))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        # API response does not include vision info; existing value should remain True
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", _make_fake_fetch(3.0, 12.0))
        _, entry = add_model_to_catalog("openai/gpt-4o")
        assert entry["supports_vision"] is True

    def test_auto_fetch_uses_portkey(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        def fake_fetch(provider_model, pricing_unit):
            assert provider_model == "openai/gpt-4o"
            return {"input": 2.5, "output": 10.0, "supports_vision": True}

        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", fake_fetch)
        model_name, entry = add_model_to_catalog("openai/gpt-4o")
        assert model_name == "gpt-4o"
        assert entry["input"] == 2.5
        assert entry["supports_vision"] is True

    def test_auto_fetch_error_propagates(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        def fake_fetch_fail(provider_model, pricing_unit):
            raise RuntimeError("network error")

        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", fake_fetch_fail)
        with pytest.raises(RuntimeError, match="network error"):
            add_model_to_catalog("openai/gpt-4o")

    def test_fetched_prices_stored_as_returned(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(
            pricing_module, "_fetch_model_pricing",
            _make_fake_fetch(2.1235, 9.9877),
        )
        _, entry = add_model_to_catalog("openai/gpt-4o")
        assert entry["input"] == 2.1235
        assert entry["output"] == 9.9877

    def test_slash_in_model_name_part_preserved(self, monkeypatch, tmp_path):
        """provider/org/model-name: only the first slash splits provider from model key."""
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", _make_fake_fetch(1.25, 10.0))
        model_name, entry = add_model_to_catalog("google/gemini/2.5-pro")
        assert model_name == "gemini/2.5-pro"
        assert entry["portkey_id"] == "google/gemini/2.5-pro"


# ---------------------------------------------------------------------------
# save_model_catalog — exception cleanup path (lines 76-81 of catalog.py)
# ---------------------------------------------------------------------------

class TestSaveModelCatalogExceptionPath:

    def test_temp_file_cleaned_up_on_write_failure(self, monkeypatch, tmp_path):
        """If writing the temp file fails the OSError is re-raised and no tmp file lingers."""
        import os
        import tempfile as _tempfile

        output_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: output_file)

        tmp_created = []

        original_mkstemp = _tempfile.mkstemp

        def fake_mkstemp(dir=None, suffix=None):
            fd, path = original_mkstemp(dir=dir, suffix=suffix)
            tmp_created.append(path)
            return fd, path

        # Make os.replace fail after the temp file is written

        def bad_replace(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(_tempfile, "mkstemp", fake_mkstemp)
        monkeypatch.setattr(os, "replace", bad_replace)
        monkeypatch.setattr(catalog_module.os, "replace", bad_replace)

        with pytest.raises(OSError, match="simulated replace failure"):
            save_model_catalog(SAMPLE_CATALOG)

    def test_unlink_oserror_is_suppressed_and_original_exception_reraised(
        self, monkeypatch, tmp_path
    ):
        """If both os.replace and os.unlink fail, the OSError from unlink is
        suppressed and the original replace failure is re-raised."""

        output_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: output_file)

        def bad_replace(src, dst):
            raise OSError("simulated replace failure")

        def bad_unlink(path):
            raise OSError("simulated unlink failure")

        monkeypatch.setattr(catalog_module.os, "replace", bad_replace)
        monkeypatch.setattr(catalog_module.os, "unlink", bad_unlink)

        with pytest.raises(OSError, match="simulated replace failure"):
            save_model_catalog(SAMPLE_CATALOG)


# ---------------------------------------------------------------------------
# _fetch_model_pricing — zero-price raises RuntimeError (pricing.py line 46)
# ---------------------------------------------------------------------------



class TestFetchModelPricing:

    def _make_urlopen_mock(self, payload: dict):
        """Return a context manager that yields a readable response with *payload*."""
        import unittest.mock as _mock

        body = json.dumps(payload).encode()
        cm = _mock.MagicMock()
        cm.__enter__ = _mock.Mock(return_value=_mock.MagicMock(read=_mock.Mock(return_value=body)))
        cm.__exit__ = _mock.Mock(return_value=False)
        return cm

    def test_zero_prices_raise_runtime_error(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps(SAMPLE_CATALOG))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        payload = {"pay_as_you_go": {
            "request_token": {"price": 0},
            "response_token": {"price": 0},
        }}
        with patch("urllib.request.urlopen", return_value=self._make_urlopen_mock(payload)):
            with pytest.raises(RuntimeError, match="No valid pricing data"):
                from src.models.pricing import _fetch_model_pricing as _fmp
                _fmp("openai/gpt-4o", 1_000_000)


# ---------------------------------------------------------------------------
# add_model_to_catalog — corrupt catalog JSON falls back to empty (lines 84-85)
# ---------------------------------------------------------------------------

class TestAddModelToCatalogCorruptCatalog:

    def test_corrupt_catalog_json_creates_fresh_catalog(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text("{ not valid json }")
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", _make_fake_fetch(2.0, 8.0))

        model_name, entry = add_model_to_catalog("openai/gpt-4o")
        assert model_name == "gpt-4o"
        assert entry["input"] == 2.0
        # Catalog file should be valid JSON now
        reloaded = json.loads(catalog_file.read_text())
        assert "gpt-4o" in reloaded["models"]


# ---------------------------------------------------------------------------
# maybe_sync_model_pricing — missing/stale logic (pricing.py lines 125-153)
# ---------------------------------------------------------------------------

import src.models.pricing as pricing_module_direct


class TestMaybeSyncModelPricing:
    """Tests for maybe_sync_model_pricing covering the paths not hit elsewhere."""

    def _build_catalog(self, portkey_id=None, last_sync=None):
        entry = {"input": 2.5, "output": 10.0}
        if portkey_id:
            entry["portkey_id"] = portkey_id
        if last_sync:
            entry["last_sync"] = last_sync
        return {
            "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
            "models": {"gpt-4o": entry},
        }

    def setup_method(self):
        """Clear the in-memory sync cache before each test."""
        pricing_module_direct._sync_cache.clear()

    def test_model_without_portkey_id_caches_and_returns(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        cat = self._build_catalog()  # no portkey_id
        catalog_file.write_text(json.dumps(cat))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)

        from src.models.pricing import maybe_sync_model_pricing
        maybe_sync_model_pricing("gpt-4o")  # should not raise or make network calls
        assert "gpt-4o" in pricing_module_direct._sync_cache

    def test_stale_timestamp_triggers_sync(self, monkeypatch, tmp_path):
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        catalog_file = tmp_path / "model_catalog.json"
        cat = self._build_catalog(portkey_id="openai/gpt-4o", last_sync=old)
        catalog_file.write_text(json.dumps(cat))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        monkeypatch.setattr(
            pricing_module_direct, "_fetch_model_pricing",
            lambda provider_model, pricing_unit: {"input": 3.0, "output": 12.0},
        )
        # Patch save_model_catalog to capture what would be saved
        saved = {}

        def fake_save(c):
            saved.update(c)

        monkeypatch.setattr(catalog_module, "save_model_catalog", fake_save)

        from src.models.pricing import maybe_sync_model_pricing
        maybe_sync_model_pricing("gpt-4o")

        assert saved.get("models", {}).get("gpt-4o", {}).get("input") == 3.0

    def test_invalid_last_sync_timestamp_triggers_sync(self, monkeypatch, tmp_path):
        """A non-ISO-format timestamp should be treated as stale and trigger a sync."""
        catalog_file = tmp_path / "model_catalog.json"
        cat = self._build_catalog(portkey_id="openai/gpt-4o", last_sync="not-a-date")
        catalog_file.write_text(json.dumps(cat))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        monkeypatch.setattr(
            pricing_module_direct, "_fetch_model_pricing",
            lambda pm, pu: {"input": 1.0, "output": 4.0},
        )
        saved = {}
        monkeypatch.setattr(catalog_module, "save_model_catalog", lambda c: saved.update(c))

        from src.models.pricing import maybe_sync_model_pricing
        maybe_sync_model_pricing("gpt-4o")
        assert saved.get("models", {}).get("gpt-4o", {}).get("input") == 1.0

    def test_sync_network_error_logs_warning_and_does_not_raise(self, monkeypatch, tmp_path, caplog):
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        cat = self._build_catalog(portkey_id="openai/gpt-4o", last_sync=old)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        monkeypatch.setattr(
            pricing_module_direct, "_fetch_model_pricing",
            lambda pm, pu: (_ for _ in ()).throw(ConnectionError("network down")),
        )

        with caplog.at_level(logging.WARNING):
            from src.models.pricing import maybe_sync_model_pricing
            maybe_sync_model_pricing("gpt-4o")  # must not raise
        assert any("Could not sync pricing" in r.message for r in caplog.records)

    def test_in_memory_cache_prevents_redundant_disk_reads(self, monkeypatch):
        from datetime import datetime
        pricing_module_direct._sync_cache["gpt-4o"] = datetime.now()

        load_calls = []
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: load_calls.append(1) or {})

        from src.models.pricing import maybe_sync_model_pricing
        maybe_sync_model_pricing("gpt-4o")
        assert load_calls == []  # cache hit — no disk read

    def test_recent_timestamp_populates_cache_without_fetch(self, monkeypatch, tmp_path):
        """When last_sync is fresh (< 1 hour old), pricing should NOT be re-fetched."""
        from datetime import datetime, timedelta
        recent = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        cat = self._build_catalog(portkey_id="openai/gpt-4o", last_sync=recent)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)

        fetch_calls = []
        monkeypatch.setattr(
            pricing_module_direct, "_fetch_model_pricing",
            lambda pm, pu: fetch_calls.append(1) or {"input": 9.0, "output": 9.0},
        )

        from src.models.pricing import maybe_sync_model_pricing
        maybe_sync_model_pricing("gpt-4o")

        # Fresh timestamp → cache populated, no network call
        assert fetch_calls == []
        assert "gpt-4o" in pricing_module_direct._sync_cache


# ---------------------------------------------------------------------------
# remove_model_from_catalog — lines 206-212 of catalog.py
# ---------------------------------------------------------------------------

class TestRemoveModelFromCatalog:

    def test_removes_existing_model_and_returns_true(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        saved = {}
        monkeypatch.setattr(catalog_module, "save_model_catalog", lambda c: saved.update(c))
        result = remove_model_from_catalog("gpt-4o-mini")
        assert result is True
        assert "gpt-4o-mini" not in saved.get("models", {})

    def test_returns_false_when_model_absent(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        result = remove_model_from_catalog("does-not-exist")
        assert result is False


# ---------------------------------------------------------------------------
# is_model_access_error — line 219 of catalog.py
# ---------------------------------------------------------------------------

class TestIsModelAccessError:

    def test_returns_true_for_portkey_router_message(self):
        from src.models.catalog import is_model_access_error
        msg = "Invalid target name found in the query router"
        assert is_model_access_error(msg) is True

    def test_returns_false_for_unrelated_error(self):
        from src.models.catalog import is_model_access_error
        msg = "Rate limit exceeded"
        assert is_model_access_error(msg) is False

    def test_case_insensitive_match(self):
        from src.models.catalog import is_model_access_error
        msg = "INVALID TARGET NAME FOUND IN THE QUERY ROUTER"
        assert is_model_access_error(msg) is True


# ---------------------------------------------------------------------------
# record_sampling_params_rejected
# ---------------------------------------------------------------------------

class TestRecordSamplingParamsRejected:

    def _catalog(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        saved = {}
        monkeypatch.setattr(catalog_module, "save_model_catalog", lambda c: saved.update(c))
        return cat, saved

    def test_records_every_sampling_field_as_refused(self, monkeypatch):
        """All four together: a model that refuses one refuses the rest."""
        cat, saved = self._catalog(monkeypatch)
        assert record_sampling_params_rejected("gpt-4o") is True
        assert set(cat["models"]["gpt-4o"]["rejects"]) == set(catalog_module._SAMPLING_FIELDS)

    def test_leaves_pricing_untouched(self, monkeypatch):
        cat, _saved = self._catalog(monkeypatch)
        record_sampling_params_rejected("gpt-4o")
        assert cat["models"]["gpt-4o"]["input"] == 2.75

    def test_returns_false_when_already_recorded(self, monkeypatch):
        cat, _saved = self._catalog(monkeypatch)
        record_sampling_params_rejected("gpt-4o")
        assert record_sampling_params_rejected("gpt-4o") is False

    def test_returns_false_for_an_unknown_model(self, monkeypatch):
        self._catalog(monkeypatch)
        assert record_sampling_params_rejected("does-not-exist") is False


# ---------------------------------------------------------------------------
# is_sampling_param_deprecated_error
# ---------------------------------------------------------------------------

class TestIsSamplingParamDeprecatedError:

    def test_returns_true_for_temperature_deprecated(self):
        msg = "azure-ai error: `temperature` is deprecated for this model."
        assert is_sampling_param_deprecated_error(msg) is True

    def test_returns_true_for_top_p_deprecated(self):
        msg = "azure-ai error: `top_p` is deprecated for this model."
        assert is_sampling_param_deprecated_error(msg) is True

    def test_case_insensitive_match(self):
        msg = "AZURE-AI ERROR: `TEMPERATURE` IS DEPRECATED FOR THIS MODEL."
        assert is_sampling_param_deprecated_error(msg) is True

    def test_returns_false_for_unrelated_invalid_request(self):
        msg = "Error code: 400 - {'type': 'invalid_request_error', 'message': 'bad request'}"
        assert is_sampling_param_deprecated_error(msg) is False

    def test_returns_false_when_deprecated_but_not_a_sampling_param(self):
        """'deprecated for this model' alone (about some other field) shouldn't match."""
        msg = "the `functions` field is deprecated for this model."
        assert is_sampling_param_deprecated_error(msg) is False



# ---------------------------------------------------------------------------
# Learned per-model request quirks: "rejects"
# ---------------------------------------------------------------------------

class TestModelRejectedFields:

    def test_absent_means_nothing_rejected(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        assert model_rejected_fields("gpt-4o") == {}

    def test_unknown_model_means_nothing_rejected(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        assert model_rejected_fields("never-heard-of-it") == {}

    def test_returns_recorded_fields(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        cat["models"]["gpt-4o"]["rejects"] = {"stream_options": "2026-07-29: nope"}
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        assert model_rejected_fields("gpt-4o") == {"stream_options": "2026-07-29: nope"}

    def test_a_malformed_rejects_value_is_ignored_not_raised(self, monkeypatch):
        """Someone hand-editing the catalog could write a list, or a string.

        A wrong type here must not stop every request for that model: the
        field is an optimisation, and the provider will say so again anyway.
        """
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        cat["models"]["gpt-4o"]["rejects"] = ["stream_options"]
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        assert model_rejected_fields("gpt-4o") == {}


class TestRecordRejectedField:

    def _catalog(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        saved = {}
        monkeypatch.setattr(catalog_module, "save_model_catalog", lambda c: saved.update(c))
        return cat, saved

    def test_records_field_with_a_dated_reason(self, monkeypatch):
        _cat, saved = self._catalog(monkeypatch)
        assert record_rejected_field("gpt-4o", "stream_options", "Extra inputs are not permitted") is True
        note = saved["models"]["gpt-4o"]["rejects"]["stream_options"]
        assert "Extra inputs are not permitted" in note
        # Dated so a reader can judge how old the belief is.
        from datetime import datetime
        assert note.startswith(datetime.now().strftime("%Y-%m-%d"))

    def test_leaves_other_fields_untouched(self, monkeypatch):
        _cat, saved = self._catalog(monkeypatch)
        record_rejected_field("gpt-4o", "stream_options", "nope")
        assert saved["models"]["gpt-4o"]["input"] == 2.75
        assert saved["models"]["gpt-4o"]["supports_vision"] is True

    def test_second_field_joins_the_first(self, monkeypatch):
        cat, saved = self._catalog(monkeypatch)
        record_rejected_field("gpt-4o", "stream_options", "nope")
        cat["models"]["gpt-4o"]["rejects"] = saved["models"]["gpt-4o"]["rejects"]
        record_rejected_field("gpt-4o", "presence_penalty", "also nope")
        assert set(saved["models"]["gpt-4o"]["rejects"]) == {"stream_options", "presence_penalty"}

    def test_already_recorded_returns_false_and_does_not_rewrite(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        cat["models"]["gpt-4o"]["rejects"] = {"stream_options": "2026-01-01: earlier note"}
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        save_called = False

        def fake_save(_c):
            nonlocal save_called
            save_called = True

        monkeypatch.setattr(catalog_module, "save_model_catalog", fake_save)
        assert record_rejected_field("gpt-4o", "stream_options", "a newer note") is False
        assert save_called is False
        # The original note survives — it records when this was first learned.
        assert cat["models"]["gpt-4o"]["rejects"]["stream_options"] == "2026-01-01: earlier note"

    def test_unknown_model_returns_false(self, monkeypatch):
        _cat, saved = self._catalog(monkeypatch)
        assert record_rejected_field("never-heard-of-it", "stream_options", "nope") is False
        assert saved == {}



class TestCheapestModel:

    def test_picks_the_lowest_combined_price(self, mock_catalog):
        assert cheapest_model() == "text-only-model"   # 0.1 + 0.3

    def test_vision_requirement_narrows_the_field(self, mock_catalog):
        assert cheapest_model(require_vision=True) == "gpt-4o-mini"   # 0.165 + 0.66

    def test_an_unpriced_model_is_skipped_not_treated_as_free(self, monkeypatch):
        """Both prices at zero is what a placeholder looks like, not a free model.

        The shipped catalog template contains exactly such an entry, so letting
        it win would quietly make an unpriced model the default for everything.
        """
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        cat["models"]["placeholder"] = {"input": 0.0, "output": 0.0, "supports_vision": True}
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        assert cheapest_model() == "text-only-model"
        assert cheapest_model(require_vision=True) == "gpt-4o-mini"

    def test_a_missing_price_field_is_skipped(self, monkeypatch):
        import copy
        cat = copy.deepcopy(SAMPLE_CATALOG)
        cat["models"]["half-priced"] = {"input": 0.01, "supports_vision": True}
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        assert cheapest_model() == "text-only-model"

    def test_none_when_nothing_qualifies(self, monkeypatch):
        cat = {"config": {}, "models": {"p": {"input": 0.0, "output": 0.0}}}
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        assert cheapest_model() is None

    def test_a_tie_resolves_the_same_way_every_time(self, monkeypatch):
        """Otherwise the answer depends on the order the file happened to be written in."""
        cat = {"config": {}, "models": {
            "zebra": {"input": 0.1, "output": 0.1, "supports_vision": True},
            "alpha": {"input": 0.1, "output": 0.1, "supports_vision": True},
        }}
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)
        assert cheapest_model() == "alpha"


# ---------------------------------------------------------------------------
# One shape for a model's quirks, whichever way the catalog spells them
# ---------------------------------------------------------------------------

class TestQuirksGatheredFromEitherSpelling:
    """A catalog may name quirks as individual flags; both spellings must work.

    This is the safety net for the change that introduced `rejects`/`prefers`:
    real catalogs carry the flag spelling on many entries, and reading them
    wrongly would silently send `temperature` to a model that refuses it.
    """

    def _catalog(self, monkeypatch, entry):
        cat = {"config": {}, "models": {"m": entry}}
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: cat)

    @pytest.mark.parametrize("flag", ["fixed_parameters", "omit_sampling_params"])
    def test_a_sampling_flag_means_every_sampling_field_is_refused(self, monkeypatch, flag):
        self._catalog(monkeypatch, {"input": 1.0, "output": 1.0, flag: True})
        assert set(model_rejected_fields("m")) == set(catalog_module._SAMPLING_FIELDS)
        assert model_accepts_sampling_params("m") is False

    @pytest.mark.parametrize("flag", ["fixed_parameters", "omit_sampling_params"])
    def test_a_sampling_flag_set_false_refuses_nothing(self, monkeypatch, flag):
        self._catalog(monkeypatch, {"input": 1.0, "output": 1.0, flag: False})
        assert model_rejected_fields("m") == {}
        assert model_accepts_sampling_params("m") is True

    def test_the_token_field_flag_becomes_the_field_name(self, monkeypatch):
        """It was never a yes/no question — it is which of two names to use."""
        self._catalog(monkeypatch, {"input": 1.0, "output": 1.0, "use_max_completion_tokens": True})
        assert model_preferences("m")["max_tokens_field"] == "max_completion_tokens"
        assert model_max_tokens_field("m") == "max_completion_tokens"

    def test_system_role_is_carried_as_a_value(self, monkeypatch):
        self._catalog(monkeypatch, {"input": 1.0, "output": 1.0, "system_role": "developer"})
        assert model_preferences("m")["system_role"] == "developer"
        assert get_model_system_role("m") == "developer"

    def test_the_canonical_spelling_is_read_directly(self, monkeypatch):
        self._catalog(monkeypatch, {
            "input": 1.0, "output": 1.0,
            "rejects": {"stream_options": "2026-07-29: refused"},
            "prefers": {"system_role": "developer", "max_tokens_field": "max_completion_tokens"},
        })
        assert model_rejected_fields("m") == {"stream_options": "2026-07-29: refused"}
        assert get_model_system_role("m") == "developer"
        assert model_max_tokens_field("m") == "max_completion_tokens"

    def test_both_spellings_at_once_keep_both_sets_of_information(self, monkeypatch):
        self._catalog(monkeypatch, {
            "input": 1.0, "output": 1.0,
            "fixed_parameters": True,
            "rejects": {"stream_options": "learned"},
        })
        rejected = model_rejected_fields("m")
        assert "stream_options" in rejected
        assert set(catalog_module._SAMPLING_FIELDS) <= set(rejected)

    def test_the_canonical_entry_wins_a_disagreement(self, monkeypatch):
        """`rejects`/`prefers` is what the sandbox learns into, so it is newer."""
        self._catalog(monkeypatch, {
            "input": 1.0, "output": 1.0,
            "system_role": "system",
            "prefers": {"system_role": "developer"},
        })
        assert get_model_system_role("m") == "developer"

    def test_an_entry_with_no_quirks_is_untouched(self, monkeypatch):
        self._catalog(monkeypatch, {"input": 1.0, "output": 1.0, "supports_vision": True})
        assert model_rejected_fields("m") == {}
        assert model_preferences("m") == {}
        assert get_model_system_role("m") == "system"
        assert model_max_tokens_field("m") == "max_tokens"
        assert model_accepts_sampling_params("m") is True

    @pytest.mark.parametrize("entry", ["not-a-dict", 42, None, []])
    def test_a_junk_entry_does_not_raise(self, monkeypatch, entry):
        """Hand-edited file: a wrong type must not break every request."""
        self._catalog(monkeypatch, entry)
        assert model_rejected_fields("m") == {}
        assert model_preferences("m") == {}
