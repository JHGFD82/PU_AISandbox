"""Tests for src/models/resolver.py: deciding which model a request actually runs on."""

import pytest

import src.models.catalog as catalog_module
import src.models.pricing as pricing_module
from src.errors import CLIError
from src.models import (
    resolve_model,
)
from src.runtime.model_role import ModelRole


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

@pytest.fixture()
def mock_catalog(monkeypatch):
    """Patch load_model_catalog to return SAMPLE_CATALOG without hitting disk."""
    monkeypatch.setattr(catalog_module, "load_model_catalog", lambda: SAMPLE_CATALOG)


# ---------------------------------------------------------------------------
# maybe_sync_model_pricing — missing/stale logic (pricing.py lines 125-153)
# ---------------------------------------------------------------------------


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
        with pytest.raises(CLIError, match="not in the catalog"):
            resolve_model(requested_model="unknown-model")

    def test_requested_model_not_vision_capable_raises(self, mock_catalog):
        with pytest.raises(CLIError, match="not able to read images"):
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
        with pytest.raises(CLIError, match="No model in the catalog can read images"):
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

        def fake_add(provider_model, api_key=None, probe=True):
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
        with pytest.raises(CLIError, match="Could not auto-register"):
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


class TestAModelThatCannotReadImagesSaysSo:
    """Reference 346a8eb5: chat refused claude-opus-4-8 and explained nothing.

    The resolver raised a plain error, so the web interface treated it as a
    fault in the sandbox and replaced it with a reference code. The message it
    had already written — naming the model and the reason — never reached the
    person, who saw eight hex digits instead.
    """

    def test_the_reason_is_a_user_facing_error(self, mock_catalog):
        """A CLIError is shown as-is; anything else becomes a reference code."""
        with pytest.raises(CLIError) as raised:
            resolve_model(requested_model="text-only-model", require_vision=True)
        assert "text-only-model" in str(raised.value)

    def test_it_says_which_file_to_change_and_what_to_put_in_it(self, mock_catalog):
        with pytest.raises(CLIError) as raised:
            resolve_model(requested_model="text-only-model", require_vision=True)
        message = str(raised.value)
        # Asked of the code rather than spelled out here: the point is that the
        # message names the file this installation actually reads, wherever
        # that is, so the reader can go and open it.
        assert str(catalog_module.get_model_catalog_path()) in message
        assert '"supports_vision": true' in message

    def test_it_does_not_tell_a_browser_to_type_a_command(self, mock_catalog):
        """This same text is shown in the web interface, which has no command line."""
        with pytest.raises(CLIError) as raised:
            resolve_model(requested_model="text-only-model", require_vision=True)
        assert "--list-models" not in str(raised.value)

    def test_the_no_models_at_all_message_is_a_sentence(self, mock_catalog, monkeypatch):
        """It read 'No able to read images models available' at one point.

        Reached when models exist and none of them can read images — a real
        search that found nothing. An empty catalog is a different answer
        (see below), because there was nothing to search.
        """
        # Models exist; none of them can read images. That is a search that
        # found nothing, which is what this message is for.
        monkeypatch.setattr(catalog_module, "model_supports_vision", lambda _m: False)
        monkeypatch.setattr(catalog_module, "cheapest_model", lambda **kw: None)
        with pytest.raises(CLIError) as raised:
            resolve_model(require_vision=True)
        assert "No model in the catalog can read images" in str(raised.value)

    def test_an_empty_catalog_is_told_apart_from_a_search_that_failed(
        self, mock_catalog, monkeypatch
    ):
        """"None of them can read images" is no use when there are none at all.

        The second says what to do; the first sends somebody looking through a
        catalog that is empty.
        """
        monkeypatch.setattr(catalog_module, "get_available_models", lambda: [])
        with pytest.raises(CLIError) as raised:
            resolve_model(require_vision=True)
        message = str(raised.value)
        assert "no models set up yet" in message
        assert "can read images" not in message
