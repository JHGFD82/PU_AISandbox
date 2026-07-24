"""
Tests for the CLI argument parser (create_argument_parser).

Validates that all subcommands, flags, and type callbacks are correctly wired
without actually invoking any runtime logic or API calls.
"""

from pathlib import Path

import pytest

from src.cli import _insert_professor_placeholder_if_needed, create_argument_parser
from src.runtime.plugin_loader import load_plugins

_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"


@pytest.fixture
def parser():
    return create_argument_parser(load_plugins(_PLUGINS_DIR))


@pytest.fixture
def parse_professor_optional(parser):
    """Parse argv the same way main() does: through the placeholder-insertion
    workaround for professor-less commands (env, webui) before parse_args().
    Calling parser.parse_args() directly on these argv would hit the same
    argparse ambiguity _insert_professor_placeholder_if_needed() exists to
    fix — see that function's docstring in src/cli.py.
    """
    plugins = load_plugins(_PLUGINS_DIR)

    def _parse(argv):
        return parser.parse_args(_insert_professor_placeholder_if_needed(argv, plugins))

    return _parse


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

class TestUsageSourcesSubcommand:

    def test_sources_list_parses(self, parser):
        args = parser.parse_args(["heller", "usage", "sources", "list"])
        assert args.command == "usage"
        assert args.usage_subcommand == "sources"
        assert args.sources_subcommand == "list"

    def test_sources_add_flags_parse(self, parser):
        args = parser.parse_args([
            "heller", "usage", "sources", "add",
            "--label", "Prof. Smith", "--path", "/tmp/shared",
            "--mode", "shared-write", "--for-professor", "smith",
        ])
        assert args.label == "Prof. Smith"
        assert args.path == "/tmp/shared"
        assert args.mode == "shared-write"
        assert args.for_professor == "smith"

    def test_sources_add_flags_default_none(self, parser):
        args = parser.parse_args(["heller", "usage", "sources", "add"])
        assert args.label is None
        assert args.path is None
        assert args.mode is None
        assert args.for_professor is None

    def test_sources_add_rejects_invalid_mode(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args([
                "heller", "usage", "sources", "add", "--mode", "read-write",
            ])

    def test_sources_remove_requires_label(self, parser):
        args = parser.parse_args(["heller", "usage", "sources", "remove", "Johnson"])
        assert args.label == "Johnson"

    def test_professor_flag_not_shadowed_by_for_professor(self, parser):
        """--for-professor must not collide with the top-level positional professor."""
        args = parser.parse_args([
            "heller", "usage", "sources", "add", "--for-professor", "smith",
        ])
        assert args.professor == "heller"
        assert args.for_professor == "smith"


class TestEnvSubcommand:

    def test_env_requires_no_professor(self, parse_professor_optional):
        # 'env' alone parses fine with no professor positional supplied.
        args = parse_professor_optional(["env", "list"])
        assert args.command == "env"
        # main() converts the "" placeholder back to None after parse_args();
        # parse_args() alone still returns the raw placeholder value.
        assert args.professor == ""
        assert args.env_subcommand == "list"

    def test_env_add_professor_parses(self, parse_professor_optional):
        args = parse_professor_optional(["env", "add-professor"])
        assert args.env_subcommand == "add-professor"
        assert args.name is None

    def test_env_add_professor_name_flag(self, parse_professor_optional):
        args = parse_professor_optional(["env", "add-professor", "--name", "Jeff Heller"])
        assert args.name == "Jeff Heller"

    def test_env_remove_professor_requires_identifier(self, parse_professor_optional):
        args = parse_professor_optional(["env", "remove-professor", "heller"])
        assert args.identifier == "heller"

    def test_env_set_requires_key(self, parse_professor_optional):
        args = parse_professor_optional(["env", "set", "WEBUI_SESSION_SECRET"])
        assert args.key == "WEBUI_SESSION_SECRET"
        assert args.generate is False

    def test_env_set_generate_flag(self, parse_professor_optional):
        args = parse_professor_optional(["env", "set", "WEBUI_SESSION_SECRET", "--generate"])
        assert args.generate is True

    def test_env_unset_requires_key(self, parse_professor_optional):
        args = parse_professor_optional(["env", "unset", "WEBUI_SESSION_SECRET"])
        assert args.key == "WEBUI_SESSION_SECRET"


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
