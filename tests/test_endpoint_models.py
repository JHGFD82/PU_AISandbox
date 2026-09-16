"""Tests for src/models/endpoint_models.py — endpoint models kept in the catalog."""

import json
from types import SimpleNamespace

import pytest

import src.models.catalog as catalog_module
import src.models.endpoint_models as endpoint_models
import src.settings as settings_mod
import src.settings_store as settings_store


CLUSTER = {
    "name": "My HPC Cluster",
    "base_url": "http://my-cluster.internal:8000/v1",
    "default_model": "llama-3-70b",
}

SANDBOX_MODEL = {"input": 0.15, "output": 0.6, "supports_vision": True}


@pytest.fixture
def catalog_file(monkeypatch, tmp_path):
    """A catalog holding one sandbox model, with one endpoint defined and no key."""
    path = tmp_path / "model_catalog.json"
    path.write_text(json.dumps({
        "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
        "models": {"gpt-4o-mini": dict(SANDBOX_MODEL)},
    }))
    monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: path)
    monkeypatch.setattr(catalog_module, "_catalog_cache", None)
    monkeypatch.setattr(settings_mod, "ENDPOINTS", {"my_cluster": dict(CLUSTER)})
    monkeypatch.setattr(settings_store, "get_value", lambda _path: None)
    monkeypatch.setattr(endpoint_models, "_last_asked", {})
    monkeypatch.setattr(endpoint_models, "_last_answer", {})
    monkeypatch.setattr(endpoint_models, "_default_reported", set())
    return path


def _models(path):
    return json.loads(path.read_text())["models"]


def _endpoint_answers(monkeypatch, *names, fails=False):
    """Make every endpoint answer with *names*, or fail to answer; count the asking."""
    asked = []

    def list_them(config):
        asked.append(config.api_name)
        if fails:
            raise ConnectionError("switched off")
        return sorted(names)

    monkeypatch.setattr(endpoint_models, "models_on_endpoint", list_them)
    return asked


class TestAddingWhatAnEndpointRuns:
    def test_each_listed_model_is_added_under_the_name_m_takes(self, catalog_file, monkeypatch):
        _endpoint_answers(monkeypatch, "llama-3-70b", "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        models = _models(catalog_file)
        assert "my_cluster:llama-3-70b" in models
        assert "my_cluster:qwen-2.5-72b" in models

    def test_an_entry_says_where_it_runs_and_carries_no_price(self, catalog_file, monkeypatch):
        """Calls to an endpoint are counted but never costed, and a price of any
        kind would put it in the running for "cheapest model" too."""
        _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        entry = _models(catalog_file)["my_cluster:qwen-2.5-72b"]
        assert entry["endpoint"] == "my_cluster"
        assert entry["model"] == "qwen-2.5-72b"
        assert "input" not in entry and "output" not in entry

    def test_the_default_model_is_added_even_if_it_is_not_listed(self, catalog_file, monkeypatch):
        _endpoint_answers(monkeypatch, fails=True)
        endpoint_models.sync_endpoint_models()
        assert "my_cluster:llama-3-70b" in _models(catalog_file)

    def test_sandbox_models_are_left_alone(self, catalog_file, monkeypatch):
        _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        assert _models(catalog_file)["gpt-4o-mini"] == SANDBOX_MODEL

    def test_an_endpoint_marked_not_openai_compatible_is_not_asked(self, catalog_file, monkeypatch):
        monkeypatch.setattr(
            settings_mod, "ENDPOINTS", {"odd": dict(CLUSTER, openai_compatible=False)}
        )
        asked = _endpoint_answers(monkeypatch, "anything")
        endpoint_models.sync_endpoint_models()
        assert asked == []
        assert not any(name.startswith("odd:") for name in _models(catalog_file))


class TestTakingOutWhatCannotBeUsed:
    def test_a_model_the_endpoint_stops_listing_is_taken_out(self, catalog_file, monkeypatch):
        _endpoint_answers(monkeypatch, "llama-3-70b", "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        _endpoint_answers(monkeypatch, "llama-3-70b")
        endpoint_models.sync_endpoint_models(force=True)
        models = _models(catalog_file)
        assert "my_cluster:qwen-2.5-72b" not in models
        assert "my_cluster:llama-3-70b" in models

    def test_nothing_is_taken_out_when_the_endpoint_does_not_answer(self, catalog_file, monkeypatch):
        """A cluster down for the afternoon has not stopped running its models."""
        _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        _endpoint_answers(monkeypatch, fails=True)
        endpoint_models.sync_endpoint_models(force=True)
        assert "my_cluster:qwen-2.5-72b" in _models(catalog_file)

    def test_an_empty_answer_takes_nothing_out(self, catalog_file, monkeypatch):
        _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        _endpoint_answers(monkeypatch)
        endpoint_models.sync_endpoint_models(force=True)
        assert "my_cluster:qwen-2.5-72b" in _models(catalog_file)

    def test_a_default_model_the_endpoint_does_not_list_is_not_added(self, catalog_file, monkeypatch, caplog):
        """default_model = "gemma4" beside Ollama's gemma4:12b-mlx put the same
        model in the catalog twice, and the untagged one could not be used."""
        import logging

        _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        with caplog.at_level(logging.WARNING):
            endpoint_models.sync_endpoint_models()
            endpoint_models.sync_endpoint_models(force=True)
        assert set(_models(catalog_file)) == {"gpt-4o-mini", "my_cluster:qwen-2.5-72b"}
        # Said, with the models it does run, and said once.
        assert caplog.text.count("does not run a model by that name") == 1
        assert "qwen-2.5-72b" in caplog.text

    def test_a_default_model_added_before_the_endpoint_answered_is_taken_out(
        self, catalog_file, monkeypatch
    ):
        _endpoint_answers(monkeypatch, fails=True)
        endpoint_models.sync_endpoint_models()
        assert "my_cluster:llama-3-70b" in _models(catalog_file)
        _endpoint_answers(monkeypatch, "llama-3-70b-instruct")
        endpoint_models.sync_endpoint_models(force=True)
        assert set(_models(catalog_file)) == {"gpt-4o-mini", "my_cluster:llama-3-70b-instruct"}

    def test_ollamas_latest_tag_is_the_same_model(self, catalog_file, monkeypatch, caplog):
        """Ollama answers to llama-3-70b for llama-3-70b:latest."""
        import logging

        _endpoint_answers(monkeypatch, "llama-3-70b:latest")
        with caplog.at_level(logging.WARNING):
            endpoint_models.sync_endpoint_models()
        assert set(_models(catalog_file)) == {"gpt-4o-mini", "my_cluster:llama-3-70b:latest"}
        assert "does not run a model by that name" not in caplog.text

    def test_between_askings_the_last_answer_still_holds(self, catalog_file, monkeypatch):
        """Asked at most hourly, so without this a stray default reappears for
        the rest of the hour."""
        _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        endpoint_models.sync_endpoint_models()  # not asked again
        assert "my_cluster:llama-3-70b" not in _models(catalog_file)

    def test_models_of_an_endpoint_no_longer_defined_are_taken_out(self, catalog_file, monkeypatch):
        _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        monkeypatch.setattr(settings_mod, "ENDPOINTS", {})
        endpoint_models.sync_endpoint_models(force=True)
        assert set(_models(catalog_file)) == {"gpt-4o-mini"}

    def test_what_was_added_by_hand_stays(self, catalog_file, monkeypatch):
        _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        data = json.loads(catalog_file.read_text())
        data["models"]["my_cluster:qwen-2.5-72b"]["supports_vision"] = True
        catalog_file.write_text(json.dumps(data))
        endpoint_models.sync_endpoint_models(force=True)
        assert _models(catalog_file)["my_cluster:qwen-2.5-72b"]["supports_vision"] is True


class TestHowOftenItAsks:
    def test_the_same_process_does_not_ask_again_within_the_hour(self, catalog_file, monkeypatch):
        """The web interface asks each time a list is shown."""
        asked = _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        endpoint_models.sync_endpoint_models()
        assert asked == ["my_cluster"]

    def test_an_endpoint_that_failed_is_not_waited_on_every_time(self, catalog_file, monkeypatch):
        asked = _endpoint_answers(monkeypatch, fails=True)
        endpoint_models.sync_endpoint_models()
        endpoint_models.sync_endpoint_models()
        assert asked == ["my_cluster"]

    def test_force_asks_now(self, catalog_file, monkeypatch):
        asked = _endpoint_answers(monkeypatch, "qwen-2.5-72b")
        endpoint_models.sync_endpoint_models()
        endpoint_models.sync_endpoint_models(force=True)
        assert asked == ["my_cluster", "my_cluster"]

    def test_no_catalog_yet_is_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            catalog_module, "get_model_catalog_path", lambda: tmp_path / "missing.json"
        )
        monkeypatch.setattr(catalog_module, "_catalog_cache", None)
        endpoint_models.sync_endpoint_models()  # does not raise


class TestAskingTheEndpoint:
    def test_it_asks_through_the_endpoints_own_connection(self, monkeypatch):
        from src.services.api_config import APIConfig

        built = {}

        def fake_client(config, timeout=None):
            built["timeout"] = timeout
            listing = [SimpleNamespace(id="b-model"), SimpleNamespace(id="a-model")]
            return SimpleNamespace(models=SimpleNamespace(list=lambda: listing))

        monkeypatch.setattr("src.services.api_config.endpoint_client", fake_client)
        config = APIConfig(api_name="c", display_name="C", base_url="http://x/v1", api_key="")
        assert endpoint_models.models_on_endpoint(config) == ["a-model", "b-model"]
        # Somebody is waiting on a list; a switched-off cluster costs seconds.
        assert built["timeout"] <= endpoint_models._LISTING_TIMEOUT_SECONDS


class TestRememberingAModelThatWasUsed:
    def test_a_name_the_endpoint_does_not_list_is_not_added(self, catalog_file, monkeypatch):
        _endpoint_answers(monkeypatch, "gemma4:12b-mlx")
        endpoint_models.sync_endpoint_models()
        assert not endpoint_models.remember_endpoint_model("my_cluster", "gemma4")
        assert "my_cluster:gemma4" not in _models(catalog_file)

    def test_a_model_named_with_the_colon_syntax_is_added(self, catalog_file):
        assert endpoint_models.remember_endpoint_model("my_cluster", "mistral-large")
        assert _models(catalog_file)["my_cluster:mistral-large"]["endpoint"] == "my_cluster"

    def test_it_is_added_only_once(self, catalog_file):
        endpoint_models.remember_endpoint_model("my_cluster", "mistral-large")
        assert not endpoint_models.remember_endpoint_model("my_cluster", "mistral-large")

    def test_no_catalog_does_not_stop_the_work(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            catalog_module, "get_model_catalog_path", lambda: tmp_path / "missing.json"
        )
        monkeypatch.setattr(catalog_module, "_catalog_cache", None)
        assert endpoint_models.remember_endpoint_model("my_cluster", "x") is False


class TestTheRestOfTheSandboxTellsThemApart:
    def test_the_cheapest_model_is_never_an_endpoints(self, catalog_file):
        from src.models import cheapest_model

        endpoint_models.remember_endpoint_model("my_cluster", "mistral-large")
        assert cheapest_model() == "gpt-4o-mini"

    def test_the_last_fallback_for_sandbox_work_skips_endpoint_models(self, catalog_file):
        """With no priced model at all, the resolver takes anything that fits —
        but a sandbox service cannot send anything to an endpoint."""
        from src.models import resolve_model

        data = json.loads(catalog_file.read_text())
        data["models"] = {
            "my_cluster:aaa": {"endpoint": "my_cluster", "model": "aaa"},
            "unpriced-sandbox-model": {},
        }
        catalog_file.write_text(json.dumps(data))
        assert resolve_model() == "unpriced-sandbox-model"

    def test_grouped_under_the_endpoints_own_name(self, catalog_file):
        from src.models import model_owner

        endpoint_models.remember_endpoint_model("my_cluster", "meta-llama/Llama-3-70B")
        assert model_owner("my_cluster:meta-llama/Llama-3-70B") == "My HPC Cluster"

    def test_endpoint_of_reads_the_entry(self, catalog_file):
        endpoint_models.remember_endpoint_model("my_cluster", "mistral-large")
        assert endpoint_models.endpoint_of("my_cluster:mistral-large") == "my_cluster"
        assert endpoint_models.endpoint_of("gpt-4o-mini") is None


class TestTestingAnEndpointModel:
    def test_it_is_tested_through_its_endpoint_by_its_own_name(self, catalog_file, monkeypatch):
        """Asking PortKey about my_cluster:llama-3-70b would be told there is no
        such model, and the entry would look out of date when it isn't."""
        from src.models.capabilities import testing_target

        endpoint_models.remember_endpoint_model("my_cluster", "meta-llama/Llama-3-70B")
        monkeypatch.setattr(
            "src.services.api_config.endpoint_client",
            lambda config, timeout=None: ("endpoint client", config.base_url),
        )
        client, asked_as = testing_target("my_cluster:meta-llama/Llama-3-70B", "sk-sandbox")
        assert client == ("endpoint client", CLUSTER["base_url"])
        assert asked_as == "meta-llama/Llama-3-70B"

    def test_a_sandbox_model_is_still_tested_through_portkey(self, catalog_file, monkeypatch):
        from src.models import capabilities

        monkeypatch.setattr(capabilities, "client_for_testing", lambda key: ("portkey", key))
        assert capabilities.testing_target("gpt-4o-mini", "sk-sandbox") == (
            ("portkey", "sk-sandbox"), "gpt-4o-mini"
        )

    def test_an_endpoint_no_longer_defined_says_so(self, catalog_file, monkeypatch):
        from src.models.capabilities import testing_target

        endpoint_models.remember_endpoint_model("my_cluster", "mistral-large")
        monkeypatch.setattr(settings_mod, "ENDPOINTS", {})
        with pytest.raises(ValueError, match="my_cluster"):
            testing_target("my_cluster:mistral-large", "sk-sandbox")
