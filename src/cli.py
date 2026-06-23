"""CLI controller: argument parsing, validation, and top-level dispatch."""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .errors import CLIError
from .runtime import ModePlugin, handle_info_commands, load_plugins

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def _add_debug_flags(parser: argparse.ArgumentParser) -> None:
    """Add shared debug flags to *parser* if they are not already present."""
    existing = {
        opt
        for action in parser._actions
        for opt in getattr(action, "option_strings", [])
    }
    if "--verbose" not in existing:
        parser.add_argument(
            '--verbose',
            dest='verbose',
            action='store_true',
            help='Enable verbose debug logging',
        )
    if "--debug-api" not in existing:
        parser.add_argument(
            '--debug-api',
            dest='debug_api',
            action='store_true',
            help='Log raw API payload details for troubleshooting provider errors',
        )


def setup_logging(verbose: bool = False) -> None:
    """Set up logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='%(asctime)s - %(levelname)s - %(message)s')


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by translate, transcribe, and prompt subparsers."""
    _add_debug_flags(parser)
    parser.add_argument('-o', '--output', dest='output_file', type=str, help='Output file path')
    parser.add_argument('-m', '--model', dest='model', type=str, help='Model to use (e.g., gpt-4o, gpt-4o-mini)')
    parser.add_argument('-t', '--temperature', dest='temperature', type=float, default=None, help='Sampling temperature override (0.0–2.0)')
    parser.add_argument('-T', '--top-p', dest='top_p', type=float, default=None, help='Nucleus sampling top-p override (0.0–1.0)')
    parser.add_argument('-M', '--max-tokens', dest='max_tokens', type=int, default=None, help='Maximum response tokens (overrides model default)')
    parser.add_argument('--dry-run', dest='dry_run', action='store_true', help='Print the prompt(s) that would be sent without making any API calls')


def add_notes_flags(parser: argparse.ArgumentParser) -> None:
    """Add the interactive and inline notes flags to a subparser."""
    parser.add_argument(
        '-n', '--notes',
        dest='notes',
        action='store_true',
        help='Interactively append ad-hoc notes to the system prompt, user prompt, or both before sending',
    )
    parser.add_argument('-ns', '--note-system', dest='note_system', type=str, default=None, metavar='TEXT',
                        help='Inline note appended to the system prompt')
    parser.add_argument('-nu', '--note-user', dest='note_user', type=str, default=None, metavar='TEXT',
                        help='Inline note appended to the user prompt')
    parser.add_argument('-nb', '--note-both', dest='note_both', type=str, default=None, metavar='TEXT',
                        help='Inline note appended to both the system and user prompts')


def _build_usage_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'usage' command and its subcommands (report, months, daily)."""
    usage_parser = subparsers.add_parser('usage', help='View token usage and costs')
    _add_debug_flags(usage_parser)
    usage_subparsers = usage_parser.add_subparsers(dest='usage_subcommand', help='Usage subcommand')

    # usage report [YYYY-MM]
    report_parser = usage_subparsers.add_parser(
        'report',
        help='Display usage report (current month by default)',
    )
    _add_debug_flags(report_parser)
    report_parser.add_argument(
        'month',
        type=str,
        nargs='?',
        default=None,
        metavar='YYYY-MM',
        help='Archived month to report on (e.g. 2025-07). Omit for current month.',
    )
    report_parser.add_argument(
        '--all-time',
        action='store_true',
        default=False,
        help='Include all-time totals aggregated from all archived months (current month only)',
    )

    # usage months
    months_parser = usage_subparsers.add_parser('months', help='List all archived months for this professor')
    _add_debug_flags(months_parser)

    # usage daily [date]
    daily_parser = usage_subparsers.add_parser('daily', help='Display daily usage')
    _add_debug_flags(daily_parser)
    daily_parser.add_argument(
        'date',
        type=str,
        nargs='?',
        default='today',
        help='Date in YYYY-MM-DD format (defaults to today)',
    )


def create_argument_parser(
    plugins: Optional[dict[str, ModePlugin]] = None,
) -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description='Princeton University AI Sandbox — document processing and AI prompt tools',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\nUsage / reporting:
  python main.py heller usage report              Current month report + budget status
  python main.py heller usage report --all-time   Above + all-time totals across all archived months
  python main.py heller usage report 2025-07      Report for a specific archived month
  python main.py heller usage months              List all archived month files
  python main.py heller usage daily               Today's usage
  python main.py heller usage daily 2026-03-01    Usage for a specific date

Global commands (no professor required):
  python main.py --show-config
  python main.py --list-models

Specifying a model (-m / --model):
  Already in catalog — use the bare model name:
    python main.py heller prompt -m gpt-4o
    python main.py heller prompt -m gpt-4o-mini
  Not yet in catalog — use 'provider/model' to auto-register from PortKey:
    python main.py heller prompt -m openai/gpt-4o-new
    python main.py heller prompt -m google/gemini-2.5-pro
  Supported auto-register providers: openai, google.
  For all other providers, add the model directly to src/model_catalog.json.

Custom prompt:
  python main.py heller prompt                   Interactive user prompt
  python main.py heller prompt -s                System prompt first, then user prompt
  python main.py heller prompt -o response.txt   Save response to file
  python main.py heller prompt -m gpt-4o-mini    Use a specific model
  python main.py heller prompt --dry-run         Preview prompt without API call

Plugin commands (e.g. translate, transcribe) are registered by installed plugins.
Run 'python main.py <professor> <command> --help' for plugin-specific usage.
        """,
    )

    # Global commands (no professor required)
    parser.add_argument(
        '--show-config',
        dest='show_config',
        action='store_true',
        help='Show professor configuration and data-file status',
    )
    parser.add_argument(
        '--list-models',
        dest='list_models',
        action='store_true',
        help='List all available models and their capabilities',
    )
    _add_debug_flags(parser)

    # Professor-based commands use subparsers
    parser.add_argument(
        'professor',
        type=str,
        nargs='?',
        help='Professor name for API key lookup',
    )

    # Add subparsers for commands (usage, translate, transcribe)
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    _build_usage_subparser(subparsers)

    # translate, transcribe, transcription_review, and prompt are all registered
    # by their respective plugins in plugins/.

    # Register plugin subcommands (each unique plugin object called once).
    if plugins:
        _seen: set[int] = set()
        for _p in plugins.values():
            if id(_p) not in _seen:
                _seen.add(id(_p))
                _p.register_subparsers(subparsers)

    return parser


def _available_commands_hint(plugins: dict[str, ModePlugin]) -> str:
    """Return a formatted string listing built-in and plugin commands for error messages."""
    lines = [
        "\nBuilt-in commands:",
        "  usage report [YYYY-MM] [--all-time]  Token usage report",
        "  usage months                         List archived month files",
        "  usage daily [YYYY-MM-DD]             Daily usage",
    ]
    if plugins:
        lines.append("\nPlugin commands: " + ", ".join(sorted(plugins)))
    return "\n".join(lines)


def main() -> None:
    """Main entry point for the CLI application."""
    # Parse args first so logging level can honor --verbose.
    _plugins = load_plugins(Path(__file__).parent.parent / "plugins")

    try:
        parser = create_argument_parser(_plugins)
        args = parser.parse_args()

        setup_logging(verbose=getattr(args, 'verbose', False))
        if getattr(args, 'debug_api', False):
            os.environ["PU_SANDBOX_DEBUG_API"] = "1"
            logger.warning(
                "Raw API debugging enabled via --debug-api; "
                "responses may include sensitive data."
            )

        # Handle global commands (no professor required)
        if args.show_config or args.list_models:
            if handle_info_commands(args):
                return

        # All other commands require professor name
        if not args.professor:
            raise CLIError(
                "Professor name is required.\n"
                "Usage: python main.py <professor_name> <command> [options]"
                + _available_commands_hint(_plugins)
                + "\n\nOr for global commands: python main.py --show-config | --list-models"
            )

        # Handle professor-specific commands
        if not args.command:
            raise CLIError(
                f"No command specified for professor '{args.professor}'."
                + _available_commands_hint(_plugins)
                + "\n\nRun 'python main.py --help' for full usage information."
            )

        # Route to appropriate handler
        if args.command == 'usage':
            if handle_info_commands(args):
                return
        elif _plugins and args.command in _plugins:
            model = getattr(args, 'model', None)
            temperature = getattr(args, 'temperature', None)
            top_p = getattr(args, 'top_p', None)
            max_tokens = getattr(args, 'max_tokens', None)
            _plugins[args.command].run(
                args, args.professor, model, temperature, top_p, max_tokens
            )
        else:
            raise CLIError(f"Unknown command: {args.command}")

    except CLIError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
