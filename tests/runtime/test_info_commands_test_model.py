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


class TestWhoseKeyATestIsBilledTo:
    """Testing makes real requests, so it is always somebody's key — never a guess between several."""

    def test_the_person_named(self, monkeypatch):
        from src.runtime.info_commands import _key_for_testing

        monkeypatch.setattr("src.config.get_api_key", lambda netid: (f"sk-{netid}", netid))
        assert _key_for_testing("heller") == "sk-heller"

    def test_the_only_person_there_is(self, monkeypatch):
        from src.runtime.info_commands import _key_for_testing

        monkeypatch.setattr("src.config.load_professor_config", lambda: {"heller": {}})
        monkeypatch.setattr("src.config.get_api_key", lambda netid: (f"sk-{netid}", netid))
        assert _key_for_testing(None) == "sk-heller"

    def test_nobody_set_up_says_to_add_someone(self, monkeypatch):
        from src.runtime.info_commands import _key_for_testing

        monkeypatch.setattr("src.config.load_professor_config", lambda: {})
        with pytest.raises(CLIError, match="add-professor"):
            _key_for_testing(None)

    def test_several_people_and_none_named_lists_who_to_choose_from(self, monkeypatch):
        from src.runtime.info_commands import _key_for_testing

        monkeypatch.setattr("src.config.load_professor_config", lambda: {"smith": {}, "heller": {}})
        with pytest.raises(CLIError, match="Choose from: heller, smith"):
            _key_for_testing(None)


class TestASweepOfTheCatalog:
    """With no model named, every model is tried, and each says what happened to it."""

    @pytest.fixture
    def sweep(self, monkeypatch):
        from types import SimpleNamespace

        from src.models.capabilities import CapabilityReport

        # Each model tested before, so a test that finds nothing new is told
        # apart from one that has never been tested at all.
        tested = {"supports_vision": False, "last_tested": "2026-01-01T09:00:00"}
        state = SimpleNamespace(catalog={"models": {m: dict(tested) for m in IN_CATALOG}},
                                reports={}, saves=[], unreachable=set())

        def report(name):
            return state.reports.get(name, CapabilityReport(
                findings={}, settled=[], unsettled=[], reachable=True, missing=False))

        def target(name, key):
            if name in state.unreachable:
                raise ValueError(f"no route to {name}")
            return object(), name

        monkeypatch.setattr("src.models.get_available_models", lambda: list(IN_CATALOG))
        monkeypatch.setattr("src.runtime.info_commands._key_for_testing", lambda p: "sk-test")
        monkeypatch.setattr("src.models.load_model_catalog", lambda: state.catalog)
        monkeypatch.setattr("src.models.save_model_catalog",
                            lambda c: state.saves.append({k: dict(v) for k, v in c["models"].items()}))
        monkeypatch.setattr("src.models.capabilities.testing_target", target)
        monkeypatch.setattr("src.models.capabilities.probe_model_capabilities",
                            lambda asked_as, client: report(asked_as))
        return state

    def _report(self, **kw):
        from src.models.capabilities import CapabilityReport

        return CapabilityReport(**({"findings": {}, "settled": [], "unsettled": [],
                                    "reachable": True, "missing": False} | kw))

    def test_what_is_learned_is_saved_as_it_goes(self, sweep, capsys):
        sweep.reports["gpt-4o"] = self._report(findings={"supports_vision": True},
                                               settled=["reads images"])
        _settings_test_model(_args())
        out = capsys.readouterr().out
        assert "reads images" in out and "saved" in out
        assert sweep.saves[-1]["gpt-4o"]["supports_vision"] is True
        assert "1 of 3 updated." in out

    def test_a_model_that_has_not_changed_is_not_counted_as_updated(self, sweep, capsys):
        """Every test records when it happened; that alone is not news about the model."""
        _settings_test_model(_args())
        out = capsys.readouterr().out
        assert out.count("already recorded correctly") == 3
        assert "0 of 3 updated." in out

    def test_but_when_it_was_tested_is_still_kept(self, sweep):
        _settings_test_model(_args())
        assert sweep.saves[-1]["gpt-4o"]["last_tested"] > "2026-01-01T09:00:00"

    def test_a_model_never_tested_before_counts_as_updated(self, sweep, capsys):
        """Being asked at all is the difference between 'cannot' and 'nobody knows'."""
        sweep.catalog["models"]["gpt-4o"] = {"supports_vision": False}
        _settings_test_model(_args())
        out = capsys.readouterr().out
        assert "1 of 3 updated." in out

    def test_a_model_that_could_not_be_reached_is_left_as_it_was(self, sweep, capsys):
        sweep.reports["gpt-4o"] = self._report(reachable=False, unsettled=["timed out"])
        _settings_test_model(_args())
        out = capsys.readouterr().out
        assert "could not be reached — nothing changed" in out and "timed out" in out

    def test_a_model_with_no_way_to_test_it_is_skipped(self, sweep, capsys):
        sweep.unreachable.add("gemini-2.5-pro")
        _settings_test_model(_args())
        assert "could not be tested — nothing changed\n  no route to gemini-2.5-pro" in capsys.readouterr().out

    def test_a_model_that_no_longer_exists_is_named_and_kept(self, sweep, capsys):
        """One failed request is not reason enough to delete what somebody configured."""
        sweep.reports["gpt-4o"] = self._report(missing=True)
        _settings_test_model(_args())
        out = capsys.readouterr().out
        assert "1 no longer exist: gpt-4o" in out and "--remove-missing" in out
        assert "gpt-4o" in sweep.catalog["models"]

    def test_and_is_removed_when_asked(self, sweep, capsys):
        sweep.reports["gpt-4o"] = self._report(missing=True)
        _settings_test_model(_args(remove_missing=True))
        assert "Removed 1: gpt-4o" in capsys.readouterr().out
        assert "gpt-4o" not in sweep.saves[-1]
