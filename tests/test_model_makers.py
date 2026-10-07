"""Tests for src/models/makers.py — who makes which model, from OpenRouter's list."""

import json
from datetime import datetime, timedelta

import pytest

from src.models import makers

# A few entries shaped the way OpenRouter sends them.
_OPENROUTER_MODELS = [
    {"id": "qwen/qwen3-235b-a22b", "name": "Qwen: Qwen3 235B A22B"},
    {"id": "qwen/qwen-2.5-72b-instruct:free", "name": "Qwen: Qwen2.5 72B Instruct (free)"},
    {"id": "google/gemma-3-27b-it", "name": "Google: Gemma 3 27B"},
    {"id": "google/gemini-2.5-pro", "name": "Google: Gemini 2.5 Pro"},
    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Meta: Llama 3.3 70B Instruct"},
    {"id": "meta-llama/llama-4-maverick", "name": "Meta: Llama 4 Maverick"},
    # Somebody's adjusted copy of a Llama. One copy must not take the family.
    {"id": "nousresearch/llama-hermes", "name": "Nous: Llama Hermes"},
    {"id": "~anthropic/claude-sonnet-latest", "name": "Anthropic: Claude Sonnet Latest"},
    {"id": "openrouter/auto", "name": "Auto Router"},
]


class TestBoilingTheListDown:
    def test_a_company_goes_by_the_name_before_the_colon(self):
        summary = makers.summarise(_OPENROUTER_MODELS)
        assert summary["makers"]["meta-llama"] == "Meta"
        assert summary["makers"]["qwen"] == "Qwen"

    def test_a_family_belongs_to_whoever_has_most_of_it(self):
        assert makers.summarise(_OPENROUTER_MODELS)["families"]["llama"] == "meta-llama"

    def test_the_newest_of_alias_is_the_same_company(self):
        assert makers.summarise(_OPENROUTER_MODELS)["models"]["claude-sonnet-latest"] == "anthropic"

    def test_openrouters_own_router_makes_nothing(self):
        summary = makers.summarise(_OPENROUTER_MODELS)
        assert "openrouter" not in summary["makers"]
        assert "auto" not in summary["models"]

    def test_a_variant_after_a_colon_is_the_same_model(self):
        assert "qwen-2.5-72b-instruct" in makers.summarise(_OPENROUTER_MODELS)["models"]


class TestFindingAModelsMaker:
    """Against the tests' stand-in list, tests/fixtures/model_makers.json."""

    @pytest.mark.parametrize("name, maker", [
        ("gpt-4o", "OpenAI"),                      # listed by its own name
        ("qwen3.8:27b-mlx", "Qwen"),               # somebody's own computer
        ("gemma4:12b-mlx", "Google"),
        ("Llama-3.3-70B-Instruct", "Meta"),        # capitals are no obstacle
        ("gpt-oss:20b", "OpenAI"),
        ("o3-mini", "OpenAI"),
        ("meta-llama/Llama-3-70B", "Meta"),        # its company in front
    ])
    def test_it_is_found(self, name, maker):
        assert makers.model_maker(name) == maker

    def test_one_nobody_lists_is_not_guessed(self):
        assert makers.model_maker("strange-thing") is None

    def test_a_short_name_turns_into_the_name_a_company_goes_by(self):
        assert makers.maker_named("OpenAI") == "OpenAI"
        assert makers.maker_named("meta-llama") == "Meta"
        assert makers.maker_named("nobody") is None


class TestAskingOpenRouter:
    def _date_the_list(self, when):
        path = makers.makers_path()
        saved = json.loads(path.read_text())
        saved["fetched"] = when.isoformat(timespec="seconds")
        path.write_text(json.dumps(saved))

    def test_a_list_younger_than_a_month_is_not_asked_for_again(self, monkeypatch):
        asked = []
        monkeypatch.setattr(makers, "_fetch", lambda: asked.append(1) or {})
        makers.refresh_model_makers()
        assert asked == []

    def test_a_month_old_list_is_replaced(self, monkeypatch):
        self._date_the_list(datetime.now() - timedelta(days=31))
        monkeypatch.setattr(makers, "_fetch",
                            lambda: makers.summarise(_OPENROUTER_MODELS))
        makers.refresh_model_makers()
        assert makers.model_maker("gemma-3-27b-it") == "Google"
        assert makers.model_maker("gpt-4o") is None, "the old list should be gone"

    def test_with_no_list_at_all_it_is_asked_for(self, monkeypatch):
        makers.makers_path().unlink()
        monkeypatch.setattr(makers, "_fetch",
                            lambda: makers.summarise(_OPENROUTER_MODELS))
        makers.refresh_model_makers()
        assert makers.model_maker("qwen3.8:27b-mlx") == "Qwen"

    def test_when_openrouter_cannot_be_reached_the_old_list_stays(self):
        """The root conftest's _fetch always fails, as an offline one would."""
        self._date_the_list(datetime.now() - timedelta(days=31))
        makers.refresh_model_makers()
        assert makers.model_maker("gpt-4o") == "OpenAI"

    def test_after_a_failure_it_waits_before_asking_again(self, monkeypatch):
        """Or an offline computer would wait out the timeout on every list."""
        makers.makers_path().unlink()
        asked = []

        def failing():
            asked.append(1)
            raise RuntimeError("offline")

        monkeypatch.setattr(makers, "_fetch", failing)
        makers.refresh_model_makers()
        makers.refresh_model_makers()
        assert asked == [1]

    def test_a_damaged_saved_list_counts_as_none(self):
        makers.makers_path().write_text("{not json")
        assert makers.load_makers() is None
        assert makers.model_maker("gpt-4o") is None
