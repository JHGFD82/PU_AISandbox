"""CLI entry point: parses command-line arguments, validates them, and routes each command to the right handler.

Importing this module discovers all installed plugins and builds the
argument parser. The ``main()`` function at the bottom is what runs when you
type ``python main.py ...``. Credentials and other per-installation settings
are read on demand from ``.settings`` (see ``src/settings_store.py``) rather
than being loaded into the process environment up front.
"""

import argparse
import importlib
import logging
import os
import sys
from pathlib import Path

# Loading this hands terminal line-editing to GNU Readline, which manages
# its own input buffer instead of the operating system's default line
# buffer (a "cooked mode" limit of roughly 1024 characters on macOS).
# Without it, pasting a long system or user prompt into an interactive
# `input()` prompt can be silently cut off partway through. Loaded by
# module name (rather than a plain `import readline`) purely so it doesn't
# leave an unused-import binding for linters to flag; it's not available
# on some Windows Python builds, so this is best-effort.
try:
    importlib.import_module("readline")
except ImportError:
    pass

from .config import normalize_netid
from .errors import CLIError
from .runtime import ModePlugin, handle_info_commands, load_plugins

logger = logging.getLogger(__name__)


def _add_debug_flags(parser: argparse.ArgumentParser) -> None:
    """Add --verbose and --debug-api flags to *parser*.

    These are offered in two places — before the professor name
    (``main.py --verbose heller translate ...``) and after the command
    (``main.py heller translate ... --verbose``) — because both read
    naturally and the tool's own usage line advertises the first one.

    Neither flag has a default here, which is the point. Adding the same
    flag to a command's own parser normally makes that copy's "off" default
    overwrite whatever the earlier one already set, so ``--verbose`` typed
    before the professor name was silently discarded by the time the command
    was parsed. ``SUPPRESS`` means a flag that wasn't typed writes nothing at
    all, so whichever position the user did type it in survives. The
    off-by-default values are set once, on the top-level parser, in
    ``create_argument_parser()``.
    """
    parser.add_argument(
        '--verbose',
        dest='verbose',
        action='store_true',
        default=argparse.SUPPRESS,
        help='Enable verbose debug logging',
    )
    parser.add_argument(
        '--debug-api',
        dest='debug_api',
        action='store_true',
        default=argparse.SUPPRESS,
        help='Log raw API payload details for troubleshooting provider errors',
    )


def setup_logging(verbose: bool = False) -> None:
    """Configure how log messages are formatted and which detail level is shown.

    At the default level (``verbose=False``), only informational messages and
    above are shown. Passing ``verbose=True`` (triggered by ``--verbose``) also
    surfaces debug messages, which include per-page progress and API call details.

    Args:
        verbose: When ``True``, show detailed debug output in the terminal.
                 Defaults to ``False``.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='%(asctime)s - %(levelname)s - %(message)s')


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the standard set of flags that every command supports to a subcommand's argument parser.

    Adds ``-o`` (output file), ``-m`` (model), ``-t`` (temperature),
    ``-T`` (top-p), ``-M`` (max tokens), ``--dry-run``, and the debug flags.
    Calling this once per subcommand keeps flag definitions in one place so
    that all commands behave consistently.

    Args:
        parser: The subcommand parser to attach the flags to. Typically the
                object returned by ``subparsers.add_parser('mycommand')``.
    """
    _add_debug_flags(parser)
    parser.add_argument('-o', '--output', dest='output_file', type=str, help='Output file path')
    parser.add_argument('-m', '--model', dest='model', type=str, help='Model to use (e.g., gpt-4o, gpt-4o-mini)')
    parser.add_argument('-t', '--temperature', dest='temperature', type=float, default=None, help='Sampling temperature override (0.0–2.0)')
    parser.add_argument('-T', '--top-p', dest='top_p', type=float, default=None, help='Nucleus sampling top-p override (0.0–1.0)')
    parser.add_argument('-M', '--max-tokens', dest='max_tokens', type=int, default=None, help='Maximum response tokens (overrides model default)')
    parser.add_argument('--dry-run', dest='dry_run', action='store_true', help='Print the prompt(s) that would be sent without making any API calls')


def add_notes_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the notes flags to a subcommand's argument parser.

    The notes flags let users append extra context to the system prompt, the
    user prompt, or both — either interactively at runtime (``-n``) or as a
    single string passed directly on the command line (``-ns``, ``-nu``,
    ``-nb``).

    Args:
        parser: The subcommand parser to attach the flags to.
    """
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

    # usage sources list|add|remove
    _build_usage_sources_subparser(usage_subparsers)


def _build_usage_sources_subparser(usage_subparsers: argparse._SubParsersAction) -> None:
    """Register 'usage sources' and its list/add/remove subcommands.

    Lets one installation of this package include another installation's
    usage-tracking data when building reports (e.g. a professor's own copy
    of this tool, synced to a shared Dropbox folder). See
    ``docs/webui-plugin-plan.md`` section 1 and ``src/tracking/source_config.py``
    for the full design.
    """
    sources_parser = usage_subparsers.add_parser(
        'sources',
        help='Manage external/remote usage-data sources for aggregate reports',
    )
    _add_debug_flags(sources_parser)
    sources_sub = sources_parser.add_subparsers(dest='sources_subcommand', help='Sources subcommand')

    sources_list = sources_sub.add_parser('list', help='List configured external sources')
    _add_debug_flags(sources_list)

    sources_add = sources_sub.add_parser(
        'add',
        help='Add an external usage-data source (prompts interactively for anything not passed as a flag)',
    )
    _add_debug_flags(sources_add)
    sources_add.add_argument('--label', type=str, default=None, help="A short name for this source, e.g. 'Prof. Smith'")
    sources_add.add_argument('--path', type=str, default=None, help="The other installation's data/ folder")
    sources_add.add_argument(
        '--mode', type=str, choices=['read-only', 'shared-write'], default=None,
        help="'read-only' (default) if only the other side writes there; 'shared-write' if this "
             "installation records usage there too",
    )
    sources_add.add_argument(
        '--for-professor', dest='for_professor', type=str, default=None, metavar='PROFESSOR',
        help='Which professor this source is for (required for --mode shared-write)',
    )

    sources_remove = sources_sub.add_parser('remove', help='Remove a configured external source by label')
    _add_debug_flags(sources_remove)
    sources_remove.add_argument('label', type=str, help='The label of the source to remove (see: usage sources list)')


def _build_settings_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'settings' command: add/remove people and manage optional .settings values.

    Named after the file it edits. It was called 'env' until 2026-07, after
    the file it edited had already been replaced: there is no .env any more,
    so the name pointed at nothing and quietly suggested that environment
    variables were involved somewhere.

    Unlike every other built-in command, 'settings' never requires a person's
    netID on the command line — you need it precisely when nobody is
    configured yet (adding the first person). See
    ``_insert_professor_placeholder_if_needed()`` and ``_dispatch()`` below
    for how that exception is wired in, the same way an individual plugin
    can opt out via ``requires_professor = False``.

    API keys and other secrets are always entered at a hidden prompt by the
    command handler, never accepted as a flag — so they never end up in
    shell history or a process listing.
    """
    settings_parser = subparsers.add_parser(
        'settings', help="Add/remove people and manage optional .settings values"
    )
    _add_debug_flags(settings_parser)
    settings_sub = settings_parser.add_subparsers(
        dest='settings_subcommand', help='settings subcommand',
    )

    add_prof = settings_sub.add_parser(
        'add-professor',
        help='Add a new professor (prompts interactively for anything not passed as a flag)',
    )
    _add_debug_flags(add_prof)
    add_prof.add_argument(
        '--netid', type=str, default=None,
        help="Their university netID, e.g. 'jh43' — this identifies them everywhere",
    )
    add_prof.add_argument(
        '--name', type=str, default=None,
        help="Their display name, shown in reports and the web interface, e.g. 'Jeff Heller'",
    )

    setup_parser = settings_sub.add_parser(
        'setup',
        help='Choose where the sandbox keeps your files (runs automatically when needed)',
    )
    _add_debug_flags(setup_parser)

    remove_prof = settings_sub.add_parser('remove-professor', help='Remove a configured professor')
    _add_debug_flags(remove_prof)
    remove_prof.add_argument(
        'identifier', type=str,
        help='netID or display name of the person to remove (see: --show-config)',
    )

    list_parser = settings_sub.add_parser(
        'list', help='List optional .settings values and whether each is currently set',
    )
    _add_debug_flags(list_parser)

    set_parser = settings_sub.add_parser(
        'set',
        help='Set an optional .settings value (prompts for it; hidden input for secrets)',
    )
    _add_debug_flags(set_parser)
    set_parser.add_argument(
        'key', type=str,
        help="Dotted path to set, e.g. webui.session_secret or endpoints.hpc_cluster.key",
    )
    set_parser.add_argument(
        '--generate', action='store_true',
        help='Auto-generate a random value instead of prompting (secrets only)',
    )

    unset_parser = settings_sub.add_parser('unset', help='Remove an optional .settings value')
    _add_debug_flags(unset_parser)
    unset_parser.add_argument('key', type=str, help='The variable to remove')


def create_argument_parser(
    plugins: dict[str, ModePlugin] | None = None,
) -> argparse.ArgumentParser:
    """Build the command-line parser that interprets everything a user types after
    ``python main.py``.

    Sets up the top-level flags (``--show-config``, ``--list-models``,
    ``--verbose``, ``--debug-api``), the professor name argument, the built-in
    ``usage`` subcommand tree, and any commands registered by installed plugins
    (e.g., ``translate``, ``transcribe``, ``prompt``).

    Args:
        plugins: A dictionary mapping command names to plugin objects, as
                 returned by ``load_plugins()``. Pass ``None`` or an empty dict
                 to build a parser with only the built-in commands.

    Returns:
        A configured parser ready to call ``.parse_args()`` on.
    """
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
    # The one place the debug flags get an "off" value. Every other parser
    # adds them with SUPPRESS so that a flag typed in one position isn't
    # undone by a copy of itself somewhere else — see _add_debug_flags().
    parser.set_defaults(verbose=False, debug_api=False)

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
    _build_settings_subparser(subparsers)

    # translate, transcribe, transcription_review, and prompt are all registered
    # by their respective plugins in plugins/.

    # Register plugin subcommands (each unique plugin object called once).
    for _p in dict.fromkeys(plugins.values() if plugins else []):
        _p.register_subparsers(subparsers)

    return parser


def _available_commands_hint(plugins: dict[str, ModePlugin]) -> str:
    """Return a formatted string listing built-in and plugin commands for error messages."""
    lines = [
        "\nBuilt-in commands:",
        "  usage report [YYYY-MM] [--all-time]  Token usage report",
        "  usage months                         List archived month files",
        "  usage daily [YYYY-MM-DD]             Daily usage",
        "  settings add-professor                  Add someone (no netID needed first)",
        "  settings remove-professor <identifier>  Remove someone configured",
        "  settings list                           List optional .settings values and their status",
        "  settings set <path> / settings unset <path>   Set or remove an optional .settings value",
    ]
    if plugins:
        lines.append("\nPlugin commands: " + ", ".join(sorted(plugins)))
    return "\n".join(lines)


def _run_info_command(args: argparse.Namespace) -> None:
    """Run a reporting command (``--show-config``, ``usage``, ``settings``) and confirm it ran.

    ``handle_info_commands()`` reports whether it recognised the command,
    and that answer was previously thrown away at both call sites. It can
    only say "no" if a command was added to the parser but never wired up
    to a handler — at which point the old behaviour was to return quietly,
    so the person who typed the command saw nothing at all happen and no
    explanation why. Checking the answer turns that into a message naming
    the command.

    Args:
        args: The parsed command-line flags for this run.

    Raises:
        CLIError: If no handler recognised the command.
    """
    if not handle_info_commands(args):
        raise CLIError(
            f"The '{getattr(args, 'command', None) or 'requested'}' command was "
            "recognised on the command line but isn't wired up to anything. This "
            "is a fault in the sandbox itself, not something you typed wrong — "
            "please report it to whoever looks after this installation."
        )


def _dispatch(args: argparse.Namespace, plugins: dict[str, ModePlugin]) -> None:
    """Check that the parsed arguments form a valid command, then hand off to the right handler.

    Handles global commands (``--show-config``, ``--list-models``) first, since
    those don't require a professor name. Otherwise, confirms that both a
    professor name and a command were supplied before routing to the ``usage``
    reporter or a plugin command (e.g., ``translate``, ``prompt``). The
    built-in ``settings`` command is the other exception to "a netID is
    required" — you need it precisely when no professor is configured yet,
    so it's exempted the same way a plugin can opt out via
    ``requires_professor = False`` (see ``webui`` for an example).

    Args:
        args: The object holding all parsed command-line flags for the current
              run, as returned by ``parser.parse_args()``.
        plugins: A dictionary mapping command names to plugin objects, keyed by
                 command name (e.g., 'translate', 'prompt').

    Raises:
        CLIError: If the professor name or command is missing, or if the
                  command is not recognized.
    """
    # Handle global commands (no professor required)
    if args.show_config or args.list_models:
        _run_info_command(args)
        return

    # A plugin can opt out of the professor requirement (e.g. a shared local
    # web server, not a per-professor API call) by setting
    # requires_professor = False on its plugin object. Every other plugin —
    # including ones that don't define this attribute at all — still
    # requires a professor name, which is why this defaults to True.
    command_requires_professor = args.command != "settings" and getattr(
        plugins.get(args.command), "requires_professor", True
    )

    # All other commands require professor name
    if not args.professor and command_requires_professor:
        raise CLIError(
            "Professor name is required.\n"
            "Usage: python main.py <professor_name> <command> [options]"
            + _available_commands_hint(plugins)
            + "\n\nOr for global commands: python main.py --show-config | --list-models"
        )

    if not args.command:
        raise CLIError(
            f"No command specified for professor '{args.professor}'."
            + _available_commands_hint(plugins)
            + "\n\nRun 'python main.py --help' for full usage information."
        )

    # Normalise the netID once, here, before anything downstream uses it.
    # Everything past this point treats it as a file and folder name
    # directly, so this is the boundary where "JH43" and "jh43 " become the
    # one spelling that names that person's usage file — rather than each
    # part of the sandbox making its own guess, which is how one person's
    # spending previously ended up split across two files.
    if args.professor:
        args.professor = normalize_netid(args.professor)

    if args.command in ('usage', 'settings'):
        _run_info_command(args)
    elif args.command in plugins:
        plugins[args.command].run(
            args,
            args.professor,
            getattr(args, 'model', None),
            getattr(args, 'temperature', None),
            getattr(args, 'top_p', None),
            getattr(args, 'max_tokens', None),
        )
    else:
        raise CLIError(f"Unknown command: {args.command}")


def _insert_professor_placeholder_if_needed(
    argv: list[str], plugins: dict[str, ModePlugin]
) -> list[str]:
    """Work around a real argparse limitation for professor-less commands like ``webui``.

    The command line is built as an optional ``professor`` positional
    followed immediately by a subparsers action (``command``). Argparse
    resolves this correctly when exactly one token follows — e.g.
    ``python main.py webui`` parses as ``professor=None, command='webui'``
    on its own — but does *not* reliably do the same once a second token
    is added, such as ``python main.py webui serve``: argparse instead
    consumes ``'webui'`` into the ``professor`` slot and then fails because
    ``'serve'`` isn't a valid top-level command. This is a known rough edge
    of mixing an optional positional with subparsers, not something specific
    to this project's commands.

    The fix is to detect that situation before parsing and insert an empty
    string in the ``professor`` slot ourselves, which argparse *does*
    consistently route correctly regardless of how many tokens follow. The
    empty string is converted back to ``None`` after parsing (see
    ``main()``) — callers never see the placeholder.

    ``settings`` needs the same treatment even though it isn't a plugin — it's
    a built-in command, but one that (like ``webui``) never requires a
    professor name, and for the same reason ``webui`` doesn't: it needs to
    work before anyone is configured yet (``settings add-professor``).

    The command name is looked for after any leading flags rather than at
    the very front, so that a global flag typed before the command —
    ``python main.py --verbose webui serve`` — still works. It previously
    checked only the first word, so any global flag turned a working command
    into ``error: argument command: invalid choice: 'serve'``, which points
    at the wrong word entirely.

    Args:
        argv: The raw command-line arguments, not including the program
              name (i.e. ``sys.argv[1:]``).
        plugins: The command-name-to-plugin mapping from ``load_plugins()``,
                 used to check whether the first argument names a command
                 that declared ``requires_professor = False``.

    Returns:
        *argv* unchanged, or with an empty-string placeholder inserted just
        before the command name when needed.
    """
    # The first word that isn't a flag. Every global flag is a simple on/off
    # switch that takes no value of its own, so nothing between the flags can
    # be mistaken for the command. If a value-taking global flag is ever
    # added, its value would be found here instead — it wouldn't name a
    # command, so no placeholder is inserted and the behaviour is the same as
    # if this helper hadn't run. That's the safe direction to fail in.
    index = next((i for i, token in enumerate(argv) if not token.startswith("-")), None)
    if index is None:
        return argv

    command = argv[index]
    if command != "settings":
        plugin = plugins.get(command)
        if plugin is None or getattr(plugin, "requires_professor", True):
            return argv

    return argv[:index] + [""] + argv[index:]


def main() -> None:
    """Run the AI Sandbox tool from the command line.

    Discovers installed plugins, builds the argument parser, reads the flags
    typed by the user, configures logging, and routes the request to the
    appropriate command handler. Any user-facing errors are printed to the
    terminal and the process exits with a failure code (exit code 1) so that
    scripts can detect the failure.
    """
    # Parse args first so logging level can honor --verbose.
    _plugins = load_plugins(Path(__file__).parent.parent / "plugins")

    try:
        parser = create_argument_parser(_plugins)
        argv = _insert_professor_placeholder_if_needed(sys.argv[1:], _plugins)
        args = parser.parse_args(argv)
        if args.professor == "":
            args.professor = None

        setup_logging(verbose=getattr(args, 'verbose', False))
        if getattr(args, 'debug_api', False):
            os.environ["PU_SANDBOX_DEBUG_API"] = "1"
            logger.warning(
                "Raw API debugging enabled via --debug-api; "
                "responses may include sensitive data."
            )

        _dispatch(args, _plugins)

    except CLIError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
