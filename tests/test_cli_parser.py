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

    def test_sources_add_rejects_invalid_mode(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args([
                "heller", "usage", "sources", "add", "--mode", "read-write",
            ])

class TestSettingsSubcommand:

    def test_settings_requires_no_netid(self, parse_professor_optional):
        # 'settings' alone parses fine with no netID positional supplied.
        args = parse_professor_optional(["settings", "list"])
        assert args.command == "settings"
        # main() converts the "" placeholder back to None after parse_args();
        # parse_args() alone still returns the raw placeholder value.
        assert args.professor == ""
        assert args.settings_subcommand == "list"

    def test_settings_add_professor_parses(self, parse_professor_optional):
        args = parse_professor_optional(["settings", "add-professor"])
        assert args.settings_subcommand == "add-professor"
        assert args.name is None

    def test_env_add_professor_name_flag(self, parse_professor_optional):
        args = parse_professor_optional(["settings", "add-professor", "--name", "Jeff Heller"])
        assert args.name == "Jeff Heller"

class TestGlobalFlagsBeforeProfessorlessCommand:
    """A global flag typed before a professor-less command must not break it.

    ``python main.py --verbose webui serve`` used to fail with
    ``error: argument command: invalid choice: 'serve'`` — the placeholder
    workaround only looked at the first word, saw ``--verbose``, and did
    nothing. The error named 'serve', so it pointed at the wrong word too.
    """

    def test_verbose_before_webui_subcommand(self, parse_professor_optional):
        args = parse_professor_optional(["--verbose", "webui", "serve"])
        assert args.command == "webui"
        assert args.webui_subcommand == "serve"
        assert args.verbose is True
        assert args.professor == ""

    def test_multiple_global_flags_before_command(self, parse_professor_optional):
        args = parse_professor_optional(["--debug-api", "--verbose", "webui", "serve"])
        assert args.command == "webui"
        assert args.webui_subcommand == "serve"
        assert args.verbose is True
        assert args.debug_api is True

    def test_verbose_before_settings_subcommand(self, parse_professor_optional):
        args = parse_professor_optional(["--verbose", "settings", "list"])
        assert args.command == "settings"
        assert args.settings_subcommand == "list"
        assert args.verbose is True

    def test_flags_keep_their_position(self):
        """The placeholder goes immediately before the command, not at the front.

        The flag stays where the user typed it; only the professor slot is
        filled in. Checked directly because it's the shape argparse is known
        to handle, and a change here would be invisible from parse results.
        """
        plugins = load_plugins(_PLUGINS_DIR)
        result = _insert_professor_placeholder_if_needed(
            ["--verbose", "webui", "serve"], plugins
        )
        assert result == ["--verbose", "", "webui", "serve"]

    def test_professor_command_is_left_alone(self):
        """A command that does need a professor gets no placeholder."""
        plugins = load_plugins(_PLUGINS_DIR)
        argv = ["--verbose", "heller", "prompt"]
        assert _insert_professor_placeholder_if_needed(argv, plugins) == argv

    def test_flags_only_argv_is_left_alone(self):
        """No command word at all — nothing to insert a placeholder before."""
        plugins = load_plugins(_PLUGINS_DIR)
        argv = ["--list-models"]
        assert _insert_professor_placeholder_if_needed(argv, plugins) == argv


class TestDebugFlagsInEitherPosition:
    """--verbose and --debug-api work before the professor name or after the command.

    Both positions are offered, and the tool's own usage line advertises the
    first one. Before this was fixed, a command's own copy of the flag wrote
    its "off" default over the value the earlier one had already set, so
    ``main.py --verbose heller translate ...`` ran with debug logging
    silently switched off — no error, just no debug output.
    """

    @pytest.mark.parametrize("argv", [
        ["--verbose", "heller", "prompt"],
        ["heller", "prompt", "--verbose"],
        ["--verbose", "heller", "usage", "report"],
        ["heller", "usage", "report", "--verbose"],
        ["--verbose", "heller", "usage", "sources", "list"],
    ])
    def test_verbose_is_honored(self, parser, argv):
        assert parser.parse_args(argv).verbose is True

    @pytest.mark.parametrize("argv", [
        ["--debug-api", "heller", "prompt"],
        ["heller", "prompt", "--debug-api"],
        ["--debug-api", "heller", "usage", "report"],
    ])
    def test_debug_api_is_honored(self, parser, argv):
        assert parser.parse_args(argv).debug_api is True

    def test_both_flags_together(self, parser):
        args = parser.parse_args(["--verbose", "--debug-api", "heller", "prompt"])
        assert args.verbose is True
        assert args.debug_api is True

    @pytest.mark.parametrize("argv", [
        [],
        ["heller", "prompt"],
        ["heller", "usage", "report"],
        ["--list-models"],
    ])
    def test_off_by_default(self, parser, argv):
        """Suppressing the per-command defaults must not lose the value entirely."""
        args = parser.parse_args(argv)
        assert args.verbose is False
        assert args.debug_api is False

    def test_verbose_before_professorless_command(self, parse_professor_optional):
        assert parse_professor_optional(["--verbose", "settings", "list"]).verbose is True


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


class TestSettingsAreEditedInFilesNotByCommand:
    """The command line stopped being a settings editor.

    Anyone comfortable typing these commands can open settings.toml, and anyone
    who would rather not has the web interface's settings page. What survived is
    only what an editor cannot do: make the files in the first place, take an
    API key at a hidden prompt, hash a passphrase, generate a secret, or find
    something out from a provider and write down the answer.
    """

    GONE = [
        ["settings", "set", "webui.session_secret"],
        ["settings", "unset", "webui.session_secret"],
        ["settings", "remove-professor", "jh43"],
        ["jh43", "usage", "sources", "add"],
        ["jh43", "usage", "sources", "remove", "lab"],
    ]

    KEPT = [
        ["settings", "setup"],
        ["settings", "add-professor"],
        ["settings", "list"],
        ["settings", "export-shared"],
        ["settings", "test-model"],
        ["settings", "model-quirks"],
        ["webui", "set-passphrase"],
        ["webui", "set-session-secret"],
        ["jh43", "usage", "sources", "list"],
        ["jh43", "usage", "report"],
    ]

    @pytest.mark.parametrize("argv", GONE)
    def test_the_editing_commands_are_gone(self, parse_professor_optional, argv):
        with pytest.raises(SystemExit):
            parse_professor_optional(argv)

    @pytest.mark.parametrize("argv", KEPT)
    def test_what_earns_its_place_still_parses(self, parse_professor_optional, argv):
        parse_professor_optional(argv)

    def test_a_secret_is_generated_rather_than_typed(self, parse_professor_optional):
        """Its only requirement is being unguessable, so nobody should choose it."""
        args = parse_professor_optional(["webui", "set-session-secret"])
        assert args.webui_subcommand == "set-session-secret"

    def test_the_help_says_where_settings_are_edited_instead(self):
        """Removing a command without saying so leaves someone hunting for it."""
        from src.cli import _available_commands_hint

        text = _available_commands_hint({})
        assert "settings.toml" in text and "preferences.toml" in text
        assert "web interface" in text

    def test_reading_is_still_offered(self, parse_professor_optional):
        """'list' says whether a secret is set; opening the file shows the secret."""
        args = parse_professor_optional(["settings", "list"])
        assert args.settings_subcommand == "list"


class TestTheDocsDoNotNameACommandThatIsGone:
    """A reference telling someone to run a command that no longer exists is
    worse than no reference: they will assume they typed it wrong."""

    GONE = ["settings set ", "settings unset", "settings remove-professor",
            "usage sources add", "usage sources remove"]

    def test_no_document_names_a_removed_command(self):
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for f in list((root / "docs").glob("*.md")) + [root / "README.md"]:
            for number, line in enumerate(f.read_text().splitlines(), 1):
                for gone in self.GONE:
                    if gone in line:
                        offenders.append(f"{f.name}:{number} {line.strip()[:70]}")
        assert not offenders, "\n".join(offenders)

    def test_the_reference_says_where_settings_are_edited_instead(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "docs" / "cli-reference.md").read_text()
        assert "There is no command for changing a setting" in text
        assert "preferences.toml" in text
