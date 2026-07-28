#!/usr/bin/env python3
"""Start the PU AI Sandbox: sets everything up the first time, then opens it.

Run this once after downloading the sandbox, and any time you want the web
interface afterwards:

    python3 start.py

It works out what needs doing and does it — finds a suitable Python,
installs what the sandbox needs, walks you through first-time setup, then
starts the web interface and opens it in your browser. Nothing to configure
beforehand.

This file is deliberately written in old-fashioned Python, using nothing
newer than version 3.6 understands. It has to be: the whole reason it
exists is to run on whatever Python a computer happens to have and sort out
the rest, and a file that won't even parse can't tell anyone what's wrong.
The sandbox itself needs a newer Python, which this goes and finds.
"""

import hashlib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv")
REQUIREMENTS = os.path.join(HERE, "requirements.txt")
# Records which requirements.txt the packages in .venv were installed from,
# so a second run doesn't reinstall anything that's already there.
STAMP = os.path.join(VENV_DIR, ".requirements-stamp")

MINIMUM = (3, 11)
MINIMUM_TEXT = "3.11"


def say(message):
    """Print a line and flush it, so progress appears as it happens."""
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def venv_python():
    """Return the path to the Python inside the sandbox's own environment."""
    if os.name == "nt":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def version_of(python):
    """Return a (major, minor) tuple for *python*, or None if it can't be run."""
    try:
        out = subprocess.check_output(
            [python, "-c", "import sys; print('%d %d' % sys.version_info[:2])"],
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        parts = out.decode("utf-8", "replace").split()
        return (int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def find_python():
    """Find a Python new enough to run the sandbox.

    Looks at the one running this file first, then at the usual names and
    places. A computer very often already has a suitable Python installed
    that simply isn't the first one found — searching for it turns "install
    Python before you can start" into nothing the person has to do at all.

    Returns:
        The path to a usable Python, or None if there isn't one.
    """
    if sys.version_info[:2] >= MINIMUM:
        return sys.executable

    candidates = []
    for minor in (13, 12, 11):
        name = "python3.%d" % minor
        candidates.append(name)
        candidates.append(os.path.join("/opt/homebrew/bin", name))
        candidates.append(os.path.join("/usr/local/bin", name))
        candidates.append(
            "/Library/Frameworks/Python.framework/Versions/3.%d/bin/python3" % minor
        )
    candidates.append("python3")

    for candidate in candidates:
        found = version_of(candidate)
        if found is not None and found >= MINIMUM:
            return candidate
    return None


def explain_missing_python():
    """Say plainly that a newer Python is needed, and how to get one."""
    running = "%d.%d.%d" % sys.version_info[:3]
    say("")
    say("The sandbox needs Python %s or newer, and this computer only has %s."
        % (MINIMUM_TEXT, running))
    say("")
    say("Macs come with an older Python that can't run it. Installing a newer")
    say("one alongside is safe — it won't disturb anything already there.")
    say("")
    say("  The simplest way: download the latest installer from")
    say("      https://www.python.org/downloads/")
    say("  and run it. Then run this again:")
    say("      python3 start.py")
    say("")
    say("  If you use Homebrew, this does the same thing:")
    say("      brew install python@3.13")
    say("")


def requirements_fingerprint():
    """Return a short fingerprint of requirements.txt, for spotting changes."""
    try:
        with open(REQUIREMENTS, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except IOError:
        return ""


def environment_is_ready():
    """Return whether .venv already holds exactly what requirements.txt asks for."""
    if not os.path.exists(venv_python()):
        return False
    try:
        with open(STAMP, "r") as handle:
            return handle.read().strip() == requirements_fingerprint()
    except IOError:
        return False


def build_environment(python):
    """Create the sandbox's own environment and install what it needs.

    Args:
        python: A Python new enough to run the sandbox.

    Returns:
        True if the environment is ready, False if something went wrong
        (already explained to the person by the time this returns).
    """
    if not os.path.exists(venv_python()):
        say("Setting up a private space for the sandbox's software...")
        try:
            subprocess.check_call([python, "-m", "venv", VENV_DIR])
        except (OSError, subprocess.CalledProcessError):
            say("")
            say("Could not create that space in:")
            say("    %s" % VENV_DIR)
            say("Check you can write to that folder, then run this again.")
            return False

    say("Installing what the sandbox needs. This takes a few minutes the")
    say("first time — about 200 MB is downloaded — and is instant after that.")
    say("")
    try:
        subprocess.check_call(
            [venv_python(), "-m", "pip", "install", "--upgrade", "pip", "--quiet"]
        )
        # Not quiet: this is the long step, and silence for several minutes
        # reads as a hang. Watching package names go by is the difference
        # between "it's working" and "something's broken".
        subprocess.check_call(
            [venv_python(), "-m", "pip", "install", "-r", REQUIREMENTS]
        )
    except (OSError, subprocess.CalledProcessError):
        say("")
        say("Could not download what the sandbox needs.")
        say("This is almost always the network — a dropped connection, or a")
        say("university proxy. Check you can reach the internet and run this")
        say("again; it will pick up where it left off.")
        return False

    try:
        with open(STAMP, "w") as handle:
            handle.write(requirements_fingerprint())
    except IOError:
        # Only means the next run reinstalls unnecessarily. Not worth stopping.
        pass
    return True


def open_browser_shortly(url):
    """Open *url* in the browser a moment from now, in the background.

    The web interface has to be running before the browser asks for it, and
    starting it is the last thing this file does — so the wait happens in a
    separate thread while the server takes over this one.
    """
    import threading
    import webbrowser

    def wait_then_open():
        time.sleep(2.0)
        webbrowser.open(url)

    thread = threading.Thread(target=wait_then_open)
    thread.daemon = True
    thread.start()


def ask_where_to_set_up():
    """Ask whether to answer the setup questions here or in a browser.

    Both routes ask the same things and record the same answers; this is
    only about which is more comfortable. Offered because the people this
    sandbox is for did not choose to be at a command line, and a form is a
    kinder place to paste an API key than a terminal prompt.

    Returns:
        Either ``"browser"`` or ``"terminal"``. Anything unreadable — no
        terminal attached, an interrupted prompt — answers ``"terminal"``,
        because that route works without a browser and never leaves a server
        running that nobody is looking at.
    """
    say("")
    say("This sandbox hasn't been set up on this computer yet.")
    say("It's a couple of questions. Where would you rather answer them?")
    say("")
    say("  1. Here, in this window")
    say("  2. In your web browser")
    say("")
    try:
        answer = raw_input("Choose 1 or 2 [1]: ").strip()  # noqa: F821
    except NameError:
        try:
            answer = input("Choose 1 or 2 [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return "terminal"
    except (EOFError, KeyboardInterrupt):
        return "terminal"
    return "browser" if answer == "2" else "terminal"


def main():
    say("")
    say("Princeton AI Sandbox")
    say("=" * 60)

    python = find_python()
    if python is None:
        explain_missing_python()
        return 1

    if not environment_is_ready():
        if not build_environment(python):
            return 1
        say("")
        say("Software installed.")
    say("")

    sandbox = os.path.join(HERE, "main.py")

    # First-time setup, but only if this copy has never been used. Asking the
    # sandbox itself rather than checking for the marker file here, so there
    # is one answer to "is this set up?" rather than two that can disagree.
    already = subprocess.call(
        [venv_python(), "-c",
         "import sys; from src import paths; sys.exit(0 if paths.is_installed() else 1)"],
        cwd=HERE,
    )
    if already != 0:
        # Run as its own step so its questions are answered before the web
        # interface starts, rather than competing with a server for the same
        # terminal.
        if ask_where_to_set_up() == "browser":
            setup = subprocess.call([venv_python(), sandbox, "webui", "setup"])
        else:
            setup = subprocess.call([venv_python(), sandbox, "settings", "setup"])
        if setup != 0:
            return setup

    say("")
    say("Starting the web interface. It will open in your browser.")
    say("Leave this window open while you use it; close it or press Ctrl-C to stop.")
    say("")
    open_browser_shortly("http://127.0.0.1:8000")
    try:
        return subprocess.call([venv_python(), sandbox, "webui", "serve"])
    except KeyboardInterrupt:
        say("")
        say("Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
