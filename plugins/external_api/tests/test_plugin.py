"""Tests for plugins/external_api/plugin.py — ExternalAPIPlugin."""

import argparse
from unittest.mock import MagicMock, patch

import pytest

# Import the plugin module to trigger _register() side-effects
import importlib, sys
from pathlib import Path

_PLUGIN_PATH = Path(__file__).parent.parent / "plugin.py"
_spec = importlib.util.spec_from_file_location("external_api_plugin", _PLUGIN_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

ExternalAPIPlugin = _mod.ExternalAPIPlugin
plugin = _mod.plugin
_print_api_list = _mod._print_api_list
_dry_run_display = _mod._dry_run_display
_resolve_output_path = _mod._resolve_output_path


# ---------------------------------------------------------------------------
# Plugin identity
# ---------------------------------------------------------------------------

class TestPluginIdentity:
    def test_commands(self):
        assert plugin.commands == ["api-call"]

    def test_has_register_subparsers(self):
        assert callable(plugin.register_subparsers)

    def test_has_run(self):
        assert callable(plugin.run)


# ---------------------------------------------------------------------------
# register_subparsers
# ---------------------------------------------------------------------------

class TestRegisterSubparsers:
    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        plugin.register_subparsers(sub)
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
        monkeypatch.setattr(_mod, "list_apis", lambda: ["pu_sandbox"])
        monkeypatch.setattr(_mod, "load_api_config", MagicMock(side_effect=ValueError("no key")))

        args = argparse.Namespace(list_apis=True, api_name=None, command="api-call")
        plugin.run(args, "test_prof", None, None, None, None)
        out = capsys.readouterr().out
        assert "pu_sandbox" in out


# ---------------------------------------------------------------------------
# run — missing --api raises CLIError
# ---------------------------------------------------------------------------

class TestRunMissingApi:
    def test_raises_cli_error(self, monkeypatch):
        from src.errors import CLIError
        monkeypatch.setattr(_mod, "list_apis", lambda: ["pu_sandbox"])

        args = argparse.Namespace(list_apis=False, api_name=None, command="api-call")
        with pytest.raises(CLIError, match="--api"):
            plugin.run(args, "test_prof", None, None, None, None)


# ---------------------------------------------------------------------------
# run — dry run
# ---------------------------------------------------------------------------

class TestRunDryRun:
    def test_dry_run_no_api_call(self, monkeypatch, capsys):
        from src.services.external_api_config import ExternalAPIConfig

        fake_cfg = ExternalAPIConfig(
            api_name="pu_sandbox",
            display_name="PU AI Sandbox",
            base_url="https://api.example.com/v1",
            api_key="key",
            openai_compatible=True,
            default_model="gpt-4o",
        )
        monkeypatch.setattr(_mod, "load_api_config", lambda n: fake_cfg)
        monkeypatch.setattr(_mod, "TokenTracker", lambda **kw: MagicMock())

        fake_svc = MagicMock()
        fake_svc.build_messages.return_value = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "[Interactive prompt — text would be entered at runtime]"},
        ]
        monkeypatch.setattr(
            "src.services.external_api_call_service.APICallService",
            lambda *a, **kw: fake_svc,
        )
        # Also patch the import inside run()
        monkeypatch.setattr(_mod, "APICallService", lambda *a, **kw: fake_svc)

        args = argparse.Namespace(
            list_apis=False,
            api_name="pu_sandbox",
            include_system_prompt=False,
            dry_run=True,
            output_file=None,
        )
        plugin.run(args, "test_prof", None, None, None, None)
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "PU AI Sandbox" in out


# ---------------------------------------------------------------------------
# _resolve_output_path
# ---------------------------------------------------------------------------

class TestResolveOutputPath:
    def test_none_when_no_output(self):
        args = argparse.Namespace(output_file=None)
        assert _resolve_output_path(args) is None

    def test_absolute_path_returned(self, tmp_path):
        args = argparse.Namespace(output_file="response.txt")
        result = _resolve_output_path(args)
        assert result is not None
        assert result.endswith("response.txt")
