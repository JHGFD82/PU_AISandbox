"""
Tests for cli.main() and setup_logging() (no API calls):
  - setup_logging() runs without error
  - main() with --list-models → calls handle_info_commands, returns normally
  - main() with no args → CLIError → sys.exit(1)
  - main() with professor but no command → CLIError → sys.exit(1)
  - main() with professor + prompt command → SandboxProcessor.run called
  - main() with professor + usage command → handle_info_commands called
  - main() with unknown command → CLIError → sys.exit(1)
"""

import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from src.cli import main, setup_logging


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:

    def test_runs_without_error(self):
        # Should complete without raising
        setup_logging()


# ---------------------------------------------------------------------------
# main() — global commands (no professor required)
# ---------------------------------------------------------------------------


class TestMainGlobalCommands:

    def test_list_models_calls_handle_info_commands(self):
        with patch("src.cli.handle_info_commands", return_value=True) as mock_handler, \
             patch("sys.argv", ["main.py", "--list-models"]):
            main()
        mock_handler.assert_called_once()

    def test_list_models_returns_without_error(self):
        with patch("src.cli.handle_info_commands", return_value=True), \
             patch("sys.argv", ["main.py", "--list-models"]):
            main()  # Should not raise


# ---------------------------------------------------------------------------
# main() — missing professor → sys.exit(1)
# ---------------------------------------------------------------------------


class TestMainNoProfessor:

    def test_no_args_exits_with_code_1(self):
        with patch("sys.argv", ["main.py"]), \
             pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_no_args_prints_error_to_stderr(self, capsys):
        with patch("sys.argv", ["main.py"]), \
             pytest.raises(SystemExit):
            main()
        err = capsys.readouterr().err
        assert "Error" in err


# ---------------------------------------------------------------------------
# main() — professor present but no command → sys.exit(1)
#
# Note: argparse's own subcommand handling may intercept bare-professor invocations
# before reaching main()'s CLIError check.  We bypass argparse here by mocking
# parse_args so the CLIError branch ("if not args.command") is exercised directly.
# ---------------------------------------------------------------------------


class TestMainNoCommand:

    def _make_args_professor_only(self):
        return Namespace(
            show_config=False,
            list_models=False,
            professor="heller",
            command=None,
        )

    def test_professor_only_exits_with_code_1(self):
        with patch("src.cli.create_argument_parser") as mock_factory, \
             pytest.raises(SystemExit) as exc_info:
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = self._make_args_professor_only()
            mock_factory.return_value = mock_parser
            main()
        assert exc_info.value.code == 1

    def test_professor_only_prints_error_to_stderr(self, capsys):
        with patch("src.cli.create_argument_parser") as mock_factory, \
             pytest.raises(SystemExit):
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = self._make_args_professor_only()
            mock_factory.return_value = mock_parser
            main()
        err = capsys.readouterr().err
        assert "Error" in err


# ---------------------------------------------------------------------------
# main() — professor + usage command
# ---------------------------------------------------------------------------


class TestMainUsageCommand:

    def test_usage_report_calls_handle_info_commands(self):
        with patch("src.cli.handle_info_commands", return_value=True) as mock_handler, \
             patch("sys.argv", ["main.py", "heller", "usage", "report"]):
            main()
        mock_handler.assert_called_once()

    def test_usage_months_calls_handle_info_commands(self):
        with patch("src.cli.handle_info_commands", return_value=True) as mock_handler, \
             patch("sys.argv", ["main.py", "heller", "usage", "months"]):
            main()
        mock_handler.assert_called_once()


# ---------------------------------------------------------------------------
# main() — professor + prompt command
# ---------------------------------------------------------------------------


class TestMainPromptCommand:

    def test_prompt_creates_sandbox_processor_and_calls_run(self):
        mock_sandbox = MagicMock()
        with patch("src.cli.SandboxProcessor", return_value=mock_sandbox) as mock_cls, \
             patch("sys.argv", ["main.py", "heller", "prompt"]):
            main()
        mock_cls.assert_called_once_with("heller", model=None, temperature=None, top_p=None, max_tokens=None)
        mock_sandbox.run.assert_called_once()

    def test_prompt_passes_model_temperature_top_p(self):
        mock_sandbox = MagicMock()
        with patch("src.cli.SandboxProcessor", return_value=mock_sandbox) as mock_cls, \
             patch("sys.argv", ["main.py", "heller", "prompt", "-m", "gpt-4o", "-t", "1.0", "-T", "0.8"]):
            main()
        mock_cls.assert_called_once_with("heller", model="gpt-4o", temperature=1.0, top_p=0.8, max_tokens=None)


# ---------------------------------------------------------------------------
# main() — unknown command → sys.exit(1)
# ---------------------------------------------------------------------------


class TestMainUnknownCommand:

    def test_unknown_command_exits_with_code_1(self):
        # Inject a fake Namespace with an unrecognised command to bypass argparse
        fake_args = Namespace(
            show_config=False,
            list_models=False,
            professor="heller",
            command="fly",
            model=None,
        )
        with patch("src.cli.create_argument_parser") as mock_parser_factory, \
             pytest.raises(SystemExit) as exc_info:
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = fake_args
            mock_parser_factory.return_value = mock_parser
            main()
        assert exc_info.value.code == 1
