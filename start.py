#!/usr/bin/env python3
"""Start the PU AI Sandbox: sets everything up the first time, then opens it.

Run this once after downloading the sandbox, and any time you want the web
interface afterwards:

    python3 start.py

It works out what needs doing and does it — finds a suitable Python,
installs what the sandbox needs, opens first-time setup in your browser,
then starts the web interface in that same window. Nothing to configure
beforehand, and no questions asked in this window: everything this file
starts is answered in the browser.

The command line can do all of it too (``python main.py settings setup``,
``settings add-professor``, and the rest), and someone who prefers that is
free to use it. This file is the other way in, and it doesn't ask which
one you'd rather have.

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


def wait_for_go_ahead():
    """Stop and let the person read before several minutes of installing begins.

    The lines above this explain what is about to happen and roughly how long
    it takes. Without a pause they are on screen for about a second: pip prints
    around 180 lines for a first install, so the explanation scrolls away before
    anyone has read it.

    Anything other than Q carries on, so the obvious thing — pressing return —
    is the one that works. Deciding not to install is not a mistake, so it is
    not treated as one: nothing has happened yet, and running this again picks
    up exactly here.

    Returns:
        True to go ahead, False if the person would rather not.
    """
    # Nothing to ask when there is nobody to answer. Run from a script, a
    # scheduled job or a continuous-integration runner, stdin is not a
    # terminal, and waiting for a keypress there is a hang with no explanation.
    if not sys.stdin.isatty():
        return True

    try:
        answer = input("[Press return to install, or Q to quit.] ")
    except (EOFError, KeyboardInterrupt):
        # Ctrl-D or Ctrl-C at the prompt means the same as Q, and the newline
        # keeps the next line from starting halfway across the screen.
        say("")
        return False
    return answer.strip().lower() != "q"


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


def main():
    say("")
    say("Princeton AI Sandbox")
    say("=" * 60)

    python = find_python()
    if python is None:
        explain_missing_python()
        return 1

    if not environment_is_ready():
        say("Installing what the sandbox needs. This takes a few minutes the")
        say("first time — about 200 MB is downloaded — and is instant after")
        say("that. You will see each piece arrive as it downloads.")
        say("")
        if not wait_for_go_ahead():
            say("Nothing was installed. Run this again when you are ready.")
            return 0
        say("")
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
    url = "http://127.0.0.1:8000"
    needs_setup = already != 0
    if needs_setup:
        # Setup runs as its own step, on the same address the web interface
        # will use afterwards, and it stops as soon as it has an answer. The
        # browser opened here lands on the setup page and follows itself to
        # the sandbox once the answer is in, so nobody has to find a second
        # window. Answering the same questions at the command line instead
        # is still there — `python main.py settings setup` — but this file
        # is the route for someone who just wants to open the sandbox.
        say("")
        say("This sandbox hasn't been set up on this computer yet.")
        say("Setup will open in your browser. It asks where your files should")
        say("go, who will be using this, and which models they may send to.")
        say("You will need an API key to hand — Princeton faculty get one")
        say("from OIT — and Princeton's own AI Sandbox documentation lists")
        say("the models it currently offers.")
        say("")
        open_browser_shortly(url)
        setup = subprocess.call([venv_python(), sandbox, "webui", "setup"])
        if setup != 0:
            return setup

    say("")
    if needs_setup:
        # Setup already opened a window, and its last page moves itself here.
        say("Starting the web interface. The setup page will move to it.")
    else:
        say("Starting the web interface. It will open in your browser.")
        open_browser_shortly(url)
    say("Leave this window open while you use it; close it or press Ctrl-C to stop.")
    say("")
    try:
        return subprocess.call([venv_python(), sandbox, "webui", "serve"])
    except KeyboardInterrupt:
        say("")
        say("Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
