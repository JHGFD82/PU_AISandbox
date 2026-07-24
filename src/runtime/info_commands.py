"""Info/report command handlers for CLI runtime actions."""

import argparse
import getpass
import logging
import os
import secrets
from datetime import datetime
from typing import Optional

from .. import env_editor
from ..config import LANGUAGE_MAP, get_registered_env_fields, load_professor_config
from ..errors import CLIError
from ..models.catalog import get_pricing_unit, load_model_catalog
from ..services.api_config import env_var_for_endpoint, list_apis
from ..tracking.source_config import (
    add_source,
    get_configured_sources,
    get_source_id,
    remove_source,
)
from ..tracking.token_tracker import TokenTracker, get_archive_dir, get_usage_data_path

logger = logging.getLogger(__name__)


def _list_optional_env_fields() -> list[tuple[str, str, str, bool]]:
    """Return every optional .env variable this installation knows about, for display.

    Combines two sources: plugin-declared fields (registered via
    ``register_env_field()``, e.g. the webui plugin's secrets) and
    alternate-API-endpoint keys derived from ``apis.json`` (which don't go
    through the registry since their names depend on what's configured
    there, not on any plugin).

    Returns:
        A list of ``(key, label, section, secret)`` tuples, sorted by
        section then key.
    """
    fields = [(f.key, f.label, f.section, f.secret) for f in get_registered_env_fields()]
    for api_name in list_apis():
        fields.append((
            env_var_for_endpoint(api_name),
            f"API key for the '{api_name}' endpoint (see apis.json)",
            "Alternate API endpoints",
            True,
        ))
    return sorted(fields, key=lambda t: (t[2], t[0]))


def show_professor_config() -> None:
    """Display current professor configuration and data-file status."""
    professors = load_professor_config()

    if not professors:
        print("No professors configured in .env file.")
        print("Add one with: python main.py env add-professor")
        print("Or by hand, in the format:")
        print("  PROF_[ID]_NAME=Professor Name")
        print("  PROF_[ID]_KEY=api_key")
        print("  PROF_[ID]_BACKUP_KEY=backup_api_key")
        _print_optional_settings()
        return

    print("\nCurrent Professor Configuration:")
    print("=" * 60)

    for safe_name, prof in professors.items():
        primary_set = "set" if os.environ.get(prof['primary_key']) else "NOT SET"
        backup_set = "set" if os.environ.get(prof['backup_key']) else "not set"

        # Data file on disk
        usage_path = get_usage_data_path(safe_name)
        usage_exists = usage_path.exists()
        usage_label = str(usage_path) if usage_exists else f"{usage_path}  (not yet created)"

        # Archived months
        archive_dir = get_archive_dir(safe_name)
        archived_months = sorted(p.stem for p in archive_dir.glob("*.json")) if archive_dir.exists() else []

        print(f"\n  {prof['name']}")
        print(f"    Safe name:    {safe_name}")
        print(f"    Primary key:  {prof['primary_key']} ({primary_set})")
        print(f"    Backup key:   {prof['backup_key']} ({backup_set})")
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
    """Print every optional .env setting this installation knows about and whether it's set.

    Never prints a secret's value — only whether it's currently set. Run
    after a `git pull` (or any update) to see whether a new optional
    feature has shown up since you last checked; new entries appear here
    automatically once a plugin registers them, no separate "what's new"
    tracking needed.
    """
    fields = _list_optional_env_fields()
    if not fields:
        return

    print("\n" + "=" * 60)
    print("Optional settings (all unset by default — see .env.template):")
    current_section = None
    for key, label, section, _secret in fields:
        if section != current_section:
            print(f"\n  [{section}]")
            current_section = section
        status = "set" if os.environ.get(key) else "not set"
        print(f"    {key}  ({status})  {label}")
    print("\nSet one with: python main.py env set <KEY>")


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


def _print_daily_usage(token_tracker: TokenTracker, professor_name: str, date: Optional[str] = None) -> None:
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


def handle_info_commands(args: argparse.Namespace) -> bool:
    """Handle info/reporting commands without API-key dependent runtime initialization."""
    # Global info commands (no professor required)
    if getattr(args, 'show_config', False):
        show_professor_config()
        return True

    if args.list_models:
        list_available_models()
        return True

    if getattr(args, 'command', None) == 'env':
        _handle_env_command(args)
        return True

    # Usage commands (professor required)
    if getattr(args, 'command', None) == 'usage':
        if not args.professor:
            raise CLIError("Professor name is required for usage commands.")

        usage_subcommand = getattr(args, 'usage_subcommand', None)

        if usage_subcommand == 'sources':
            _handle_usage_sources(args)
            return True

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


def _handle_env_command(args: argparse.Namespace) -> None:
    """Handle 'env add-professor/remove-professor/list/set/unset'.

    This is the CLI-side half of directly editing .env — see
    ``src/env_editor.py`` for why writing to .env directly is safe here
    (every edit is triggered by a person typing a command at their own
    keyboard, never over a network call or as part of syncing between
    machines).
    """
    sub = getattr(args, 'env_subcommand', None)

    if sub == 'add-professor':
        _env_add_professor_interactive(args)
        return
    if sub == 'remove-professor':
        _env_remove_professor(args)
        return
    if sub == 'list':
        _print_optional_settings()
        return
    if sub == 'set':
        _env_set_value(args)
        return
    if sub == 'unset':
        _env_unset_value(args)
        return

    raise CLIError(
        "No env subcommand specified.\n"
        "Usage: python main.py env add-professor\n"
        "       python main.py env remove-professor <identifier>\n"
        "       python main.py env list\n"
        "       python main.py env set <KEY>\n"
        "       python main.py env unset <KEY>"
    )


def _env_add_professor_interactive(args: argparse.Namespace) -> None:
    """Add a professor, prompting interactively for their name and keys.

    Keys are always entered at a hidden prompt (never a command-line flag),
    so they never end up in shell history or a process listing.
    """
    name = getattr(args, 'name', None) or input("Professor's display name (e.g. 'Jeff Heller'): ").strip()
    primary_key = getpass.getpass("Primary API key (hidden): ")
    backup_key = getpass.getpass("Backup API key (optional, hidden — press Enter to skip): ")

    try:
        safe_name = env_editor.add_professor(name, primary_key, backup_key or None)
    except ValueError as e:
        raise CLIError(str(e)) from e

    print(f"\nAdded professor '{name}' (safe name: '{safe_name}').")
    print(f"Try it out: python main.py {safe_name} usage report")


def _env_remove_professor(args: argparse.Namespace) -> None:
    """Remove a professor by safe name or display name, after a yes/no confirmation.

    Confirmation matters here specifically because this deletes real key
    material from .env, not just a display entry.
    """
    identifier = args.identifier
    confirm = input(
        f"Remove professor '{identifier}' from .env? This deletes their API key(s). [y/N]: "
    ).strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled — nothing was removed.")
        return
    try:
        removed_name = env_editor.remove_professor(identifier)
    except ValueError as e:
        raise CLIError(str(e)) from e
    print(f"Removed professor '{removed_name}'.")


def _env_set_value(args: argparse.Namespace) -> None:
    """Set an optional .env variable, prompting for the value (hidden input if it's a secret).

    Unregistered keys are treated as secret by default — hiding input when
    it wasn't necessary is a minor inconvenience; echoing a value that
    turns out to be a key would not be.
    """
    key = args.key.strip().upper()
    known_secrets = {k: secret for k, _label, _section, secret in _list_optional_env_fields()}
    is_secret = known_secrets.get(key, True)

    if getattr(args, 'generate', False):
        if not is_secret:
            raise CLIError(
                f"--generate is only for secret values; '{key}' isn't registered as one. "
                f"Use 'python main.py env set {key}' instead."
            )
        value = secrets.token_urlsafe(32)
    else:
        prompt = f"Value for {key}: "
        value = getpass.getpass(prompt) if is_secret else input(prompt).strip()

    try:
        env_editor.set_optional_value(key, value)
    except ValueError as e:
        raise CLIError(str(e)) from e

    print(f"\n{key} set (value hidden)." if is_secret else f"\n{key}={value}")


def _env_unset_value(args: argparse.Namespace) -> None:
    """Remove an optional .env variable."""
    key = args.key.strip().upper()
    env_editor.unset_optional_value(key)
    print(f"{key} removed (if it was set).")


def _handle_usage_sources(args: argparse.Namespace) -> None:
    """Handle 'usage sources list/add/remove'. See docs/webui-plugin-plan.md section 1.

    Note that the external-source configuration itself (data_sources.json)
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
