"""Running git, for the two things in the web interface that need it.

git is the program that fetches code — a plugin from someone else's repository,
or a newer version of the sandbox itself. Both of those happen while a person is
sitting in front of a browser with no terminal open, which is what this module
is written around: git must never stop and wait for something nobody can see,
and when it fails it must fail with words rather than a number.

``plugin_install.py`` and ``upgrade.py`` both go through here, so the awkward
facts about git on a Mac are written down once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class GitUnusable(Exception):
    """git is not on this computer, or is there and cannot be run.

    Kept separate from the errors each caller raises, because the two callers
    describe the consequence differently — one could not fetch a plugin, the
    other could not update the sandbox — while the cause is the same.
    """


def usable_git(purpose: str) -> str:
    """Return where git is on this computer, or say why it cannot be used.

    Being installed is not the same as working. A Mac without the Xcode command
    line tools still has a ``/usr/bin/git`` — a stand-in that exists, is found,
    and then fails with an xcrun error the moment it is asked to do anything.
    Asking it its version is a cheap way to tell a real git from that
    stand-in, and turns "could not fetch, here is a paragraph about xcrun" into
    something somebody can act on.

    Args:
        purpose: What cannot happen without git, as the end of a sentence —
                 for example "a plugin cannot be fetched". It is written into
                 the message so each caller explains its own consequence.

    Returns:
        The path to a git that answered when it was asked its version.

    Raises:
        GitUnusable: If git is missing, or is there and cannot run.
    """
    found = shutil.which("git")
    advice = ("Install it and try again — on a Mac, `xcode-select --install` "
              "is enough.")
    if found is None:
        raise GitUnusable(
            f"git is not installed on this computer, and it is what fetches "
            f"code, so {purpose}. {advice}"
        )
    try:
        done = subprocess.run([found, "--version"], capture_output=True,
                              text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise GitUnusable(f"git is installed at {found} but cannot be run: {e}") from e
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip()
        raise GitUnusable(
            f"There is a git at {found}, but running it does not work, so "
            f"{purpose}.\n\n{said}\n\n{advice}" if said else
            f"There is a git at {found}, but running it does not work, so "
            f"{purpose}. {advice}"
        )
    return found


def quiet_environment() -> dict[str, str]:
    """Return the surroundings to run git in so it can never stop and ask.

    git is built for somebody at a keyboard. Asked for an address it needs a
    password for, it stops and asks — and on a server with nobody watching,
    "stops and asks" means a request that hangs until it is timed out, or on
    one route does not time out at all. Every setting here turns one of those
    waits into an immediate, explainable failure.

    Returns:
        A copy of the current surroundings with those settings added, ready to
        hand to ``subprocess``.
    """
    environment = dict(os.environ)
    environment.update({
        # Fail rather than stopping to ask for a username at the terminal.
        "GIT_TERMINAL_PROMPT": "0",
        # Fail rather than opening a password box on somebody's screen.
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        # An ssh address fails rather than waiting for a passphrase. Its own
        # setting, because ssh is a separate program and does not read the two
        # above.
        "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
        # Do not wait for, or take, the lock an editor's background git may be
        # holding on the same folder. Nothing here writes anything that needs
        # it.
        "GIT_OPTIONAL_LOCKS": "0",
        # git's own words in one language, whatever the computer is set to, so
        # that anything read back is read the same way everywhere.
        "LC_ALL": "C",
    })
    return environment


def run_git(git: str, folder: Path, *arguments: str,
            timeout: int) -> subprocess.CompletedProcess[str]:
    """Run one git command in *folder* and hand back what it said.

    Nothing here is given to a shell to take apart: the command is a list, so a
    folder name with a space or a quote in it is one argument and not several.

    Args:
        git: Where git is, as ``usable_git()`` found it.
        folder: The folder to run in. Passed with git's own ``-C`` rather than
                by changing this program's own folder, which would affect
                everything else happening at the same time.
        *arguments: The rest of the command, one argument per item.
        timeout: How many seconds to wait before giving up on it.

    Returns:
        What git said, whether or not it worked. The caller decides what a
        non-zero result means — the two callers word it differently.

    Raises:
        GitUnusable: If git could not be started at all.
        subprocess.TimeoutExpired: If it ran longer than *timeout*.
    """
    command = [git, "-C", str(folder), *arguments]
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            check=False, env=quiet_environment(),
        )
    except OSError as e:
        raise GitUnusable(f"Could not run git: {e}") from e


def what_it_said(done: subprocess.CompletedProcess[str]) -> str:
    """Return git's own message from a finished command, tidied of blank ends.

    git says useful things — "repository not found", "could not resolve host" —
    and says them better than anything guessed from an exit code. Sometimes it
    says them on the error channel and sometimes on the ordinary one, so both
    are looked at.

    Returns:
        What it said, or an empty string if it said nothing at all.
    """
    return (done.stderr or done.stdout or "").strip()
