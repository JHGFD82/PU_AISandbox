"""
Tests for src/runtime/info_commands.py:
  - list_available_models
  - _print_daily_usage
  - handle_info_commands  (all branches)

No API calls, no cloud I/O; TokenTracker is either mocked or backed by tmp_path.
"""

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

import src.env_editor as env_editor_mod
import src.runtime.info_commands as info_mod
import src.tracking.source_config as source_config_mod
from src.errors import CLIError
from src.runtime.info_commands import (
    _print_daily_usage,
    handle_info_commands,
    list_available_models,
    show_professor_config,
)


@pytest.fixture(autouse=True)
def _redirect_data_sources_file(tmp_path, monkeypatch):
    """Keep 'usage sources' tests from touching the real repo-root data_sources.json."""
    monkeypatch.setattr(source_config_mod, "DATA_SOURCES_FILE", tmp_path / "data_sources.json")


@pytest.fixture(autouse=True)
def _redirect_env_path(tmp_path, monkeypatch):
    """Keep 'env' tests from touching the real repo-root .env file."""
    monkeypatch.setattr(env_editor_mod, "ENV_PATH", tmp_path / ".env")
    for key in list(__import__("os").environ.keys()):
        if key.startswith("PROF_") or key in ("TEST_SECRET_VAR",):
            monkeypatch.delenv(key, raising=False)

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
        env_subcommand=None,
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

    def test_usage_missing_professor_raises_cli_error(self):
        args = _make_ns(command="usage", professor=None, usage_subcommand="report")
        with pytest.raises(CLIError, match="Professor name is required"):
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

    def test_sources_list_prints_no_sources_message_when_empty(self, capsys):
        args = _make_ns(command="usage", professor="testprof",
                        usage_subcommand="sources", sources_subcommand="list")
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "No external usage-data sources configured" in out

    def test_sources_add_read_only_with_flags(self, capsys, tmp_path):
        args = _make_ns(command="usage", professor="testprof",
                        usage_subcommand="sources", sources_subcommand="add",
                        label="Johnson", path=str(tmp_path), mode="read-only")
        result = handle_info_commands(args)
        assert result is True
        out = capsys.readouterr().out
        assert "Added source 'Johnson'" in out

    def test_sources_add_then_list_shows_it(self, capsys, tmp_path):
        add_args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="sources", sources_subcommand="add",
                            label="Johnson", path=str(tmp_path), mode="read-only")
        handle_info_commands(add_args)
        capsys.readouterr()  # discard

        list_args = _make_ns(command="usage", professor="testprof",
                             usage_subcommand="sources", sources_subcommand="list")
        handle_info_commands(list_args)
        out = capsys.readouterr().out
        assert "Johnson" in out

    def test_sources_add_shared_write_requires_for_professor_prompt(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_: "smith")
        args = _make_ns(command="usage", professor="testprof",
                        usage_subcommand="sources", sources_subcommand="add",
                        label="Smith", path=str(tmp_path), mode="shared-write", for_professor=None)
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "Add this on the other installation" in out
        assert "--for-professor smith" in out

    def test_sources_add_shared_write_with_for_professor_flag_skips_prompt(self, capsys, tmp_path):
        args = _make_ns(command="usage", professor="testprof",
                        usage_subcommand="sources", sources_subcommand="add",
                        label="Smith", path=str(tmp_path), mode="shared-write", for_professor="smith")
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "smiths-imac" not in out  # sanity: no leftover placeholder text
        assert "--for-professor smith" in out

    def test_sources_remove_existing(self, capsys, tmp_path):
        add_args = _make_ns(command="usage", professor="testprof",
                            usage_subcommand="sources", sources_subcommand="add",
                            label="Johnson", path=str(tmp_path), mode="read-only")
        handle_info_commands(add_args)
        capsys.readouterr()

        remove_args = _make_ns(command="usage", professor="testprof",
                               usage_subcommand="sources", sources_subcommand="remove",
                               label="Johnson")
        handle_info_commands(remove_args)
        out = capsys.readouterr().out
        assert "Removed source 'Johnson'" in out

    def test_sources_remove_missing_reports_not_found(self, capsys):
        args = _make_ns(command="usage", professor="testprof",
                        usage_subcommand="sources", sources_subcommand="remove",
                        label="Nobody")
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "No configured source named 'Nobody'" in out

    def test_sources_invalid_subcommand_raises(self):
        args = _make_ns(command="usage", professor="testprof",
                        usage_subcommand="sources", sources_subcommand="bogus")
        with pytest.raises(CLIError, match="Invalid usage sources subcommand"):
            handle_info_commands(args)

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
        monkeypatch.setattr(info_mod, "load_professor_config", lambda: {})
        show_professor_config()
        out = capsys.readouterr().out
        assert "No professors configured" in out

    def test_no_professors_shows_format_hint(self, capsys, monkeypatch):
        monkeypatch.setattr(info_mod, "load_professor_config", lambda: {})
        show_professor_config()
        out = capsys.readouterr().out
        assert "PROF_[ID]_NAME" in out

    def test_professor_shown_primary_key_set_no_archive(self, capsys, monkeypatch):
        """Primary key set, backup NOT set, usage file missing, no archive dir."""
        profs = {"heller": {"name": "Jeff Heller", "primary_key": "KEY_A", "backup_key": "KEY_B"}}
        monkeypatch.setattr(info_mod, "load_professor_config", lambda: profs)
        monkeypatch.setattr(info_mod, "get_usage_data_path", lambda _: self._make_mock_usage(exists=False))
        mock_archive = MagicMock()
        mock_archive.exists.return_value = False
        monkeypatch.setattr(info_mod, "get_archive_dir", lambda _: mock_archive)
        monkeypatch.setenv("KEY_A", "real-key")
        monkeypatch.delenv("KEY_B", raising=False)
        show_professor_config()
        out = capsys.readouterr().out
        assert "Jeff Heller" in out
        assert "heller" in out
        assert "(set)" in out
        assert "not yet created" in out
        assert "none" in out

    def test_professor_shown_primary_key_not_set_usage_exists_with_archives(
            self, capsys, monkeypatch, tmp_path):
        """Primary NOT SET, backup set, usage file exists, archive dir has months."""
        profs = {"heller": {"name": "Jeff Heller", "primary_key": "KEY_A", "backup_key": "KEY_B"}}
        monkeypatch.setattr(info_mod, "load_professor_config", lambda: profs)
        monkeypatch.setattr(info_mod, "get_usage_data_path", lambda _: self._make_mock_usage(exists=True))
        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()
        (archive_dir / "2026-01.json").write_text("{}")
        (archive_dir / "2026-02.json").write_text("{}")
        monkeypatch.setattr(info_mod, "get_archive_dir", lambda _: archive_dir)
        monkeypatch.delenv("KEY_A", raising=False)
        monkeypatch.setenv("KEY_B", "backup-key")
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


class TestHandleInfoCommandsEnv:

    def test_add_professor_with_flag_and_prompted_keys(self, capsys, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        args = _make_ns(command="env", env_subcommand="add-professor", name="Jeff Heller")
        result = handle_info_commands(args)
        assert result is True
        out = capsys.readouterr().out
        assert "Added professor 'Jeff Heller'" in out
        assert "jeff_heller" in out

    def test_add_professor_prompts_for_name_when_not_given(self, capsys, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_: "Jeff Heller")
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        args = _make_ns(command="env", env_subcommand="add-professor", name=None)
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "Jeff Heller" in out

    def test_add_professor_blank_name_raises_cli_error(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        args = _make_ns(command="env", env_subcommand="add-professor", name="   ")
        with pytest.raises(CLIError, match="blank"):
            handle_info_commands(args)

    def test_remove_professor_confirmed(self, capsys, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        handle_info_commands(_make_ns(command="env", env_subcommand="add-professor", name="Jeff Heller"))
        capsys.readouterr()

        monkeypatch.setattr("builtins.input", lambda *_: "y")
        args = _make_ns(command="env", env_subcommand="remove-professor", identifier="jeff_heller")
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "Removed professor 'Jeff Heller'" in out

    def test_remove_professor_declined(self, capsys, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *_: "sk-test-key")
        handle_info_commands(_make_ns(command="env", env_subcommand="add-professor", name="Jeff Heller"))
        capsys.readouterr()

        monkeypatch.setattr("builtins.input", lambda *_: "n")
        args = _make_ns(command="env", env_subcommand="remove-professor", identifier="jeff_heller")
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "Cancelled" in out

    def test_remove_unknown_professor_raises_cli_error(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_: "y")
        args = _make_ns(command="env", env_subcommand="remove-professor", identifier="nobody")
        with pytest.raises(CLIError, match="No configured professor"):
            handle_info_commands(args)

    def test_set_secret_value_hides_it_in_output(self, capsys, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *_: "super-secret")
        args = _make_ns(command="env", env_subcommand="set", key="WEBUI_SESSION_SECRET", generate=False)
        handle_info_commands(args)
        out = capsys.readouterr().out
        assert "super-secret" not in out
        assert "hidden" in out

    def test_set_generate_produces_a_value_without_prompting(self, capsys, monkeypatch):
        def _boom(*_a, **_k):
            raise AssertionError("should not prompt when --generate is used")
        monkeypatch.setattr("getpass.getpass", _boom)
        args = _make_ns(command="env", env_subcommand="set", key="WEBUI_SESSION_SECRET", generate=True)
        handle_info_commands(args)
        import os
        assert os.environ.get("WEBUI_SESSION_SECRET")

    def test_unset_removes_value(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *_: "some-value")
        handle_info_commands(_make_ns(command="env", env_subcommand="set", key="WEBUI_SESSION_SECRET"))
        handle_info_commands(_make_ns(command="env", env_subcommand="unset", key="WEBUI_SESSION_SECRET"))
        import os
        assert "WEBUI_SESSION_SECRET" not in os.environ

    def test_no_subcommand_raises_cli_error(self):
        args = _make_ns(command="env", env_subcommand=None)
        with pytest.raises(CLIError, match="No env subcommand"):
            handle_info_commands(args)

    def test_list_returns_true(self):
        args = _make_ns(command="env", env_subcommand="list")
        assert handle_info_commands(args) is True
