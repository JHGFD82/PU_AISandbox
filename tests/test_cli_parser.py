"""
Tests for the CLI argument parser (create_argument_parser).

Validates that all subcommands, flags, and type callbacks are correctly wired
without actually invoking any runtime logic or API calls.
"""

from pathlib import Path

import pytest

from src.cli import create_argument_parser
from src.runtime.plugin_loader import load_plugins

_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"


@pytest.fixture
def parser():
    return create_argument_parser(load_plugins(_PLUGINS_DIR))


# ---------------------------------------------------------------------------
# prompt subcommand
# ---------------------------------------------------------------------------

class TestPromptSubcommand:

    def test_command_set_to_prompt(self, parser):
        args = parser.parse_args(["heller", "prompt"])
        assert args.command == "prompt"

    def test_include_system_prompt_defaults_false(self, parser):
        args = parser.parse_args(["heller", "prompt"])
        assert args.include_system_prompt is False

    def test_include_system_prompt_short_flag(self, parser):
        args = parser.parse_args(["heller", "prompt", "-s"])
        assert args.include_system_prompt is True

    def test_include_system_prompt_long_flag(self, parser):
        args = parser.parse_args(["heller", "prompt", "--system"])
        assert args.include_system_prompt is True

    def test_no_user_prompt_argument(self, parser):
        args = parser.parse_args(["heller", "prompt"])
        assert not hasattr(args, "user_prompt")

    def test_output_file_defaults_none(self, parser):
        args = parser.parse_args(["heller", "prompt"])
        assert args.output_file is None

    def test_output_file_stored_when_provided(self, parser):
        args = parser.parse_args(["heller", "prompt", "-o", "out.txt"])
        assert args.output_file == "out.txt"

    def test_model_defaults_none(self, parser):
        args = parser.parse_args(["heller", "prompt"])
        assert args.model is None

    def test_model_stored_when_provided(self, parser):
        args = parser.parse_args(["heller", "prompt", "-m", "gpt-4o"])
        assert args.model == "gpt-4o"

    def test_dry_run_defaults_false(self, parser):
        args = parser.parse_args(["heller", "prompt"])
        assert args.dry_run is False

    def test_dry_run_sets_true(self, parser):
        args = parser.parse_args(["heller", "prompt", "--dry-run"])
        assert args.dry_run is True

    def test_temperature_defaults_none(self, parser):
        args = parser.parse_args(["heller", "prompt"])
        assert args.temperature is None

    def test_temperature_short_flag_stored(self, parser):
        args = parser.parse_args(["heller", "prompt", "-t", "0.7"])
        assert args.temperature == pytest.approx(0.7)

    def test_top_p_defaults_none(self, parser):
        args = parser.parse_args(["heller", "prompt"])
        assert args.top_p is None

    def test_top_p_short_flag_stored(self, parser):
        args = parser.parse_args(["heller", "prompt", "-T", "1.0"])
        assert args.top_p == pytest.approx(1.0)

    def test_all_flags_together(self, parser):
        args = parser.parse_args(["heller", "prompt", "-s", "-o", "resp.txt", "-m", "gpt-4o-mini"])
        assert args.include_system_prompt is True
        assert args.output_file == "resp.txt"
        assert args.model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Global flags (no professor required)
# ---------------------------------------------------------------------------

class TestGlobalFlags:

    def test_list_models_flag(self, parser):
        args = parser.parse_args(["--list-models"])
        assert args.list_models is True

    def test_list_models_defaults_false(self, parser):
        args = parser.parse_args([])
        assert args.list_models is False

    def test_professor_is_optional(self, parser):
        # Should not raise even with no args
        args = parser.parse_args([])
        assert args.professor is None
