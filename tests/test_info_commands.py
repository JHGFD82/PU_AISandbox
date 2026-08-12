"""
Tests for src/runtime/info_commands.py:
  - list_available_models
  - _print_daily_usage
  - handle_info_commands  (all branches)

No API calls, no cloud I/O; TokenTracker is either mocked or backed by tmp_path.
"""

import argparse
from unittest.mock import MagicMock, patch

import pytest

import src.runtime.info_commands as info_mod
import src.settings_store as settings_store_mod
from src.errors import CLIError
from src.runtime.info_commands import (
    _print_daily_usage,
    handle_info_commands,
    list_available_models,
    show_professor_config,
)


@pytest.fixture(autouse=True)
def _redirect_settings_path(tmp_path, monkeypatch):
    """Keep 'env' and 'usage sources' tests from touching the real repo-root settings.toml file."""
    monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", tmp_path / "settings.toml")

# ---------------------------------------------------------------------------
# Shared test catalog (matches model_catalog.json schema)
# ---------------------------------------------------------------------------

SAMPLE_CATALOG = {
    "config": {
        "pricing_unit": 1_000_000,
        "monthly_limit": 250.0,
    },
    "models": {
        "gpt-4o": {
            "input": 2.75,
            "output": 11.0,
            "supports_vision": True,
        },
        "text-only-model": {
            "input": 0.10,
            "output": 0.30,
            "supports_vision": False,
        },
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ns(**kwargs) -> argparse.Namespace:
    """Build a minimal Namespace, setting sensible falsy defaults for every
    attribute that handle_info_commands inspects."""
    defaults = dict(
        list_models=False,
        command=None,
        professor=None,
        usage_subcommand=None,
        month=None,
        all_time=False,
        date="today",
        sources_subcommand=None,
        label=None,
        path=None,
        mode=None,
        for_professor=None,
        settings_subcommand=None,
        name=None,
        identifier=None,
        key=None,
        generate=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# list_available_models
# ---------------------------------------------------------------------------


class TestListAvailableModels:

    def test_prints_model_names(self, capsys, monkeypatch):
        monkeypatch.setattr(info_mod, "load_model_catalog", lambda: SAMPLE_CATALOG)
        monkeypatch.setattr(info_mod, "get_pricing_unit", lambda: 1_000_000)
        list_available_models()
        out = capsys.readouterr().out
        assert "gpt-4o" in out
        assert "text-only-model" in out

    def test_vision_checkmark_shown(self, capsys, monkeypatch):
        monkeypatch.setattr(info_mod, "load_model_catalog", lambda: SAMPLE_CATALOG)
        monkeypatch.setattr(info_mod, "get_pricing_unit", lambda: 1_000_000)
        list_available_models()
        out = capsys.readouterr().out
        assert "✓" in out   # gpt-4o supports vision
        assert "✗" in out   # text-only-model does not

    def test_prints_input_price(self, capsys, monkeypatch):
        monkeypatch.setattr(info_mod, "load_model_catalog", lambda: SAMPLE_CATALOG)
        monkeypatch.setattr(info_mod, "get_pricing_unit", lambda: 1_000_000)
        list_available_models()
        out = capsys.readouterr().out
        assert "2.750" in out

    def test_prints_pricing_unit(self, capsys, monkeypatch):
        monkeypatch.setattr(info_mod, "load_model_catalog", lambda: SAMPLE_CATALOG)
        monkeypatch.setattr(info_mod, "get_pricing_unit", lambda: 1_000_000)
        list_available_models()
        out = capsys.readouterr().out
        assert "1,000,000" in out


# ---------------------------------------------------------------------------
# _print_daily_usage
# ---------------------------------------------------------------------------


class TestPrintDailyUsage:

    def _make_tracker(self, daily_data: dict) -> MagicMock:
        tracker = MagicMock()
        tracker.get_daily_usage.return_value = daily_data
        return tracker

    def test_no_usage_prints_no_usage_message(self, capsys):
        tracker = self._make_tracker({"call_count": 0, "total_tokens": 0,
                                      "total_cost": 0.0})
        _print_daily_usage(tracker, "testprof", date="2026-03-01")
        out = capsys.readouterr().out
        assert "No usage" in out

    def test_usage_present_prints_token_counts(self, capsys):
        tracker = self._make_tracker({
            "call_count": 3,
            "total_tokens": 1500,
            "total_input_tokens": 1000,
            "total_output_tokens": 500,
            "total_cost": 0.05,
        })
        _print_daily_usage(tracker, "testprof", date="2026-03-01")
        out = capsys.readouterr().out
        assert "1,500" in out
        assert "0.0500" in out
        assert "3" in out

    def test_today_keyword_calls_get_daily_usage_without_args(self, capsys):
        tracker = self._make_tracker({"call_count": 0, "total_tokens": 0, "total_cost": 0.0})
        _print_daily_usage(tracker, "testprof", date="today")
        tracker.get_daily_usage.assert_called_once_with()

    def test_specific_date_passed_to_tracker(self, capsys):
        tracker = self._make_tracker({"call_count": 0, "total_tokens": 0, "total_cost": 0.0})
        _print_daily_usage(tracker, "testprof", date="2026-01-15")
        tracker.get_daily_usage.assert_called_once_with("2026-01-15")

    def test_professor_name_in_output(self, capsys):
        tracker = self._make_tracker({"call_count": 1, "total_tokens": 100,
                                      "total_input_tokens": 60, "total_output_tokens": 40,
                                      "total_cost": 0.01})
        _print_daily_usage(tracker, "Dr. Yamamoto", date="2026-03-01")
        out = capsys.readouterr().out
        assert "Dr. Yamamoto" in out


# ---------------------------------------------------------------------------
# handle_info_commands — global (no professor needed) branches
# ---------------------------------------------------------------------------


class TestHandleInfoCommandsGlobal:

    def test_list_models_returns_true(self, monkeypatch, capsys):
        monkeypatch.setattr(info_mod, "load_model_catalog", lambda: SAMPLE_CATALOG)
        monkeypatch.setattr(info_mod, "get_pricing_unit", lambda: 1_000_000)
        args = _make_ns(list_models=True)
        assert handle_info_commands(args) is True

    def test_list_models_prints_output(self, monkeypatch, capsys):
        monkeypatch.setattr(info_mod, "load_model_catalog", lambda: SAMPLE_CATALOG)
        monkeypatch.setattr(info_mod, "get_pricing_unit", lambda: 1_000_000)
        handle_info_commands(_make_ns(list_models=True))
        assert "gpt-4o" in capsys.readouterr().out

    def test_no_matching_flag_returns_false(self):
        args = _make_ns()
        assert handle_info_commands(args) is False


# ---------------------------------------------------------------------------
# handle_info_commands — usage subcommand branches
# ---------------------------------------------------------------------------


class TestHandleInfoCommandsUsage:

    @pytest.fixture(autouse=True)
    def _configured(self, monkeypatch):
        """Make 'testprof' a configured netID for this class.

        Usage commands now refuse a netID nobody is configured under, so
        these tests need theirs to exist. Patched rather than written to
        settings.toml because what they are testing is the reporting, not the
        configuration lookup — that has its own test below.
        """
        monkeypatch.setattr(
            "src.runtime.info_commands.load_professor_config",
            lambda: {"testprof": {"name": "Test Prof", "key": "sk-x",
                                  "backup_key": None, "netid": "testprof"}},
        )

    def _make_mock_tracker(self):
        tracker = MagicMock()
        tracker.list_archived_months.return_value = ["2026-01", "2026-02"]
        tracker.get_daily_usage.return_value = {
            "call_count": 2,
            "total_tokens": 800,
            "total_input_tokens": 500,
            "total_output_tokens": 300,
            "total_cost": 0.04,
        }
        return tracker

    def test_usage_report_calls_print_usage_report(self):
        mock_tracker = self._make_mock_tracker()
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="report", month=None, all_time=False)
            result = handle_info_commands(args)
        assert result is True
        mock_tracker.print_usage_report.assert_called_once_with(month=None, include_all_time=False)

    def test_usage_report_passes_month_arg(self):
        mock_tracker = self._make_mock_tracker()
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="report", month="2026-01", all_time=False)
            handle_info_commands(args)
        mock_tracker.print_usage_report.assert_called_once_with(month="2026-01", include_all_time=False)

    def test_usage_report_passes_all_time_flag(self):
        mock_tracker = self._make_mock_tracker()
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="report", month=None, all_time=True)
            handle_info_commands(args)
        mock_tracker.print_usage_report.assert_called_once_with(month=None, include_all_time=True)

    def test_usage_months_returns_true(self, capsys):
        mock_tracker = self._make_mock_tracker()
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="months")
            result = handle_info_commands(args)
        assert result is True

    def test_usage_months_prints_archived_list(self, capsys):
        mock_tracker = self._make_mock_tracker()
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="months")
            handle_info_commands(args)
        out = capsys.readouterr().out
        assert "2026-01" in out
        assert "2026-02" in out

    def test_usage_months_no_archives_message(self, capsys):
        mock_tracker = self._make_mock_tracker()
        mock_tracker.list_archived_months.return_value = []
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="months")
            handle_info_commands(args)
        out = capsys.readouterr().out
        assert "No archived months" in out

    def test_usage_daily_returns_true(self, capsys):
        mock_tracker = self._make_mock_tracker()
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="daily", date="today")
            result = handle_info_commands(args)
        assert result is True

    def test_usage_daily_calls_get_daily_usage(self, capsys):
        mock_tracker = self._make_mock_tracker()
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="daily", date="2026-03-01")
            handle_info_commands(args)
        mock_tracker.get_daily_usage.assert_called_with("2026-03-01")

    def test_usage_missing_netid_raises_cli_error(self):
        args = _make_ns(command="usage", professor=None, usage_subcommand="report")
        with pytest.raises(CLIError, match="netID is required"):
            handle_info_commands(args)

    def test_invalid_usage_subcommand_raises_cli_error(self):
        mock_tracker = MagicMock()
        with patch("src.runtime.info_commands.TokenTracker", return_value=mock_tracker):
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="unknown")
            with pytest.raises(CLIError, match="Invalid usage subcommand"):
                handle_info_commands(args)


# ---------------------------------------------------------------------------
# handle_info_commands — usage sources subcommand branches
# ---------------------------------------------------------------------------


class TestHandleInfoCommandsUsageSources:

    def test_sources_list_returns_true(self, capsys):
        args = _make_ns(command="usage", professor="testprof",
                        usage_subcommand="sources", sources_subcommand="list")
        assert handle_info_commands(args) is True

    def test_sources_list_says_so_when_there_are_none(self, capsys):
        """And says where one is set, rather than leaving a bare 'none'."""
        args = _make_ns(command="usage", professor="testprof",
                        usage_subcommand="sources", sources_subcommand="list")
        handle_info_commands(args)
        out = capsys.readouterr().out.lower()
        assert "nobody" in out or "no " in out
        assert "settings page" in out

    def test_sources_does_not_construct_token_tracker(self, capsys):
        """'usage sources' shouldn't need a working professor config at all."""
        with patch("src.runtime.info_commands.TokenTracker") as mock_cls:
            args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="sources", sources_subcommand="list")
            handle_info_commands(args)
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# show_professor_config
# ---------------------------------------------------------------------------


class TestShowProfessorConfig:

    def _make_mock_usage(self, exists: bool = False, path_str: str = "/data/token_usage_heller.json") -> MagicMock:
        m = MagicMock()
        m.exists.return_value = exists
        m.__str__ = MagicMock(return_value=path_str)
        return m

    def test_no_professors_prints_instructions(self, capsys, monkeypatch):
        monkeypatch.setattr(info_mod, "load_professor_config", dict)
        show_professor_config()
        out = capsys.readouterr().out
        assert "No professors configured" in out

    def test_no_professors_shows_format_hint(self, capsys, monkeypatch):
        monkeypatch.setattr(info_mod, "load_professor_config", dict)
        show_professor_config()
        out = capsys.readouterr().out
        assert "professors.<netid>" in out

    def test_professor_shown_primary_key_set_no_archive(self, capsys, monkeypatch):
        """Primary key set, backup NOT set, usage file missing, no archive dir."""
        profs = {"heller": {"name": "Jeff Heller", "key": "real-key", "backup_key": None}}
        monkeypatch.setattr(info_mod, "load_professor_config", lambda: profs)
        monkeypatch.setattr(info_mod, "get_usage_data_path", lambda _: self._make_mock_usage(exists=False))
        mock_archive = MagicMock()
        mock_archive.exists.return_value = False
        monkeypatch.setattr(info_mod, "get_archive_dir", lambda _: mock_archive)
        show_professor_config()
        out = capsys.readouterr().out
        assert "Jeff Heller" in out
        assert "heller" in out
        assert "Primary key:  set" in out
        assert "not yet created" in out
        assert "none" in out

    def test_professor_shown_primary_key_not_set_usage_exists_with_archives(
            self, capsys, monkeypatch, tmp_path):
        """Primary NOT SET, backup set, usage file exists, archive dir has months."""
        profs = {"heller": {"name": "Jeff Heller", "key": "", "backup_key": "backup-key"}}
        monkeypatch.setattr(info_mod, "load_professor_config", lambda: profs)
        monkeypatch.setattr(info_mod, "get_usage_data_path", lambda _: self._make_mock_usage(exists=True))
        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()
        (archive_dir / "2026-01.json").write_text("{}")
        (archive_dir / "2026-02.json").write_text("{}")
        monkeypatch.setattr(info_mod, "get_archive_dir", lambda _: archive_dir)
        show_professor_config()
        out = capsys.readouterr().out
        assert "NOT SET" in out
        assert "not yet created" not in out
        assert "2026-01" in out
        assert "2026-02" in out


# ---------------------------------------------------------------------------
# handle_info_commands — show_config branch
# ---------------------------------------------------------------------------


class TestHandleInfoCommandsShowConfig:

    def test_show_config_returns_true(self, monkeypatch):
        monkeypatch.setattr(info_mod, "show_professor_config", lambda: None)
        args = _make_ns(show_config=True)
        assert handle_info_commands(args) is True

    def test_show_config_delegates_to_show_professor_config(self, monkeypatch):
        called = []
        monkeypatch.setattr(info_mod, "show_professor_config", lambda: called.append(True))
        handle_info_commands(_make_ns(show_config=True))
        assert called


# ---------------------------------------------------------------------------
# handle_info_commands — env subcommand branches
# ---------------------------------------------------------------------------


class TestHandleInfoCommandsSettings:

    def test_usage_for_an_unconfigured_netid_is_an_error(self, monkeypatch):
        """A mistyped netID must not produce a report full of zeroes.

        Before this check, `usage report` for a netID nobody had heard of
        printed a perfectly formatted report showing no spending — which
        reads as "you are well within budget", not as "there is nobody by
        that name here". For a tool whose job is tracking spending, being
        told you are fine when the question was never answered is the worst
        possible failure.
        """
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        handle_info_commands(_make_ns(command="settings", settings_subcommand="add-professor",
                                      netid="jh43", name="Jeff Heller"))
        args = _make_ns(command="usage", usage_subcommand="report", professor="zz99")
        with pytest.raises(CLIError) as excinfo:
            handle_info_commands(args)
        message = str(excinfo.value)
        assert "zz99" in message
        assert "jh43" in message          # says who *is* configured
        assert "add-professor" in message  # and how to fix it

    def test_add_professor_with_flags_and_prompted_keys(self, capsys, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        args = _make_ns(command="settings", settings_subcommand="add-professor",
                        netid="jh43", name="Jeff Heller")
        result = handle_info_commands(args)
        assert result is True
        out = capsys.readouterr().out
        assert "Jeff Heller" in out
        assert "jh43" in out

    def test_add_professor_prompts_for_anything_not_passed(self, capsys, monkeypatch):
        """netID first, then display name — the order the prompts ask in."""
        answers = iter(["jh43", "Jeff Heller"])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        args = _make_ns(command="settings", settings_subcommand="add-professor", netid=None, name=None)
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "Jeff Heller" in out
        assert "jh43" in out

    def test_add_professor_blank_name_raises_cli_error(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        args = _make_ns(command="settings", settings_subcommand="add-professor",
                        netid="jh43", name="   ")
        with pytest.raises(CLIError, match="blank"):
            handle_info_commands(args)

    def test_add_professor_rejects_a_display_name_as_the_netid(self, monkeypatch):
        """The likeliest mistake, so it gets an error that names the fix."""
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        args = _make_ns(command="settings", settings_subcommand="add-professor",
                        netid="Jeff Heller", name="Jeff Heller")
        with pytest.raises(CLIError, match="netID"):
            handle_info_commands(args)

    def test_no_subcommand_raises_cli_error(self):
        args = _make_ns(command="settings", settings_subcommand=None)
        with pytest.raises(CLIError, match="No settings subcommand"):
            handle_info_commands(args)

    def test_list_returns_true(self):
        args = _make_ns(command="settings", settings_subcommand="list")
        assert handle_info_commands(args) is True


class TestTheModelQuirksCommand:
    """Saying "try that again" without editing the catalog by hand."""

    def _args(self, model=None):
        import argparse

        return argparse.Namespace(settings_subcommand="model-quirks", model=model)

    def _run(self, monkeypatch, known, model=None):
        from src.runtime import info_commands

        cleared = {}
        monkeypatch.setattr("src.models.models_with_rejected_fields", lambda: known)
        monkeypatch.setattr(
            "src.models.clear_rejected_fields",
            lambda name: cleared.setdefault(name, known.get(name, {})) or known.get(name, {}),
        )
        info_commands._settings_model_quirks(self._args(model))
        return cleared

    def test_it_lists_what_has_been_learned(self, monkeypatch, capsys):
        self._run(monkeypatch, {"fussy": {"stream_options": "2026-07-29: not permitted"}})
        out = capsys.readouterr().out
        assert "fussy" in out
        assert "stream_options" in out
        assert "2026-07-29" in out

    def test_it_says_plainly_when_nothing_has_been_learned(self, monkeypatch, capsys):
        """Which is the ordinary case."""
        self._run(monkeypatch, {})
        assert "Nothing" in capsys.readouterr().out

    def test_a_wall_of_provider_detail_is_cut_down_to_read(self, monkeypatch, capsys):
        long_note = "2026-07-29: " + ("x" * 400)
        self._run(monkeypatch, {"fussy": {"stream_options": long_note}})
        out = capsys.readouterr().out
        assert "…" in out
        assert "x" * 400 not in out

    def test_naming_a_model_forgets_it(self, monkeypatch, capsys):
        cleared = self._run(
            monkeypatch, {"fussy": {"stream_options": "2026-07-29: no"}}, model="fussy",
        )
        assert "fussy" in cleared
        assert "stream_options" in capsys.readouterr().out

    def test_naming_a_model_with_nothing_recorded_says_so(self, monkeypatch):
        import pytest

        from src.errors import CLIError

        with pytest.raises(CLIError, match="Nothing has been recorded"):
            self._run(monkeypatch, {}, model="quiet")
