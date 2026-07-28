"""The questions setup asks at the terminal, and the wording it asks them in.

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

from .. import first_run, paths
from ..errors import CLIError

_RULE = "─" * 64


def _describe(candidate: first_run.ExtrasCandidate) -> list[str]:
    """Return the lines describing what was found at one location."""
    lines = []
    if candidate.settings_file:
        people = candidate.people
        who = "no one configured yet" if people == 0 else (
            "1 person configured" if people == 1 else f"{people} people configured"
        )
        lines.append(f"    your settings and API keys   ({who})")
    if candidate.has_catalog:
        lines.append("    your model catalogue")
    if candidate.months:
        months = "1 month" if candidate.months == 1 else f"{candidate.months} months"
        lines.append(f"    your usage history           ({months})")
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
    """Ask where the person's own files should be kept."""
    default = paths.DEFAULT_EXTRAS_ROOT
    print_fn(
        "\nWhere should the sandbox keep your files?\n"
        "\nThis is where your API keys, your usage history and your saved\n"
        "conversations will live. Keeping them outside the sandbox folder is\n"
        "what lets you replace it with a newer version later without losing\n"
        "any of it.\n"
    )
    while True:
        typed = input_fn(f"Folder [{default}]: ").strip()
        chosen = Path(typed).expanduser() if typed else default
        _warn_if_synced(chosen, print_fn)
        if chosen.exists() and not chosen.is_dir():
            print_fn(f"  {chosen} is a file, not a folder. Try another.")
            continue
        return chosen


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
        if candidate.in_package:
            print_fn(
                "\nYour files are currently inside the sandbox folder itself:\n"
                + "\n".join(_describe(candidate))
                + "\n\nThat's why replacing the sandbox with a newer version would\n"
                  "destroy them. Moving them out fixes that for good — nothing is\n"
                  "deleted, and everything keeps working exactly as it does now."
            )
            destination = paths.DEFAULT_EXTRAS_ROOT
            print_fn(f"\nThey would move to: {destination}")
            _warn_if_synced(destination, print_fn)
            if not _ask_yes_no("Move them there?", default=True,
                               input_fn=input_fn, print_fn=print_fn):
                destination = _ask_for_location(input_fn, print_fn)
            try:
                moved = first_run.move_out_of_package(destination)
            except (FileExistsError, OSError) as e:
                raise CLIError(f"Could not move your files: {e}") from e
            first_run.complete_setup(destination)
            print_fn("\nMoved:")
            for item in moved:
                print_fn(f"    {item}")
            print_fn(f"\nDone. Your files now live in {destination},")
            print_fn("and the sandbox folder can be replaced whenever you like.")
            return destination

        print_fn(
            f"\nFound your files already at {candidate.path}:\n"
            + "\n".join(_describe(candidate))
        )
        if _ask_yes_no("\nUse these?", default=True,
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
    print_fn(
        "\nDone. Next, add the person who'll be using this:\n"
        "    python main.py settings add-professor"
    )
    return chosen
