"""Tests for src/runtime/command_runner.py — _CommandMixin methods."""

import argparse
import sys
from typing import Optional
from unittest.mock import patch

import pytest

from src.runtime.command_runner import _CommandMixin
from src.errors import CLIError


# ---------------------------------------------------------------------------
# Minimal concrete implementation so we can instantiate _CommandMixin
# ---------------------------------------------------------------------------

class _FakeMixin(_CommandMixin):
    """Concrete subclass of _CommandMixin for testing its static helpers."""

    def __init__(self):
        pass


def _make_mixin():
    return _FakeMixin()


# ---------------------------------------------------------------------------
# _collect_multiline
# ---------------------------------------------------------------------------

class TestCollectMultiline:

    def test_collects_lines_until_sentinel(self, monkeypatch):
        inputs = iter(["line one", "line two", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        result = _CommandMixin._collect_multiline("Enter text")
        assert result == "line one\nline two"

    def test_eof_ends_collection(self, monkeypatch):
        call_count = 0

        def raise_eof(*_):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "first line"
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        result = _CommandMixin._collect_multiline("Enter text")
        assert result == "first line"

    def test_empty_input_returns_empty_string(self, monkeypatch):
        inputs = iter(["---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        result = _CommandMixin._collect_multiline("Enter text")
        assert result == ""

    def test_prints_label_before_collecting(self, monkeypatch, capsys):
        inputs = iter(["---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        _CommandMixin._collect_multiline("My Label")
        out = capsys.readouterr().out
        assert "My Label" in out


# ---------------------------------------------------------------------------
# _collect_notes
# ---------------------------------------------------------------------------

class TestCollectNotes:

    def test_system_note_only(self, monkeypatch):
        inputs = iter(["system", "my system note", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        sys_note, usr_note = _CommandMixin._collect_notes()
        assert sys_note == "my system note"
        assert usr_note is None

    def test_user_note_only(self, monkeypatch):
        inputs = iter(["user", "my user note", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        sys_note, usr_note = _CommandMixin._collect_notes()
        assert sys_note is None
        assert usr_note == "my user note"

    def test_both_note(self, monkeypatch):
        inputs = iter(["both", "shared note", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        sys_note, usr_note = _CommandMixin._collect_notes()
        assert sys_note == "shared note"
        assert usr_note == "shared note"

    def test_separate_notes(self, monkeypatch):
        inputs = iter(["separate", "sys text", "---", "usr text", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        sys_note, usr_note = _CommandMixin._collect_notes()
        assert sys_note == "sys text"
        assert usr_note == "usr text"

    def test_eof_returns_none_none(self, monkeypatch):
        def raise_eof(*_):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        sys_note, usr_note = _CommandMixin._collect_notes()
        assert sys_note is None
        assert usr_note is None

    def test_invalid_choice_reprompts(self, monkeypatch):
        inputs = iter(["invalid", "system", "note text", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        sys_note, usr_note = _CommandMixin._collect_notes()
        assert sys_note == "note text"

    def test_shows_prompts_when_provided(self, monkeypatch, capsys):
        inputs = iter(["user", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        _CommandMixin._collect_notes(system_prompt="SYS", user_prompt="USR")
        out = capsys.readouterr().out
        assert "SYS" in out
        assert "USR" in out

    def test_empty_note_returns_none_none(self, monkeypatch):
        inputs = iter(["both", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        sys_note, usr_note = _CommandMixin._collect_notes()
        assert sys_note is None
        assert usr_note is None


# ---------------------------------------------------------------------------
# _dry_run_display
# ---------------------------------------------------------------------------

class TestDryRunDisplay:

    def test_shows_model_and_prompts(self, capsys):
        _CommandMixin._dry_run_display("gpt-4o", "SYSTEM_PROMPT", "USER_PROMPT")
        out = capsys.readouterr().out
        assert "gpt-4o" in out
        assert "SYSTEM_PROMPT" in out
        assert "USER_PROMPT" in out
        assert "DRY RUN" in out

    def test_shows_optional_sampling_params(self, capsys):
        _CommandMixin._dry_run_display(
            "gpt-4o", "sys", "usr",
            temperature=0.5, top_p=0.9, max_tokens=100
        )
        out = capsys.readouterr().out
        assert "0.5" in out
        assert "0.9" in out
        assert "100" in out

    def test_shows_note_when_provided(self, capsys):
        _CommandMixin._dry_run_display("gpt-4o", "sys", "usr", note="Note here")
        out = capsys.readouterr().out
        assert "Note here" in out

    def test_omits_none_sampling_params(self, capsys):
        _CommandMixin._dry_run_display("gpt-4o", "sys", "usr")
        out = capsys.readouterr().out
        assert "Temperature" not in out
        assert "Top-p" not in out
        assert "Max tokens" not in out


# ---------------------------------------------------------------------------
# _resolve_output_path
# ---------------------------------------------------------------------------

class TestResolveOutputPath:

    def test_returns_none_when_no_output_file(self):
        mixin = _make_mixin()
        args = argparse.Namespace(output_file=None, input_file=None)
        assert mixin._resolve_output_path(args) is None

    def test_absolute_output_file_returned_as_is(self, tmp_path):
        mixin = _make_mixin()
        abs_out = str(tmp_path / "out.txt")
        args = argparse.Namespace(output_file=abs_out, input_file=None)
        result = mixin._resolve_output_path(args)
        assert result == abs_out

    def test_relative_output_placed_next_to_input(self, tmp_path):
        mixin = _make_mixin()
        input_file = str(tmp_path / "input.txt")
        args = argparse.Namespace(output_file="out.txt", input_file=input_file)
        result = mixin._resolve_output_path(args)
        assert result == str(tmp_path / "out.txt")

    def test_relative_output_without_input_file_made_absolute(self, tmp_path):
        mixin = _make_mixin()
        args = argparse.Namespace(output_file="out.txt", input_file=None)
        result = mixin._resolve_output_path(args)
        import os
        assert os.path.isabs(result)
        assert result.endswith("out.txt")


# ---------------------------------------------------------------------------
# Additional branch-coverage tests for previously untested paths
# ---------------------------------------------------------------------------

class TestCollectNotesBranches:
    """Cover the system_prompt-only and user_prompt-only display branches."""

    def test_shows_only_user_prompt_when_system_is_none(self, monkeypatch, capsys):
        """Branch 97->100: system_prompt is None but user_prompt is not None."""
        inputs = iter(["user", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        _CommandMixin._collect_notes(system_prompt=None, user_prompt="USR_PROMPT")
        out = capsys.readouterr().out
        assert "USR_PROMPT" in out
        assert "SYSTEM PROMPT" not in out

    def test_shows_only_system_prompt_when_user_is_none(self, monkeypatch, capsys):
        """Branch 100->103: system_prompt is not None but user_prompt is None."""
        inputs = iter(["system", "note text", "---"])
        monkeypatch.setattr("builtins.input", lambda *_: next(inputs))
        sys_note, usr_note = _CommandMixin._collect_notes(system_prompt="SYS_PROMPT", user_prompt=None)
        out = capsys.readouterr().out
        assert "SYS_PROMPT" in out
        assert "USER PROMPT" not in out
        assert sys_note == "note text"


# ---------------------------------------------------------------------------
# _apply_inline_notes
# ---------------------------------------------------------------------------

class _Svc:
    """Minimal service stand-in for _apply_inline_notes tests."""


class TestApplyInlineNotes:

    def test_note_both_sets_system_and_user(self):
        args = argparse.Namespace(note_both="shared", note_system=None, note_user=None)
        svc = _Svc()
        _CommandMixin._apply_inline_notes(svc, args)
        assert svc.system_note == "shared"
        assert svc.user_note == "shared"

    def test_note_system_overrides_both_for_system_slot(self):
        """note_system takes precedence over note_both for the system slot."""
        args = argparse.Namespace(note_both="both_text", note_system="sys_only", note_user=None)
        svc = _Svc()
        _CommandMixin._apply_inline_notes(svc, args)
        assert svc.system_note == "sys_only"
        assert svc.user_note == "both_text"

    def test_note_user_sets_only_user(self):
        args = argparse.Namespace(note_both=None, note_system=None, note_user="usr_text")
        svc = _Svc()
        _CommandMixin._apply_inline_notes(svc, args)
        assert svc.user_note == "usr_text"
        assert not hasattr(svc, "system_note")

    def test_no_notes_leaves_service_unchanged(self):
        args = argparse.Namespace(note_both=None, note_system=None, note_user=None)
        svc = _Svc()
        _CommandMixin._apply_inline_notes(svc, args)
        assert not hasattr(svc, "system_note")
        assert not hasattr(svc, "user_note")

    def test_missing_note_attrs_default_to_none(self):
        """Args without note attributes should not raise."""
        args = argparse.Namespace()
        svc = _Svc()
        _CommandMixin._apply_inline_notes(svc, args)
        assert not hasattr(svc, "system_note")
        assert not hasattr(svc, "user_note")


# ---------------------------------------------------------------------------
# _sampling_kwargs
# ---------------------------------------------------------------------------

class TestSamplingKwargs:

    def test_returns_all_three_keys_from_args(self):
        args = argparse.Namespace(temperature=0.7, top_p=0.9, max_tokens=512)
        result = _CommandMixin._sampling_kwargs(args)
        assert result == {"temperature": 0.7, "top_p": 0.9, "max_tokens": 512}

    def test_returns_none_when_attrs_absent(self):
        args = argparse.Namespace()
        result = _CommandMixin._sampling_kwargs(args)
        assert result == {"temperature": None, "top_p": None, "max_tokens": None}

    def test_none_values_propagate(self):
        args = argparse.Namespace(temperature=None, top_p=None, max_tokens=None)
        result = _CommandMixin._sampling_kwargs(args)
        assert result["temperature"] is None
        assert result["top_p"] is None
        assert result["max_tokens"] is None

