"""Tests for src/models/pricing.py: looking up a model's prices and adding it to the catalog."""

import json
import logging
from unittest.mock import patch

import pytest

import src.models.catalog as catalog_module
import src.models.pricing as pricing_module


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
            "last_tested": "2026-08-03T18:19:02",
        },
        "my_cluster:llama-3-70b": {
            "endpoint": "my_cluster",
            "model": "llama-3-70b",
        },
    },
}


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

# ---------------------------------------------------------------------------
# maybe_sync_model_pricing — missing/stale logic (pricing.py lines 125-153)
# ---------------------------------------------------------------------------

import src.models.pricing as pricing_module_direct


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


class TestAnAutoAddedModelAnnouncesWhatItCannotDo:
    """Why every automatically added model arrives unable to read images.

    PortKey's pricing service reports prices and nothing else, so the flag
    falls back to false. Chat requires vision, so such a model is refused there
    until someone edits the catalog — and nothing used to say so.
    """

    def test_it_warns_that_the_flag_was_assumed(self, monkeypatch, tmp_path, caplog):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing",
                            lambda pm, unit: {"input": 1.0, "output": 2.0})
        with caplog.at_level(logging.WARNING):
            _, entry = add_model_to_catalog("openai/some-new-model")
        assert entry["supports_vision"] is False
        assert "chat" in caplog.text.lower()
        # Names the command that settles it, rather than asking anyone to open
        # the catalog — which is the whole point of testing on add.
        assert "settings test-model" in caplog.text

    def test_it_says_nothing_when_the_answer_was_reported(self, monkeypatch, tmp_path, caplog):
        catalog_file = tmp_path / "model_catalog.json"
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing",
                            lambda pm, unit: {"input": 1.0, "output": 2.0,
                                              "supports_vision": True})
        with caplog.at_level(logging.WARNING):
            _, entry = add_model_to_catalog("openai/seeing-model")
        assert entry["supports_vision"] is True
        assert "supports_vision" not in caplog.text

    def test_an_answer_already_in_the_catalog_is_not_overwritten(
        self, monkeypatch, tmp_path, caplog
    ):
        """Correcting the flag by hand must survive the next price refresh."""
        catalog_file = tmp_path / "model_catalog.json"
        catalog_file.write_text(json.dumps({
            "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
            "models": {"corrected": {"input": 1.0, "output": 2.0,
                                     "supports_vision": True}},
        }))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: catalog_file)
        monkeypatch.setattr(pricing_module, "_fetch_model_pricing",
                            lambda pm, unit: {"input": 9.0, "output": 9.0})
        with caplog.at_level(logging.WARNING):
            _, entry = add_model_to_catalog("openai/corrected")
        assert entry["supports_vision"] is True
        assert "supports_vision" not in caplog.text
