"""Opens the sandbox's web interface: the loading page, the icon, and starting afresh.

``start.py`` does the part every copy of the sandbox needs — finding a Python
new enough to run it and installing its software into ``.venv`` — and then,
if the web interface is here, hands over to this file. What this file does is
the web interface's business alone, which is why it lives in the web
interface's plugin folder: a copy with the plugin removed has no browser page
to show and no use for an icon.

It is started three ways, all through ``start.py``:

* ``python3 start.py`` — from a terminal. ``open_from_a_terminal()``.
* ``start.py --launch`` — what the icon runs. ``launch()`` stops a copy
  already running, starts ``--run-hidden`` on its own with no window, opens
  the browser once the loading page answers, and is finished.
* ``start.py --run-hidden`` — the long-running part of an icon start.
  ``run_hidden()``.

``start.py`` loads this file by its path and passes itself in (``attach()``),
so the two share one copy of everything about finding a Python and installing.
Like ``start.py``, this has to run on old Pythons — whatever the icon happens
to start it with — and before anything is installed, so it uses nothing
newer than Python 3.6 understands and nothing that doesn't come with Python.
It never imports from the plugin's own code or from ``src/``; when it needs
an answer only the sandbox has (which port to use), it asks the sandbox, by
running it.
"""

import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any  # noqa: F401 — used in a type comment below

FOLDER = os.path.dirname(os.path.abspath(__file__))
# The sandbox's own folder: this file is plugins/webui/launcher/launcher.py.
PACKAGE = os.path.dirname(os.path.dirname(os.path.dirname(FOLDER)))
VENV_DIR = os.path.join(PACKAGE, ".venv")
SANDBOX = os.path.join(PACKAGE, "main.py")
START_PY = os.path.join(PACKAGE, "start.py")
LOADING_TEMPLATE = os.path.join(FOLDER, "loading.html")
OPEN_SCRIPT = os.path.join(FOLDER, "open-sandbox.sh")
WEBUI_SETTINGS = os.path.join(PACKAGE, "plugins", "webui", "settings.toml")
WEBUI_SETTINGS_MODULE = os.path.join(PACKAGE, "plugins", "webui", "src", "settings.py")

# start.py, handed in by attach(). Everything about finding a Python and
# installing the sandbox's software is asked of it rather than repeated here.
START = None  # type: Any


def attach(start):
    """Remember *start* — the start.py module — for everything below to use."""
    global START
    START = start


def say(message):
    """Print a line, the way start.py does."""
    START.say(message)


# ── Which port ──────────────────────────────────────────────────────────────
#
# The web interface's port is one of its settings — `port` under [webui] —
# and like every setting it can be changed in a shared settings file or in
# preferences.toml. So the answer is the sandbox's, and this asks it rather
# than keeping a number of its own that could disagree. Everything started
# from here is then told the port it was given (`--port`), so the loading
# page and the sandbox can't end up on two different ones.
#
# Asking needs the sandbox's software. Before that is installed there is no
# asking, and the plugin's own settings.toml is read instead: the only layer
# there is to read at that point. If installing turns up a different answer,
# the loading page moves itself — see move_to().

PORT = None  # type: Any
URL = ""

# Run with the sandbox's own Python: loads the web interface's settings the
# way the web interface does, and prints the port they come to.
_ASK_FOR_THE_PORT = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('_pu_webui_port', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "print(module.WEBUI_PORT)\n"
)


def use_port(port):
    """Make *port* the one everything below uses."""
    global PORT, URL
    PORT = port
    URL = "http://127.0.0.1:%d" % port


def port_from_the_sandbox():
    """Ask the sandbox which port its settings give the web interface.

    Returns:
        The port, or None if the sandbox can't be asked yet (its software
        isn't installed) or didn't answer with a number.
    """
    python = START.venv_python()
    if not os.path.exists(python):
        return None
    try:
        with open(os.devnull, "w") as quiet:
            out = subprocess.check_output(
                [python, "-c", _ASK_FOR_THE_PORT, WEBUI_SETTINGS_MODULE],
                cwd=PACKAGE, stderr=quiet, **START.WINDOWLESS)
        return int(out.decode("utf-8", "replace").strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def port_from_the_plugins_own_file():
    """Read the port from plugins/webui/settings.toml, without the layers above it.

    Only for before the sandbox's software is installed, when there is
    nothing to ask. A whole TOML reader only comes with Python 3.11, so this
    reads the one line it needs: `port = …` in the [webui] section.

    Returns:
        The port, or None if the file doesn't say.
    """
    import re

    section = None
    try:
        with open(WEBUI_SETTINGS, "r") as handle:
            for line in handle:
                stripped = line.split("#", 1)[0].strip()
                heading = re.match(r"^\[([^\]]+)\]$", stripped)
                if heading:
                    section = heading.group(1).strip()
                    continue
                found = re.match(r"^port\s*=\s*(\d+)$", stripped)
                if section == "webui" and found:
                    return int(found.group(1))
    except IOError:
        pass
    return None


def the_port():
    """Return the web interface's port: the sandbox's answer if it can give one."""
    return port_from_the_sandbox() or port_from_the_plugins_own_file()


def settle_the_port():
    """Work out the port and use it. Returns False if nothing says what it is."""
    port = the_port()
    if port is None:
        return False
    use_port(port)
    return True


def explain_no_port():
    """Return why the sandbox can't start when no port can be found."""
    return ("The web interface's settings don't say which port to use. Its "
            "settings file, plugins/webui/settings.toml, should have a line "
            "`port = 8000` under [webui]; restoring that file from a fresh "
            "download puts it back.")


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
        folder = os.path.dirname(TOKEN_FILE)
        if not os.path.isdir(folder):
            os.makedirs(folder)
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


def port_is_taken(port=None):
    """Whether anything at all is listening on *port* (the sandbox's, if not given)."""
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        return probe.connect_ex(("127.0.0.1", port or PORT)) == 0
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
               '<script type="application/json" id="state">{{STATE}}</script>'
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

    def __init__(self, token, log="", port=None):
        """
        Args:
            token: The stop token of this start, so a double-click on the icon
                   while this is showing can stop it like any other copy.
            log: Where what happens is written down, to point at if it goes wrong.
            port: The port to answer on; the sandbox's, if not given.
        """
        import threading

        self.token = token
        self.log = log
        self.port = port or PORT
        self.phase = "starting"
        self.headline = "Starting the sandbox…"
        self.detail = ""
        # Set when the sandbox's port turns out to be another one, to send the
        # page there — see move_to().
        self.address = None
        self.seen = threading.Event()
        self._server = None

    def start(self):
        """Start answering. Returns False if the address is already taken."""
        import threading

        try:
            server = _LoadingServer(("127.0.0.1", self.port), _LoadingHandler)
        except (OSError, IOError):
            return False
        server.page = self
        self._server = server
        # Checked for a request to stop every twentieth of a second rather than
        # the usual half second, so that stop() is as prompt as it says and
        # the sandbox is not kept waiting for the address it is taking over.
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        return True

    def show(self, phase, headline, detail=""):
        """Change what the page says. *phase* is "starting", "installing" or "problem"."""
        self.phase = phase
        self.headline = headline
        self.detail = detail

    def move_to(self, other, patience=15):
        """Send the browser to *other*, a loading page on another port, and stop.

        For when installing turns up a port different from the one this page
        was started on. The page in the browser is told the new address the
        next time it asks, goes there, and finds *other* waiting.
        """
        other.show(self.phase, self.headline, self.detail)
        self.address = "http://127.0.0.1:%d" % other.port
        other.seen.wait(patience)
        self.stop()

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
        """Stop everything this process is doing, because a new start asked it to.

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
            answer = {
                "loading": True, "phase": page.phase,
                "headline": page.headline, "detail": page.detail,
            }
            if page.address:
                answer["address"] = page.address
            self._send(200, json.dumps(answer).encode("utf-8"), "application/json")
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

def run_the_web_interface(page, env, needs_setup, in_a_terminal):
    """Run first-time setup if it is needed, then the sandbox, until it stops.

    Args:
        page: The loading page holding the address, let go of just before
              whatever needs the address next starts.
        env: The environment to run them in, carrying the stop token.
        needs_setup: Whether this copy has never been set up.
        in_a_terminal: Whether someone is watching a terminal window — and so
              whether there is anyone to tell about the icon this makes.

    Returns:
        What the sandbox exited with.
    """
    # Told, not left to work out: the loading page is on this port, and the
    # page in the browser is waiting there.
    port = ["--port", str(PORT)]
    if needs_setup:
        # Setup runs as its own step, on the same address the web interface
        # will use afterwards, and it stops as soon as it has an answer. The
        # page already in the browser reloads onto it, and its last page
        # follows itself to the sandbox once the answer is in, so nobody has
        # to find a second window. Answering the same questions at the
        # command line instead is still there — `python main.py settings
        # setup` — but this is the route for someone who just wants to open
        # the sandbox.
        page.hand_over()
        setup = subprocess.call([START.venv_python(), SANDBOX, "webui", "setup"] + port,
                                env=env, **START.WINDOWLESS)
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
        return subprocess.call([START.venv_python(), SANDBOX, "webui", "serve"] + port,
                               env=env, **START.WINDOWLESS)
    except KeyboardInterrupt:
        say("")
        say("Stopped.")
        return 0


def open_from_a_terminal():
    """Open the web interface from `python3 start.py`, once its software is installed.

    Returns:
        What to exit with.
    """
    if not settle_the_port():
        say(explain_no_port())
        return 1

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
    needs_setup = not START.is_set_up(SANDBOX)
    if needs_setup:
        say("Setup will continue in your browser. Please have your API key ready.")
    else:
        say("Starting the web interface. It will open in your browser.")
    open_in_browser(URL)
    return run_the_web_interface(page, environment_with(token), needs_setup,
                                 in_a_terminal=True)


# ── Started from the icon ──────────────────────────────────────────────────

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


def launch():
    """Open the sandbox from its icon — `start.py --launch`.

    Returns:
        What to exit with.
    """
    if not settle_the_port():
        show_a_problem("The sandbox wasn't started.", explain_no_port())
        return 1

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
            [windowless_python(), START_PY, "--run-hidden"],
            cwd=PACKAGE, env=environment_with(token), stdin=subprocess.DEVNULL,
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


def run_hidden():
    """The long-running part of a start from the icon — `start.py --run-hidden`.

    Does what a terminal start does, with the loading page saying so in the
    browser rather than a terminal window. It asks no questions: the terminal
    run that made the icon is where the go-ahead to install was given.
    Software is only installed here when an update needs something new.

    Returns:
        What to exit with.
    """
    if os.name == "nt":
        # CREATE_NO_WINDOW, for every program this and start.py start.
        START.WINDOWLESS["creationflags"] = 0x08000000

    say("Started from the icon at %s." % time.strftime("%Y-%m-%d %H:%M:%S"))
    if not settle_the_port():
        say(explain_no_port())
        return 1
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

    python = START.find_python()
    if python is None:
        START.explain_missing_python()
        return problem(
            "The sandbox needs a newer Python.",
            "It needs Python %s or newer. Install the latest from "
            "python.org, then double-click the sandbox's icon again."
            % START.MINIMUM_TEXT)

    if not START.environment_is_ready():
        page.show("installing", "Installing updated software…",
                  "A recent update needs some new software. This takes a few "
                  "minutes, and only happens once.")
        if not START.build_environment(python):
            return problem(
                "The software the sandbox needs couldn't be installed.",
                "This is almost always the network — a dropped connection, "
                "or a university proxy. Check you can reach the internet, "
                "then double-click the sandbox's icon to try again.")
        page.show("starting", "Starting the sandbox…")

        # The port was read from the plugin's own file, because nothing could
        # be asked. Now something can; a different answer means a setting
        # somewhere changed it, and the page goes to where the sandbox will be.
        port = port_from_the_sandbox()
        if port and port != PORT:
            moved = LoadingPage(token, log=log, port=port)
            if moved.start():
                page.move_to(moved)
                page = moved
                use_port(port)

    if not START.has_the_web_interface(SANDBOX):
        return problem(
            "The web interface isn't installed in this copy.",
            "Everything else still works from a terminal window: "
            "`python main.py --help`. To put the web interface back, restore "
            "the plugins/webui folder.")

    code = run_the_web_interface(page, os.environ,
                                 needs_setup=not START.is_set_up(SANDBOX),
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
#
# On a Mac and on Linux the icon runs open-sandbox.sh, beside this file,
# rather than a Python directly. A Python can be removed or upgraded away, and
# an icon that runs a Python that isn't there any more does nothing at all —
# no page, no message. The script tries the Python the icon was made with,
# then the usual places for one, and if there is none it opens the loading
# page straight from disk, which then explains that Python is needed.

SHORTCUT_NAME = "PU AI Sandbox"
MAC_BUNDLE_ID = "edu.princeton.pu-ai-sandbox.launcher"


def desktop_folder():
    """Return this person's Desktop folder, wherever their system keeps it."""
    home = os.path.expanduser("~")
    try:
        if os.name == "nt":
            # Often moved into OneDrive, so asked rather than assumed.
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Environment]::GetFolderPath('Desktop')"], **START.WINDOWLESS)
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
    holds a two-line script that runs open-sandbox.sh, the icon, and a
    description saying it shows nothing in the Dock — it finishes in a second
    or two, and the sandbox runs in the browser.

    It can be moved anywhere, the Applications folder included: the paths
    it holds are to the sandbox's folder, not to wherever it sits.
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
                     "exec /bin/sh %s %s\n"
                     % (shlex.quote(OPEN_SCRIPT), shlex.quote(sys.executable)))
    os.chmod(script, 0o755)
    shutil.copyfile(os.path.join(FOLDER, "sandbox.icns"),
                    os.path.join(contents, "Resources", "sandbox.icns"))
    # Touched, so Finder notices the icon now rather than at some later point.
    os.utime(target, None)
    return target


def windows_launcher():
    """The program and arguments a Windows shortcut should run, as a pair.

    The Python launcher that comes with Python for Windows (pyw.exe) is
    preferred to any one Python: it finds whichever Python 3 is installed, so
    the icon goes on working when Python is upgraded. Without it, the Python
    that ran this, in its windowless form.
    """
    import shutil

    found = shutil.which("pyw")
    if found:
        return found, subprocess.list2cmdline(["-3", START_PY, "--launch"])
    return windowless_python(), subprocess.list2cmdline([START_PY, "--launch"])


def make_windows_shortcut(target):
    """Make a Windows shortcut that opens the sandbox with no window.

    Windows shortcuts are made by asking Windows, through PowerShell. The
    details go in environment variables rather than into the command, so a
    folder name with a quote or a space in it can't break it.
    """
    program, arguments = windows_launcher()
    env = dict(os.environ)
    env["PU_LINK_PATH"] = target
    env["PU_LINK_TARGET"] = program
    env["PU_LINK_ARGUMENTS"] = arguments
    env["PU_LINK_FOLDER"] = PACKAGE
    env["PU_LINK_ICON"] = os.path.join(FOLDER, "sandbox.ico")
    subprocess.check_call(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PU_LINK_PATH);"
         "$s.TargetPath = $env:PU_LINK_TARGET;"
         "$s.Arguments = $env:PU_LINK_ARGUMENTS;"
         "$s.WorkingDirectory = $env:PU_LINK_FOLDER;"
         "$s.IconLocation = $env:PU_LINK_ICON;"
         "$s.Description = 'Open the Princeton University AI Sandbox';"
         "$s.Save()"],
        env=env, **START.WINDOWLESS)
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
        "Exec=/bin/sh %s %s\n"
        "Icon=%s\n"
        "Terminal=false\n"
        "Categories=Education;\n"
        % (SHORTCUT_NAME,
           desktop_entry_argument(OPEN_SCRIPT),
           desktop_entry_argument(sys.executable),
           os.path.join(FOLDER, "sandbox.png"))
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


# ── From start.py ───────────────────────────────────────────────────────────

def entry(arguments):
    """Do what the words after `start.py` ask, for the three this file answers.

    Returns:
        What to exit with, or None if *arguments* aren't this file's to answer.
    """
    if arguments == ["--launch"]:
        return launch()
    if arguments == ["--run-hidden"]:
        return run_hidden()
    if arguments == ["--make-shortcut"]:
        made = make_shortcut()
        if made:
            say("The sandbox's icon is at %s" % made)
            return 0
        return 1
    return None
