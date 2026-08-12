"""The questions setup asks at the terminal, and the wording it asks them in.

Kept beside ``first_run.py`` rather than under ``src/runtime/`` for a
practical reason as well as a tidy one: importing ``src.runtime`` pulls in
the settings machinery, which cannot load until the sandbox knows where the
settings *are*. Setup has to run before that, so it must not depend on it.

The decisions themselves live in ``src/first_run.py``; this is only the
conversation. Keeping them apart means the web interface can ask in its own
way without either copy of the reasoning drifting from the other.

Everything here is written on the assumption that the person reading it did
not choose where anything went, does not think about their computer in terms
of paths, and is at the point in their day where they wanted to translate a
document. So the common answer is always the one they get by pressing Enter,
and every question is about confirming something the sandbox already worked
out rather than supplying something they'd have to go and look up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import first_run, paths
from .errors import CLIError

_RULE = "─" * 64


def _amount(months: int, conversations: int) -> str:
    """Say how much of somebody's work is somewhere, in plain words."""
    if not months and not conversations:
        return "nothing saved yet"
    parts = []
    if months:
        parts.append("1 month of spending" if months == 1
                     else f"{months} months of spending")
    if conversations:
        parts.append("1 conversation" if conversations == 1
                     else f"{conversations} conversations")
    return " and ".join(parts)


def _describe(candidate: first_run.ExtrasCandidate) -> list[str]:
    """Return the lines describing an installation that was found.

    Two parts, named separately on purpose. The settings location holds the
    settings, the keys and the model catalog. Where each person's work is kept
    is written *in* those settings, and is often somewhere else — a folder
    shared with them, or one synced between the computers they use. One list
    covering both would let "your files are here" stand for work that is not
    here at all.
    """
    lines = ["  Settings location", f"    {candidate.path}"]
    if candidate.settings_file:
        people = candidate.people
        who = "no one configured yet" if people == 0 else (
            "1 person configured" if people == 1 else f"{people} people configured"
        )
        lines.append(f"      your settings and API keys   ({who})")
    if candidate.has_catalog:
        lines.append("      your model catalog")

    if candidate.work:
        lines.append("")
        lines.append("  Data locations" if candidate.data_is_elsewhere
                     else "  Data location")
        for w in candidate.work:
            lines.append(f"      {w.name}   ({_amount(w.months, w.conversations)})")
            lines.append(f"        {w.path}")
        if candidate.data_is_elsewhere:
            lines.append("")
            lines.append("  Some of this is kept outside the settings location, in")
            lines.append("  folders named in those settings. All of it is part of")
            lines.append("  this installation, and using it means using all of it.")
    return lines


def _ask_yes_no(question: str, *, default: bool, input_fn: Callable[[str], str],
                print_fn: Callable[..., None]) -> bool:
    """Ask a yes/no question where pressing Enter takes the default."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input_fn(f"{question} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print_fn("  Please answer y or n.")


def _warn_if_synced(path: Path, print_fn: Callable[..., None]) -> None:
    """Say so if this folder is copied to a cloud service. Never refuses."""
    warning = paths.cloud_sync_warning(path)
    if warning:
        print_fn(f"\n  Note: {warning}\n")


def _ask_for_location(input_fn: Callable[[str], str],
                      print_fn: Callable[..., None]) -> Path:
    """Ask where this installation's settings should be kept."""
    default = paths.DEFAULT_EXTRAS_ROOT
    print_fn(
        "\nWhere should the sandbox keep your settings?\n"
        "\nThis is where your API keys and your settings will live, and unless\n"
        "you say otherwise later, so will your usage history and your saved\n"
        "conversations. Keeping them outside the sandbox's own folder is what\n"
        "lets you replace it with a newer version later without losing any of\n"
        "it.\n"
    )
    while True:
        typed = input_fn(f"Folder [{default}]: ").strip()
        if not typed:
            _warn_if_synced(default, print_fn)
            return default

        # Resolved, and shown, before anything is created. What someone
        # types here is a path, and a word that isn't one is still a valid
        # relative path — typing "y" would quietly make a folder called "y"
        # wherever the command happened to be run from, and put their API
        # keys in it. Showing where it actually lands turns an invisible
        # mistake into an obvious one.
        chosen = Path(typed).expanduser()
        if not chosen.is_absolute():
            chosen = (Path.cwd() / chosen).resolve()

        if chosen.exists() and not chosen.is_dir():
            print_fn(f"\n  {chosen} is a file, not a folder. Try another.")
            continue

        print_fn(f"\n  That folder is: {chosen}")
        if _is_within(chosen, paths.PACKAGE_ROOT):
            print_fn(
                "\n  Careful: that is inside the sandbox folder itself, which is\n"
                "  the one place these files should not go — replacing the sandbox\n"
                "  with a newer version would delete them along with it."
            )
        _warn_if_synced(chosen, print_fn)

        # Defaulting to no, because reaching this line at all means what was
        # typed wasn't the obvious answer.
        if _ask_yes_no("  Use this folder?", default=False,
                       input_fn=input_fn, print_fn=print_fn):
            return chosen


def _is_within(candidate: Path, parent: Path) -> bool:
    """Return whether *candidate* sits inside *parent*."""
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def run_interactive_setup(
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
) -> Path:
    """Set this copy of the sandbox up, asking as little as possible.

    Looks for an existing setup first and offers to carry it forward, so
    the usual answer is a single press of Enter. Only a genuine first
    install asks where anything should go.

    Args:
        input_fn: How to ask a question. Replaced in tests.
        print_fn: How to show a line. Replaced in tests.

    Returns:
        The folder now holding this person's own files.

    Raises:
        CLIError: If the person declines every option, or if a folder can't
                  be prepared.
    """
    print_fn(f"\n{_RULE}\nSetting up the PU AI Sandbox\n{_RULE}")

    for candidate in first_run.find_existing():
        print_fn(
            "\nFound an existing installation:\n\n"
            + "\n".join(_describe(candidate))
        )
        if _ask_yes_no("\nUse this installation?", default=True,
                       input_fn=input_fn, print_fn=print_fn):
            _warn_if_synced(candidate.path, print_fn)
            first_run.complete_setup(candidate.path)
            print_fn(f"\nDone. Using {candidate.path}.")
            return candidate.path

    # Nothing found anywhere: a genuine first install.
    chosen = _ask_for_location(input_fn, print_fn)
    try:
        copied = first_run.initialize_extras(chosen)
    except FileExistsError:
        # Somebody typed the path of a setup that find_existing() didn't
        # look in. That folder is real; carry it forward rather than
        # refusing outright.
        print_fn(f"\n{chosen} already holds a setup — using it as it is.")
        first_run.complete_setup(chosen)
        return chosen
    except OSError as e:
        raise CLIError(f"Could not prepare {chosen}: {e}") from e

    first_run.complete_setup(chosen)
    print_fn(f"\nCreated {chosen}")
    for name in copied:
        print_fn(f"    {name}")
    print_fn(_next_steps())
    return chosen


def _next_steps() -> str:
    """What to do now that the files exist.

    Setup makes a folder and three files, none of which does anything on its
    own: there is nobody to bill, and no model to send anything to. Ending with
    "Done" and a path leaves a person to work that out. This says the two
    things that are still missing, in the order they are needed, and gives
    something small to try once they are there.
    """
    return (
        "\nDone — but the sandbox cannot do anything yet. Two things are missing.\n"
        "\n1. Whoever is using this, and their API key:\n"
        "       python main.py settings add-professor\n"
        "\n   It asks for their netID, their name, and their key. The key is\n"
        "   typed at a hidden prompt, so it never reaches your shell history.\n"
        "\n2. At least one model. Which ones you can use depends on your\n"
        "   institution's AI sandbox — Princeton's are listed in its own\n"
        "   documentation, so check there for the current names. Then add one:\n"
        "\n       python main.py webui serve\n"
        "\n   and on the Settings page, under Models, type a name like\n"
        "   openai/gpt-4o. The price is looked up and the model is tried, so\n"
        "   the sandbox knows what it can do. You can also add models by hand\n"
        "   in model_catalog.json.\n"
        "\n   (Using only an external endpoint of your own? Then you need no\n"
        "   models here at all — see docs/configuration.md.)\n"
        "\nThen try it:\n"
        "       python main.py <netid> prompt\n"
        "           Type a question, end with --- on its own line.\n"
        "       python main.py <netid> prompt --dry-run\n"
        "           See what would be sent, without spending anything.\n"
        "       python main.py <netid> usage report\n"
        "           What it has cost so far.\n"
        "\nThat is the smallest part of what this does. Translation and\n"
        "transcription, budgets, shared settings and alternate endpoints are\n"
        "all in the docs — start with README.md, then docs/cli-reference.md.\n"
    )
