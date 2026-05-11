"""Tests for plugins/prompt/plugin.py.

Covers:
- PromptPlugin.commands / register_subparsers
- PromptPlugin.run: dry-run path, normal path, empty-prompt error,
  send_prompt exception, output-file path, include_system_prompt flag
- _collect_multiline: sentinel line, multi-line, EOF
- _dry_run_display: output formatting, optional params
- _resolve_output_path: with and without output_file
"""

from __future__ import annotations

import argparse
import sys
import types
from io import StringIO
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# Ensure the plugin module can be imported (repo root must be on sys.path).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import plugins.prompt.plugin as plugin_mod
from plugins.prompt.plugin import (
    PromptPlugin,
    _collect_multiline,
    _dry_run_display,
    _resolve_output_path,
)
from src.errors import CLIError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kw) -> argparse.Namespace:
    defaults = dict(
        dry_run=False,
        include_system_prompt=False,
        output_file=None,
        auto_save=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# PromptPlugin — class attributes
# ---------------------------------------------------------------------------

class TestPluginIdentity:

    def test_commands_contains_prompt(self):
        assert "prompt" in PromptPlugin.commands

    def test_module_level_plugin_instance(self):
        assert isinstance(plugin_mod.plugin, PromptPlugin)


# ---------------------------------------------------------------------------
# PromptPlugin.register_subparsers
# ---------------------------------------------------------------------------

class TestRegisterSubparsers:

    def test_registers_prompt_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        PromptPlugin().register_subparsers(subparsers)
        args = parser.parse_args(["prompt"])
        assert args.command == "prompt"

    def test_system_flag_defaults_false(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        PromptPlugin().register_subparsers(subparsers)
        args = parser.parse_args(["prompt"])
        assert args.include_system_prompt is False

    def test_system_flag_set_true(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        PromptPlugin().register_subparsers(subparsers)
        args = parser.parse_args(["prompt", "-s"])
        assert args.include_system_prompt is True


# ---------------------------------------------------------------------------
# PromptPlugin.run — dry-run path
# ---------------------------------------------------------------------------

class TestPluginRunDryRun:

    def test_dry_run_prints_and_returns_early(self, monkeypatch, capsys):
        svc_mock = MagicMock()
        svc_mock._get_model.return_value = "gpt-4o"
        svc_mock.build_prompts.return_value = ("sys", "usr")
        monkeypatch.setattr(plugin_mod, "get_api_key", lambda p: ("fake-key", None))
        monkeypatch.setattr(plugin_mod, "TokenTracker", lambda **_: MagicMock())
        monkeypatch.setattr(plugin_mod, "PromptService", lambda *a, **kw: svc_mock)

        args = _make_args(dry_run=True)
        PromptPlugin().run(args, "prof", None, None, None, None)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        svc_mock.build_prompts.assert_called_once()

    def test_dry_run_with_custom_sampling_params(self, monkeypatch, capsys):
        svc_mock = MagicMock()
        svc_mock._get_model.return_value = "gpt-4o"
        svc_mock.build_prompts.return_value = ("sys", "usr")
        monkeypatch.setattr(plugin_mod, "get_api_key", lambda p: ("fake-key", None))
        monkeypatch.setattr(plugin_mod, "TokenTracker", lambda **_: MagicMock())
        monkeypatch.setattr(plugin_mod, "PromptService", lambda *a, **kw: svc_mock)

        args = _make_args(dry_run=True)
        PromptPlugin().run(args, "prof", "gpt-4o", 0.7, 0.9, 2000)

        out = capsys.readouterr().out
        assert "Temperature: 0.7" in out
        assert "Top-p: 0.9" in out
        assert "Max tokens: 2000" in out


# ---------------------------------------------------------------------------
# PromptPlugin.run — normal path
# ---------------------------------------------------------------------------

class TestPluginRunNormal:

    def _setup(self, monkeypatch, response_text="The answer", output_file=None):
        svc_mock = MagicMock()
        svc_mock.send_prompt.return_value = response_text
        monkeypatch.setattr(plugin_mod, "get_api_key", lambda p: ("fake-key", None))
        monkeypatch.setattr(plugin_mod, "TokenTracker", lambda **_: MagicMock())
        monkeypatch.setattr(plugin_mod, "PromptService", lambda *a, **kw: svc_mock)
        monkeypatch.setattr(plugin_mod, "_collect_multiline", lambda label: "my question")
        return svc_mock

    def test_send_prompt_called_and_output_printed(self, monkeypatch, capsys):
        svc_mock = self._setup(monkeypatch, "Forty-two")
        args = _make_args()
        PromptPlugin().run(args, "prof", None, None, None, None)
        out = capsys.readouterr().out
        assert "Forty-two" in out
        svc_mock.send_prompt.assert_called_once_with("my question", None)

    def test_raises_cli_error_when_prompt_is_blank(self, monkeypatch):
        monkeypatch.setattr(plugin_mod, "get_api_key", lambda p: ("fake-key", None))
        monkeypatch.setattr(plugin_mod, "TokenTracker", lambda **_: MagicMock())
        monkeypatch.setattr(plugin_mod, "PromptService", lambda *a, **kw: MagicMock())
        monkeypatch.setattr(plugin_mod, "_collect_multiline", lambda label: "   ")

        with pytest.raises(CLIError, match="No prompt text provided"):
            PromptPlugin().run(_make_args(), "prof", None, None, None, None)

    def test_send_prompt_exception_becomes_cli_error(self, monkeypatch):
        svc_mock = MagicMock()
        svc_mock.send_prompt.side_effect = RuntimeError("api down")
        monkeypatch.setattr(plugin_mod, "get_api_key", lambda p: ("fake-key", None))
        monkeypatch.setattr(plugin_mod, "TokenTracker", lambda **_: MagicMock())
        monkeypatch.setattr(plugin_mod, "PromptService", lambda *a, **kw: svc_mock)
        monkeypatch.setattr(plugin_mod, "_collect_multiline", lambda label: "question")

        with pytest.raises(CLIError, match="api down"):
            PromptPlugin().run(_make_args(), "prof", None, None, None, None)

    def test_output_file_saved_when_specified(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, "Response text")
        saved: list = []
        monkeypatch.setattr(
            plugin_mod.FileOutputHandler, "save_to_text_file",
            lambda text, path, label="": saved.append((text, path))
        )
        output_path = str(tmp_path / "out.txt")
        args = _make_args(output_file=output_path)
        PromptPlugin().run(args, "prof", None, None, None, None)
        assert len(saved) == 1
        assert saved[0][0] == "Response text"

    def test_include_system_prompt_passes_collected_value(self, monkeypatch, capsys):
        svc_mock = MagicMock()
        svc_mock.send_prompt.return_value = "ok"
        monkeypatch.setattr(plugin_mod, "get_api_key", lambda p: ("fake-key", None))
        monkeypatch.setattr(plugin_mod, "TokenTracker", lambda **_: MagicMock())
        monkeypatch.setattr(plugin_mod, "PromptService", lambda *a, **kw: svc_mock)
        # First call = system prompt, second = user prompt
        call_seq = iter(["my system prompt", "my user prompt"])
        monkeypatch.setattr(plugin_mod, "_collect_multiline", lambda label: next(call_seq))

        args = _make_args(include_system_prompt=True)
        PromptPlugin().run(args, "prof", None, None, None, None)
        svc_mock.send_prompt.assert_called_once_with("my user prompt", "my system prompt")

    def test_include_system_prompt_empty_becomes_none(self, monkeypatch, capsys):
        svc_mock = MagicMock()
        svc_mock.send_prompt.return_value = "ok"
        monkeypatch.setattr(plugin_mod, "get_api_key", lambda p: ("fake-key", None))
        monkeypatch.setattr(plugin_mod, "TokenTracker", lambda **_: MagicMock())
        monkeypatch.setattr(plugin_mod, "PromptService", lambda *a, **kw: svc_mock)
        # Empty system prompt → should pass None to send_prompt
        call_seq = iter(["", "my user prompt"])
        monkeypatch.setattr(plugin_mod, "_collect_multiline", lambda label: next(call_seq))

        args = _make_args(include_system_prompt=True)
        PromptPlugin().run(args, "prof", None, None, None, None)
        svc_mock.send_prompt.assert_called_once_with("my user prompt", None)


# ---------------------------------------------------------------------------
# _collect_multiline
# ---------------------------------------------------------------------------

class TestCollectMultiline:

    def test_sentinel_ends_collection(self, monkeypatch):
        inputs = iter(["hello", "world", "---"])
        monkeypatch.setattr("builtins.input", lambda: next(inputs))
        result = _collect_multiline("Prompt")
        assert result == "hello\nworld"

    def test_eof_ends_collection(self, monkeypatch):
        call_count = [0]

        def fake_input():
            call_count[0] += 1
            if call_count[0] == 1:
                return "line one"
            raise EOFError

        monkeypatch.setattr("builtins.input", fake_input)
        result = _collect_multiline("Prompt")
        assert result == "line one"

    def test_empty_input_returns_empty_string(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "---")
        result = _collect_multiline("Prompt")
        assert result == ""

    def test_label_printed(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda: "---")
        _collect_multiline("My Label")
        out = capsys.readouterr().out
        assert "My Label" in out


# ---------------------------------------------------------------------------
# _dry_run_display
# ---------------------------------------------------------------------------

class TestDryRunDisplay:

    def test_shows_model_and_prompts(self, capsys):
        _dry_run_display("gpt-4o", "sys content", "usr content")
        out = capsys.readouterr().out
        assert "gpt-4o" in out
        assert "sys content" in out
        assert "usr content" in out
        assert "DRY RUN" in out

    def test_optional_params_shown_when_provided(self, capsys):
        _dry_run_display("gpt-4o", "s", "u", temperature=0.5, top_p=0.8, max_tokens=1000)
        out = capsys.readouterr().out
        assert "0.5" in out
        assert "0.8" in out
        assert "1000" in out

    def test_optional_params_hidden_when_none(self, capsys):
        _dry_run_display("gpt-4o", "s", "u")
        out = capsys.readouterr().out
        assert "Temperature" not in out
        assert "Top-p" not in out
        assert "Max tokens" not in out


# ---------------------------------------------------------------------------
# _resolve_output_path
# ---------------------------------------------------------------------------

class TestResolveOutputPath:

    def test_none_when_no_output_file(self):
        args = _make_args(output_file=None)
        assert _resolve_output_path(args) is None

    def test_returns_absolute_path(self, tmp_path):
        rel = "out.txt"
        args = _make_args(output_file=rel)
        result = _resolve_output_path(args)
        assert result is not None
        assert Path(result).is_absolute()

    def test_already_absolute_path_returned(self, tmp_path):
        abs_path = str(tmp_path / "result.txt")
        args = _make_args(output_file=abs_path)
        result = _resolve_output_path(args)
        assert result == abs_path
