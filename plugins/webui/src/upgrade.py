"""Moving this copy of the sandbox onto a newer version, from the browser.

Somebody at a terminal upgrades the sandbox by asking git for the newer files
and then running ``start.py``. Most of the people this sandbox was built for
never open a terminal, so this module does the same two things for them, and
the web interface puts a button on it.

What an update *is*, here: the ``package`` location — the code — is a copy of a
git repository. Updating means asking that repository what has changed and
moving this copy forward onto it. The ``settings`` and ``data`` locations are
somewhere else entirely (see ``src/paths.py``), so an update never touches
anybody's API keys, spending records or conversations.

Two things are worth knowing before changing anything in here.

The first is that this runs inside the very program it is replacing. That is
survivable — Python has already read the code it is running, so new files on
disk change nothing until the sandbox restarts — but it is why the order of
what happens matters so much, and why the last step is deliberately somebody
else's job.

The second is the failure this module exists to avoid. Getting the new files is
easy; the danger is stopping halfway, with new code on disk and the extra
software it needs not installed, so the next start fails and the person is left
with no working sandbox and no browser to fix it from. Everything below is
arranged so that cannot happen: the files only move when the move is clean,
anything that goes wrong afterwards puts them back, and the record of what is
installed is only written once installing has actually worked — because a
missing record is exactly what makes the next ``python3 start.py`` notice and
repair things by itself.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from src.paths import PACKAGE_ROOT

# Picked out of sys.modules rather than imported by name — see the comment in
# plugin_install.py, and app.py's module docstring for the whole story.
git_tool = sys.modules["_pu_webui_git_tool"]

logger = logging.getLogger(__name__)

# Completes the sentence git_tool.usable_git() builds when git is missing or
# is the stand-in a Mac has before the developer tools are installed.
_WHAT_GIT_IS_FOR = "the sandbox cannot update itself"

# How long each part is given. Looking at files on this computer is instant, so
# a long wait there means something is wrong rather than something is slow.
# Fetching and installing both cross the network and are allowed to take a
# while — a campus connection on a bad day is still a working connection.
_LOOKING_TIMEOUT_SECONDS = 15
_FETCHING_TIMEOUT_SECONDS = 120
_MOVING_TIMEOUT_SECONDS = 60
_INSTALLING_TIMEOUT_SECONDS = 900

# Enough of a list to see what an update contains without turning the page into
# a changelog nobody asked for.
_HOW_MANY_CHANGES_TO_LIST = 20
_HOW_MANY_FILES_TO_NAME = 10

# Enough of pip's output to see what went wrong. Pip says a great deal before
# it fails, and almost all of what matters is at the end.
_HOW_MANY_LINES_OF_PIP = 25

# What the page draws its progress bar out of. Four whatever happens: the step
# that installs new software is skipped when nothing new is needed, and the
# same step then does the tidying up instead. A total that changed halfway
# would show as a bar jumping backwards.
TOTAL_STEPS = 4

# Held for the whole of an update. The settings page can be open in an ordinary
# window and inside the chat window's settings panel at the same time, and two
# updates running at once in one folder is the worst thing that could happen
# here — far worse than one of them being told to wait.
_only_one_at_a_time = threading.Lock()

# start.py, once it has been loaded. See _launcher().
_launcher_module = None


class UpgradeError(Exception):
    """Something the person can read and act on, rather than a traceback.

    The same role ``InstallError`` plays in plugin_install.py: everything that
    goes wrong in here is turned into one of these, with a message written for
    somebody who is looking at a browser and has no terminal open.
    """


class StoppedPartway(UpgradeError):
    """The new files are in place, but the software they need is not.

    The one failure that leaves anything behind, and so the one that has to be
    described differently. The files are deliberately *not* put back by
    themselves: installing software fails for passing reasons far more often
    than for lasting ones — a dropped connection, a slow mirror — and quietly
    undoing the whole update because a download timed out would throw away
    work the person can usually get for free by pressing the button again. So
    both ways out are offered instead, and they choose.

    Nothing is lost by waiting either way. The record of what is installed has
    not been written, so the sandbox is not restarted into this state, and the
    next ``python3 start.py`` finishes the installing by itself.

    Attributes:
        was: The version to go back to, if going back is what they choose.
    """

    def __init__(self, message: str, was: str) -> None:
        super().__init__(message)
        self.was = was


@dataclass(frozen=True)
class Version:
    """Which version a copy of the sandbox is.

    There is no version number to show. Nothing in this project bumps one, and
    the two that exist in the source disagree with each other, so showing one
    would be showing something untrue. A commit — one recorded change to the
    code — has an identifier and a date that cannot drift, because they are the
    thing itself rather than a label somebody has to remember to update.

    Attributes:
        sha: The short identifier of that change, as git writes it, e.g.
             ``c4c9199``. Shown to people as an identifier, not a number.
        date: The day it was made, as ``YYYY-MM-DD``.
        subject: The one-line summary written with it.
    """

    sha: str
    date: str
    subject: str


@dataclass(frozen=True)
class Available:
    """What was found when the published version was last looked at.

    Attributes:
        current: The version this copy is, or None if that cannot be told.
        latest: The newest published version, or None if it was not reached.
        behind: How many changes have been published since this copy's version.
                Zero means this copy is up to date.
        changes: The one-line summaries of those changes, newest first.
        blocked: Why this copy cannot update itself, in words, or None if it
                 can. Something about this installation rather than about the
                 network — a copy downloaded as a ZIP file, or one with edited
                 files in it — so it will still be true the next time.
        offline: Why the published version could not be reached this time, or
                 None. Unlike *blocked*, this is worth simply trying again.
        requirements_changing: Whether the update also needs new software
                               installed, which makes it take longer.
        defaults_changing: Whether the update changes the sandbox's built-in
                           default settings.
        checked_at: When this was found out, or None if nothing has been
                    looked at yet.
    """

    current: Optional[Version] = None
    latest: Optional[Version] = None
    behind: int = 0
    changes: list[str] = field(default_factory=list)
    blocked: Optional[str] = None
    offline: Optional[str] = None
    requirements_changing: bool = False
    defaults_changing: bool = False
    checked_at: Optional[str] = None


@dataclass(frozen=True)
class Applied:
    """What an update actually did.

    Attributes:
        was: The identifier of the version that was here before.
        now: The identifier of the version that is here now.
        installed_dependencies: Whether new software had to be installed.
    """

    was: str
    now: str
    installed_dependencies: bool


# ── The messages ────────────────────────────────────────────────────────────
# Kept together and named, because these are the whole point of the preflight
# below: every one of them has to leave somebody knowing what to do next.

_NOT_A_CLONE = (
    "This copy of the sandbox was not fetched with git — it was most likely "
    "downloaded as a ZIP file — so there is nothing for it to update itself "
    "from.\n\n"
    "To move to a newer version, download a fresh copy and run "
    "`python3 start.py`. Your `settings` location and your `data` locations "
    "are kept outside this folder, so nothing you have is affected, and the "
    "fresh copy will offer to go on using them."
)

_INSIDE_SOMETHING_ELSE = (
    "The sandbox is sitting inside another git repository ({top}) rather than "
    "being one of its own. Updating from here would change that other "
    "repository instead of the sandbox, so it will not be attempted.\n\n"
    "Move the sandbox out of that folder, or update it from a terminal."
)

_NOT_ON_A_BRANCH = (
    "This copy is parked on one particular version rather than following the "
    "main line of development, so there is nothing for it to move forward "
    "onto.\n\n"
    "In a terminal, `git switch main` puts it back."
)

_NOTHING_TO_FOLLOW = (
    "This copy's branch ({branch}) is not following anything online, so there "
    "is nowhere to look for a newer version."
)

_FILES_HAVE_BEEN_CHANGED = (
    "Some of the sandbox's own files have been changed on this computer, so "
    "updating would either undo those changes or stop halfway. Nothing has "
    "been touched.\n\n"
    "Changed: {files}\n\n"
    "Settings edited by hand inside the `package` location are the usual "
    "reason. Those belong in `preferences.toml`, which lives in your "
    "`settings` location alongside your API keys — outside the package, so "
    "updating never disturbs it. Move your changes there and check again."
)

_IT_HAS_CHANGES_OF_ITS_OWN = (
    "This copy has {how_many} of its own that have not been published, so it "
    "cannot simply be moved forward onto the published version. Somebody has "
    "been working on the code here.\n\n"
    "Updating this copy is a job for a terminal."
)

_TOOK_TOO_LONG = (
    "git was still working after {seconds} seconds and was stopped, so "
    "nothing was changed. This usually means the network is very slow or is "
    "not answering."
)


def _now() -> str:
    """Return the time right now, written the one way this module writes it."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(*arguments: str, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run one git command in the package location.

    Raises:
        GitUnusable: If git is missing or cannot be run.
        UpgradeError: If it ran longer than *timeout*.
    """
    git = git_tool.usable_git(_WHAT_GIT_IS_FOR)
    try:
        return git_tool.run_git(git, PACKAGE_ROOT, *arguments, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UpgradeError(_TOOK_TOO_LONG.format(seconds=timeout)) from None


def _said(done: subprocess.CompletedProcess[str]) -> str:
    """Return git's own words from a finished command."""
    return git_tool.what_it_said(done)


# ── What this copy is ───────────────────────────────────────────────────────

def package_version() -> Optional[Version]:
    """Return which version this copy of the sandbox is, if it can be told.

    Asks nothing of the network and changes nothing, so the settings page can
    call it on every load. Anything unexpected comes back as None rather than
    as an error: not being able to name the version is a reason to say less on
    that page, never a reason for it to fail to open.

    Returns:
        The version, or None if this copy was not fetched with git, or git is
        not usable, or git did not answer in the ordinary way.
    """
    try:
        done = _run("log", "-1", "--format=%h%x09%cs%x09%s",
                    timeout=_LOOKING_TIMEOUT_SECONDS)
    except (git_tool.GitUnusable, UpgradeError, OSError):
        return None
    if done.returncode != 0:
        return None
    parts = done.stdout.strip().split("\t", 2)
    if len(parts) != 3:
        return None
    return Version(sha=parts[0], date=parts[1], subject=parts[2])


def why_this_copy_cannot_be_updated() -> Optional[str]:
    """Return why this copy cannot update itself, or None if it can.

    Everything asked here is about this computer — no network — so it is quick
    and can be asked again at any time. Each answer is a sentence somebody can
    act on rather than a reason code, because these are the ones a person will
    actually meet: a sandbox downloaded as a ZIP file, a Mac without the
    developer tools, or a settings file edited in place.

    They are asked in the order they are, because a later question makes no
    sense once an earlier one has failed — there is no branch to check on a
    folder that is not a git repository at all.

    Returns:
        The reason, written for somebody with no terminal open, or None if
        nothing is in the way.
    """
    try:
        return _first_reason()
    except git_tool.GitUnusable as e:
        return str(e)
    except UpgradeError as e:
        return str(e)
    except OSError as e:
        logger.warning("Could not work out whether this copy can be updated: %s", e)
        return f"Could not work out whether this copy can be updated: {e}"


def _first_reason() -> Optional[str]:
    """The body of why_this_copy_cannot_be_updated(), without the safety net."""
    # Is this a git repository at all, and is it *this* one? `git -C` searches
    # upwards, so a sandbox unpacked inside somebody's own repository — a
    # folder under version control, a developer's larger project — would
    # otherwise be answered about by that outer repository, and updating would
    # move that instead.
    done = _run("rev-parse", "--show-toplevel", timeout=_LOOKING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        return _NOT_A_CLONE
    top = Path(done.stdout.strip())
    try:
        same = top.resolve() == Path(PACKAGE_ROOT).resolve()
    except OSError:
        same = False
    if not same:
        return _INSIDE_SOMETHING_ELSE.format(top=top)

    # Following the main line, rather than parked on one version.
    done = _run("symbolic-ref", "--quiet", "--short", "HEAD",
                timeout=_LOOKING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        return _NOT_ON_A_BRANCH
    branch = done.stdout.strip()

    # And that line has somewhere online it is following.
    done = _run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                timeout=_LOOKING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        return _NOTHING_TO_FOLLOW.format(branch=branch)

    # Nothing changed here that moving forward would undo.
    #
    # Files git was never told about are deliberately not counted
    # (--untracked-files=no): somebody's own notes sitting in the folder are
    # not a reason to refuse an update. The one case where such a file really
    # does block the move — a new plugin arriving from upstream into a folder
    # name somebody already used — is caught by git itself when the move is
    # attempted, and git says it better than a guess made here would.
    done = _run("status", "--porcelain", "--untracked-files=no",
                timeout=_LOOKING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        return f"Could not check whether anything here has been changed.\n\n{_said(done)}"
    changed = _changed_files(done.stdout)
    if changed:
        return _FILES_HAVE_BEEN_CHANGED.format(files=", ".join(changed))

    return None


def _changed_files(porcelain: str) -> list[str]:
    """Return the filenames out of ``git status --porcelain`` output.

    Each line is two characters saying what changed, a space, then the name.
    A rename is written ``old -> new``, and the new name is the useful half.

    Returns:
        Up to ten names. Ten is enough to recognise what happened; a person
        with more than ten changed files is not reading a list, they are
        opening a terminal.
    """
    names = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        name = line[3:].strip().strip('"')
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        names.append(name)
        if len(names) == _HOW_MANY_FILES_TO_NAME:
            break
    return names


# ── Looking for a newer version ─────────────────────────────────────────────

def check_for_updates() -> Available:
    """Ask the published repository what has changed, without changing anything.

    Reaches the network, so it is slow enough to be worth doing on a background
    thread and remembering the answer. Nothing on disk is altered except git's
    own private note of what the published version currently is.

    Returns:
        What was found. A copy that cannot be updated comes back with *blocked*
        filled in and nothing else attempted; a network that could not be
        reached comes back with *offline* filled in, which is worth trying
        again later.
    """
    current = package_version()
    blocked = why_this_copy_cannot_be_updated()
    if blocked is not None:
        return Available(current=current, blocked=blocked, checked_at=_now())

    try:
        return _look(current)
    except git_tool.GitUnusable as e:
        return Available(current=current, blocked=str(e), checked_at=_now())
    except UpgradeError as e:
        return Available(current=current, offline=str(e), checked_at=_now())
    except OSError as e:
        return Available(current=current, offline=str(e), checked_at=_now())


def _look(current: Optional[Version]) -> Available:
    """The body of check_for_updates(), once the copy is known to be updatable."""
    done = _run("fetch", "--quiet", "--no-tags", timeout=_FETCHING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        said = _said(done)
        return Available(
            current=current,
            offline=(
                "Could not reach the published version to check for updates. "
                "This is almost always the network — a dropped connection, or "
                "a university proxy."
                + (f"\n\n{said}" if said else "")
            ),
            checked_at=_now(),
        )

    # Both directions in one question: how many changes are here that are not
    # published, and how many are published that are not here.
    done = _run("rev-list", "--left-right", "--count", "HEAD...@{u}",
                timeout=_LOOKING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        return Available(current=current, offline=_said(done), checked_at=_now())
    ahead, behind = _two_counts(done.stdout)

    if ahead:
        how_many = "1 change" if ahead == 1 else f"{ahead} changes"
        return Available(current=current,
                         blocked=_IT_HAS_CHANGES_OF_ITS_OWN.format(how_many=how_many),
                         checked_at=_now())

    if behind == 0:
        return Available(current=current, latest=current, behind=0,
                         checked_at=_now())

    done = _run("log", "--no-merges", f"--max-count={_HOW_MANY_CHANGES_TO_LIST}",
                "--format=%s", "HEAD..@{u}", timeout=_LOOKING_TIMEOUT_SECONDS)
    changes = [line for line in done.stdout.splitlines() if line.strip()]

    done = _run("log", "-1", "--format=%h%x09%cs%x09%s", "@{u}",
                timeout=_LOOKING_TIMEOUT_SECONDS)
    parts = done.stdout.strip().split("\t", 2)
    latest = Version(*parts) if len(parts) == 3 else None

    return Available(
        current=current,
        latest=latest,
        behind=behind,
        changes=changes,
        requirements_changing=_is_changing("requirements.txt"),
        defaults_changing=_is_changing("settings.default.toml",
                                       "plugins/*/settings.toml"),
        checked_at=_now(),
    )


def _two_counts(text: str) -> tuple[int, int]:
    """Return the two numbers ``git rev-list --left-right --count`` prints."""
    parts = text.split()
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _is_changing(*paths: str) -> bool:
    """Return whether an update would alter any of *paths*.

    Used to warn about the two things a person would want warning about before
    pressing the button: an update that also has to install new software, and
    one that changes the sandbox's built-in default settings.
    """
    try:
        done = _run("diff", "--name-only", "HEAD", "@{u}", "--", *paths,
                    timeout=_LOOKING_TIMEOUT_SECONDS)
    except (git_tool.GitUnusable, UpgradeError, OSError):
        return False
    return done.returncode == 0 and bool(done.stdout.strip())


# ── Doing it ────────────────────────────────────────────────────────────────

def an_update_is_running() -> bool:
    """Return whether an update is going on right now.

    Asked by the web interface so a second request can be turned away with an
    explanation before it starts streaming anything. It is not the thing that
    makes two updates impossible — apply_update() takes the lock itself, which
    closes the gap between asking this and starting.
    """
    return _only_one_at_a_time.locked()


def apply_update(on_progress: Callable[[int, str], None]) -> Applied:
    """Move this copy onto the published version, saying what it is doing.

    Args:
        on_progress: Called as the work goes on, with the step number (1 to
                     ``TOTAL_STEPS``) and a line describing what is happening,
                     so the page can draw a progress bar and a caption. While
                     software is being installed it is called repeatedly with
                     the same step number and a changing line.

    Returns:
        What was done, including whether new software had to be installed.

    Raises:
        UpgradeError: If anything went wrong. The files are always back as
                      they were before the call, and the record of what is
                      installed has deliberately not been written — which is
                      what makes the next ``python3 start.py`` notice and
                      finish the job.
    """
    if not _only_one_at_a_time.acquire(blocking=False):
        raise UpgradeError(
            "An update is already running. Wait for it to finish — it may be "
            "going on in another window."
        )
    try:
        return _apply(on_progress)
    finally:
        _only_one_at_a_time.release()


def _apply(on_progress: Callable[[int, str], None]) -> Applied:
    """The body of apply_update(), with the one-at-a-time lock already held."""
    # Asked again, rather than trusting the last look. Somebody may have edited
    # a file, or unplugged a network, between seeing the button and pressing
    # it, and every one of these answers stops the update before it starts
    # rather than partway through.
    on_progress(1, "Checking this copy can be updated")
    blocked = why_this_copy_cannot_be_updated()
    if blocked is not None:
        raise UpgradeError(blocked)

    on_progress(2, "Asking for what has changed")
    done = _run("fetch", "--quiet", "--no-tags", timeout=_FETCHING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        raise UpgradeError(
            "Could not reach the published version, so nothing was changed. "
            "This is almost always the network — a dropped connection, or a "
            "university proxy.\n\n" + _said(done)
        )

    # Written down before anything moves. Everything after this point can put
    # the files back, and this is what it puts them back to.
    done = _run("rev-parse", "HEAD", timeout=_LOOKING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        raise UpgradeError("Could not work out which version this copy is, so "
                           "nothing was changed.\n\n" + _said(done))
    was = done.stdout.strip()

    on_progress(3, "Getting the new files")
    # --ff-only, never `git pull`. A pull that cannot be done cleanly stops
    # halfway and leaves the folder full of half-merged files with conflict
    # markers in them, and nobody is getting out of that from a browser. This
    # either moves cleanly or refuses and changes nothing at all — and the
    # check above has already ruled out the one case where it would refuse for
    # a reason worth explaining, so anything it says here is worth passing on
    # as it stands.
    done = _run("merge", "--ff-only", "--quiet", "@{u}",
                timeout=_MOVING_TIMEOUT_SECONDS)
    if done.returncode != 0:
        raise UpgradeError(
            "The new files could not be put in place, so nothing was "
            "changed.\n\n" + _said(done)
        )

    # Asked of the environment, not of the update. "Did requirements.txt change
    # in this update?" is the wrong question and is wrong in the case that
    # matters most: pressing Try again after installing failed. By then the
    # files have already moved, so nothing changed in *this* pass, and asking
    # that question would skip the installing and then write the record saying
    # it had been done. Asking what is actually installed is right both times.
    installed = False
    if dependencies_are_current():
        on_progress(4, "Finishing up")
    else:
        on_progress(4, "Installing new software")
        try:
            _install_dependencies(lambda line: on_progress(4, line))
        except UpgradeError as e:
            raise StoppedPartway(str(e), was[:7]) from e
        installed = True

    _record_what_is_installed()

    now = package_version()
    return Applied(was=was[:7], now=now.sha if now else "",
                   installed_dependencies=installed)


def _put_the_files_back(sha: str) -> None:
    """Return the package location to the version it was at, undoing an update.

    Safe to do because nothing was changed here in the first place: the check
    before the update refuses outright if anything had been. Deliberately does
    not also run ``git clean``, which would delete files git was never told
    about — on this computer those are somebody's own work.

    Only ever reached because somebody asked for it. An update that stops
    partway leaves the files alone and offers this as one of two choices; see
    StoppedPartway.

    Raises:
        UpgradeError: If the files could not be put back, which is the one
                      thing worth saying loudly.
    """
    try:
        done = _run("reset", "--hard", sha, timeout=_MOVING_TIMEOUT_SECONDS)
    except (git_tool.GitUnusable, UpgradeError, OSError) as e:
        logger.error("Could not put the files back to %s: %s", sha, e)
        raise UpgradeError(
            f"The update did not work, and putting the files back did not "
            f"work either.\n\n{e}\n\nIn a terminal, `git reset --hard {sha}` "
            f"in the sandbox's folder will do it."
        ) from e
    if done.returncode != 0:
        logger.error("Could not put the files back to %s: %s", sha, _said(done))
        raise UpgradeError(
            f"The update did not work, and putting the files back did not "
            f"work either.\n\n{_said(done)}\n\nIn a terminal, "
            f"`git reset --hard {sha}` in the sandbox's folder will do it."
        )
    logger.info("Put the files back to %s after an update that did not work", sha)


def put_the_files_back(sha: str) -> str:
    """Undo an update by hand, at somebody's request.

    Used by the "Put it back" button after an update stopped partway.

    Args:
        sha: The version to go back to, as the failed update reported it.

    Returns:
        The version that is now in place.

    Raises:
        UpgradeError: If it could not be done.
    """
    if not _only_one_at_a_time.acquire(blocking=False):
        raise UpgradeError("An update is running. Wait for it to finish.")
    try:
        _put_the_files_back(sha)
        now = package_version()
        return now.sha if now else sha
    finally:
        _only_one_at_a_time.release()


# ── The software an update may need ─────────────────────────────────────────

def _launcher():
    """Return ``start.py``, loaded so that its answers can be asked for.

    ``start.py`` is where three facts live: how to recognise that
    ``requirements.txt`` has changed, where the note recording that is kept,
    and which Python is the sandbox's own. This module needs all three, and
    copying them would mean two answers that have to agree for ever.

    The sharing only goes this way. ``start.py`` cannot ask anything of the
    code in ``src/``: it has to run on whatever Python the computer happens to
    have, before the sandbox's own environment exists, and everything in
    ``src/`` needs a newer one than that. So ``start.py`` keeps the facts and
    this asks it.

    Loaded from its path rather than by name, because a plain import would
    depend on what happened to be findable and would collide with anything else
    called ``start``. Reading the file is safe: everything in it is a constant
    or a definition, and it only does anything when it is run directly.
    """
    global _launcher_module
    if _launcher_module is None:
        path = Path(PACKAGE_ROOT) / "start.py"
        spec = importlib.util.spec_from_file_location("_pu_sandbox_launcher", path)
        if spec is None or spec.loader is None:
            raise UpgradeError(f"Could not read {path}, which is what knows "
                               "how the sandbox's software is installed.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _launcher_module = module
    return _launcher_module


def _the_sandboxes_own_python() -> str:
    """Return the Python to install the new software into.

    Asks ``start.py`` rather than using whichever Python happens to be running
    this, so that there is one answer to "which Python is the sandbox's". They
    are normally the same — main.py hands every command over to that Python
    before anything else happens — but a copy being worked on by somebody with
    their own environment set up is the case where they are not, and the
    software still belongs in the sandbox's own.

    Falls back to the running one when there is no such environment, which is
    what a developer running from a checkout looks like.
    """
    own = _launcher().venv_python()
    return own if os.path.exists(own) else sys.executable


def dependencies_are_current() -> bool:
    """Return whether the sandbox's environment already holds what is asked for.

    "The environment" is the private copy of Python the sandbox keeps for
    itself, and ``requirements.txt`` is the list of extra software it needs.
    ``start.py`` keeps a note of which list it last installed; this compares
    that note against the list as it is now.
    """
    try:
        launcher = _launcher()
    except UpgradeError:
        return True
    try:
        with open(launcher.STAMP, "r", encoding="utf-8") as handle:
            return handle.read().strip() == launcher.requirements_fingerprint()
    except OSError:
        return False


def _install_dependencies(on_line: Callable[[str], None]) -> None:
    """Install the extra software the new version needs, reporting as it goes.

    Deliberately not ``pip install --upgrade``: pip's ordinary behaviour is to
    leave alone anything that already satisfies the list, so an update touches
    as little as it can. And deliberately no upgrade of pip itself, which
    ``start.py`` does — doing that inside the very Python currently answering
    the request buys nothing and is one more thing that can fail.

    Args:
        on_line: Called with each line worth showing somebody, so the page can
                 say what is being installed rather than sitting still for a
                 minute.

    Raises:
        UpgradeError: If installing did not work, carrying pip's own words.
    """
    launcher = _launcher()
    command = [_the_sandboxes_own_python(), "-m", "pip", "install",
               "-r", launcher.REQUIREMENTS]

    try:
        # Read line by line rather than waited for, because waiting means a
        # minute of a page showing nothing at all.
        running = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=os.environ.copy(),
        )
    except OSError as e:
        raise UpgradeError(f"Could not start installing the new software: {e}") from e

    # A separate watch on the clock, rather than a timeout on the wait below.
    # Reading the output is what takes the time, and an installer that has
    # stopped saying anything at all would otherwise never be given up on.
    gave_up: list[bool] = []

    def give_up() -> None:
        gave_up.append(True)
        running.kill()

    watchdog = threading.Timer(_INSTALLING_TIMEOUT_SECONDS, give_up)
    watchdog.start()

    said: list[str] = []
    try:
        if running.stdout is not None:
            for line in running.stdout:
                line = line.rstrip()
                if not line or _nothing_happened(line):
                    continue
                said.append(line)
                if _worth_saying_aloud(line):
                    on_line(line)
        running.wait()
    finally:
        watchdog.cancel()

    if gave_up:
        raise UpgradeError(
            f"Installing the new software was still going after "
            f"{_INSTALLING_TIMEOUT_SECONDS // 60} minutes and was stopped. "
            "This usually means the network is not answering."
        )
    if running.returncode != 0:
        raise UpgradeError(
            "The new files are in place, but the extra software the new "
            "version needs could not be installed.\n\n"
            + "\n".join(said[-_HOW_MANY_LINES_OF_PIP:])
        )


def _nothing_happened(line: str) -> bool:
    """Return whether a line of pip's output describes something it did not do.

    Pip names every package that was already there, which on an ordinary update
    is nearly every line it prints and none of the ones that matter. Kept out
    of the record as well as off the screen: when installing fails, the last
    thing wanted is the reason buried under twenty lines saying that nothing
    happened.
    """
    return line.startswith("Requirement already satisfied")


def _worth_saying_aloud(line: str) -> bool:
    """Return whether a line belongs in the caption under the progress bar.

    Only the narration — "Collecting", "Downloading", "Installing". What went
    wrong is deliberately left out: it is about to be shown properly and in
    full, and flashing it up as a caption first only means seeing it twice,
    once in a space too small to read it in.
    """
    if line.startswith(("ERROR", "WARNING")):
        return False
    return line[:1].isupper()


def _record_what_is_installed() -> None:
    """Write down which list of software is now installed.

    ``start.py`` reads this note at every start to decide whether it needs to
    install anything, and goes straight to the web interface when it matches.
    Writing it is the last thing an update does, and it is deliberately not
    written when anything went wrong: a note that does not match is exactly
    what makes the next ``python3 start.py`` notice and put things right.
    """
    try:
        launcher = _launcher()
        with open(launcher.STAMP, "w", encoding="utf-8") as handle:
            handle.write(launcher.requirements_fingerprint())
    except (OSError, UpgradeError) as e:
        # Not worth failing an update that otherwise worked. The only cost is
        # that the next start installs the software again unnecessarily.
        logger.warning("Could not write down what is installed: %s", e)
