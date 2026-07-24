"""Info/report command handlers for CLI runtime actions."""

import argparse
import logging
import os
from datetime import datetime
from typing import Optional

from ..config import load_professor_config, LANGUAGE_MAP
from ..models.catalog import load_model_catalog, get_pricing_unit
from ..errors import CLIError
from ..tracking.token_tracker import TokenTracker, get_usage_data_path, get_archive_dir
from ..tracking.source_config import add_source, get_configured_sources, get_source_id, remove_source

logger = logging.getLogger(__name__)


def show_professor_config() -> None:
    """Display current professor configuration and data-file status."""
    professors = load_professor_config()

    if not professors:
        print("No professors configured in .env file.")
        print("Add entries in the format:")
        print("  PROF_[ID]_NAME=Professor Name")
        print("  PROF_[ID]_KEY=api_key")
        print("  PROF_[ID]_BACKUP_KEY=backup_api_key")
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
