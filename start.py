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


def read_one_key():
    """Return a single keypress, without waiting for return and without echoing it.

    A choice between two things is one keypress. Typing a letter, watching it
    appear at the end of the prompt and then pressing return is the gesture for
    entering text, and this is not text.

    The terminal is put into cbreak mode rather than raw: cbreak stops it
    waiting for a whole line, while leaving Ctrl-C to interrupt as it always
    does. Whatever happens, the old settings go back — a terminal left in
    cbreak outlives this program and breaks the shell it was run from.

    Returns:
        The character pressed, or ``None`` if this terminal cannot be read a
        key at a time — in which case the caller should ask for a whole line
        instead.
    """
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError:
            return None
        return msvcrt.getwch()

    try:
        import termios
        import tty
    except ImportError:
        # Not a terminal this can be done on. Say so rather than guessing.
        return None

    fd = sys.stdin.fileno()
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        return None
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def wait_for_go_ahead():
    """Stop and let the person read before several minutes of installing begins.

    The lines above this explain what is about to happen and roughly how long
    it takes. Without a pause they are on screen for about a second: pip prints
    around 180 lines for a first install, so the explanation scrolls away before
    anyone has read it.

    Return starts, Q stops, and every other key is ignored rather than being
    answered with a complaint — there are two things to do here and no way to
    get them wrong.

    Deciding not to install is not a mistake, so it is not treated as one:
    nothing has happened yet, and running this again picks up exactly here.

    Returns:
        True to go ahead, False if the person would rather not.
    """
    # Nothing to ask when there is nobody to answer. Run from a script, a
    # scheduled job or a continuous-integration runner, stdin is not a
    # terminal, and waiting for a keypress there is a hang with no explanation.
    if not sys.stdin.isatty():
        return True

    say("[Press return to install, or Q to quit.] ")
    try:
        while True:
            key = read_one_key()
            if key is None:
                # This terminal will not give up one key at a time. Fall back
                # to a typed line, which works anywhere.
                say("")
                return input("[Press return to install, or Q to quit.] "
                             ).strip().lower() != "q"
            if key in ("\r", "\n"):
                say("")
                return True
            if key in ("q", "Q"):
                say("")
                return False
            # Ctrl-C and Ctrl-D reach here as characters when the terminal is
            # not generating signals, and mean what Q means. An empty string is
            # the end of the input: reading again would only return it again,
            # so treating it as "anything else" would spin here forever.
            if key in ("", "\x03", "\x04"):
                say("")
                return False
            # Anything else: not an answer to this question, so wait for one.
    except (EOFError, KeyboardInterrupt):
        say("")
        return False


def active_environment():
    """Return a plain description of the environment this is running in.

    Somebody who has made an environment of their own and activated it is
    entitled to expect this script to say what it intends to do with it. It
    does not use it — it makes one of its own — and saying so is the whole
    point of knowing this.

    Returns:
        A phrase that finishes "You are currently in ...", or None when this
        is running against a plain system Python with nothing activated.
    """
    conda = os.environ.get("CONDA_DEFAULT_ENV")
    if conda:
        if conda == "base":
            return "conda's base environment"
        return "the conda environment '%s'" % conda

    activated = os.environ.get("VIRTUAL_ENV")
    if activated and not is_the_sandboxes_own(activated):
        return "the environment in %s" % activated

    # Nothing activated in the shell, but running from inside one anyway —
    # somebody who called an environment's python by its full path.
    base = getattr(sys, "base_prefix", sys.prefix)
    if sys.prefix != base and not is_the_sandboxes_own(sys.prefix):
        return "the environment in %s" % sys.prefix
    return None


def is_the_sandboxes_own(folder):
    """Whether *folder* is the environment this script makes and manages.

    Running this from inside .venv is an ordinary thing to do on a second run,
    and without this check the script would name that folder as the person's
    own and promise not to install anything into it — while installing into
    exactly it.
    """
    try:
        return os.path.realpath(folder) == os.path.realpath(VENV_DIR)
    except OSError:
        return False


def explain_where_the_software_goes():
    """Say where the sandbox's software is about to be put, and where it isn't.

    Without this the script said only that it was installing something, and a
    person who had just made an environment of their own had no way to tell
    whether it was about to fill that or make another. Both answers are
    reasonable to expect; the script owes them the one that is true.
    """
    say("The sandbox keeps its software in an environment of its own, in a")
    say("folder named .venv inside this one. That is what is about to be made.")

    active = active_environment()
    if active is None:
        return
    say("")
    say("You are currently in %s." % active)
    say("Nothing will be installed into it and it will not be changed.")
    say("")
    say("If you would rather the sandbox used it instead, press Q, then run:")
    say("    pip install -r requirements.txt")
    say("    python main.py webui serve")
    say("")
    say("Either way works. The sandbox uses .venv whenever there is one, and")
    say("whatever is active when there is not.")


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
    say("The Princeton University AI Sandbox needs Python %s or newer, "
        % MINIMUM_TEXT)
    say("and this computer only has %s."
        % running)
    say("")
    say("Macs come with an older Python that can't run it. Installing a newer")
    say("one alongside is safe — it won't disturb anything already there.")
    say("")
    say("  The simplest way: download the latest installer from")
    say("      https://www.python.org/downloads/")
    say("  Run the installer, and then run this again:")
    say("      python3 start.py")
    say("")
    say("  If you use Homebrew, you can install it with:")
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
        say("Could not download the necessary files for the sandbox.")
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


def has_the_web_interface(sandbox):
    """Whether this copy has the web interface at all.

    It is a plugin, and removing it is a supported thing to do: the sandbox
    keeps every one of its commands except that one. This script is the part
    that does not — it opens a browser and asks for two commands that plugin
    provides — so it has to know.

    Asked of the sandbox rather than worked out from whether a folder is
    there. A plugin that is present but cannot load is the same problem as one
    that is absent, and this question is exactly the one that matters: does
    the command exist.

    Args:
        sandbox: The path to main.py.

    Returns:
        True if "webui" is a command this copy has.
    """
    try:
        quiet = open(os.devnull, "w")
    except IOError:
        return True
    try:
        return subprocess.call([venv_python(), sandbox, "webui", "--help"],
                               cwd=HERE, stdout=quiet, stderr=quiet) == 0
    except (OSError, subprocess.CalledProcessError):
        return False
    finally:
        quiet.close()


def finish_without_the_web_interface(sandbox):
    """Set the sandbox up in this window, for a copy that has no web interface.

    Everything except the browser still works, so this does the part that is
    still possible rather than failing. Without it the script announced a
    browser window, opened one at an address nothing was listening on, and
    then printed the argument parser's complaint about a command that no
    longer exists — with the wrong word quoted, because "webui" had been read
    as somebody's name.

    Args:
        sandbox: The path to main.py.

    Returns:
        What to exit with.
    """
    say("")
    say("The web interface is not installed in this copy, so there is no")
    say("browser window to open. Everything else works from this window.")

    if not is_set_up(sandbox):
        say("")
        say("Setting up here instead. It asks the same questions.")
        say("")
        result = subprocess.call([venv_python(), sandbox, "settings", "setup"],
                                 cwd=HERE)
        if result != 0:
            return result

    say("")
    say("To see what the sandbox can do:")
    say("    python main.py --help")
    say("")
    say("To put the web interface back, restore the plugins/webui folder and")
    say("run this again.")
    return 0


def is_set_up(sandbox):
    """Whether this copy has been told where the person's settings are kept.

    Asks the sandbox rather than looking for the marker file here, so there is
    one answer to "is this set up?" rather than two that can disagree.
    """
    return subprocess.call(
        [venv_python(), "-c",
         "import sys; from src import paths; "
         "sys.exit(0 if paths.is_installed() else 1)"],
        cwd=HERE,
    ) == 0


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
    say("Princeton University AI Sandbox")
    say("=" * 60)

    python = find_python()
    if python is None:
        explain_missing_python()
        return 1

    if not environment_is_ready():
        explain_where_the_software_goes()
        say("")
        say("About 200 MB will be automatically downloaded and installed. This can")
        say("take several minutes, depending on your internet connection.")
        say("")
        if not wait_for_go_ahead():
            say("Nothing was installed. Run this again when you are ready.")
            return 0
        say("")
        if not build_environment(python):
            return 1
        say("")
        say("Software installed into %s." % VENV_DIR)
    say("")

    sandbox = os.path.join(HERE, "main.py")

    # Before anything is promised. Everything below this line is about a
    # browser, and a copy with the web interface removed has none.
    if not has_the_web_interface(sandbox):
        return finish_without_the_web_interface(sandbox)

    url = "http://127.0.0.1:8000"
    # First-time setup, but only if this copy has never been used.
    needs_setup = not is_set_up(sandbox)
    if needs_setup:
        # Setup runs as its own step, on the same address the web interface
        # will use afterwards, and it stops as soon as it has an answer. The
        # browser opened here lands on the setup page and follows itself to
        # the sandbox once the answer is in, so nobody has to find a second
        # window. Answering the same questions at the command line instead
        # is still there — `python main.py settings setup` — but this file
        # is the route for someone who just wants to open the sandbox.
        say("")
        say("Setup will continue in your browser. Please have your API key ready.")
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
