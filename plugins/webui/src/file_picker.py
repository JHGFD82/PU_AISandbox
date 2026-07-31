"""The "Browse…" button: opening this computer's own file chooser.

A web page can't be told where a folder is. A browser's file box hands back
a name and the file's contents, never a location on disk, and that is a
deliberate protection — a page from the internet has no business knowing
how your computer is laid out. It leaves anyone configuring the sandbox
typing a path by hand, which is the one thing this sandbox's users should
never have to do.

The way round it is that this particular web page isn't from the internet.
The server is running on the same computer as the browser showing it, so
the chooser can be opened *there* — the real Finder window on macOS, the
real Explorer window on Windows — and the answer is a genuine path, because
the program that asked was standing on the same machine all along.

Everything here is about that one trick, done through whichever chooser the
computer already has:

* **macOS** — ``osascript``, which is always present.
* **Windows** — PowerShell's folder and file dialogs, always present.
* **Linux** — ``zenity`` or ``kdialog``, if either is installed.
* **Anywhere else** — Python's own ``tkinter`` chooser, if that was built in.

If none of them can be used, ``available()`` says so and the button is
never shown; the box stays typeable, which is what it was before. Nothing
here is ever the only way to answer a question.

Registered into ``sys.modules`` as ``_pu_webui_file_picker`` — see
``app.py``'s module docstring for why the name is flat.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

# One chooser at a time. Two windows asking the same question, with only one
# of the answers going anywhere, is a confusing thing to do to somebody —
# and it is easy to cause by double-clicking the button.
_lock = threading.Lock()

# How long a chooser may stay open before it is abandoned. Long, because the
# person on the other side of it is looking through their own files and may
# well go and do something else first. It exists only so a window closed in
# some way the sandbox can't see — a logout, a crash — doesn't leave a
# thread waiting for an answer that is never coming.
_TIMEOUT_SECONDS = 900


class PickerUnavailable(RuntimeError):
    """Raised when this computer has no file chooser the sandbox can open."""


def _quote_applescript(text: str) -> str:
    """Wrap *text* as an AppleScript string, with its quotes and backslashes escaped."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _quote_powershell(text: str) -> str:
    """Wrap *text* as a PowerShell single-quoted string, with its quotes escaped."""
    return "'" + text.replace("'", "''") + "'"


def _existing_ancestor(start: str | os.PathLike[str] | None) -> str | None:
    """Return the nearest folder that actually exists at or above *start*.

    Choosers open where they are told to and complain if told somewhere that
    isn't there. The sandbox's suggested folder usually *isn't* there yet —
    it's about to be created — so this walks up until it finds somewhere
    real to open in. The person then lands beside where the sandbox was
    going to put things, rather than wherever the chooser last happened to be.

    Args:
        start: The path the box currently holds, which may not exist, may be
               a file, or may be nothing at all.

    Returns:
        A folder that exists, or ``None`` if there is nothing to go on and
        the chooser should pick its own starting point.
    """
    if not start:
        return None
    candidate = Path(start).expanduser()
    if candidate.is_file():
        candidate = candidate.parent
    for folder in [candidate, *candidate.parents]:
        if folder.is_dir():
            return str(folder)
    return None


def _has_tkinter() -> bool:
    """Return whether Python's own chooser can be used on this computer.

    ``tkinter`` is part of Python but not always built into it, and even
    when it is, it needs a desktop to draw on — which a computer being used
    over a plain remote connection hasn't got.
    """
    if importlib.util.find_spec("tkinter") is None:
        return False
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return False
    return True


def _linux_helper() -> str | None:
    """Return the desktop chooser command installed here, if there is one."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return None
    return shutil.which("zenity") or shutil.which("kdialog")


def available() -> bool:
    """Return whether this computer has a file chooser the sandbox can open.

    Asked before the "Browse…" button is drawn, so a computer without one
    simply doesn't get a button that wouldn't work.
    """
    if sys.platform == "darwin":
        return bool(shutil.which("osascript"))
    if os.name == "nt":
        return bool(shutil.which("powershell") or shutil.which("powershell.exe"))
    return bool(_linux_helper()) or _has_tkinter()


def _run(command: list[str]) -> str | None:
    """Run one chooser and return what it printed, or ``None`` if nothing was chosen.

    Every chooser here agrees on the same two signals: a path on its output
    means that path was chosen, and anything else — a non-zero exit, no
    output at all — means the window was closed without choosing. Cancelling
    is a perfectly ordinary thing to do, so it is a result, not an error.
    """
    try:
        finished = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if finished.returncode != 0:
        return None
    chosen = finished.stdout.strip()
    return chosen or None


def _macos_command(kind: str, prompt: str, start: str | None) -> list[str]:
    """Build the ``osascript`` call that opens the macOS chooser."""
    verb = "choose folder" if kind == "folder" else "choose file"
    parts = [verb, "with prompt", _quote_applescript(prompt)]
    if start:
        parts += ["default location POSIX file", _quote_applescript(start)]
    # "tell me to activate" brings the chooser to the front, so it lands on
    # top of the browser rather than behind it — a window nobody can see
    # reads as the button having done nothing.
    #
    # "me" is this script itself, and that word is load-bearing. Routing the
    # same request through System Events (the other common way to write
    # this) means one program controlling another, which macOS holds behind
    # a permission prompt the first time. A professor who doesn't recognise
    # "Python wants to control System Events" and clicks Don't Allow has
    # turned the Browse button off permanently, with no visible way back.
    # Asking for nothing means nothing can be refused.
    script = (
        "tell me to activate\n"
        f"set chosenItem to {' '.join(parts)}\n"
        "POSIX path of chosenItem"
    )
    return ["osascript", "-e", script]


def _windows_command(kind: str, prompt: str, start: str | None) -> list[str]:
    """Build the PowerShell call that opens the Windows chooser."""
    if kind == "folder":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
            f"$dialog.Description = {_quote_powershell(prompt)};"
            "$dialog.ShowNewFolderButton = $true;"
        )
        if start:
            script += f"$dialog.SelectedPath = {_quote_powershell(start)};"
        script += (
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
            " { [Console]::Out.Write($dialog.SelectedPath) }"
        )
    else:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$dialog = New-Object System.Windows.Forms.OpenFileDialog;"
            f"$dialog.Title = {_quote_powershell(prompt)};"
        )
        if start:
            script += f"$dialog.InitialDirectory = {_quote_powershell(start)};"
        script += (
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
            " { [Console]::Out.Write($dialog.FileName) }"
        )
    powershell = shutil.which("powershell") or shutil.which("powershell.exe") or "powershell"
    # -STA because the Windows dialogs refuse to open on any other kind of
    # thread; -NoProfile so somebody's own PowerShell customisations can't
    # print anything that would be mistaken for the chosen path.
    return [powershell, "-NoProfile", "-STA", "-Command", script]


def _linux_command(helper: str, kind: str, prompt: str, start: str | None) -> list[str]:
    """Build the ``zenity`` or ``kdialog`` call that opens the Linux chooser."""
    if helper.endswith("kdialog"):
        verb = "--getexistingdirectory" if kind == "folder" else "--getopenfilename"
        return [helper, "--title", prompt, verb, start or os.path.expanduser("~")]
    command = [helper, "--file-selection", "--title", prompt]
    if kind == "folder":
        command.append("--directory")
    if start:
        # The trailing separator is what tells zenity this is the folder to
        # open in, rather than a file to preselect.
        command.append(f"--filename={start}{os.sep}")
    return command


def _tkinter_command(kind: str, prompt: str, start: str | None) -> list[str]:
    """Build the call that opens Python's own chooser, for computers with no other."""
    chooser = "askdirectory" if kind == "folder" else "askopenfilename"
    script = (
        "import tkinter, tkinter.filedialog as chooser;"
        "root = tkinter.Tk();"
        "root.withdraw();"
        'root.attributes("-topmost", True);'
        f"answer = chooser.{chooser}(title={prompt!r}, initialdir={start!r});"
        "print(answer or '', end='')"
    )
    return [sys.executable, "-c", script]


def choose(kind: str = "folder", start: str | None = None,
           prompt: str = "Choose a folder") -> Path | None:
    """Open this computer's file chooser and return what was picked.

    Blocks until the window is answered or closed, so it must not be called
    on anything that other requests are waiting on.

    Args:
        kind: ``'folder'`` to choose a folder, ``'file'`` to choose a file.
        start: Where the chooser should open. Somewhere that doesn't exist
               yet is fine — the nearest folder above it that does is used
               instead.
        prompt: The line shown at the top of the window, saying what is
                being asked for.

    Returns:
        The chosen path, or ``None`` if the window was closed without
        choosing anything. Cancelling is an ordinary answer, not a failure.

    Raises:
        PickerUnavailable: If this computer has no chooser to open. Callers
                           should have asked ``available()`` first.
    """
    if kind not in ("folder", "file"):
        raise ValueError(f"kind must be 'folder' or 'file', not {kind!r}")
    if not available():
        raise PickerUnavailable(
            "This computer has no file chooser the sandbox can open. "
            "Type the path instead."
        )

    where = _existing_ancestor(start)
    if sys.platform == "darwin":
        command = _macos_command(kind, prompt, where)
    elif os.name == "nt":
        command = _windows_command(kind, prompt, where)
    else:
        helper = _linux_helper()
        command = (_linux_command(helper, kind, prompt, where) if helper
                   else _tkinter_command(kind, prompt, where))

    with _lock:
        chosen = _run(command)
    return Path(chosen) if chosen else None


def reveal(path: str | os.PathLike[str]) -> bool:
    """Open a folder in this computer's own file browser — Finder, Explorer, Files.

    The counterpart to ``choose()``: that asks the person to point at a
    folder, this shows them one they already have. Used to open a
    conversation's folder, so that the documents supplied to it, the files a
    job produced from it and the settings that produced them can be looked at,
    copied or cited with the tools someone already knows, rather than through a
    web page.

    Runs on the computer the sandbox is running on, which is the same one the
    browser is on — every caller checks that first, for the reason given in
    ``_require_same_computer()``.

    Args:
        path: The folder to show. Nothing is opened if it doesn't exist.

    Returns:
        ``True`` if a file browser was asked to open it. ``False`` if the
        folder is missing, or if this computer has no way to open one — a
        server with no desktop, for instance. Either way nothing is raised:
        not being able to open a window is a disappointment, not a failure of
        the thing the person was doing.
    """
    folder = Path(path)
    if not folder.is_dir():
        return False
    if sys.platform == "darwin":
        command = ["open", str(folder)]
    elif sys.platform.startswith("win"):
        command = ["explorer", str(folder)]
    else:
        command = ["xdg-open", str(folder)]
    try:
        # Not waited on: a file browser stays open as long as the person wants
        # it, and waiting would hold the request until they closed the window.
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError):
        return False


def can_reveal() -> bool:
    """Whether this computer has a file browser that ``reveal()`` could open.

    A separate question from ``available()``, which asks whether a *chooser*
    can be opened. The two use different tools and a computer can have one
    without the other — a Linux machine may have ``xdg-open`` and no tkinter,
    or the reverse — so asking one and acting on the other would draw a button
    that does nothing.
    """
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return True
    return shutil.which("xdg-open") is not None
