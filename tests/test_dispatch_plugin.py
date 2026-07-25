"""Tests for src/runtime/dispatch_plugin.py — DispatchPlugin."""

import argparse
from unittest.mock import MagicMock

import pytest

from src.errors import CLIError
from src.runtime.dispatch_plugin import DispatchPlugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin(handles, command="translate"):
    """Create a mock ModePlugin with the given handles and command list."""
    p = MagicMock()
    p.handles = handles
    p.commands = [command]
    return p


def _make_dispatcher(primary_handles=("en",), command="translate"):
    primary = _make_plugin(list(primary_handles), command)
    return DispatchPlugin(command, primary), primary


# ---------------------------------------------------------------------------
# __init__ / source_registry
# ---------------------------------------------------------------------------

class TestDispatchPluginInit:

    def test_commands_contains_command(self):
        dp, _ = _make_dispatcher(["en"])
        assert dp.commands == ["translate"]

    def test_source_registry_maps_primary_handles(self):
        dp, primary = _make_dispatcher(["en", "fr"])
        assert dp.source_registry["en"] is primary
        assert dp.source_registry["fr"] is primary

    def test_empty_handles_yields_empty_registry(self):
        dp, _ = _make_dispatcher([])
        assert dp.source_registry == {}


# ---------------------------------------------------------------------------
# _absorb
# ---------------------------------------------------------------------------

class TestAbsorb:

    def test_absorb_adds_new_tokens(self):
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp", "zh"])
        dp._absorb(secondary, "translation-ea")
        assert dp.source_registry["jp"] is secondary
        assert dp.source_registry["zh"] is secondary

    def test_absorb_appends_to_secondary_list(self):
        dp, _ = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        dp._absorb(secondary, "translation-ea")
        assert secondary in dp._secondary

    def test_absorb_duplicate_token_emits_warning(self, caplog):
        import logging
        dp, primary = _make_dispatcher(["en"])
        duplicate = _make_plugin(["en"])  # same token as primary
        with caplog.at_level(logging.WARNING, logger="src.runtime.dispatch_plugin"):
            dp._absorb(duplicate, "other-plugin")
        assert "already owned" in caplog.text

    def test_absorb_duplicate_token_keeps_original_owner(self):
        dp, primary = _make_dispatcher(["en"])
        duplicate = _make_plugin(["en"])
        dp._absorb(duplicate, "other-plugin")
        assert dp.source_registry["en"] is primary

    def test_absorb_mixed_new_and_duplicate(self):
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["en", "jp"])  # "en" is duplicate, "jp" is new
        dp._absorb(secondary, "translation-ea")
        assert dp.source_registry["en"] is primary   # original kept
        assert dp.source_registry["jp"] is secondary  # new registered


# ---------------------------------------------------------------------------
# register_subparsers
# ---------------------------------------------------------------------------

class TestRegisterSubparsers:

    def _make_subparsers(self, command="translate"):
        parser = argparse.ArgumentParser()
        return parser.add_subparsers(dest="command"), parser

    def test_delegates_to_primary(self):
        dp, primary = _make_dispatcher(["en"])
        subparsers, _ = self._make_subparsers()
        # Primary's register_subparsers should add the parser to choices
        def add_parser_side_effect(sp):
            sp.add_parser("translate")
        primary.register_subparsers.side_effect = add_parser_side_effect
        dp.register_subparsers(subparsers)
        primary.register_subparsers.assert_called_once_with(subparsers)

    def test_calls_register_command_flags_on_secondaries(self):
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        dp._absorb(secondary, "translation-ea")

        subparsers, _ = self._make_subparsers()

        def add_parser_side_effect(sp):
            sp.add_parser("translate")
        primary.register_subparsers.side_effect = add_parser_side_effect

        dp.register_subparsers(subparsers)
        secondary.register_command_flags.assert_called_once()

    def test_warns_when_primary_does_not_create_parser(self, caplog):
        import logging
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        dp._absorb(secondary, "translation-ea")

        subparsers, _ = self._make_subparsers()
        # primary.register_subparsers does NOT add a parser → choices stays empty

        with caplog.at_level(logging.WARNING, logger="src.runtime.dispatch_plugin"):
            dp.register_subparsers(subparsers)

        assert "did not create a subparser" in caplog.text
        # secondary flags should NOT be registered
        secondary.register_command_flags.assert_not_called()

    def test_secondary_without_register_command_flags_is_skipped(self):
        """Secondary plugin without register_command_flags should not raise."""
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        del secondary.register_command_flags  # remove the attribute
        dp._absorb(secondary, "translation-ea")

        subparsers, _ = self._make_subparsers()

        def add_parser_side_effect(sp):
            sp.add_parser("translate")
        primary.register_subparsers.side_effect = add_parser_side_effect

        dp.register_subparsers(subparsers)  # should not raise


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

class TestRun:

    def _make_args(self, language_code):
        args = argparse.Namespace()
        args.language_code = language_code
        args.professor = "test"
        return args

    def test_routes_to_correct_owner(self):
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        dp._absorb(secondary, "translation-ea")

        args = self._make_args(("jp", "en"))
        dp.run(args, "test", None, None, None, None)
        secondary.run.assert_called_once()
        primary.run.assert_not_called()

    def test_routes_en_to_primary(self):
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        dp._absorb(secondary, "translation-ea")

        args = self._make_args(("en", "jp"))
        dp.run(args, "test", None, None, None, None)
        primary.run.assert_called_once()

    def test_injects_empty_peer_guidance_when_same_owner(self):
        dp, primary = _make_dispatcher(["en", "fr"])
        args = self._make_args(("en", "fr"))
        dp.run(args, "test", None, None, None, None)
        assert args._peer_guidance == []

    def test_collects_peer_guidance_from_dest_owner(self):
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        secondary.get_peer_guidance.return_value = "Japanese guidance"
        dp._absorb(secondary, "translation-ea")

        args = self._make_args(("en", "jp"))
        dp.run(args, "test", None, None, None, None)
        assert "Japanese guidance" in args._peer_guidance

    def test_no_peer_guidance_when_dest_has_no_method(self):
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        del secondary.get_peer_guidance  # remove method
        dp._absorb(secondary, "translation-ea")

        args = self._make_args(("en", "jp"))
        dp.run(args, "test", None, None, None, None)
        assert args._peer_guidance == []

    def test_no_peer_guidance_when_guidance_is_empty_string(self):
        dp, primary = _make_dispatcher(["en"])
        secondary = _make_plugin(["jp"])
        secondary.get_peer_guidance.return_value = ""
        dp._absorb(secondary, "translation-ea")

        args = self._make_args(("en", "jp"))
        dp.run(args, "test", None, None, None, None)
        assert args._peer_guidance == []

    def test_string_language_code_routes_to_owner(self):
        # A plain string language_code (used by transcription plugins) should
        # route to the plugin whose handles list contains that string.
        dp, primary = _make_dispatcher(["English"])
        args = self._make_args("English")  # string, not tuple
        dp.run(args, "test", None, None, None, None)
        primary.run.assert_called_once()

    def test_raises_cli_error_on_single_element_tuple(self):
        dp, _ = _make_dispatcher(["en"])
        args = self._make_args(("en",))
        with pytest.raises(CLIError, match="language-code pair"):
            dp.run(args, "test", None, None, None, None)

    def test_raises_cli_error_on_missing_language_code(self):
        dp, _ = _make_dispatcher(["en"])
        args = argparse.Namespace()  # no language_code attribute
        with pytest.raises(CLIError, match="language-code pair"):
            dp.run(args, "test", None, None, None, None)

    def test_raises_cli_error_when_source_not_owned(self):
        dp, _ = _make_dispatcher(["en"])
        args = self._make_args(("xx", "en"))
        with pytest.raises(CLIError, match="No plugin handles 'xx'"):
            dp.run(args, "test", None, None, None, None)

    def test_dest_token_not_owned_logs_debug(self, caplog):
        import logging
        dp, primary = _make_dispatcher(["en"])
        args = self._make_args(("en", "xx"))  # "xx" not in registry
        with caplog.at_level(logging.DEBUG, logger="src.runtime.dispatch_plugin"):
            dp.run(args, "test", None, None, None, None)
        assert "no plugin owns destination token" in caplog.text

    def test_forwards_run_kwargs(self):
        dp, primary = _make_dispatcher(["en"])
        args = self._make_args(("en", "en"))
        dp.run(args, "myprof", "gpt-4o", 0.5, 0.9, 1000)
        primary.run.assert_called_once_with(
            args, "myprof", "gpt-4o", 0.5, 0.9, 1000
        )


# ---------------------------------------------------------------------------
# Webui composer action passthrough (__getattr__)
# ---------------------------------------------------------------------------
# Regression coverage for a real bug: whenever a language-extension plugin
# (e.g. translation-ea) is installed, load_plugins() wraps the base
# translate/transcribe plugin in a DispatchPlugin — so jobs.py's
# getattr(p, "ui_action", None) and hasattr(p, "run_ui_action") checks were
# seeing the DispatchPlugin, not the base plugin that actually declares
# these, and silently found nothing. Caught via manual end-to-end testing
# against this project's own installation (which has translation-ea and
# transcription-ea installed), not by any of the plugin-local unit tests.

class TestUiActionPassthrough:

    def test_ui_action_proxied_from_primary(self):
        dp, primary = _make_dispatcher(["en"])
        sentinel = object()
        primary.ui_action = sentinel
        assert dp.ui_action is sentinel

    def test_run_ui_action_proxied_and_callable(self):
        dp, primary = _make_dispatcher(["en"])
        primary.run_ui_action.return_value = "result"
        result = dp.run_ui_action({"a": 1}, "prof", "gpt-4o", None, "/tmp/out")
        assert result == "result"
        primary.run_ui_action.assert_called_once_with({"a": 1}, "prof", "gpt-4o", None, "/tmp/out")

    def test_preview_ui_action_proxied_and_callable(self):
        dp, primary = _make_dispatcher(["en"])
        primary.preview_ui_action.return_value = "preview"
        result = dp.preview_ui_action({"a": 1}, "prof", "gpt-4o")
        assert result == "preview"

    def test_hasattr_false_when_primary_has_none_of_it(self):
        # spec= limits the mock's attribute surface to exactly this list —
        # unlike a bare MagicMock(), which would auto-create ui_action/
        # run_ui_action/preview_ui_action on any access and mask this bug.
        primary = MagicMock(spec=["handles", "commands", "run", "register_subparsers"])
        primary.handles = ["en"]
        primary.commands = ["translate"]
        dp = DispatchPlugin("translate", primary)

        assert getattr(dp, "ui_action", None) is None
        assert not hasattr(dp, "run_ui_action")
        assert not hasattr(dp, "preview_ui_action")

    def test_unrelated_unknown_attribute_still_raises(self):
        dp, _primary = _make_dispatcher(["en"])
        with pytest.raises(AttributeError):
            dp.something_totally_unrelated
