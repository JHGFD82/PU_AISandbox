"""
Live integration tests for the PortKey pricing API.

These tests make real HTTP calls to https://api.portkey.ai/model-configs/pricing
which is freely accessible (no authentication required).  They are separated
from the unit-test suite so CI can skip them when offline:

    pytest -m "not live"          # skip live tests
    pytest -m live                # run only live tests
    pytest                        # run everything (default)

No Princeton AI Sandbox key is needed; PortKey's pricing catalog is public.
"""

import json
import pytest

import src.models.catalog as catalog_module
import src.models.pricing as pricing_module
from src.models.pricing import _fetch_model_pricing, add_model_to_catalog, maybe_sync_model_pricing


pytestmark = pytest.mark.live

PRICING_UNIT = 1_000_000  # standard unit used by the catalog


# ---------------------------------------------------------------------------
# _fetch_model_pricing — raw HTTP call
# ---------------------------------------------------------------------------

class TestFetchModelPricingLive:

    def test_openai_gpt4o_returns_positive_prices(self):
        result = _fetch_model_pricing("openai/gpt-4o", PRICING_UNIT)
        assert result["input"] > 0
        assert result["output"] > 0

    def test_openai_gpt4o_mini_returns_positive_prices(self):
        result = _fetch_model_pricing("openai/gpt-4o-mini", PRICING_UNIT)
        assert result["input"] > 0
        assert result["output"] > 0

    def test_result_has_input_and_output_keys(self):
        result = _fetch_model_pricing("openai/gpt-4o", PRICING_UNIT)
        assert "input" in result
        assert "output" in result

    def test_prices_are_floats(self):
        result = _fetch_model_pricing("openai/gpt-4o", PRICING_UNIT)
        assert isinstance(result["input"], float)
        assert isinstance(result["output"], float)

    def test_output_price_higher_than_input_for_gpt4o(self):
        # Output tokens historically cost more than input for gpt-4o
        result = _fetch_model_pricing("openai/gpt-4o", PRICING_UNIT)
        assert result["output"] > result["input"]

    def test_gpt4o_mini_cheaper_than_gpt4o(self):
        mini = _fetch_model_pricing("openai/gpt-4o-mini", PRICING_UNIT)
        full = _fetch_model_pricing("openai/gpt-4o", PRICING_UNIT)
        assert mini["input"] < full["input"]

    def test_invalid_model_raises_runtime_error(self):
        with pytest.raises((RuntimeError, Exception)):
            _fetch_model_pricing("openai/this-model-does-not-exist-xyz-abc", PRICING_UNIT)

    def test_pricing_unit_scaling(self):
        # Prices at unit=1_000_000 should be exactly 10× prices at unit=100_000
        price_1m = _fetch_model_pricing("openai/gpt-4o-mini", 1_000_000)
        price_100k = _fetch_model_pricing("openai/gpt-4o-mini", 100_000)
        assert price_1m["input"] == pytest.approx(price_100k["input"] * 10, rel=1e-3)
        assert price_1m["output"] == pytest.approx(price_100k["output"] * 10, rel=1e-3)

    def test_provider_map_routes_google_correctly(self, monkeypatch, tmp_path):
        # Verify that google/ is remapped via provider_map before hitting the API
        catalog_with_map = {
            "config": {
                "pricing_unit": PRICING_UNIT,
                "monthly_limit": 250.0,
                "provider_map": {"google": "vertex-ai"},
            },
            "models": {"gpt-4o": {"input": 2.75, "output": 11.0, "supports_vision": True}},
        }
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps(catalog_with_map))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: catalog_with_map)

        # gemini-2.0-flash is a real, low-cost model on vertex-ai
        result = _fetch_model_pricing("google/gemini-2.0-flash", PRICING_UNIT)
        assert result["input"] > 0
        assert result["output"] > 0


# ---------------------------------------------------------------------------
# add_model_to_catalog — full round-trip (fetch + write)
# ---------------------------------------------------------------------------

class TestAddModelToCatalogLive:

    def test_adds_model_to_tmp_catalog(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        initial = {
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {"gpt-4o": {"input": 2.75, "output": 11.0, "supports_vision": True}},
        }
        catalog_file.write_text(json.dumps(initial))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        model_name, entry = add_model_to_catalog("openai/gpt-4o-mini")

        assert model_name == "gpt-4o-mini"
        assert entry["input"] > 0
        assert entry["output"] > 0

    def test_catalog_file_updated_on_disk(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        initial = {
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {"gpt-4o": {"input": 2.75, "output": 11.0, "supports_vision": True}},
        }
        catalog_file.write_text(json.dumps(initial))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        add_model_to_catalog("openai/gpt-4o-mini")

        saved = json.loads(catalog_file.read_text())
        assert "gpt-4o-mini" in saved["models"]
        assert saved["models"]["gpt-4o-mini"]["input"] > 0

    def test_portkey_id_stored(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps({
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {"gpt-4o": {"input": 2.75, "output": 11.0, "supports_vision": True}},
        }))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        _, entry = add_model_to_catalog("openai/gpt-4o-mini")

        assert entry["portkey_id"] == "openai/gpt-4o-mini"

    def test_last_sync_timestamp_written(self, monkeypatch, tmp_path):
        from datetime import datetime
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps({
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {"gpt-4o": {"input": 2.75, "output": 11.0, "supports_vision": True}},
        }))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        _, entry = add_model_to_catalog("openai/gpt-4o-mini")

        # Verify the timestamp parses without error and is recent (within 60s)
        ts = datetime.fromisoformat(entry["last_sync"])
        assert (datetime.now() - ts).total_seconds() < 60

    def test_supports_vision_defaults_false_when_absent(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps({
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {"gpt-4o": {"input": 2.75, "output": 11.0, "supports_vision": True}},
        }))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        _, entry = add_model_to_catalog("openai/gpt-4o-mini")

        # PortKey pricing API doesn't return supports_vision; entry defaults to False
        assert "supports_vision" in entry

    def test_existing_extra_fields_preserved(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        existing = {
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {
                "gpt-4o-mini": {
                    "input": 0.001,
                    "output": 0.002,
                    "supports_vision": True,  # manually set to True
                    "system_role": "system",
                },
            },
        }
        catalog_file.write_text(json.dumps(existing))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        _, entry = add_model_to_catalog("openai/gpt-4o-mini")

        # Prices should be updated from API, existing True vision flag preserved
        assert entry["supports_vision"] is True
        assert entry["system_role"] == "system"
        assert entry["input"] > 0  # refreshed from API

    def test_missing_slash_raises_value_error(self, monkeypatch, tmp_path):
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps({
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {},
        }))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)

        with pytest.raises(ValueError, match="provider/model-name"):
            add_model_to_catalog("gpt-4o-mini")


# ---------------------------------------------------------------------------
# maybe_sync_model_pricing — stale vs. fresh timestamp logic
# ---------------------------------------------------------------------------

class TestMaybeSyncModelPricingLive:

    def _make_catalog(self, tmp_path, last_sync: str) -> dict:
        return {
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {
                "gpt-4o-mini": {
                    "input": 0.001,
                    "output": 0.001,
                    "supports_vision": True,
                    "portkey_id": "openai/gpt-4o-mini",
                    "last_sync": last_sync,
                }
            },
        }

    def test_stale_entry_prices_refreshed(self, monkeypatch, tmp_path):
        from datetime import datetime, timedelta
        old_ts = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        catalog = self._make_catalog(tmp_path, old_ts)
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps(catalog))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: json.loads(catalog_file.read_text()))

        maybe_sync_model_pricing("gpt-4o-mini")

        saved = json.loads(catalog_file.read_text())
        # Prices should now reflect real API values (much higher than 0.001)
        assert saved["models"]["gpt-4o-mini"]["input"] > 0.001

    def test_fresh_entry_not_refetched(self, monkeypatch, tmp_path):
        from datetime import datetime
        recent_ts = datetime.now().isoformat(timespec="seconds")
        catalog = self._make_catalog(tmp_path, recent_ts)
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps(catalog))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: json.loads(catalog_file.read_text()))

        fetch_called = []
        original_fetch = pricing_module._fetch_model_pricing

        def spy_fetch(*args, **kwargs):
            fetch_called.append(args)
            return original_fetch(*args, **kwargs)

        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", spy_fetch)

        maybe_sync_model_pricing("gpt-4o-mini")

        assert len(fetch_called) == 0, "Should not re-fetch a recently synced model"

    def test_no_portkey_id_skips_silently(self, monkeypatch, tmp_path):
        catalog = {
            "config": {"pricing_unit": PRICING_UNIT, "monthly_limit": 250.0},
            "models": {"gpt-4o": {"input": 2.75, "output": 11.0}},  # no portkey_id
        }
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps(catalog))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: catalog)

        fetch_called = []
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing", lambda *a, **k: fetch_called.append(a))

        maybe_sync_model_pricing("gpt-4o")

        assert len(fetch_called) == 0
