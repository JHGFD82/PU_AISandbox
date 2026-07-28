"""Info/report command handlers for CLI runtime actions."""

import argparse
import getpass
import logging
import os
import secrets
from datetime import datetime

from .. import settings_store
from ..config import LANGUAGE_MAP, get_registered_settings, load_professor_config
from ..errors import CLIError
from ..models.catalog import get_pricing_unit, load_model_catalog
from ..services.api_config import credential_path_for_endpoint, list_apis
from ..settings_store import (
    add_source,
    get_configured_sources,
    get_source_id,
    remove_source,
)
from ..tracking.token_tracker import TokenTracker, get_archive_dir, get_usage_data_path

logger = logging.getLogger(__name__)


def list_optional_settings() -> list[tuple[str, str, str, bool]]:
    """Return every optional ``settings.toml`` value this installation knows about, for display.

    Combines two sources: plugin-declared fields (registered via
    ``register_setting()``, e.g. the webui plugin's secrets) and
    alternate-API-endpoint credential paths derived from the configured
    endpoints (which don't go through the registry since their names depend
    on what's configured in ``settings.*.toml``, not on any plugin).

    Shared by both the CLI (``--show-config``/``settings list``) and the web
    UI's settings page, so the two never drift on what counts as a known,
    safe-to-edit dotted path.

    Returns:
        A list of ``(dotted_path, label, section, secret)`` tuples, sorted
        by section then path.
    """
    fields = [(f.key, f.label, f.section, f.secret) for f in get_registered_settings()]
    for api_name in list_apis():
        fields.append((
            credential_path_for_endpoint(api_name),
            f"API key for the '{api_name}' endpoint (see settings.default.toml/settings.local.toml)",
            "Alternate API endpoints",
            True,
        ))
    return sorted(fields, key=lambda t: (t[2], t[0]))


def show_professor_config() -> None:
    """Display current professor configuration and data-file status."""
    professors = load_professor_config()

    if not professors:
        print("No professors configured in settings.toml.")
        print("Add one with: python main.py settings add-professor")
        print("Or by hand, under a [professors.<netid>] table — see templates/settings.template.")
        _print_optional_settings()
        return

    print("\nCurrent Professor Configuration:")
    print("=" * 60)

    for netid, prof in professors.items():
        primary_set = "set" if prof.get('key') else "NOT SET"
        backup_set = "set" if prof.get('backup_key') else "not set"

        # Data file on disk
        usage_path = get_usage_data_path(netid)
        usage_exists = usage_path.exists()
        usage_label = str(usage_path) if usage_exists else f"{usage_path}  (not yet created)"

        # Archived months
        archive_dir = get_archive_dir(netid)
        archived_months = sorted(p.stem for p in archive_dir.glob("*.json")) if archive_dir.exists() else []

        print(f"\n  {prof['name']}")
        print(f"    netID:        {netid}")
        print(f"    Primary key:  {primary_set}")
        print(f"    Backup key:   {backup_set}")
        print(f"    Usage file:   {usage_label}")
        if archived_months:
            print(f"    Archives:     {', '.join(archived_months)}")
        else:
            print("    Archives:     none")

    print("\n" + "=" * 60)
    print("Usage: python main.py <professor> <command> [options]")
    print("       python main.py --help")

    print("\n" + "=" * 60)
    print("Language codes (install a plugin to add more):")
    for code, name in sorted(LANGUAGE_MAP.items()):
        print(f"  {code}  {name}")

    _print_optional_settings()


def _print_optional_settings() -> None:
    """Print every optional ``settings.toml`` value this installation knows about and whether it's set.

    Never prints a secret's value — only whether it's currently set. Run
    after a `git pull` (or any update) to see whether a new optional
    feature has shown up since you last checked; new entries appear here
    automatically once a plugin registers them, no separate "what's new"
    tracking needed.
    """
    fields = list_optional_settings()
    if not fields:
        return

    print("\n" + "=" * 60)
    print("Optional settings (all unset by default — see templates/settings.template):")
    current_section = None
    for path, label, section, _secret in fields:
        if section != current_section:
            print(f"\n  [{section}]")
            current_section = section
        status = "set" if settings_store.get_value(path) else "not set"
        print(f"    {path}  ({status})  {label}")
    print("\nSet one with: python main.py settings set <dotted.path>")


def list_available_models() -> None:
    """List all available models and their capabilities."""
    config = load_model_catalog()
    models = config["models"]
    pricing_unit = get_pricing_unit()

    bar = "=== Available Models ==="
    print(f"\n{bar}")
    print(f"Pricing is per {pricing_unit:,} tokens\n")

    for model_name, pricing in sorted(models.items()):
        vision = "✓" if pricing.get("supports_vision", False) else "✗"
        print(f"{model_name}")
        print(f"  Vision Support: {vision}")
        print(f"  Input:  ${pricing['input']:.3f}")
        print(f"  Output: ${pricing['output']:.3f}")
        print()
    print("=" * len(bar) + "\n")


def _print_daily_usage(token_tracker: TokenTracker, professor_name: str, date: str | None = None) -> None:
    """Display daily usage report for info-only command path."""
    if date == 'today':
        usage = token_tracker.get_daily_usage()
        print(f"\nToday's usage for {professor_name}:")
    else:
        usage = token_tracker.get_daily_usage(date)
        print(f"\nUsage for {date} for {professor_name}:")

    if not usage.get('call_count'):
        print("No usage recorded for this date.")
        return

    print(f"Total tokens: {usage['total_tokens']:,}")
    print(f"  Input tokens:  {usage.get('total_input_tokens', 0):,}")
    print(f"  Output tokens: {usage.get('total_output_tokens', 0):,}")
    print(f"Total cost: ${usage['total_cost']:.4f}")
    print(f"API calls: {usage['call_count']}")


def _require_configured_netid(netid: str) -> None:
    """Stop with a helpful message if *netid* isn't someone this installation knows.

    Args:
        netid: The netID typed on the command line.

    Raises:
        CLIError: If nobody is configured under that netID. The message lists
                  who *is* configured, since the usual cause is a typo or a
                  half-remembered netID.
    """
    professors = load_professor_config()
    if netid in professors:
        return
    if professors:
        known = "\n".join(
            f"  {n}  ({c['name']})" for n, c in sorted(professors.items())
        )
        raise CLIError(
            f"Nobody with the netID '{netid}' is configured here.\n"
            f"Configured netIDs:\n{known}\n\n"
            f"To add someone: python main.py settings add-professor"
        )
    raise CLIError(
        "Nobody is configured on this installation yet.\n"
        "Add someone with: python main.py settings add-professor"
    )


def handle_info_commands(args: argparse.Namespace) -> bool:
    """Handle info/reporting commands without API-key dependent runtime initialization."""
    # Global info commands (no professor required)
    if getattr(args, 'show_config', False):
        show_professor_config()
        return True

    if args.list_models:
        list_available_models()
        return True

    if getattr(args, 'command', None) == 'settings':
        _handle_settings_command(args)
        return True

    # Usage commands (professor required)
    if getattr(args, 'command', None) == 'usage':
        if not args.professor:
            raise CLIError("A netID is required for usage commands.")

        usage_subcommand = getattr(args, 'usage_subcommand', None)

        if usage_subcommand == 'sources':
            _handle_usage_sources(args)
            return True

        # Check the netID is one this installation knows before reporting on
        # it. Without this, a mistyped netID produced a perfectly formatted
        # report full of zeroes — which reads as "you have spent nothing this
        # month", not as "there is nobody by that name here". Being told you
        # are within budget when the question was never actually answered is
        # the worst outcome for a tool whose job is tracking spending.
        _require_configured_netid(args.professor)

        token_tracker = TokenTracker(professor=args.professor)

        if usage_subcommand == 'report':
            month = getattr(args, 'month', None)
            include_all_time = getattr(args, 'all_time', False)
            token_tracker.print_usage_report(month=month, include_all_time=include_all_time)
            return True

        if usage_subcommand == 'months':
            archived = token_tracker.list_archived_months()
            current_month = datetime.now().strftime("%Y-%m")
            if archived:
                print(f"\nUsage history for {args.professor}:")
                for m in archived:
                    print(f"  {m}  (archived)")
                print(f"  {current_month}  (current)")
                print(f"\nTo view a specific month: python main.py {args.professor} usage report <YYYY-MM>")
            else:
                print(f"No archived months found for {args.professor} (current month: {current_month}).")
            return True

        if usage_subcommand == 'daily':
            date = getattr(args, 'date', 'today')
            _print_daily_usage(token_tracker, args.professor, date)
            return True

        raise CLIError("Invalid usage subcommand. Use 'report', 'months', 'daily', or 'sources'.")

    return False


def _handle_settings_command(args: argparse.Namespace) -> None:
    """Handle 'settings add-professor/remove-professor/list/set/unset'.

    This is the CLI-side half of directly editing ``settings.toml`` — see
    ``src/settings_store.py`` for why writing to it directly is safe here
    (every edit is triggered by a person typing a command at their own
    keyboard, never over a network call or as part of syncing between
    machines).
    """
    sub = getattr(args, 'settings_subcommand', None)

    if sub == 'setup':
        from .setup_prompts import run_interactive_setup
        run_interactive_setup()
        return
    if sub == 'add-professor':
        _settings_add_professor_interactive(args)
        return
    if sub == 'remove-professor':
        _settings_remove_professor(args)
        return
    if sub == 'list':
        _print_optional_settings()
        return
    if sub == 'set':
        _settings_set_value(args)
        return
    if sub == 'unset':
        _settings_unset_value(args)
        return

    raise CLIError(
        "No settings subcommand specified.\n"
        "Usage: python main.py settings setup\n"
        "       python main.py settings add-professor\n"
        "       python main.py settings remove-professor <identifier>\n"
        "       python main.py settings list\n"
        "       python main.py settings set <KEY>\n"
        "       python main.py settings unset <KEY>"
    )


def _settings_add_professor_interactive(args: argparse.Namespace) -> None:
    """Add someone, prompting interactively for their netID, name and keys.

    The netID is asked for first because it's the one that matters: it
    identifies the person everywhere in the sandbox and is what gets typed
    on the command line. The display name is only ever shown to people, so
    it can be written however reads best.

    Keys are always entered at a hidden prompt (never a command-line flag),
    so they never end up in shell history or a process listing.
    """
    netid = getattr(args, 'netid', None) or input(
        "netID (the university username they sign in with, e.g. 'jh43'): "
    ).strip()
    name = getattr(args, 'name', None) or input(
        "Display name, shown in reports and the web interface (e.g. 'Jeff Heller'): "
    ).strip()
    primary_key = getpass.getpass("Primary API key (hidden): ")
    backup_key = getpass.getpass("Backup API key (optional, hidden — press Enter to skip): ")

    try:
        netid = settings_store.add_professor(netid, name, primary_key, backup_key or None)
    except ValueError as e:
        raise CLIError(str(e)) from e

    print(f"\nAdded {name} ({netid}).")
    print(f"Try it out: python main.py {netid} usage report")


def _settings_remove_professor(args: argparse.Namespace) -> None:
    """Remove a professor by safe name or display name, after a yes/no confirmation.

    Confirmation matters here specifically because this deletes real key
    material from settings.toml, not just a display entry.
    """
    identifier = args.identifier
    confirm = input(
        f"Remove professor '{identifier}' from settings.toml? This deletes their API key(s). [y/N]: "
    ).strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled — nothing was removed.")
        return
    try:
        removed_name = settings_store.remove_professor(identifier)
    except ValueError as e:
        raise CLIError(str(e)) from e
    print(f"Removed professor '{removed_name}'.")


def _settings_set_value(args: argparse.Namespace) -> None:
    """Set an optional ``settings.toml`` value, prompting for it (hidden input if it's a secret).

    *key* is a dotted path (e.g. ``webui.session_secret``,
    ``endpoints.hpc_cluster.key``), not an environment-variable name.
    Unregistered paths are treated as secret by default — hiding input when
    it wasn't necessary is a minor inconvenience; echoing a value that
    turns out to be a key would not be.
    """
    path = args.key.strip()
    known_secrets = {k: secret for k, _label, _section, secret in list_optional_settings()}
    is_secret = known_secrets.get(path, True)

    if getattr(args, 'generate', False):
        if not is_secret:
            raise CLIError(
                f"--generate is only for secret values; '{path}' isn't registered as one. "
                f"Use 'python main.py settings set {path}' instead."
            )
        value = secrets.token_urlsafe(32)
    else:
        prompt = f"Value for {path}: "
        value = getpass.getpass(prompt) if is_secret else input(prompt).strip()

    try:
        settings_store.set_value(path, value)
    except ValueError as e:
        raise CLIError(str(e)) from e

    print(f"\n{path} set (value hidden)." if is_secret else f"\n{path}={value}")


def _settings_unset_value(args: argparse.Namespace) -> None:
    """Remove an optional ``settings.toml`` value."""
    path = args.key.strip()
    settings_store.unset_value(path)
    print(f"{path} removed (if it was set).")


def _handle_usage_sources(args: argparse.Namespace) -> None:
    """Handle 'usage sources list/add/remove'. See docs/webui-plugin-plan.md section 1.

    Note that the external-source configuration itself (in ``settings.toml``)
    isn't scoped to the professor named on the command line — every usage
    subcommand requires a professor argument for consistency, but 'sources'
    manages this installation's config as a whole. The professor named on
    the command line only matters here as "which professor's data source
    to add/remove," via --for-professor.
    """
    sub = getattr(args, 'sources_subcommand', None)

    if sub == 'list':
        _print_configured_sources()
        return

    if sub == 'remove':
        label = args.label
        if remove_source(label):
            print(f"Removed source '{label}'.")
        else:
            print(f"No configured source named '{label}'. Run 'usage sources list' to see what's configured.")
        return

    if sub == 'add':
        _add_source_interactive(args)
        return

    raise CLIError("Invalid usage sources subcommand. Use 'list', 'add', or 'remove'.")


def _print_configured_sources() -> None:
    """Print this installation's source id and every configured external source."""
    print(f"\nThis installation's source id: {get_source_id()}")
    sources = get_configured_sources()
    if not sources:
        print("No external usage-data sources configured.")
        print("Add one with: python main.py <professor> usage sources add")
        return
    print("\nConfigured external sources:")
    for s in sources:
        prof_note = f", for={s.professor}" if s.professor else ""
        print(f"  {s.label}  [{s.mode}{prof_note}]  {s.path}")


def _add_source_interactive(args: argparse.Namespace) -> None:
    """Add a source, prompting interactively for any value not already passed as a flag."""
    label = getattr(args, 'label', None) or input("Label for this source (e.g. 'Prof. Smith'): ").strip()
    path = getattr(args, 'path', None) or input("Path to the other installation's data/ folder: ").strip()

    mode = getattr(args, 'mode', None)
    if not mode:
        raw = input("Mode — 'read-only' or 'shared-write' [read-only]: ").strip().lower()
        mode = raw or 'read-only'

    for_professor = getattr(args, 'for_professor', None)
    if mode == 'shared-write' and not for_professor:
        for_professor = input(
            "Which professor is this source for (safe name, e.g. 'smith'): "
        ).strip()

    resolved_path = os.path.expanduser(path)
    if not os.path.exists(resolved_path):
        print(
            f"Note: '{resolved_path}' doesn't exist yet. That's fine if it will appear once the "
            f"other side syncs — just double check the path if that's unexpected."
        )

    try:
        add_source(label=label, path=path, mode=mode, professor=for_professor)
    except ValueError as e:
        raise CLIError(str(e)) from e

    print(f"\nAdded source '{label}' ({mode}) -> {path}")

    if mode == 'shared-write':
        this_source_id = get_source_id()
        print(
            "\nAdd this on the other installation so both sides see each other's activity:\n\n"
            f"    python main.py {for_professor} usage sources add \\\n"
            f"        --label \"This installation\" \\\n"
            f"        --path \"{path}\" \\\n"
            f"        --mode shared-write \\\n"
            f"        --for-professor {for_professor}\n"
            f"\n(This installation's own source id is '{this_source_id}' — every usage record "
            f"it writes from now on will be tagged with that.)"
        )
