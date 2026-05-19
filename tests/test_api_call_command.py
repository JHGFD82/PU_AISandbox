"""Tests for src/commands/api_call.py — the api-call command."""

import argparse
from unittest.mock import MagicMock

import pytest

from src.commands import api_call as _cmd
from src.services.api_config import APIConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> APIConfig:
    defaults = dict(
        api_name="pu_sandbox",
        display_name="PU AI Sandbox",
        base_url="https://api.example.com/v1",
        api_key="key",
        openai_compatible=True,
        default_model="gpt-4o",
    )
    defaults.update(kwargs)
    return APIConfig(**defaults)


# ---------------------------------------------------------------------------
# register_subparser
# ---------------------------------------------------------------------------

class TestRegisterSubparser:
    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        _cmd.register_subparser(sub)
        return parser

    def test_api_call_subcommand_registered(self):
        parser = self._build_parser()
        args = parser.parse_args(["api-call", "--api", "pu_sandbox"])
        assert args.api_name == "pu_sandbox"

    def test_list_apis_flag(self):
        parser = self._build_parser()
        args = parser.parse_args(["api-call", "--list-apis"])
        assert args.list_apis is True

    def test_system_flag(self):
        parser = self._build_parser()
        args = parser.parse_args(["api-call", "--api", "x", "-s"])
        assert args.include_system_prompt is True

    def test_dry_run_flag(self):
        parser = self._build_parser()
        args = parser.parse_args(["api-call", "--api", "x", "--dry-run"])
        assert args.dry_run is True


# ---------------------------------------------------------------------------
# run — --list-apis
# ---------------------------------------------------------------------------

class TestRunListApis:
    def test_list_apis_exits_early(self, monkeypatch, capsys):
        monkeypatch.setattr(_cmd, "list_apis", lambda: ["pu_sandbox"])
        monkeypatch.setattr(_cmd, "load_api_config", MagicMock(side_effect=ValueError("no key")))

        args = argparse.Namespace(list_apis=True, api_name=None, command="api-call")
        _cmd.run(args, "test_prof", None, None, None, None)
        out = capsys.readouterr().out
        assert "pu_sandbox" in out


# ---------------------------------------------------------------------------
# run — missing --api raises CLIError
# ---------------------------------------------------------------------------

class TestRunMissingApi:
    def test_raises_cli_error(self, monkeypatch):
        from src.errors import CLIError
        monkeypatch.setattr(_cmd, "list_apis", lambda: ["pu_sandbox"])
        monkeypatch.setattr(_cmd, "get_default_api_name", lambda: None)

        args = argparse.Namespace(list_apis=False, api_name=None, command="api-call")
        with pytest.raises(CLIError, match="--api"):
            _cmd.run(args, "test_prof", None, None, None, None)


# ---------------------------------------------------------------------------
# run — dry run
# ---------------------------------------------------------------------------

class TestRunDryRun:
    def test_dry_run_no_api_call(self, monkeypatch, capsys):
        fake_cfg = _make_config()
        monkeypatch.setattr(_cmd, "load_api_config", lambda n: fake_cfg)
        monkeypatch.setattr(_cmd, "TokenTracker", lambda **kw: MagicMock())

        fake_svc = MagicMock()
        fake_svc.build_messages.return_value = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "[Interactive prompt — text would be entered at runtime]"},
        ]
        monkeypatch.setattr(_cmd, "APICallService", lambda *a, **kw: fake_svc)

        args = argparse.Namespace(
            list_apis=False,
            api_name="pu_sandbox",
            include_system_prompt=False,
            dry_run=True,
            output_file=None,
        )
        _cmd.run(args, "test_prof", None, None, None, None)
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "PU AI Sandbox" in out


# ---------------------------------------------------------------------------
# run — colon syntax routes correctly
# ---------------------------------------------------------------------------

class TestRunColonSyntax:
    def test_colon_model_sets_api_name(self, monkeypatch, capsys):
        from src.errors import CLIError
        captured = {}

        def fake_load(name):
            captured["api_name"] = name
            return _make_config(api_name=name)

        monkeypatch.setattr(_cmd, "load_api_config", fake_load)
        monkeypatch.setattr(_cmd, "TokenTracker", lambda **kw: MagicMock())
        monkeypatch.setattr(_cmd, "get_default_api_name", lambda: None)

        fake_svc = MagicMock()
        fake_svc.build_messages.return_value = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
        ]
        monkeypatch.setattr(_cmd, "APICallService", lambda *a, **kw: fake_svc)

        args = argparse.Namespace(
            list_apis=False,
            api_name=None,
            include_system_prompt=False,
            dry_run=True,
            output_file=None,
        )
        _cmd.run(args, "test_prof", "della:qwen-preview", None, None, None)
        assert captured["api_name"] == "della"


# ---------------------------------------------------------------------------
# _print_api_list
# ---------------------------------------------------------------------------

class TestPrintApiList:
    def test_empty_list(self, monkeypatch, capsys):
        monkeypatch.setattr(_cmd, "list_apis", lambda: [])
        _cmd._print_api_list()
        assert "No APIs" in capsys.readouterr().out

    def test_shows_configured_apis(self, monkeypatch, capsys):
        monkeypatch.setattr(_cmd, "list_apis", lambda: ["pu_sandbox"])
        monkeypatch.setattr(_cmd, "get_default_api_name", lambda: "pu_sandbox")
        monkeypatch.setattr(_cmd, "load_api_config", lambda n: _make_config())
        _cmd._print_api_list()
        out = capsys.readouterr().out
        assert "pu_sandbox" in out
        assert "[default]" in out
