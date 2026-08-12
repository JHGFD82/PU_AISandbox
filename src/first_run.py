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
from dataclasses import dataclass, field
from pathlib import Path

from . import paths


@dataclass(frozen=True)
class WhereTheirWorkIs:
    """Where one person's work is kept, and how much of it is there.

    A settings location says, for each person, where their work belongs: in
    that same folder, or in one shared with them. So a folder holding settings
    is not necessarily a folder holding everything — reading the settings is
    the only way to find the rest, and saying "your files are here" without
    doing that is a guess.

    Attributes:
        netid: Whose work this is.
        name: Their display name, for saying whose.
        path: The folder their work is in.
        elsewhere: Whether that folder is somewhere other than the settings
                   location — a shared folder, typically one that syncs.
        months: How many months of spending are recorded there.
        conversations: How many conversations are saved there.
    """

    netid: str
    name: str
    path: Path
    elsewhere: bool
    months: int
    conversations: int

    @property
    def is_empty(self) -> bool:
        """Whether there is nothing of theirs there yet."""
        return not self.months and not self.conversations


@dataclass
class ExtrasCandidate:
    """What was found at one settings location, and at the data locations it
    points to.

    Attributes:
        path: The settings location looked at.
        settings_file: The settings file found there, or ``None``.
        people: How many people are configured in it. ``0`` if there are
                none or it couldn't be read.
        has_catalog: Whether a model catalog is there.
        months: How many months of usage history there are, across every data
                location the settings point to — which may be nowhere near
                this folder.
        work: Where each person's work is, and how much is there. Empty when
              there are no settings to read it from.
    """

    path: Path
    settings_file: Path | None
    people: int
    has_catalog: bool
    months: int
    work: list["WhereTheirWorkIs"] = field(default_factory=list)

    @property
    def data_is_elsewhere(self) -> bool:
        """Whether any of this setup's work is kept outside this folder."""
        return any(w.elsewhere for w in self.work)

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


def _read_professors(settings_file: Path) -> dict[str, dict]:
    """Return the people configured in *settings_file*, keyed by netID.

    Read with the standard library rather than through ``settings_store``,
    for the same reason ``_count_people()`` is: that module needs to know
    where the settings file is, and that is the question being answered.
    """
    try:
        with settings_file.open("rb") as f:
            people = tomllib.load(f).get("professors", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {netid: entry for netid, entry in people.items() if isinstance(entry, dict)}


def _where_their_work_is(settings_location: Path, netid: str, entry: dict) -> Path:
    """Return the folder holding one person's work, given their settings.

    A folder set to shared-write is theirs and holds their work. One set to
    read-only is somebody else's record being watched, never written to, so
    their work is still here.
    """
    path = str(entry.get("usage_path") or "").strip()
    if path and str(entry.get("usage_mode") or "") == "shared-write":
        return Path(path).expanduser()
    return settings_location / paths.DATA_DIRNAME


def _one_persons_work(netid: str, where: Path, elsewhere: bool) -> tuple[set[str], int]:
    """Return which months hold their spending, and how many conversations.

    The months come back as names rather than a count so that two people who
    both worked in March are one March when the whole setup is summed up.

    The two shapes differ, and the difference is the whole point of them: a
    folder shared with one person holds only them, so nothing in it is filed
    under a netID, while this installation's own data folder holds everybody
    and so files everything under one.

    Args:
        netid: Whose work to count.
        where: The folder to look in.
        elsewhere: Whether *where* is a folder of theirs alone.

    Returns:
        The months with something in them, and how many conversations are
        saved.
    """
    if not where.is_dir():
        return set(), 0

    months: set[str] = set()
    if elsewhere:
        archives = where / "archives"
        months |= {p.stem for p in archives.glob("*.json")} if archives.is_dir() else set()
        calls = where / "calls"
        months |= {p.name for p in calls.iterdir() if p.is_dir()} if calls.is_dir() else set()
        conversations = where / paths.CONVERSATIONS_DIRNAME
    else:
        archives = where / "archives" / netid
        months |= {p.stem for p in archives.glob("*.json")} if archives.is_dir() else set()
        if (where / f"token_usage_{netid}.json").is_file():
            months.add("current")
        conversations = where / paths.CONVERSATIONS_DIRNAME / netid

    saved = (len([p for p in conversations.iterdir() if p.is_dir()])
             if conversations.is_dir() else 0)
    return months, saved


def _count_months(data_dir: Path) -> int:
    """Return roughly how many months of usage history *data_dir* holds.

    Looks only at this folder, for the case where there are no settings to
    say otherwise — a data folder with history in it and no settings file
    beside it is still worth carrying forward.
    """
    if not data_dir.is_dir():
        return 0
    archives = data_dir / "archives"
    months = {p.stem for p in archives.glob("*/*.json")} if archives.is_dir() else set()
    # A setup used only this month has no archives yet, but does have a
    # current-month file per person.
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

    work: list[WhereTheirWorkIs] = []
    every_month: set[str] = set()
    for netid, entry in sorted(_read_professors(found_settings).items()
                               if found_settings else []):
        where = _where_their_work_is(path, netid, entry)
        elsewhere = where != path / paths.DATA_DIRNAME
        theirs, conversations = _one_persons_work(netid, where, elsewhere)
        every_month |= theirs
        work.append(WhereTheirWorkIs(
            netid=netid, name=str(entry.get("name") or netid), path=where,
            elsewhere=elsewhere, months=len(theirs), conversations=conversations))

    # Every month anybody has, wherever it is kept. With no settings to read,
    # this folder is all there is to go on — a data folder with history in it
    # and nothing beside it is still worth carrying forward.
    months = len(every_month) if work else _count_months(path / paths.DATA_DIRNAME)
    return ExtrasCandidate(
        path=path,
        settings_file=found_settings,
        people=_count_people(found_settings) if found_settings else 0,
        has_catalog=(path / paths.MODEL_CATALOG_FILENAME).is_file(),
        months=months,
        work=work,
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
