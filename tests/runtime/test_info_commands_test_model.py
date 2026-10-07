"""Tests for `settings test-model` — naming a model, and adding one that
isn't in the catalog yet.

Two things were wrong. A model named the way every other part of the sandbox
takes it — provider first, `anthropic/claude-sonnet-5` — was reported as
missing from a catalog that then listed `claude-sonnet-5` among what it knows.
And a model genuinely not in the catalog could only be refused, though asking
a provider what a model can do and writing the answer down is exactly what
adding one is.

Nothing here makes a real request: probing costs money, and what is being
tested is which path is taken, not what a provider says.
"""

import argparse
from unittest.mock import patch

import pytest

from src.errors import CLIError
from src.runtime.info_commands import _settings_test_model

IN_CATALOG = ["claude-sonnet-5", "gpt-4o", "gemini-2.5-pro"]


def _args(model=None, professor="jh43", remove_missing=False):
    return argparse.Namespace(model=model, professor=professor,
                              remove_missing=remove_missing)


@pytest.fixture
def sandbox():
    """Every outside thing this touches, replaced. Yields what got called."""
    calls = {}

    def remember(name):
        def recorder(*a, **k):
            calls.setdefault(name, []).append((a, k))
            if name == "add":
                return a[0].split("/", 1)[1], {}
            return None
        return recorder

    with patch("src.models.get_available_models", return_value=list(IN_CATALOG)), \
         patch("src.runtime.info_commands._key_for_testing", return_value="sk-test"), \
         patch("src.models.add_model_to_catalog", side_effect=remember("add")) as add, \
         patch("src.models.capabilities.client_for_testing", return_value=object()), \
         patch("src.models.capabilities.probe_model_capabilities") as probe, \
         patch("src.models.load_model_catalog", return_value={"models": {}}), \
         patch("src.models.save_model_catalog"):
        # The real shape, so what the handler does with it is what it would
        # really do — a stand-in missing a field passes for the wrong reason.
        from src.models.capabilities import CapabilityReport

        probe.return_value = CapabilityReport(
            findings={}, settled=[], unsettled=[], reachable=True, missing=False)
        calls["add_mock"] = add
        calls["probe_mock"] = probe
        yield calls


class TestNamingAModelTheWayEverythingElseTakesIt:

    def test_the_provider_form_finds_a_model_that_is_in_the_catalog(self, sandbox):
        _settings_test_model(_args("anthropic/claude-sonnet-5"))
        tried = [c[0][0] for c in sandbox["probe_mock"].call_args_list]
        assert tried == ["claude-sonnet-5"]
        sandbox["add_mock"].assert_not_called()

    def test_the_bare_name_still_works(self, sandbox):
        _settings_test_model(_args("claude-sonnet-5"))
        assert [c[0][0] for c in sandbox["probe_mock"].call_args_list] == ["claude-sonnet-5"]

    def test_naming_none_tests_them_all(self, sandbox):
        _settings_test_model(_args(None))
        tried = sorted(c[0][0] for c in sandbox["probe_mock"].call_args_list)
        assert tried == sorted(IN_CATALOG)


class TestAddingOneThatIsNotThereYet:

    def test_a_provider_named_model_is_added(self, sandbox):
        _settings_test_model(_args("anthropic/claude-opus-9"))
        sandbox["add_mock"].assert_called_once()
        assert sandbox["add_mock"].call_args[0][0] == "anthropic/claude-opus-9"

    def test_it_is_added_rather_than_tested(self, sandbox):
        """add_model_to_catalog probes on its own; probing again would be a
        second set of paid requests for the same answers."""
        _settings_test_model(_args("anthropic/claude-opus-9"))
        sandbox["probe_mock"].assert_not_called()

    def test_the_key_is_the_one_that_was_asked_for(self, sandbox):
        _settings_test_model(_args("anthropic/claude-opus-9"))
        assert sandbox["add_mock"].call_args[1]["api_key"] == "sk-test"

    def test_a_name_with_no_provider_is_refused_with_the_form_to_use(self, sandbox):
        with pytest.raises(CLIError) as raised:
            _settings_test_model(_args("claude-opus-9"))
        said = str(raised.value)
        assert "openai/claude-opus-9" in said, "it does not show the form to type"
        sandbox["add_mock"].assert_not_called()

    def test_that_refusal_does_not_contradict_itself(self, sandbox):
        """The old one said a model was not in the catalog and then listed it."""
        with pytest.raises(CLIError) as raised:
            _settings_test_model(_args("claude-opus-9"))
        said = str(raised.value)
        first_line = said.splitlines()[0]
        assert "claude-sonnet-5" not in first_line

    def test_a_model_that_cannot_be_added_says_why(self, sandbox):
        sandbox["add_mock"].side_effect = RuntimeError("no price for that provider")
        with pytest.raises(CLIError) as raised:
            _settings_test_model(_args("nowhere/nothing"))
        said = str(raised.value)
        assert "nowhere/nothing" in said
        assert "no price for that provider" in said

    def test_nothing_is_billed_before_it_is_needed(self, sandbox):
        """A name with no provider cannot be added, so no key is fetched for
        it — a typo should not cost anything or ask whose key to use."""
        with patch("src.runtime.info_commands._key_for_testing") as key:
            with pytest.raises(CLIError):
                _settings_test_model(_args("claude-opus-9"))
            key.assert_not_called()
