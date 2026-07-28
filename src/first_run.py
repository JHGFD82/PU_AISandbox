"""Deciding what a not-yet-set-up copy of the sandbox should do.

A freshly downloaded package has no marker file (see ``src/paths.py``), and
that absence means one of three things:

* **A first install.** Nobody has used the sandbox on this computer.
* **An upgrade.** The package was replaced; the person's own files are
  still sitting where they were, untouched.

Rather than asking someone to work out which of those they are, this module
goes and looks. Everything here answers questions and prepares folders; none
of it prompts. The command line and the web interface each ask in their own
way, and both get the same answers from here.

**Nothing in this module overwrites a settings file.** That isn't a prompt
someone can click past — it falls out of the control flow. Finding settings
at a location means that location is an existing setup, which is the
``adopt`` path; only somewhere without them can be initialised. A wrong
answer at this point would cost API keys and months of history, so it is
made unreachable rather than confirmed.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import paths


@dataclass(frozen=True)
class ExtrasCandidate:
    """What was found at one possible location for someone's own files.

    Attributes:
        path: The folder looked at.
        settings_file: The settings file found there, or ``None``.
        people: How many people are configured in it. ``0`` if there are
                none or it couldn't be read.
        has_catalog: Whether a model catalog is there.
        months: How many months of usage history are there.
    """

    path: Path
    settings_file: Path | None
    people: int
    has_catalog: bool
    months: int

    @property
    def is_usable(self) -> bool:
        """Whether this looks like a real setup worth offering to carry forward."""
        return self.settings_file is not None or self.months > 0


def _count_people(settings_file: Path) -> int:
    """Return how many people are configured in *settings_file*.

    Parsed here with the standard library rather than through
    ``settings_store``, because that module needs to know where the settings
    file *is* — which is the question being answered. A file that can't be
    read counts as nobody; this is for describing a folder to someone, not
    for loading it.
    """
    try:
        with settings_file.open("rb") as f:
            return len(tomllib.load(f).get("professors", {}))
    except (OSError, tomllib.TOMLDecodeError):
        return 0


def _count_months(data_dir: Path) -> int:
    """Return roughly how many months of usage history *data_dir* holds."""
    if not data_dir.is_dir():
        return 0
    archives = data_dir / "archives"
    months = {p.stem for p in archives.glob("*/*.json")} if archives.is_dir() else set()
    # An installation used only this month has no archives yet, but does
    # have a current-month file per person.
    if any(data_dir.glob("token_usage_*.json")):
        months.add("current")
    return len(months)


def inspect_extras(path: Path) -> ExtrasCandidate:
    """Look at one folder and report what of a setup is there.

    Args:
        path: The folder to look at. It need not exist.

    Returns:
        A description of what was found, for showing to someone before they
        decide.
    """
    settings_file = path / paths.SETTINGS_FILENAME
    found_settings = settings_file if settings_file.is_file() else None
    return ExtrasCandidate(
        path=path,
        settings_file=found_settings,
        people=_count_people(found_settings) if found_settings else 0,
        has_catalog=(path / paths.MODEL_CATALOG_FILENAME).is_file(),
        months=_count_months(path / paths.DATA_DIRNAME),
    )


def find_existing() -> list[ExtrasCandidate]:
    """Look in the likely places for a setup this package could carry forward.

    Returns:
        Only the places that hold something worth carrying forward. Empty
        means this is a genuine first install — the only case where someone
        should be asked to choose from scratch.
    """
    candidate = inspect_extras(paths.DEFAULT_EXTRAS_ROOT)
    return [candidate] if candidate.is_usable else []


def initialize_extras(path: Path) -> list[str]:
    """Create a new extras folder and fill it with the starting-point files.

    Args:
        path: Where to create it. Created along with any missing parents.

    Returns:
        The names of the files copied in, for reporting.

    Raises:
        FileExistsError: If a settings file is already there. Initialising
                         over an existing setup would destroy API keys and
                         usage history, so it is refused rather than
                         confirmed — see this module's docstring.
    """
    settings_file = path / paths.SETTINGS_FILENAME
    if settings_file.exists():
        raise FileExistsError(
            f"{settings_file} already exists — that folder is an existing "
            "setup, not an empty one. Carry it forward instead of starting "
            "fresh there."
        )

    path.mkdir(parents=True, exist_ok=True)
    (path / paths.DATA_DIRNAME).mkdir(exist_ok=True)

    copied = []
    template = paths.template_path("settings.template")
    if template.is_file():
        shutil.copyfile(template, settings_file)
        # The keys go in here. Owner-only from the moment it exists, rather
        # than whatever the account's default happens to be.
        settings_file.chmod(0o600)
        copied.append(paths.SETTINGS_FILENAME)

    prefs_template = paths.template_path("preferences.template.toml")
    prefs = path / paths.PREFERENCES_FILENAME
    if prefs_template.is_file() and not prefs.exists():
        shutil.copyfile(prefs_template, prefs)
        copied.append(paths.PREFERENCES_FILENAME)

    catalog_template = paths.template_path("model_catalog.template.json")
    catalog = path / paths.MODEL_CATALOG_FILENAME
    if catalog_template.is_file() and not catalog.exists():
        shutil.copyfile(catalog_template, catalog)
        copied.append(paths.MODEL_CATALOG_FILENAME)

    return copied


def complete_setup(extras_root: Path) -> None:
    """Record that this package is set up against *extras_root*.

    The last step of every route through setup. Once the marker is written,
    the package stops asking.
    """
    extras_root.mkdir(parents=True, exist_ok=True)
    paths.write_install_marker(extras_root)
