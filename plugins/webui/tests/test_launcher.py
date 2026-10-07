"""Tests for the web interface's launcher (plugins/webui/launcher/launcher.py).

What opens the sandbox in a browser: the loading page, stopping a copy
already running, the icon. start.py hands over to it once the sandbox's
software is installed, so like start.py it has to run on the Python a Mac
ships — the first class below checks it still can.
"""

import ast
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_FOLDER = Path(__file__).resolve().parents[1] / "launcher"
_LAUNCHER = _FOLDER / "launcher.py"
_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def start():
    """Load start.py as a module without running it."""
    spec = importlib.util.spec_from_file_location("_start_under_test", _ROOT / "start.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture
def launcher(start):
    """The launcher, loaded the way start.py loads it, with start.py handed in."""
    module = start.the_web_interfaces_launcher()
    assert module is not None
    return module


class TestRunsOnAnOldPython:
    def test_nothing_newer_than_the_python_macos_ships(self):
        tree = ast.parse(_LAUNCHER.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.Match), "match statement needs 3.10+"
            assert not isinstance(node, ast.JoinedStr), "f-strings need 3.6; keep it plain"

    @pytest.mark.skipif(not Path("/usr/bin/python3").exists(),
                        reason="no system python to check against")
    def test_the_system_python_can_actually_parse_it(self):
        result = subprocess.run(
            ["/usr/bin/python3", "-c",
             "import ast; ast.parse(open(%r).read())" % str(_LAUNCHER)],
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode()

    def test_it_never_imports_the_sandbox_itself(self):
        """It runs before the sandbox's software is installed."""
        source = _LAUNCHER.read_text(encoding="utf-8")
        assert "from src" not in source and "import src" not in source
        assert "fastapi" not in source


def _free_port():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def own_port(launcher, monkeypatch, tmp_path):
    """Point the launcher at a port of the test's own, and its token file at tmp_path.

    Never 8000: a real sandbox may be running there, and these tests stop things.
    """
    port = _free_port()
    monkeypatch.setattr(launcher, "PORT", port)
    monkeypatch.setattr(launcher, "URL", "http://127.0.0.1:%d" % port)
    monkeypatch.setattr(launcher, "TOKEN_FILE", str(tmp_path / ".stop-token"))
    return port


@pytest.fixture
def web_server(own_port):
    """Something answering POST /__stop on the test's port, with a status of the test's choosing."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    heard = {"status": 200, "tokens": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            heard["tokens"].append(self.headers.get("X-Sandbox-Stop-Token"))
            self.send_response(heard["status"])
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", own_port), Handler)
    # The usual half-second check for "stop" would be half a second added to
    # every test that uses this.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield heard
    server.shutdown()
    server.server_close()


class TestStoppingTheCopyAlreadyRunning:
    """Every start is a fresh launcher, because starting is when the sandbox
    looks for a newer version. But only this sandbox is ever stopped, and
    only by asking."""

    def test_nothing_there_is_nothing_to_stop(self, launcher, own_port):
        assert launcher.ask_the_running_copy_to_stop() == launcher.NOTHING_RUNNING

    def test_it_presents_the_token_the_last_start_left(self, launcher, web_server, monkeypatch):
        launcher.new_stop_token()
        left = launcher.read_stop_token()
        monkeypatch.setattr(launcher, "port_is_taken", lambda: False)
        assert launcher.ask_the_running_copy_to_stop() == launcher.STOPPED
        assert web_server["tokens"] == [left]

    def test_a_copy_in_the_middle_of_something_is_left_running(self, launcher, web_server):
        web_server["status"] = 409
        assert launcher.ask_the_running_copy_to_stop() == launcher.BUSY

    @pytest.mark.parametrize("status", [403, 404, 405, 500])
    def test_anything_that_does_not_agree_is_somebody_elses(self, launcher, web_server, status):
        web_server["status"] = status
        assert launcher.ask_the_running_copy_to_stop() == launcher.SOMEONE_ELSE

    def test_a_copy_that_agrees_but_never_lets_go_is_not_waited_on_forever(
            self, launcher, web_server, monkeypatch):
        monkeypatch.setattr(launcher, "STOP_PATIENCE_SECONDS", 0.5)
        monkeypatch.setattr(launcher, "port_is_taken", lambda: True)
        assert launcher.ask_the_running_copy_to_stop() == launcher.SOMEONE_ELSE

    def test_something_that_is_not_a_web_server_is_somebody_elses(
            self, launcher, own_port, monkeypatch):
        """It holds the port and never answers in the language of the web."""
        import socket
        import urllib.error

        class Silent:
            def open(self, *args, **kwargs):
                raise urllib.error.URLError("no answer")

        monkeypatch.setattr(launcher, "_local_opener", Silent)
        listener = socket.socket()
        listener.bind(("127.0.0.1", own_port))
        listener.listen(1)
        try:
            assert launcher.ask_the_running_copy_to_stop() == launcher.SOMEONE_ELSE
        finally:
            listener.close()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_the_token_is_readable_by_this_person_only(self, launcher, own_port):
        import os
        import stat

        launcher.new_stop_token()
        mode = stat.S_IMODE(os.stat(launcher.TOKEN_FILE).st_mode)
        assert mode == 0o600

    def test_each_start_makes_a_new_token(self, launcher, own_port):
        assert launcher.new_stop_token() != launcher.new_stop_token()


class TestTheLoadingPage:
    """What the browser shows while the sandbox starts."""

    @pytest.fixture
    def page(self, launcher, own_port):
        page = launcher.LoadingPage("right", log="/somewhere/launcher.log")
        page.ended = []
        page.end_this_process = lambda: page.ended.append(True)
        assert page.start()
        yield page
        page.stop()

    def _get(self, launcher, path):

        return launcher._local_opener().open(launcher.URL + path, timeout=5)

    def _post_stop(self, launcher, token):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(launcher.URL + "/__stop", data=b"",
                                         headers={"X-Sandbox-Stop-Token": token})
        try:
            return launcher._local_opener().open(request, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_it_shows_the_logo_and_what_is_happening(self, launcher, page):
        body = self._get(launcher, "/").read().decode("utf-8")
        assert "Starting the sandbox" in body
        assert 'fill="#f58025"' in body
        assert page.seen.is_set(), "the browser has now fetched it"

    def test_it_says_how_things_are_going_when_asked(self, launcher, page):
        import json

        page.show("installing", "Installing updated software…", "A few minutes.")
        answer = json.loads(self._get(launcher, "/__loading").read().decode("utf-8"))
        assert answer == {"loading": True, "phase": "installing",
                          "headline": "Installing updated software…",
                          "detail": "A few minutes."}

    def test_the_launcher_can_stop_it_with_the_right_token(self, launcher, page):
        import time

        assert self._post_stop(launcher, "right") == 200
        # The answer goes before the handler gets to the next line, which is
        # the point of the order — so allow it a moment.
        deadline = time.time() + 2
        while not page.ended and time.time() < deadline:
            time.sleep(0.01)
        assert page.ended == [True]

    def test_not_with_any_other(self, launcher, page):
        assert self._post_stop(launcher, "wrong") == 403
        assert self._post_stop(launcher, "") == 403
        assert page.ended == []

    def test_not_halfway_through_installing(self, launcher, page):
        page.show("installing", "Installing updated software…")
        assert self._post_stop(launcher, "right") == 409
        assert page.ended == []

    def test_a_second_one_cannot_take_the_same_port(self, launcher, page):
        assert launcher.LoadingPage("other").start() is False

    def test_the_words_are_written_as_words(self, launcher):
        """A folder or an error message could contain anything."""
        body = launcher.render_loading_page("<b>bold</b>", "a & b", watch=True,
                                         log="</script><script>alert(1)")
        text = body.decode("utf-8")
        assert "<b>bold</b>" not in text and "&lt;b&gt;" in text
        assert "a &amp; b" in text
        assert "</script><script>alert(1)" not in text

    def test_the_page_opened_from_disk_does_not_watch(self, launcher):
        text = launcher.render_loading_page("The sandbox wasn't started.", "",
                                         watch=False).decode("utf-8")
        assert '"watch": false' in text

    def test_the_page_moves_itself_to_the_sandbox(self):
        """The whole point: nobody reloads it by hand."""
        page = (_FOLDER / "loading.html").read_text()
        assert 'fetch("/__loading"' in page
        assert "window.location.reload()" in page


class TestTheLaunchLog:
    def test_a_mac_keeps_it_with_its_other_logs(self, launcher, monkeypatch):
        monkeypatch.setattr(launcher.sys, "platform", "darwin")
        assert launcher.launch_log_path().endswith(
            "Library/Logs/PU_AISandbox-launcher.log")

    def test_linux_keeps_it_in_the_state_folder(self, launcher, monkeypatch):
        monkeypatch.setattr(launcher.sys, "platform", "linux")
        monkeypatch.setenv("XDG_STATE_HOME", "/state")
        assert launcher.launch_log_path() == "/state/PU_AISandbox/launcher.log"

    def test_never_inside_the_sandbox_itself(self, launcher):
        assert not launcher.launch_log_path().startswith(launcher.PACKAGE)


class TestTheIcon:
    """Made here, on this computer, so it needs no signing and holds the right paths."""

    @pytest.mark.skipif(shutil.which("osacompile") is None, reason="needs a Mac")
    def test_a_mac_application_that_can_ask_to_open_protected_folders(
            self, launcher, tmp_path, monkeypatch):
        import plistlib

        monkeypatch.setattr(launcher.sys, "executable", '/Python "Folder"/python3')
        app = tmp_path / "PU AI Sandbox.app"
        launcher.make_mac_app(str(app))
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        # The name a Mac shows when it asks, and in Activity Monitor — not "applet".
        assert info["CFBundleExecutable"] == launcher.SHORTCUT_NAME
        assert (app / "Contents" / "MacOS" / launcher.SHORTCUT_NAME).exists()
        assert info["CFBundleName"] == info["CFBundleDisplayName"] == launcher.SHORTCUT_NAME
        assert info["CFBundleIdentifier"] == launcher.MAC_BUNDLE_ID
        assert info["LSUIElement"] is True, "no Dock icon for something over in a second"
        for key in launcher.MAC_FOLDER_ACCESS:
            assert info[key] == launcher.MAC_FOLDER_REASON
        assert "CFBundleIconName" not in info, "the generic AppleScript icon"
        assert (app / "Contents" / "Resources" / "sandbox.icns").exists()
        # Signed again after being changed, or a Mac won't remember the answer.
        subprocess.check_call(["codesign", "--verify", "--strict", str(app)])
        script = subprocess.check_output(["osadecompile", str(app)]).decode("utf-8")
        assert launcher.OPEN_SCRIPT in script
        assert '/Python \\"Folder\\"/python3' in script

    def test_applescript_text_keeps_quotes_and_backslashes(self, launcher):
        assert launcher.applescript_text('a "b" \\c') == '"a \\"b\\" \\\\c"'

    def test_a_plain_script_application_when_osacompile_fails(
            self, launcher, tmp_path, monkeypatch):
        import os
        import plistlib

        monkeypatch.setenv("PATH", str(tmp_path))  # no osacompile to be found
        monkeypatch.setattr(launcher.sys, "executable", "/Python Folder/python3")
        app = tmp_path / "PU AI Sandbox.app"
        launcher.make_mac_app(str(app))
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        assert info["CFBundleExecutable"] == "launch"
        assert info["CFBundleIdentifier"] == launcher.MAC_BUNDLE_ID
        assert info["LSUIElement"] is True, "no Dock icon for something over in a second"
        script = app / "Contents" / "MacOS" / "launch"
        text = script.read_text()
        # Through open-sandbox.sh, which copes with that Python having gone.
        assert "exec /bin/sh " in text
        assert launcher.OPEN_SCRIPT in text
        assert text.rstrip().endswith("'/Python Folder/python3'"), \
            "a space in a path must not split it"
        assert os.access(str(script), os.X_OK)
        assert (app / "Contents" / "Resources" / "sandbox.icns").exists()

    def test_making_it_again_replaces_ours(self, launcher, tmp_path):
        app = tmp_path / "PU AI Sandbox.app"
        launcher.make_mac_app(str(app))
        (app / "Contents" / "stale").write_text("x")
        launcher.make_mac_app(str(app))
        assert not (app / "Contents" / "stale").exists()

    def test_but_never_someone_elses(self, launcher, tmp_path):
        app = tmp_path / "PU AI Sandbox.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents" / "theirs").write_text("keep me")
        with pytest.raises(OSError):
            launcher.make_mac_app(str(app))
        assert (app / "Contents" / "theirs").read_text() == "keep me"

    def test_a_linux_desktop_entry(self, launcher, tmp_path, monkeypatch):
        import os

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        made = launcher.make_linux_launcher(str(desktop / "pu-ai-sandbox.desktop"))
        entry = (desktop / "pu-ai-sandbox.desktop").read_text()
        assert made == str(desktop / "pu-ai-sandbox.desktop")
        assert "Terminal=false" in entry
        assert "open-sandbox.sh" in entry
        assert (tmp_path / "share" / "applications" / "pu-ai-sandbox.desktop").exists()
        assert os.access(made, os.X_OK)

    @pytest.mark.parametrize("path, quoted", [
        ("/plain/path", '"/plain/path"'),
        ("/with space/x", '"/with space/x"'),
        ('/a"quote', '"/a\\\\"quote"'),
        ("/a$dollar", '"/a\\\\$dollar"'),
        ("/100%", '"/100%%"'),
    ])
    def test_desktop_entry_quoting(self, launcher, path, quoted):
        assert launcher.desktop_entry_argument(path) == quoted

    def test_an_icon_already_there_is_left_alone(self, launcher, tmp_path, monkeypatch):
        existing = tmp_path / "PU AI Sandbox.app"
        existing.mkdir()
        monkeypatch.setattr(launcher, "shortcut_path", lambda: str(existing))
        made = []
        monkeypatch.setattr(launcher, "make_shortcut", lambda: made.append(1))
        launcher.make_shortcut_if_missing()
        assert made == []

    def test_a_first_terminal_run_makes_one(self, launcher, tmp_path, monkeypatch):
        said = []
        monkeypatch.setattr(launcher, "say", said.append)
        monkeypatch.setattr(launcher, "shortcut_path", lambda: str(tmp_path / "missing"))
        monkeypatch.setattr(launcher, "make_shortcut", lambda: "/Desktop/PU AI Sandbox.app")
        launcher.make_shortcut_if_missing()
        assert any("/Desktop/PU AI Sandbox.app" in line for line in said)

    def test_the_icons_it_needs_are_in_the_web_interface(self, launcher):
        """They are the web interface's, so they go if it is removed."""
        for name in ("sandbox.icns", "sandbox.ico", "sandbox.png", "loading.html"):
            assert (_FOLDER / name).exists(), name




class TestWhichPort:
    """The port is the web interface's setting, and the sandbox is asked for it,
    so the launcher and the sandbox can't disagree."""

    def test_the_sandbox_is_asked_and_its_layers_apply(self, launcher, monkeypatch):
        """The same answer webui serve would come to, preferences.toml included."""
        from src.settings import plugin_settings

        # The Python running these tests has the sandbox's software; a .venv
        # in the package may not exist (GitHub Actions installs without one).
        monkeypatch.setattr(launcher.START, "venv_python", lambda: sys.executable)

        expected = plugin_settings(
            launcher.WEBUI_SETTINGS_MODULE, "webui")["webui"].get("port", 8000)
        assert launcher.port_from_the_sandbox() == expected

    def test_before_installing_the_plugins_own_file_is_read(self, launcher, monkeypatch):
        monkeypatch.setattr(launcher.START, "venv_python", lambda: "/nowhere/python")
        assert launcher.port_from_the_sandbox() is None
        assert launcher.the_port() == launcher.port_from_the_plugins_own_file()

    @pytest.mark.parametrize("text, port", [
        ("[webui]\nport = 8123\n", 8123),
        ("[webui]\nport=8124  # a comment\n", 8124),
        ("[other]\nport = 1\n[webui]\nhost = 'x'\nport = 8125\n", 8125),
        ("[webui]\n# port = 1\n", None),
        ("[other]\nport = 1\n", None),
    ])
    def test_reading_the_one_line_it_needs(self, launcher, monkeypatch, tmp_path, text, port):
        settings = tmp_path / "settings.toml"
        settings.write_text(text)
        monkeypatch.setattr(launcher, "WEBUI_SETTINGS", str(settings))
        assert launcher.port_from_the_plugins_own_file() == port

    def test_the_shipped_file_names_a_port(self, launcher):
        assert isinstance(launcher.port_from_the_plugins_own_file(), int)

    def test_setup_and_the_sandbox_are_told_the_port(self, launcher, own_port, monkeypatch):
        ran = []

        def fake_call(args, **kwargs):
            ran.append(args)
            return 0

        class Page:
            def hand_over(self):
                pass

        monkeypatch.setattr(launcher.subprocess, "call", fake_call)
        monkeypatch.setattr(launcher, "make_shortcut_if_missing", lambda: None)
        monkeypatch.setattr(launcher.START, "say", lambda line: None)
        launcher.run_the_web_interface(Page(), {}, needs_setup=True, in_a_terminal=True)
        assert [args[-4:] for args in ran] == [
            ["webui", "setup", "--port", str(own_port)],
            ["webui", "serve", "--port", str(own_port)],
        ]

    def test_a_page_moves_itself_when_the_port_turns_out_different(self, launcher, own_port):
        import json
        import threading

        first = launcher.LoadingPage("t")
        second = launcher.LoadingPage("t", port=_free_port())
        assert first.start() and second.start()
        try:
            second.seen.set()   # the browser has already arrived there
            first.show("starting", "Starting the sandbox…")
            mover = threading.Thread(target=first.move_to, args=(second,))
            mover.start()
            mover.join(5)
            assert first.address == "http://127.0.0.1:%d" % second.port
            assert not launcher.port_is_taken(own_port), "the old page let go"
            answer = launcher._local_opener().open(
                "http://127.0.0.1:%d/__loading" % second.port, timeout=5).read()
            assert json.loads(answer.decode("utf-8"))["headline"] == "Starting the sandbox…"
        finally:
            first.stop()
            second.stop()

    def test_the_page_follows_an_address_it_is_given(self):
        page = (_FOLDER / "loading.html").read_text()
        assert "answer.address" in page and "window.location.replace(answer.address)" in page


class TestWhenPythonHasGone:
    """An icon whose Python has been removed must still say something."""

    def _copy(self, tmp_path):
        """open-sandbox.sh in a package of its own, whose start.py only records how it ran."""
        folder = tmp_path / "plugins" / "webui" / "launcher"
        folder.mkdir(parents=True)
        script = folder / "open-sandbox.sh"
        script.write_text((_FOLDER / "open-sandbox.sh").read_text())
        record = tmp_path / "ran.txt"
        (tmp_path / "start.py").write_text(
            "import sys\nopen(%r, 'w').write(sys.executable + ' ' + ' '.join(sys.argv[1:]))\n"
            % str(record))
        return script, record

    @pytest.mark.skipif(sys.platform == "win32", reason="a script for Mac and Linux")
    def test_the_python_the_icon_was_made_with_comes_first(self, tmp_path):
        script, record = self._copy(tmp_path)
        subprocess.run(["/bin/sh", str(script), sys.executable], check=True, timeout=30)
        assert record.read_text() == sys.executable + " --launch"

    @pytest.mark.skipif(sys.platform == "win32", reason="a script for Mac and Linux")
    def test_a_python_that_has_gone_is_passed_over(self, tmp_path):
        script, record = self._copy(tmp_path)
        subprocess.run(["/bin/sh", str(script), "/removed/python3"], check=True, timeout=30)
        assert record.read_text().endswith(" --launch")

    def test_the_page_explains_when_nothing_filled_it_in(self):
        """open-sandbox.sh opens it straight from disk when there is no Python."""
        page = (_FOLDER / "loading.html").read_text()
        assert '<script type="application/json" id="state">{{STATE}}</script>' in page
        assert "The sandbox needs Python" in page
        assert "python.org" in page

    def test_filled_in_it_does_not(self, launcher):
        import json
        import re

        page = launcher.render_loading_page("Starting the sandbox…", "", watch=True).decode()
        state = re.search(r'id="state">(.*?)</script>', page).group(1)
        assert json.loads(state) == {"watch": True, "log": ""}

    def test_windows_uses_the_python_launcher_when_there_is_one(self, launcher, monkeypatch):
        """pyw.exe finds whichever Python 3 is installed, so an upgrade doesn't break the icon."""
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: r"C:\Windows\pyw.exe")
        program, arguments = launcher.windows_launcher()
        assert program == r"C:\Windows\pyw.exe"
        assert arguments.startswith("-3 ") and arguments.endswith("--launch")

    def test_and_the_python_that_made_it_when_there_is_not(self, launcher, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: None)
        program, arguments = launcher.windows_launcher()
        assert program == launcher.windowless_python()
        assert not arguments.startswith("-3")
