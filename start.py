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

The first time it runs, it also puts an icon for the sandbox on the Desktop.
Double-clicking that opens the sandbox with no terminal window at all: a page
saying it is starting appears in the browser, and turns into the sandbox when
it is ready. Each double-click starts a fresh copy — stopping the one already
running, if there is one — because starting is when the sandbox looks for a
newer version. The Quit button in the sandbox stops it. (`start.py --launch`
is what the icon runs; `start.py --make-shortcut` makes the icon again.)

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
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any  # noqa: F401 — used in a type comment below

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv")
REQUIREMENTS = os.path.join(HERE, "requirements.txt")
# Records which requirements.txt the packages in .venv were installed from,
# so a second run doesn't reinstall anything that's already there.
STAMP = os.path.join(VENV_DIR, ".requirements-stamp")

MINIMUM = (3, 11)
MINIMUM_TEXT = "3.11"

# Where the sandbox listens. The web interface's own settings say the same
# (plugins/webui/settings.toml), and the two have to agree.
PORT = 8000
URL = "http://127.0.0.1:%d" % PORT

# Extra settings for every program this file starts. Empty in a terminal.
# Started from the icon on Windows, it holds the flag that stops each of them
# opening a black window of its own — see run_hidden().
WINDOWLESS = {}


def say(message):
    """Print a line and flush it, so progress appears as it happens.

    Started from its icon on Windows, there is nowhere to print to at all
    (``pythonw`` has no window), and saying nothing beats stopping over it.
    """
    if sys.stdout is None:
        return
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
            stderr=subprocess.STDOUT, **WINDOWLESS
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
            subprocess.check_call([python, "-m", "venv", VENV_DIR], **WINDOWLESS)
        except (OSError, subprocess.CalledProcessError):
            say("")
            say("Could not create that space in:")
            say("    %s" % VENV_DIR)
            say("Check you can write to that folder, then run this again.")
            return False

    try:
        subprocess.check_call(
            [venv_python(), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
            **WINDOWLESS
        )
        # Not quiet: this is the long step, and silence for several minutes
        # reads as a hang. Watching package names go by is the difference
        # between "it's working" and "something's broken".
        subprocess.check_call(
            [venv_python(), "-m", "pip", "install", "-r", REQUIREMENTS],
            **WINDOWLESS
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
                               cwd=HERE, stdout=quiet, stderr=quiet,
                               **WINDOWLESS) == 0
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
        cwd=HERE, **WINDOWLESS
    ) == 0


# ── Stopping a copy that is already running ────────────────────────────────
#
# Every start is a fresh start. The sandbox looks for a newer version when it
# starts and not again, so a copy left running for a week would never find
# one; opening the sandbox is how somebody finds out an update is waiting.
# So whatever copy of *this* sandbox is already running is asked to stop first.
#
# Only this copy, and only by asking. Each start makes up a random code — the
# stop token — keeps it in a file only this person can read, and hands it to
# the server it starts. Stopping means presenting the same code, which nothing
# but this file can do. Something else on the port can't answer it and is
# left alone; so is another copy of the sandbox somewhere else on this
# computer, which keeps a token of its own.

# Both names are shared with plugins/webui/src/stopping.py, which this file
# can't import (it has to run on Pythons too old for the sandbox).
STOP_TOKEN_VARIABLE = "PU_SANDBOX_STOP_TOKEN"
STOP_TOKEN_HEADER = "X-Sandbox-Stop-Token"
TOKEN_FILE = os.path.join(VENV_DIR, ".stop-token")

# What asking the running copy to stop can come to.
NOTHING_RUNNING = "nothing running"
STOPPED = "stopped"
BUSY = "busy"
SOMEONE_ELSE = "someone else"

# How long a copy that agreed to stop is given to let go of the port.
STOP_PATIENCE_SECONDS = 15


def read_stop_token():
    """Return the stop token of the copy started last, or "" if there isn't one."""
    try:
        with open(TOKEN_FILE, "r") as handle:
            return handle.read().strip()
    except IOError:
        return ""


def new_stop_token():
    """Make up a new stop token and keep it where the next start will look.

    The file is readable by this person only: the code is what proves a
    request to stop comes from here.

    Returns:
        The new token.
    """
    import binascii

    token = binascii.hexlify(os.urandom(24)).decode("ascii")
    try:
        if not os.path.isdir(VENV_DIR):
            os.makedirs(VENV_DIR)
        descriptor = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(token)
    except (IOError, OSError):
        # This start still works; only the next one can't stop it, and will
        # say the port is in use.
        pass
    return token


def environment_with(token):
    """Return this process's environment with *token* added, for the sandbox to be given."""
    env = dict(os.environ)
    env[STOP_TOKEN_VARIABLE] = token
    return env


def _local_opener():
    """A way of making web requests that goes straight to this computer.

    A university proxy set up for the whole system would otherwise be asked
    to fetch 127.0.0.1 — which, from the proxy, is the proxy itself.
    """
    import urllib.request

    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def port_is_taken():
    """Whether anything at all is listening on the sandbox's port."""
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        return probe.connect_ex(("127.0.0.1", PORT)) == 0
    except (OSError, socket.error):
        return False
    finally:
        probe.close()


def ask_the_running_copy_to_stop():
    """Stop this sandbox if it is already running, and say how that went.

    Returns:
        NOTHING_RUNNING if the port was free; STOPPED if this sandbox was
        running and has now stopped; BUSY if it is running and in the middle
        of something it would lose (it says so itself, and is left running);
        SOMEONE_ELSE if the port is held by something this can't stop.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        URL + "/__stop", data=b"",
        headers={STOP_TOKEN_HEADER: read_stop_token()},
    )
    try:
        _local_opener().open(request, timeout=5).close()
    except urllib.error.HTTPError as e:
        return BUSY if e.code == 409 else SOMEONE_ELSE
    except (urllib.error.URLError, OSError):
        # Nothing answered in the language of the web. Either nothing is
        # there, or something is that isn't a web server.
        return SOMEONE_ELSE if port_is_taken() else NOTHING_RUNNING

    deadline = time.time() + STOP_PATIENCE_SECONDS
    while time.time() < deadline:
        if not port_is_taken():
            return STOPPED
        time.sleep(0.25)
    return SOMEONE_ELSE


def explain_the_port_is_taken():
    """Return a plain explanation of why the port being in use stops the sandbox."""
    return ("Something else on this computer is using port %d, which the sandbox "
            "needs. It may be a copy of the sandbox started some other way — "
            "from a terminal window, say. Quit that (or close its window) and "
            "try again." % PORT)


# ── The page that says the sandbox is starting ─────────────────────────────

# The loading page and the icons belong to the web interface, and live in its
# plugin folder: a copy with the web interface removed has no browser to show
# a page in and no reason for an icon. Read from there as plain files, never
# imported — this runs before the software the plugin needs is installed.
WEBUI_LAUNCHER = os.path.join(HERE, "plugins", "webui", "launcher")
LOADING_TEMPLATE = os.path.join(WEBUI_LAUNCHER, "loading.html")


def render_loading_page(headline, detail, watch, log=""):
    """Fill in the loading page.

    Args:
        headline: The large line, e.g. "Starting the sandbox…".
        detail: The sentence under it. May be empty.
        watch: True for the page served while starting, which asks how things
               are going and moves itself to the sandbox. False for a page
               opened from disk to say why it could not start.
        log: Where what happened is written down, shown if something goes wrong.

    Returns:
        The page, as bytes ready to send or save.
    """
    import html
    import json

    try:
        with open(LOADING_TEMPLATE, "r") as handle:
            page = handle.read()
    except IOError:
        # The words matter more than the look.
        page = "<!DOCTYPE html><h1>{{HEADLINE}}</h1><p>{{DETAIL}}</p>" \
               "<script>const STATE = {{STATE}};</script>"
    state = json.dumps({"watch": bool(watch), "log": log}).replace("</", "<\\/")
    page = page.replace("{{HEADLINE}}", html.escape(headline))
    page = page.replace("{{DETAIL}}", html.escape(detail))
    page = page.replace("{{STATE}}", state)
    return page.encode("utf-8")


class LoadingPage(object):
    """A small web server that holds the sandbox's address while it gets going.

    It answers at the sandbox's own address, so the browser can be opened
    straight away rather than after a guess at how long starting takes, and
    so the page it shows only has to reload itself once the sandbox has taken
    the address over. Built from what comes with Python, because it runs
    before anything has been installed.
    """

    def __init__(self, token, log=""):
        """
        Args:
            token: The stop token of this start, so a double-click on the icon
                   while this is showing can stop it like any other copy.
            log: Where what happens is written down, to point at if it goes wrong.
        """
        import threading

        self.token = token
        self.log = log
        self.phase = "starting"
        self.headline = "Starting the sandbox…"
        self.detail = ""
        self.seen = threading.Event()
        self._server = None

    def start(self):
        """Start answering. Returns False if the address is already taken."""
        import threading

        try:
            server = _LoadingServer(("127.0.0.1", PORT), _LoadingHandler)
        except (OSError, IOError):
            return False
        server.page = self
        self._server = server
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        return True

    def show(self, phase, headline, detail=""):
        """Change what the page says. *phase* is "starting", "installing" or "problem"."""
        self.phase = phase
        self.headline = headline
        self.detail = detail

    def hand_over(self, patience=15):
        """Let go of the address, so the sandbox can take it.

        Waits (up to *patience* seconds) for the browser to have fetched the
        page at least once. Let go of sooner, a browser slow to open would
        find nothing at the address and show an error instead.
        """
        if self._server is None:
            return
        self.seen.wait(patience)
        self.stop()

    def stop(self):
        """Stop answering, straight away."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def end_this_process(self):
        """Stop everything this file is doing, because a new start asked it to.

        A moment from now rather than at once, so the answer saying yes has
        gone before the process that sent it does.
        """
        import threading

        timer = threading.Timer(0.3, os._exit, [0])
        timer.daemon = True
        timer.start()

    def linger(self, minutes=10):
        """Keep showing a problem for a while, then stop, so the page can say it."""
        time.sleep(minutes * 60)
        self.stop()


class _LoadingServer(ThreadingMixIn, HTTPServer):
    """The server behind LoadingPage, answering each request on its own thread."""

    daemon_threads = True
    # Not on Windows: there, this option lets a second program take a port
    # another is already listening on, rather than being refused.
    allow_reuse_address = os.name != "nt"
    # The LoadingPage this is serving, set once it has started.
    page = None  # type: Any


class _LoadingHandler(BaseHTTPRequestHandler):
    """Answers the loading page's three addresses: /, /__loading and /__stop."""

    def log_message(self, format, *args):
        # Every half-second question from the page would otherwise be printed.
        pass

    def _page(self):
        server = self.server
        assert isinstance(server, _LoadingServer)
        return server.page

    def _send(self, status, body, kind):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        import json

        page = self._page()
        path = self.path.split("?", 1)[0]
        if path == "/__loading":
            body = json.dumps({
                "loading": True, "phase": page.phase,
                "headline": page.headline, "detail": page.detail,
            }).encode("utf-8")
            self._send(200, body, "application/json")
        elif path == "/":
            self._send(200, render_loading_page(
                page.headline, page.detail, watch=True, log=page.log),
                "text/html; charset=utf-8")
            page.seen.set()
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        import hmac

        page = self._page()
        if self.path.split("?", 1)[0] != "/__stop":
            self._send(404, b"Not found", "text/plain")
            return
        given = self.headers.get(STOP_TOKEN_HEADER, "")
        if not page.token or not hmac.compare_digest(
                given.encode("utf-8"), page.token.encode("utf-8")):
            self._send(403, b"Not allowed", "text/plain")
            return
        if page.phase == "installing":
            # Stopping halfway through installing would leave the software
            # half-installed. The launcher opens this page instead, which
            # says what is happening.
            self._send(409, b"Installing", "text/plain")
            return
        self._send(200, b"Stopping", "text/plain")
        page.end_this_process()


def open_in_browser(url):
    """Open *url* in the person's usual browser."""
    import webbrowser

    webbrowser.open(url)


# ── Starting the web interface, from either way in ─────────────────────────

def run_the_web_interface(sandbox, page, env, needs_setup, in_a_terminal):
    """Run first-time setup if it is needed, then the sandbox, until it stops.

    Args:
        sandbox: The path to main.py.
        page: The loading page holding the address, let go of just before
              whatever needs the address next starts.
        env: The environment to run them in, carrying the stop token.
        needs_setup: Whether this copy has never been set up.
        in_a_terminal: Whether someone is watching a terminal window — and so
              whether there is anyone to tell about the icon this makes.

    Returns:
        What the sandbox exited with.
    """
    if needs_setup:
        # Setup runs as its own step, on the same address the web interface
        # will use afterwards, and it stops as soon as it has an answer. The
        # page already in the browser reloads onto it, and its last page
        # follows itself to the sandbox once the answer is in, so nobody has
        # to find a second window. Answering the same questions at the
        # command line instead is still there — `python main.py settings
        # setup` — but this file is the route for someone who just wants to
        # open the sandbox.
        page.hand_over()
        setup = subprocess.call([venv_python(), sandbox, "webui", "setup"],
                                env=env, **WINDOWLESS)
        if setup != 0:
            return setup

    if in_a_terminal:
        make_shortcut_if_missing()
        say("")
        say("Leave this window open while you use the sandbox. To stop it,")
        say("use Quit in the sandbox, close this window, or press Ctrl-C.")
        say("")
    if not needs_setup:
        page.hand_over()
    try:
        return subprocess.call([venv_python(), sandbox, "webui", "serve"],
                               env=env, **WINDOWLESS)
    except KeyboardInterrupt:
        say("")
        say("Stopped.")
        return 0


def main():
    """Set up and open the sandbox from a terminal window — `python3 start.py`."""
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

    outcome = ask_the_running_copy_to_stop()
    if outcome == BUSY:
        say("The sandbox is already running, and is in the middle of something")
        say("it would lose if it were restarted. Opening it in your browser.")
        open_in_browser(URL)
        return 0
    if outcome == SOMEONE_ELSE:
        say(explain_the_port_is_taken())
        return 1
    if outcome == STOPPED:
        say("Stopped the copy of the sandbox that was already running, so this")
        say("one starts fresh and looks for a newer version.")
        say("")

    token = new_stop_token()
    page = LoadingPage(token)
    if not page.start():
        say(explain_the_port_is_taken())
        return 1

    # First-time setup, but only if this copy has never been used.
    needs_setup = not is_set_up(sandbox)
    if needs_setup:
        say("Setup will continue in your browser. Please have your API key ready.")
    else:
        say("Starting the web interface. It will open in your browser.")
    open_in_browser(URL)
    return run_the_web_interface(sandbox, page, environment_with(token),
                                 needs_setup, in_a_terminal=True)


# ── Started from the icon ──────────────────────────────────────────────────
#
# The icon runs `start.py --launch`, which is over in a second or two: it
# stops a running copy, starts `start.py --run-hidden` on its own with no
# window and with its output going to a log file, and opens the browser once
# the loading page answers. The hidden one is what goes on running.

def launch_log_path():
    """Where a sandbox started from its icon writes down what happened.

    Outside the sandbox's own folder on purpose: anything written inside it
    is one more thing for an update to trip over.
    """
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Logs", "PU_AISandbox-launcher.log")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, "PU_AISandbox", "launcher.log")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    return os.path.join(base, "PU_AISandbox", "launcher.log")


def show_a_problem(headline, detail):
    """Say why the sandbox could not be started, in the browser.

    Started from its icon there is no window to print in, and the browser is
    where the person is already looking. The page is written to a file and
    opened from there, since nothing is running to serve it.
    """
    import tempfile

    say("%s %s" % (headline, detail))
    path = os.path.join(tempfile.gettempdir(), "PU_AISandbox-problem.html")
    try:
        with open(path, "wb") as handle:
            handle.write(render_loading_page(headline, detail, watch=False,
                                             log=launch_log_path()))
    except IOError:
        return
    try:
        from urllib.request import pathname2url
        open_in_browser("file:" + pathname2url(path))
    except Exception:   # a browser that won't open leaves only the log
        pass


def windowless_python():
    """The Python to start the hidden part with: one that opens no window.

    On Windows, python.exe always brings a black console window with it, and
    pythonw.exe, next to it, never does. Everywhere else they are the same.
    """
    if os.name != "nt":
        return sys.executable
    beside = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return beside if os.path.exists(beside) else sys.executable


def launch():
    """Open the sandbox from its icon — `start.py --launch`.

    Returns:
        What to exit with.
    """
    outcome = ask_the_running_copy_to_stop()
    if outcome == BUSY:
        # It says what it is doing, and it is where the person wants to be.
        open_in_browser(URL)
        return 0
    if outcome == SOMEONE_ELSE:
        show_a_problem("The sandbox wasn't started.", explain_the_port_is_taken())
        return 1

    log_path = launch_log_path()
    try:
        folder = os.path.dirname(log_path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        log = open(log_path, "w")
    except (IOError, OSError):
        log = open(os.devnull, "w")

    detached = {}
    if os.name == "nt":
        # A new process group, so it is not stopped along with this one; and
        # no window.
        detached["creationflags"] = 0x00000200 | 0x08000000
    else:
        # Its own session, so it is not stopped along with whatever ran this.
        detached["start_new_session"] = True

    token = new_stop_token()
    try:
        child = subprocess.Popen(
            [windowless_python(), os.path.abspath(__file__), "--run-hidden"],
            cwd=HERE, env=environment_with(token), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, close_fds=True, **detached
        )
    except OSError as e:
        show_a_problem("The sandbox couldn't be started.", str(e))
        return 1
    finally:
        log.close()

    # The loading page is up within a second on any ordinary computer; this
    # allows for one that is very busy.
    deadline = time.time() + 30
    while time.time() < deadline:
        if child.poll() is not None:
            show_a_problem(
                "The sandbox stopped before it had started.",
                "Double-click its icon to try again. If the same thing "
                "happens, run `python3 start.py` in a terminal window in the "
                "sandbox's folder, which says what is wrong as it goes.")
            return 1
        if the_loading_page_answers():
            open_in_browser(URL)
            return 0
        time.sleep(0.25)
    show_a_problem("The sandbox is taking too long to start.",
                   "Double-click its icon to try again.")
    return 1


def the_loading_page_answers():
    """Whether the loading page (and not something else) is answering yet."""
    import json

    try:
        response = _local_opener().open(URL + "/__loading", timeout=2)
        try:
            return bool(json.loads(response.read().decode("utf-8")).get("loading"))
        finally:
            response.close()
    except Exception:
        return False


def run_hidden():
    """The long-running part of a start from the icon — `start.py --run-hidden`.

    Does what main() does, with the loading page saying so in the browser
    rather than a terminal window. It asks no questions: the terminal run
    that made the icon is where the go-ahead to install was given. Software
    is only installed here when an update needs something new.

    Returns:
        What to exit with.
    """
    if os.name == "nt":
        # CREATE_NO_WINDOW, for every program this starts.
        WINDOWLESS["creationflags"] = 0x08000000

    say("Started from the icon at %s." % time.strftime("%Y-%m-%d %H:%M:%S"))
    token = os.environ.get(STOP_TOKEN_VARIABLE, "")
    log = launch_log_path()
    page = LoadingPage(token, log=log)
    if not page.start():
        say(explain_the_port_is_taken())
        return 1

    def problem(headline, detail):
        page.show("problem", headline, detail)
        say("%s %s" % (headline, detail))
        page.linger()
        return 1

    python = find_python()
    if python is None:
        explain_missing_python()
        return problem(
            "The sandbox needs a newer Python.",
            "It needs Python %s or newer. Install the latest from "
            "python.org, then double-click the sandbox's icon again."
            % MINIMUM_TEXT)

    if not environment_is_ready():
        page.show("installing", "Installing updated software…",
                  "A recent update needs some new software. This takes a few "
                  "minutes, and only happens once.")
        if not build_environment(python):
            return problem(
                "The software the sandbox needs couldn't be installed.",
                "This is almost always the network — a dropped connection, "
                "or a university proxy. Check you can reach the internet, "
                "then double-click the sandbox's icon to try again.")
        page.show("starting", "Starting the sandbox…")

    sandbox = os.path.join(HERE, "main.py")
    if not has_the_web_interface(sandbox):
        return problem(
            "The web interface isn't installed in this copy.",
            "Everything else still works from a terminal window: "
            "`python main.py --help`. To put the web interface back, restore "
            "the plugins/webui folder.")

    code = run_the_web_interface(sandbox, page, os.environ,
                                 needs_setup=not is_set_up(sandbox),
                                 in_a_terminal=False)
    say("The sandbox stopped (exit code %s)." % code)
    if code != 0:
        # Nothing is left answering, so a page still open would sit on
        # "Starting…" for ever. Put one back that says what happened — unless
        # a new start has already taken the address, which is how a copy is
        # usually replaced.
        page = LoadingPage(token, log=log)
        if page.start():
            return problem(
                "The sandbox stopped unexpectedly.",
                "Double-click its icon to start it again.")
    return code


# ── The icon ────────────────────────────────────────────────────────────────
#
# Made by this file, on the computer it runs on, rather than shipped: each one
# holds the full path to this folder and to a Python, which differ from one
# computer to the next. Being made here also spares it the checks a Mac or
# Windows applies to programs downloaded from the internet — nothing about it
# was downloaded — so it needs no signing.

SHORTCUT_NAME = "PU AI Sandbox"
ICON_FOLDER = WEBUI_LAUNCHER
MAC_BUNDLE_ID = "edu.princeton.pu-ai-sandbox.launcher"


def desktop_folder():
    """Return this person's Desktop folder, wherever their system keeps it."""
    home = os.path.expanduser("~")
    try:
        if os.name == "nt":
            # Often moved into OneDrive, so asked rather than assumed.
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Environment]::GetFolderPath('Desktop')"], **WINDOWLESS)
            found = out.decode("utf-8", "replace").strip()
            if found:
                return found
        elif sys.platform != "darwin":
            out = subprocess.check_output(["xdg-user-dir", "DESKTOP"])
            found = out.decode("utf-8", "replace").strip()
            if found:
                return found
    except (OSError, subprocess.CalledProcessError):
        pass
    return os.path.join(home, "Desktop")


def shortcut_path():
    """Where the icon goes on this computer."""
    if sys.platform == "darwin":
        return os.path.join(desktop_folder(), SHORTCUT_NAME + ".app")
    if os.name == "nt":
        return os.path.join(desktop_folder(), SHORTCUT_NAME + ".lnk")
    return os.path.join(desktop_folder(), "pu-ai-sandbox.desktop")


def make_shortcut_if_missing():
    """Put the sandbox's icon on the Desktop, the first time there's reason to."""
    if os.path.exists(shortcut_path()):
        return
    made = make_shortcut()
    if made:
        say("")
        say("From now on you can open the sandbox without this window: double-")
        say("click its icon, which is now on your Desktop:")
        say("    %s" % made)


def make_shortcut():
    """Make the sandbox's icon, replacing one made before.

    Returns:
        Where it was put, or None if it couldn't be made (already explained).
    """
    try:
        if sys.platform == "darwin":
            return make_mac_app(shortcut_path())
        if os.name == "nt":
            return make_windows_shortcut(shortcut_path())
        return make_linux_launcher(shortcut_path())
    except (IOError, OSError, subprocess.CalledProcessError) as e:
        say("")
        say("Couldn't put an icon for the sandbox on your Desktop (%s)." % e)
        say("The sandbox itself is fine; run `python3 start.py` to open it.")
        return None


def make_mac_app(target):
    """Make a small Mac application that opens the sandbox.

    An application on a Mac is a folder with a particular layout. This one
    holds a three-line script that runs `start.py --launch`, the icon, and a
    description saying it shows nothing in the Dock — it finishes in a second
    or two, and the sandbox runs in the browser.

    It can be moved anywhere, the Applications folder included: the paths
    it holds are to this folder, not to wherever it sits.
    """
    import plistlib
    import shlex
    import shutil

    if os.path.exists(target):
        # Replace only one of ours; anything else there is somebody's own.
        info = os.path.join(target, "Contents", "Info.plist")
        try:
            with open(info, "rb") as handle:
                ours = plistlib.load(handle).get("CFBundleIdentifier") == MAC_BUNDLE_ID
        except Exception:
            ours = False
        if not ours:
            raise OSError("something else is already called %s"
                          % os.path.basename(target))
        shutil.rmtree(target)

    contents = os.path.join(target, "Contents")
    os.makedirs(os.path.join(contents, "MacOS"))
    os.makedirs(os.path.join(contents, "Resources"))
    with open(os.path.join(contents, "Info.plist"), "wb") as handle:
        plistlib.dump({
            "CFBundleExecutable": "launch",
            "CFBundleIconFile": "sandbox.icns",
            "CFBundleIdentifier": MAC_BUNDLE_ID,
            "CFBundleName": SHORTCUT_NAME,
            "CFBundleDisplayName": SHORTCUT_NAME,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleInfoDictionaryVersion": "6.0",
            "LSUIElement": True,
        }, handle)
    script = os.path.join(contents, "MacOS", "launch")
    with open(script, "w") as handle:
        handle.write("#!/bin/sh\n"
                     "# Opens the Princeton University AI Sandbox. Made by start.py;\n"
                     "# run `python3 start.py --make-shortcut` to make it again.\n"
                     "exec %s %s --launch\n"
                     % (shlex.quote(sys.executable),
                        shlex.quote(os.path.abspath(__file__))))
    os.chmod(script, 0o755)
    shutil.copyfile(os.path.join(ICON_FOLDER, "sandbox.icns"),
                    os.path.join(contents, "Resources", "sandbox.icns"))
    # Touched, so Finder notices the icon now rather than at some later point.
    os.utime(target, None)
    return target


def make_windows_shortcut(target):
    """Make a Windows shortcut that opens the sandbox with no window.

    Windows shortcuts are made by asking Windows, through PowerShell. The
    details go in environment variables rather than into the command, so a
    folder name with a quote or a space in it can't break it.
    """
    env = dict(os.environ)
    env["PU_LINK_PATH"] = target
    env["PU_LINK_TARGET"] = windowless_python()
    env["PU_LINK_ARGUMENTS"] = subprocess.list2cmdline(
        [os.path.abspath(__file__), "--launch"])
    env["PU_LINK_FOLDER"] = HERE
    env["PU_LINK_ICON"] = os.path.join(ICON_FOLDER, "sandbox.ico")
    subprocess.check_call(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PU_LINK_PATH);"
         "$s.TargetPath = $env:PU_LINK_TARGET;"
         "$s.Arguments = $env:PU_LINK_ARGUMENTS;"
         "$s.WorkingDirectory = $env:PU_LINK_FOLDER;"
         "$s.IconLocation = $env:PU_LINK_ICON;"
         "$s.Description = 'Open the Princeton University AI Sandbox';"
         "$s.Save()"],
        env=env, **WINDOWLESS)
    return target


def desktop_entry_argument(text):
    """Quote *text* as one argument on the Exec line of a Linux .desktop file.

    The format has its own rules: arguments go in double quotes, four
    characters are escaped with a backslash inside them, and every backslash
    is then doubled again because the whole line is itself an escaped string.
    A percent sign is doubled too, because %f and friends mean something.
    """
    quoted = text
    for special in ("\\", '"', "`", "$"):
        quoted = quoted.replace(special, "\\" + special)
    return '"%s"' % quoted.replace("\\", "\\\\").replace("%", "%%")


def make_linux_launcher(target):
    """Make a Linux desktop entry that opens the sandbox.

    One copy goes on the Desktop and one in the applications menu. Some
    desktops (GNOME's, for one) ask once whether to trust a desktop entry
    before they will run it; this marks it trusted where that can be done.
    """
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=%s\n"
        "Comment=Open the Princeton University AI Sandbox\n"
        "Exec=%s %s --launch\n"
        "Icon=%s\n"
        "Terminal=false\n"
        "Categories=Education;\n"
        % (SHORTCUT_NAME,
           desktop_entry_argument(sys.executable),
           desktop_entry_argument(os.path.abspath(__file__)),
           os.path.join(ICON_FOLDER, "sandbox.png"))
    )
    menu = os.path.join(os.environ.get("XDG_DATA_HOME")
                        or os.path.join(os.path.expanduser("~"), ".local", "share"),
                        "applications")
    places = [os.path.join(menu, "pu-ai-sandbox.desktop")]
    if os.path.isdir(os.path.dirname(target)):
        places.insert(0, target)
    for place in places:
        folder = os.path.dirname(place)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(place, "w") as handle:
            handle.write(entry)
        os.chmod(place, 0o755)
        try:
            subprocess.call(["gio", "set", place, "metadata::trusted", "true"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    return places[0]


USAGE = """Usage: python3 start.py [--make-shortcut]

  (nothing)         Set the sandbox up if it needs it, then open it.
  --make-shortcut   Put the sandbox's icon on your Desktop again, e.g. after
                    moving this folder.

The icon itself runs `start.py --launch`, which you don't need to type."""


def entry(arguments):
    """Choose what to do from the words after `start.py`."""
    if not arguments:
        return main()
    if arguments == ["--launch"]:
        return launch()
    if arguments == ["--run-hidden"]:
        return run_hidden()
    if arguments == ["--make-shortcut"]:
        if not has_the_web_interface(os.path.join(HERE, "main.py")):
            say("The icon opens the web interface, which isn't installed in this")
            say("copy (or hasn't been set up yet: run `python3 start.py` first).")
            return 1
        made = make_shortcut()
        if made:
            say("The sandbox's icon is at %s" % made)
            return 0
        return 1
    say(USAGE)
    return 0 if arguments in (["-h"], ["--help"]) else 2


if __name__ == "__main__":
    sys.exit(entry(sys.argv[1:]))
