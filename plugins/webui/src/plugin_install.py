"""Fetching a plugin from a git repository into ``plugins/``.

A plugin is program code that becomes part of the sandbox: it runs in the same
process, under the same API keys, with the same access to everything. Fetching
one is therefore not like adding a model or a professor, and this module is
written on that basis — it is deliberately narrow about what it will fetch and
where it will put it, and every one of those limits is explained where it is
enforced rather than left as a bare regular expression.

What it does not do is decide *whether* somebody may install a plugin. That is
the caller's question, and ``app.py`` answers it: unlocked, and asked from the
computer the sandbox is running on.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Long enough for a slow network and a large repository, short enough that a
# server thread is not held forever by a host that accepted the connection and
# then said nothing.
_CLONE_TIMEOUT_SECONDS = 300

# Letters, digits, hyphen and underscore, and it may not begin with a dot or an
# underscore. This is the whole defence against a name deciding where it lands:
# "../../src" is not a folder name, and neither is one that starts a hidden
# folder or shadows a private module. The plugin loader also reads this name as
# an importable module name, which is the second reason to keep it plain.
_FOLDER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*$")


class InstallError(Exception):
    """Something the person can read and act on, rather than a traceback."""


@dataclass
class Installed:
    """What was fetched, and where it went.

    Attributes:
        name: The folder it was put in, which is the name the sandbox will
              know the plugin by.
        path: Where that folder is.
        commands: The commands the plugin says it adds, if it could be asked.
    """

    name: str
    path: Path
    commands: list[str]


def suggested_folder_name(repository: str) -> str:
    """Return the folder name a repository would sensibly go into.

    The last part of the address, without the ``.git`` that git addresses
    conventionally end in. Offered rather than imposed, because a repository
    name and a plugin name are not always the same word.

    Args:
        repository: The repository address.

    Returns:
        A name that would pass ``check_folder_name()``, or an empty string if
        nothing usable could be got out of the address.
    """
    tail = repository.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", tail).strip("-_")
    return cleaned if cleaned and _FOLDER_NAME.fullmatch(cleaned) else ""


def check_repository(repository: str) -> str:
    """Return *repository* if it is an address this will fetch from.

    Only ``https://``. That is not tidiness: git understands addresses that
    run programs rather than fetch anything — ``ext::sh -c ...`` is a
    documented git transport that executes the command it is given — and
    ``file://`` would reach anything on this computer. An address beginning
    with a dash is read by git as a flag rather than a place. Requiring
    ``https://`` rules out all three, and leaves every ordinary repository
    people actually use.

    Args:
        repository: What was typed.

    Returns:
        The address, trimmed.

    Raises:
        InstallError: If it is not an ``https://`` address.
    """
    address = repository.strip()
    if not address:
        raise InstallError("Type the address of the repository to install from.")
    if not address.lower().startswith("https://"):
        raise InstallError(
            "Only https:// addresses can be installed from — for example "
            "https://github.com/someone/their-plugin.\n\n"
            "Other kinds of git address can name a program to run rather than "
            "a repository to fetch, so they are not accepted here."
        )
    return address


def check_folder_name(name: str, plugins_dir: Path) -> Path:
    """Return where *name* would go, having settled that it may go there.

    Args:
        name: The folder to create inside ``plugins/``.
        plugins_dir: The ``plugins/`` folder itself.

    Returns:
        The full path the plugin would be fetched into.

    Raises:
        InstallError: If the name is not a plain folder name, or something is
                      already there.
    """
    # The box is labelled "Install into plugins/", so typing that prefix back
    # is the natural reading of it rather than a mistake, and refusing it for
    # containing a slash would be answering a reasonable answer with a lecture
    # about slashes. Only that prefix, and only at the front: anything else
    # with a separator in it is still refused below.
    cleaned = name.strip().strip("/")
    for prefix in ("plugins/", "./"):
        while cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    cleaned = cleaned.strip("/")

    if not _FOLDER_NAME.fullmatch(cleaned):
        raise InstallError(
            f"'{name}' is not a folder name. Use letters, digits, hyphens and "
            "underscores only, starting with a letter or digit — for example "
            "translation-ea."
        )

    target = plugins_dir / cleaned
    # Belt and braces. The pattern above already makes this impossible, and
    # the day somebody widens the pattern is the day this matters.
    if target.parent.resolve() != plugins_dir.resolve():
        raise InstallError(f"'{name}' would not land inside the plugins folder.")
    if target.exists():
        raise InstallError(
            f"There is already a folder called '{cleaned}' in plugins. Remove it "
            "first, or install this under a different name."
        )
    return target


def install_from_git(repository: str, name: str, plugins_dir: Path) -> Installed:
    """Fetch a plugin from *repository* into ``plugins/<name>``.

    Nothing is left behind by a fetch that does not work out: a repository that
    turns out not to hold a plugin is removed again rather than left as a
    folder the sandbox will try to load on next start and complain about ever
    after.

    Args:
        repository: An ``https://`` git address.
        name: The folder to put it in.
        plugins_dir: The ``plugins/`` folder.

    Returns:
        What was installed.

    Raises:
        InstallError: With something the person can act on — a bad address, a
                      name that will not do, git not being installed, the fetch
                      failing, or the repository not holding a plugin.
    """
    address = check_repository(repository)
    target = check_folder_name(name, plugins_dir)

    git = _usable_git()

    # Arguments as a list, never a command line: nothing here is handed to a
    # shell to take apart. The `--` says that what follows is a place and a
    # folder, not more flags, whatever it happens to begin with.
    command = [git, "clone", "--quiet", "--", address, str(target)]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True,
            timeout=_CLONE_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        _remove(target)
        raise InstallError(
            f"{address} did not finish downloading within "
            f"{_CLONE_TIMEOUT_SECONDS // 60} minutes, so it was stopped and "
            "nothing was installed."
        ) from None
    except OSError as e:
        _remove(target)
        raise InstallError(f"Could not run git: {e}") from e

    if done.returncode != 0:
        _remove(target)
        # git's own words. It is better at saying "repository not found" and
        # "could not resolve host" than a guess made from an exit code.
        said = (done.stderr or done.stdout or "").strip()
        raise InstallError(
            f"Could not fetch {address}.\n\n{said}" if said
            else f"Could not fetch {address}."
        )

    if not (target / "plugin.py").is_file():
        _remove(target)
        raise InstallError(
            f"{address} was downloaded, but it does not hold a plugin — there is "
            "no plugin.py in it. Nothing was installed.\n\n"
            "Check that the address is the plugin itself rather than a folder "
            "of several."
        )

    logger.info("Installed plugin %s from %s", target.name, address)
    return Installed(name=target.name, path=target, commands=_commands_named_in(target))


def _usable_git() -> str:
    """Return the path to a git that works, or say why there isn't one.

    Being on the computer is not the same as working. A Mac without the Xcode
    command line tools still has ``/usr/bin/git`` — a stub that exists, is
    found, and then fails with an xcrun error the moment it is run. Asking it
    its version is a cheap way to tell the two apart, and turns "could not
    fetch, here is a paragraph about xcrun" into something somebody can act
    on.

    Raises:
        InstallError: If git is missing, or is there and cannot run.
    """
    found = shutil.which("git")
    advice = ("Install it and try again — on a Mac, `xcode-select --install` "
              "is enough.")
    if found is None:
        raise InstallError(
            "git is not installed on this computer, and it is what fetches a "
            f"plugin. {advice}"
        )
    try:
        done = subprocess.run([found, "--version"], capture_output=True,
                              text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise InstallError(f"git is installed at {found} but cannot be run: {e}") from e
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip()
        raise InstallError(
            f"There is a git at {found}, but running it does not work, so a "
            f"plugin cannot be fetched.\n\n{said}\n\n{advice}" if said else
            f"There is a git at {found}, but running it does not work. {advice}"
        )
    return found


def _commands_named_in(folder: Path) -> list[str]:
    """Return the commands a plugin's source says it adds, as text.

    Read rather than imported. Importing is how a plugin's code starts
    running, and the point of naming its commands here is to show somebody
    what they have just fetched *before* the sandbox restarts into it.

    Returns:
        The commands found, or an empty list if the file does not say plainly.
    """
    try:
        source = (folder / "plugin.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # The annotation is stepped over rather than searched through: every
    # plugin writes `commands: list[str] = [...]`, and a pattern that took the
    # first pair of brackets after the name took the ones in `list[str]` and
    # came back with nothing for every real plugin there is.
    found = re.search(r"^\s*commands\s*(?::[^=\n]*)?=\s*\[([^\]]*)\]",
                      source, re.M | re.S)
    if not found:
        return []
    return [c for c in re.findall(r"[\"']([A-Za-z0-9_-]+)[\"']", found.group(1))]


def _remove(folder: Path) -> None:
    """Take away a folder that should not be left behind."""
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
