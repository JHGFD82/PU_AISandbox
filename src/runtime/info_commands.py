"""Info/report command handlers for CLI runtime actions."""

import argparse
import getpass
import logging
from datetime import datetime

from .. import settings_store
from ..config import LANGUAGE_MAP, get_registered_settings, load_professor_config
from ..errors import CLIError
from ..models.catalog import get_pricing_unit, load_model_catalog
from ..services.api_config import credential_path_for_endpoint, list_apis
from ..settings_store import (
    get_configured_sources,
    get_source_id,
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
            f"API key for the '{api_name}' endpoint (see settings.default.toml or preferences.toml)",
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
    from .. import paths
    from ..config import get_registered_settings

    fields = get_registered_settings()
    if not fields:
        return

    print("\n" + "=" * 60)
    print("Optional settings — whether each is set, never what it is set to:")
    current_section = None
    for field in fields:
        if field.section != current_section:
            print(f"\n  [{field.section}]")
            current_section = field.section
        status = "set" if settings_store.get_value(field.key) else "not set"
        print(f"    {field.key}  ({status})")
        print(f"        {field.label}")
        # How to change it: a command where the value cannot simply be typed —
        # a hash, or something that should be generated — and the file
        # otherwise. Said per setting rather than once at the bottom, because
        # the answer is not the same for all of them.
        if field.set_with:
            print(f"        Change it with: python main.py {field.set_with}")
        else:
            print("        Change it by editing settings.toml, or on the web "
                  "interface's settings page.")
    print(f"\nThat file is {paths.settings_path()}")


def list_available_models() -> None:
    """List all available models and their capabilities."""
    config = load_model_catalog()
    models = config["models"]
    pricing_unit = get_pricing_unit()

    bar = "=== Available Models ==="
    print(f"\n{bar}")
    print(f"Pricing is per {pricing_unit:,} tokens\n")

    # Ignoring capitals, so one capitalised name does not sit alone at the top.
    for model_name, pricing in sorted(models.items(), key=lambda pair: pair[0].lower()):
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
        _warn_about_folders_that_are_not_there(args.professor)

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
    """Handle the settings subcommands.

    What is here and what is not follows one rule: a command earns its place by
    doing something opening ``settings.toml`` in an editor cannot. Setup makes
    the file in the first place; adding a professor takes their key at a hidden
    prompt, so it never reaches shell history or a process listing; export-shared
    gathers every plugin's settings and the explanations their authors wrote;
    test-model and model-quirks find things out by asking a provider and write
    down the answers.

    Plain reading and writing of a value went the other way. Anyone comfortable
    typing these commands can open the file, and every one of these settings is
    also on the web interface's settings page for anyone who would rather not.
    ``list`` stays because it reports whether a secret is set without printing
    it, which reading the file does not do.
    """
    sub = getattr(args, 'settings_subcommand', None)

    if sub == 'setup':
        from ..setup_prompts import run_interactive_setup
        run_interactive_setup()
        return
    if sub == 'add-professor':
        _settings_add_professor_interactive(args)
        return
    if sub == 'list':
        _print_optional_settings()
        return
    if sub == 'export-shared':
        _settings_export_shared(args)
        return
    if sub == 'model-quirks':
        _settings_model_quirks(args)
        return
    if sub == 'test-model':
        _settings_test_model(args)
        return

    raise CLIError(
        "No settings subcommand specified.\n"
        "Usage: python main.py settings setup\n"
        "       python main.py settings add-professor\n"
        "       python main.py settings list\n"
        "       python main.py settings export-shared\n"
        "       python main.py settings model-quirks [MODEL]\n"
        "       python main.py settings test-model [MODEL]"
    )


def _key_for_testing(requested: str | None) -> str:
    """Return an API key to run the capability tests with.

    Testing a model means making real requests, which need somebody's key. Any
    professor's will do — the answers are about the model, not about them — so
    this doesn't ask when there is only one person set up.

    Args:
        requested: The netID named with ``--professor``, or ``None`` to pick.

    Returns:
        The API key to use.

    Raises:
        CLIError: If nobody is set up, or if there is more than one person and
                  none was named — with the list to choose from.
    """
    from ..config import get_api_key, load_professor_config

    if requested:
        key, _ = get_api_key(requested)
        return key

    people = load_professor_config()
    if not people:
        raise CLIError(
            "Nobody is set up yet, and testing a model means making real requests "
            "with somebody's API key.\nAdd someone first: python main.py settings add-professor"
        )
    if len(people) > 1:
        names = ", ".join(sorted(people))
        raise CLIError(
            "More than one person is set up, so say whose API key to test with — the "
            f"requests are billed to it.\nChoose from: {names}\n"
            "  python main.py settings test-model <model> --professor <netid>"
        )
    key, _ = get_api_key(next(iter(people)))
    return key


def _settings_test_model(args: argparse.Namespace) -> None:
    """Find out what a model can do by trying it, and save the answers.

    The catalog has to know several things about a model — whether it can
    read images, what it wants the response-length setting called, and so on —
    that no provider publishes anywhere readable. This settles them by sending
    the model a few very small requests and seeing which it accepts.

    A model that is not in the catalog yet is added by this, provided it is
    named as its provider and then the model — ``anthropic/claude-sonnet-5``.
    The two are the same job: both come down to asking the provider what the
    model can do and writing the answer down, and somebody who has just been
    told a model is missing is asking for it to be there.

    A model already in the catalog can be named either way. The catalog is
    keyed by the model alone, but every other place a model is typed takes the
    provider too, and refusing that here meant saying 'anthropic/claude-sonnet-5'
    was not in a catalog while listing claude-sonnet-5 in the same breath.

    With no model named, tests every model in the catalog. That is the way to
    correct a catalog built before testing existed, where anything added
    automatically was recorded as unable to read images because there was no
    way to find out.
    """
    from ..console import print_banner
    from ..models import (
        add_model_to_catalog, get_available_models, load_model_catalog,
        save_model_catalog,
    )
    from ..models.capabilities import (
        apply_capability_report, client_for_testing, probe_model_capabilities,
    )

    available = get_available_models()
    typed = args.model

    # The catalog is keyed by the model alone — 'claude-sonnet-5'. Everywhere
    # else a model is named as its provider and then the model, which is what
    # -m takes, what the web interface asks for and what the documentation
    # shows. Both are accepted here, because the alternative was telling
    # somebody that 'anthropic/claude-sonnet-5' is not in a catalog and then
    # listing claude-sonnet-5 among the models it knows about.
    wanted = typed.split("/", 1)[1] if typed and "/" in typed else typed

    if typed is not None and wanted not in available:
        if "/" not in typed:
            raise CLIError(
                f"'{typed}' isn't in the catalog. To add it, name it as its "
                "provider and then the model — for example "
                f"'openai/{typed}' or 'anthropic/{typed}' — and this will look "
                "up its price and find out what it can do.\n"
                f"Already in the catalog: {', '.join(sorted(available, key=str.lower))}"
            )
        # Named with a provider and not here yet, so this is the request to
        # add it. Testing a model that is not in the catalog and adding one
        # are the same job: both come down to asking the provider what it can
        # do and writing the answer down.
        api_key = _key_for_testing(getattr(args, 'professor', None))
        print_banner("ADDING A MODEL")
        print(f"{typed} is not in the catalog yet. Looking up its price and\n"
              "finding out what it can do. This costs a fraction of a cent.\n")
        try:
            added, _entry = add_model_to_catalog(typed, api_key=api_key)
        except Exception as e:
            raise CLIError(
                f"Could not add '{typed}': {e}\n"
                "A model that does not exist, or one this key cannot reach, "
                "looks like this."
            ) from e
        print(f"\n{added} added.")
        print("=" * 60)
        return

    targets = [wanted] if typed else sorted(available, key=str.lower)

    api_key = _key_for_testing(getattr(args, 'professor', None))
    remove_missing = bool(getattr(args, 'remove_missing', False))

    client = client_for_testing(api_key)

    print_banner("TESTING WHAT THESE MODELS CAN DO")
    print(
        f"Trying {len(targets)} model{'s' if len(targets) != 1 else ''} with a few very "
        "small requests each.\nThis costs a fraction of a cent and takes a moment per model.\n"
    )

    catalog = load_model_catalog()
    changed = 0
    gone: list[str] = []
    for name in targets:
        print(f"{name}")
        report = probe_model_capabilities(name, client)
        if report.missing:
            # Not a failure to test — there is nothing there to test. Named
            # separately because the answer is different: this entry is stale
            # and wants taking out, not trying again later.
            gone.append(name)
            print("  no such model — this entry is out of date")
            continue
        if not report.reachable:
            print("  could not be reached — nothing changed")
            for line in report.unsettled:
                print(f"  {line}")
            continue

        before = dict(catalog["models"].get(name, {}))
        after = apply_capability_report(before, report)
        for line in report.settled:
            print(f"  {line}")
        for line in report.unsettled:
            print(f"  (not settled) {line}")
        if after != before:
            catalog["models"][name] = after
            changed += 1
            # Written now rather than at the end. A sweep of the whole
            # catalog is a few minutes of requests, and keeping it all until
            # the last one means an interruption anywhere throws away every
            # answer already paid for.
            save_model_catalog(catalog)
            print("  saved")
        else:
            print("  already recorded correctly")

    if gone:
        # Not removed on its own. A model can 404 for a day because a provider
        # is mid-change or access was altered, and quietly deleting an entry
        # somebody configured is not something to do on one failed request.
        for name in gone:
            if remove_missing:
                catalog["models"].pop(name, None)
                changed += 1
        if remove_missing:
            save_model_catalog(catalog)
            print(f"\nRemoved {len(gone)}: {', '.join(gone)}")
        else:
            print(f"\n{len(gone)} no longer exist: {', '.join(gone)}")
            print("They cannot be used, and every request for one will fail. To take")
            print("them out:")
            print("  python main.py settings test-model --remove-missing")

    print(f"\n{changed} of {len(targets)} updated.")
    print("=" * 60)


def _settings_model_quirks(args: argparse.Namespace) -> None:
    """Show what models have been found to refuse, or forget it for one of them.

    When a provider turns down part of a request, the sandbox notes which part
    and leaves it out from then on, so the same failure doesn't happen twice.
    Nothing is ever forgotten on its own — that would bring the failure back on
    a schedule — but providers do change, and this is how somebody says "try
    that again" without opening the catalog file and editing it by hand.

    With no model named, lists what has been learned and when. With one, forgets
    it: the next request includes those parts again, and if the provider still
    objects the note comes straight back.
    """
    from ..console import print_banner
    from ..models import clear_rejected_fields, models_with_rejected_fields

    known = models_with_rejected_fields()

    if args.model is None:
        print_banner("WHAT MODELS HAVE BEEN FOUND TO REFUSE")
        if not known:
            print("Nothing. No provider has turned down part of a request.")
            print("=" * 60)
            return
        for name in sorted(known, key=str.lower):
            print(f"\n{name}")
            for field, note in sorted(known[name].items()):
                when, _, said = note.partition(": ")
                print(f"  {field}  (noted {when})")
                # What a provider says when it refuses something is often a
                # wall of machine-readable detail. The whole of it stays in the
                # catalog; enough to recognise it is what belongs on screen.
                said = " ".join(said.split())
                print(f"    {said[:110] + '…' if len(said) > 110 else said}")
        print(
            "\nTo have one of these worked out again — say a provider has since "
            "started\naccepting it — run:"
        )
        print("  python main.py settings model-quirks <model>")
        print("=" * 60)
        return

    forgotten = clear_rejected_fields(args.model)
    if not forgotten:
        if args.model in known:
            raise CLIError(f"Nothing was recorded against '{args.model}'.")
        raise CLIError(
            f"Nothing has been recorded against '{args.model}'. Run "
            "'python main.py settings model-quirks' to see which models have anything."
        )
    print(f"Forgot what '{args.model}' was found to refuse:")
    for field in sorted(forgotten):
        print(f"  {field}")
    print(
        "\nThe next request will include these again. If the provider still turns "
        "them\ndown, that is noted afresh and nothing is lost."
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


def _warn_about_folders_that_are_not_there(professor: str) -> None:
    """Say so, before any figure is printed, if this person's folder is missing.

    Their work is kept wherever their settings say, and a folder that is not
    there reads as no spending: the report simply has nothing to add. That is
    the same output as a person who genuinely has not spent anything, which is
    the one thing a spending report must never be unclear about.

    Args:
        professor: Whose report is about to be printed.
    """
    from ..tracking.token_tracker import unreadable_folders

    missing = [s for s in unreadable_folders()
               if (s.professor or "").strip().lower() == professor.strip().lower()]
    for source in missing:
        print(
            f"\n  Note: {professor}'s work is kept in\n"
            f"      {source.resolved_path()}\n"
            "  and that folder is not there at the moment. An external drive that\n"
            "  is not plugged in, or a synced folder that has not come down yet,\n"
            "  looks like this. The figures below leave out whatever is in it."
        )


def _handle_usage_sources(args: argparse.Namespace) -> None:
    """Handle 'usage sources list'.

    Lists every folder this installation reads usage from, for everybody, not
    only for the person named on the command line — a professor is asked for by
    every usage subcommand, and this one has nothing to do with them.
    """
    sub = getattr(args, 'sources_subcommand', None)

    if sub == 'list':
        _print_configured_sources()
        return

    raise CLIError(
        "Usage: python main.py <professor> usage sources list\n"
        "\nA person's usage folder is set on the web interface's settings page,\n"
        "beside their name — or by adding usage_path to their table in\n"
        "settings.toml yourself."
    )


def _print_configured_sources() -> None:
    """Print this installation's source id and every configured external source."""
    print(f"\nThis installation's source id: {get_source_id()}")
    sources = get_configured_sources()
    if not sources:
        print("Nobody's usage is being read from anywhere else.")
        print("Set a folder on the settings page of the web interface, beside "
              "the person's name.")
        return
    print("\nUsage also read from:")
    for s in sources:
        who = s.professor or "everyone found there"
        print(f"  {who}  [{s.mode}]  {s.path}")


def _settings_export_shared(args: argparse.Namespace) -> None:
    """Write a shared settings draft for whoever looks after one to edit and place.

    Gathers every setting the package and the installed plugins have, with the
    explanations their authors wrote, all commented out — so a draft placed
    unedited changes nothing for anybody. Where a shared file is already in use,
    its decisions are carried across untouched and anything new since is marked,
    which is what makes coming back for a second draft worth doing.

    Deliberately writes a file for the person to move rather than putting it in
    the shared location itself. The sandbox never writes to a shared settings
    file: it belongs to a group, and several installations writing to a folder
    that syncs is how conflicted copies happen.

    Args:
        args: The parsed flags. ``--output`` chooses where to write, ``--from``
              names an existing shared file to carry decisions across from.
    """
    from pathlib import Path

    from .. import paths, settings_store
    from ..shared_settings import build_shared_settings, count_new

    if args.from_existing:
        existing = Path(args.from_existing).expanduser()
        if not existing.exists():
            raise CLIError(
                f"No shared settings file at '{existing}'.\n"
                "Check the path, or leave --from off to start a fresh draft."
            )
    else:
        configured = settings_store.get_shared_settings_path()
        existing = configured if configured is not None and configured.exists() else None

    destination = (
        Path(args.output).expanduser() if args.output
        else paths.extras_root() / "shared-settings.toml"
    )

    text = build_shared_settings(
        plugins_dir=paths.PACKAGE_ROOT / "plugins",
        package_defaults=paths.PACKAGE_ROOT / "settings.default.toml",
        existing=existing,
    )
    try:
        destination.write_text(text, encoding="utf-8")
    except OSError as error:
        raise CLIError(f"Could not write '{destination}': {error}") from error

    print(f"\nWrote a shared settings draft to:\n    {destination}\n")
    if existing is not None:
        new = count_new(text)
        print(f"Carried your existing decisions across from:\n    {existing}")
        if new:
            print(
                f"\n{new} setting{'s' if new != 1 else ''} did not exist when that file "
                f"was made. Search the draft for 'NEW:' to find {'them' if new != 1 else 'it'}."
            )
        else:
            print("\nNothing has appeared since that file was made.")
    print(
        f"\nEverything in it is commented out, so placing it unedited changes nothing.\n"
        f"\nWhat to do next:\n"
        f"  1. Open {destination.name} and uncomment what the group should share.\n"
        f"  2. Rename it if you like — '{destination.stem}' is just what the draft\n"
        f"     comes out as, and each installation points at a path, not a name.\n"
        f"     Renaming now saves telling everyone a new path later.\n"
        f"  3. Put it somewhere every member can read: a synced folder, a\n"
        f"     network share, anywhere they all have access to.\n"
        f"  4. Tell each member to run this once:\n"
        f"         python main.py settings set shared_settings.path <where you put it>\n"
    )
