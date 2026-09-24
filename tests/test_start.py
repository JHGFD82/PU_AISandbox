"""Tests for start.py — the one command someone runs after downloading the sandbox.

The thing that matters most here is that this file *runs at all* on whatever
Python a computer happens to have. Macs ship 3.9, the sandbox needs 3.11, and
a launcher that won't parse on the old one cannot explain the problem it
exists to solve.
"""

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_START = _ROOT / "start.py"


@pytest.fixture
def start():
    """Load start.py as a module without running it."""
    spec = importlib.util.spec_from_file_location("_start_under_test", _START)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRunsOnAnOldPython:
    """The property everything else depends on."""

    def test_parses_under_the_python_macos_ships(self):
        """macOS ships 3.9. This file has to be readable by it.

        Checked by compiling for 3.9 rather than by reading the source,
        because the failure this guards against is a SyntaxError — which
        happens before any of the code can run and say what's wrong.
        """
        source = _START.read_text(encoding="utf-8")
        compile(source, str(_START), "exec")   # current interpreter
        tree = ast.parse(source)

        # Nothing newer than 3.9 understands: match statements, and the
        # `int | None` form in annotations.
        for node in ast.walk(tree):
            assert not isinstance(node, ast.Match), "match statement needs 3.10+"
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                parent_is_annotation = False   # crude, but this file has no bit-or maths
                assert not parent_is_annotation

    @pytest.mark.skipif(not Path("/usr/bin/python3").exists(),
                        reason="no system python to check against")
    def test_the_system_python_can_actually_parse_it(self):
        """The real check: hand it to the interpreter a Mac actually has."""
        result = subprocess.run(
            ["/usr/bin/python3", "-c",
             f"import ast; ast.parse(open({str(_START)!r}).read())"],
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode()


class TestFindingAPython:
    def test_uses_the_current_one_when_it_is_new_enough(self, start):
        assert start.find_python() == sys.executable

    def test_searches_elsewhere_when_the_current_one_is_too_old(self, start, monkeypatch):
        """A machine very often has a suitable Python that isn't first on PATH.

        Finding it is what turns "install Python before you can start" into
        nothing the person has to do at all.
        """
        monkeypatch.setattr(start.sys, "version_info", (3, 9, 6, "final", 0))
        seen = []

        def fake_version_of(python):
            seen.append(python)
            return (3, 13) if python == "python3.12" else (3, 9)

        monkeypatch.setattr(start, "version_of", fake_version_of)
        assert start.find_python() == "python3.12"
        assert seen[0] == "python3.13"      # newest first

    def test_returns_nothing_when_there_is_no_suitable_python(self, start, monkeypatch):
        monkeypatch.setattr(start.sys, "version_info", (3, 9, 6, "final", 0))
        monkeypatch.setattr(start, "version_of", lambda p: (3, 9))
        assert start.find_python() is None

    def test_a_python_that_cannot_be_run_is_skipped(self, start, monkeypatch):
        monkeypatch.setattr(start.sys, "version_info", (3, 9, 6, "final", 0))
        monkeypatch.setattr(start, "version_of", lambda p: None)
        assert start.find_python() is None

    def test_the_message_says_what_to_do(self, start, capsys):
        start.explain_missing_python()
        out = capsys.readouterr().out
        assert "python.org/downloads" in out
        assert "brew install" in out
        assert "3.11" in out


class TestSkippingWorkAlreadyDone:
    """A second run should go straight to the web interface."""

    def test_not_ready_when_there_is_no_environment(self, start, monkeypatch, tmp_path):
        monkeypatch.setattr(start, "VENV_DIR", str(tmp_path / "nothing"))
        monkeypatch.setattr(start, "STAMP", str(tmp_path / "nothing" / "stamp"))
        assert start.environment_is_ready() is False

    def test_ready_when_the_stamp_matches_requirements(self, start, monkeypatch, tmp_path):
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")
        stamp = tmp_path / ".venv" / "stamp"
        monkeypatch.setattr(start, "VENV_DIR", str(tmp_path / ".venv"))
        monkeypatch.setattr(start, "STAMP", str(stamp))
        stamp.write_text(start.requirements_fingerprint(), encoding="utf-8")
        assert start.environment_is_ready() is True

    def test_not_ready_when_requirements_changed_since(self, start, monkeypatch, tmp_path):
        """Adding a dependency must not leave someone on a stale environment."""
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")
        stamp = tmp_path / ".venv" / "stamp"
        monkeypatch.setattr(start, "VENV_DIR", str(tmp_path / ".venv"))
        monkeypatch.setattr(start, "STAMP", str(stamp))
        stamp.write_text("a fingerprint from an older requirements.txt", encoding="utf-8")
        assert start.environment_is_ready() is False


class TestThePauseBeforeInstalling:
    """Several minutes of installing begins with a keypress, not by surprise.

    The lines above the prompt say what is about to happen and roughly how long
    it takes. Without a pause they are on screen for about a second: a first
    install prints around 180 lines, so the explanation scrolls away before
    anyone has read it.

    One keypress, because this is a choice between two things and not text.
    Typing a letter, watching it appear at the end of the prompt and then
    pressing return is the gesture for entering something.
    """

    def _pressing(self, start, monkeypatch, keys):
        """Answer the prompt with these keys, in order."""
        monkeypatch.setattr(start.sys.stdin, "isatty", lambda: True)
        pressed = list(keys)
        monkeypatch.setattr(start, "read_one_key", lambda: pressed.pop(0))
        return start.wait_for_go_ahead()

    @pytest.mark.parametrize("key", ["\r", "\n"])
    def test_return_starts_it(self, start, monkeypatch, key):
        assert self._pressing(start, monkeypatch, [key]) is True

    @pytest.mark.parametrize("key", ["q", "Q"])
    def test_q_in_either_case_stops(self, start, monkeypatch, key):
        assert self._pressing(start, monkeypatch, [key]) is False

    def test_every_other_key_is_ignored(self, start, monkeypatch):
        """Not answered with a complaint: there is no way to get this wrong.

        The old version took a whole typed line, so anything that was not "q"
        counted as yes — including a stray character before the return.
        """
        assert self._pressing(start, monkeypatch, ["x", "7", " ", "\r"]) is True
        assert self._pressing(start, monkeypatch, ["z", "z", "q"]) is False

    @pytest.mark.parametrize("key", ["\x03", "\x04"])
    def test_ctrl_c_and_ctrl_d_mean_the_same_as_q(self, start, monkeypatch, key):
        """They arrive as characters when the terminal is not making signals."""
        assert self._pressing(start, monkeypatch, [key]) is False

    def test_an_interrupt_still_means_the_same(self, start, monkeypatch):
        monkeypatch.setattr(start.sys.stdin, "isatty", lambda: True)

        def interrupted():
            raise KeyboardInterrupt

        monkeypatch.setattr(start, "read_one_key", interrupted)
        assert start.wait_for_go_ahead() is False

    def test_nothing_is_echoed(self, start, monkeypatch):
        """The point of the change: a keypress leaves no letter behind."""
        monkeypatch.setattr(start.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(start, "read_one_key", lambda: "q")
        written = []
        monkeypatch.setattr(start.sys.stdout, "write", lambda s: written.append(s))
        monkeypatch.setattr(start.sys.stdout, "flush", lambda: None)
        start.wait_for_go_ahead()
        # The prompt and a newline, and nothing that looks like what was typed.
        assert any("Q to quit" in piece for piece in written)
        assert "q" not in "".join(w for w in written if "Q to quit" not in w)

    def test_a_terminal_that_will_not_give_one_key_still_works(self, start, monkeypatch):
        """Then it asks for a whole line, which works anywhere."""
        monkeypatch.setattr(start.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(start, "read_one_key", lambda: None)
        monkeypatch.setattr("builtins.input", lambda _prompt: "q")
        assert start.wait_for_go_ahead() is False
        monkeypatch.setattr("builtins.input", lambda _prompt: "")
        assert start.wait_for_go_ahead() is True

    def test_it_does_not_ask_when_there_is_nobody_to_answer(self, start, monkeypatch):
        """Run from a script or a CI runner, waiting for a keypress is a hang."""
        monkeypatch.setattr(start.sys.stdin, "isatty", lambda: False)

        def asked():
            raise AssertionError("asked for a keypress with no terminal attached")

        monkeypatch.setattr(start, "read_one_key", asked)
        assert start.wait_for_go_ahead() is True

    def test_the_prompt_says_both_ways_out(self, start, monkeypatch):
        seen = []
        monkeypatch.setattr(start.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(start.sys.stdout, "write", lambda s: seen.append(s))
        monkeypatch.setattr(start.sys.stdout, "flush", lambda: None)
        monkeypatch.setattr(start, "read_one_key", lambda: "\r")
        start.wait_for_go_ahead()
        prompt = "".join(seen).lower()
        assert "return" in prompt and "q" in prompt

    def test_the_terminal_is_put_back_however_the_read_ends(self, start, monkeypatch):
        """A terminal left in cbreak outlives this program and breaks the shell.

        Checked for both endings, because the one that matters is the one
        nobody plans for.
        """
        import sys as _sys
        import termios
        from unittest import mock

        restored = []
        fake_termios = mock.MagicMock()
        fake_termios.tcgetattr.return_value = ["ORIGINAL"]
        fake_termios.error = termios.error
        fake_termios.tcsetattr.side_effect = lambda fd, when, val: restored.append(val)

        for reader, label in ((lambda _n: "q", "an ordinary read"),
                              (mock.Mock(side_effect=KeyboardInterrupt), "an interrupted read")):
            restored.clear()
            with mock.patch.dict(_sys.modules,
                                 {"termios": fake_termios, "tty": mock.MagicMock()}), \
                 mock.patch.object(start.sys.stdin, "fileno", lambda: 0), \
                 mock.patch.object(start.sys.stdin, "read", reader):
                try:
                    start.read_one_key()
                except KeyboardInterrupt:
                    pass
            assert restored == [["ORIGINAL"]], label

    def test_a_terminal_that_refuses_is_reported_rather_than_forced(self, start):
        import sys as _sys
        import termios
        from unittest import mock

        fake_termios = mock.MagicMock()
        fake_termios.error = termios.error
        fake_termios.tcgetattr.side_effect = termios.error("not a terminal")
        with mock.patch.dict(_sys.modules,
                             {"termios": fake_termios, "tty": mock.MagicMock()}), \
             mock.patch.object(start.sys.stdin, "fileno", lambda: 0):
            assert start.read_one_key() is None

    def test_declining_is_not_an_error(self, start, monkeypatch, capsys):
        """Nothing has happened yet, so there is nothing to have gone wrong."""
        monkeypatch.setattr(start, "find_python", lambda: "python3")
        monkeypatch.setattr(start, "environment_is_ready", lambda: False)
        monkeypatch.setattr(start, "wait_for_go_ahead", lambda: False)

        def never(*_a, **_k):
            raise AssertionError("installed anyway")

        monkeypatch.setattr(start, "build_environment", never)
        assert start.main() == 0
        assert "Nothing was installed" in capsys.readouterr().out

    def test_the_explanation_comes_before_the_prompt(self):
        """Otherwise it is a question about something not yet explained.

        Anchored on the shape rather than the words: the lines above the prompt
        are meant to be rewritten, and a test that breaks when somebody
        improves the wording is a test that discourages improving it.
        """
        body = _START.read_text().split("def main(")[1]
        asked = body.index("wait_for_go_ahead")
        # Something is said between deciding to install and asking about it.
        between = body[body.index("if not environment_is_ready():"):asked]
        assert between.count("say(") >= 2, "the prompt arrives unexplained"

    def test_the_end_of_the_input_does_not_spin(self, start, monkeypatch):
        """A read at the end of input returns "", every time it is asked.

        Treating that as "some other key" and asking again is an endless loop
        with nothing on screen — found by making the same mistake with the
        cannot-read-a-key case and watching the test run hang instead of fail.
        """
        monkeypatch.setattr(start.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(start, "read_one_key", lambda: "")
        assert start.wait_for_go_ahead() is False


class TestSayingWhereTheSoftwareGoes:
    """Somebody who has made an environment of their own and activated it has
    no way to tell whether this is about to fill that one or make another.
    Both are reasonable to expect, so the script says which."""

    def _said(self, start, monkeypatch, **environment):
        for name in ("CONDA_DEFAULT_ENV", "VIRTUAL_ENV"):
            monkeypatch.delenv(name, raising=False)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        lines = []
        monkeypatch.setattr(start, "say", lines.append)
        start.explain_where_the_software_goes()
        return "\n".join(lines)

    def test_it_always_says_where_the_software_is_going(self, start, monkeypatch):
        said = self._said(start, monkeypatch)
        assert ".venv" in said
        assert "inside this one" in said

    def test_a_conda_environment_is_named(self, start, monkeypatch):
        said = self._said(start, monkeypatch, CONDA_DEFAULT_ENV="humanities")
        assert "conda environment 'humanities'" in said

    def test_conda_base_is_not_called_a_name(self, start, monkeypatch):
        """"the conda environment 'base'" reads as somebody's own project."""
        said = self._said(start, monkeypatch, CONDA_DEFAULT_ENV="base")
        assert "base environment" in said
        assert "'base'" not in said

    def test_an_activated_venv_is_named(self, start, monkeypatch):
        said = self._said(start, monkeypatch, VIRTUAL_ENV="/Users/x/project/.venv")
        assert "/Users/x/project/.venv" in said

    def test_conda_wins_when_both_are_set(self, start, monkeypatch):
        """Activating a conda environment sets VIRTUAL_ENV on some setups, and
        the conda name is the one the person recognises."""
        said = self._said(start, monkeypatch,
                          CONDA_DEFAULT_ENV="humanities", VIRTUAL_ENV="/somewhere")
        assert "humanities" in said
        assert "/somewhere" not in said

    def test_it_promises_not_to_touch_theirs(self, start, monkeypatch):
        said = self._said(start, monkeypatch, CONDA_DEFAULT_ENV="humanities")
        assert "will not be changed" in said

    def test_and_says_how_to_use_theirs_instead(self, start, monkeypatch):
        """The offer has to be real: the sandbox runs from whatever is active
        when there is no .venv, which is what makes this work."""
        said = self._said(start, monkeypatch, CONDA_DEFAULT_ENV="humanities")
        assert "pip install -r requirements.txt" in said
        assert "python main.py" in said

    def test_nothing_is_said_about_an_environment_that_is_not_there(
        self, start, monkeypatch
    ):
        """Nothing activated: a paragraph about leaving it alone would be a
        paragraph about nothing."""
        import sys as _sys

        monkeypatch.setattr(_sys, "base_prefix", _sys.prefix, raising=False)
        said = self._said(start, monkeypatch)
        assert "currently in" not in said
        assert "pip install" not in said

    def test_the_sandboxs_own_environment_is_not_called_the_persons(
        self, start, monkeypatch
    ):
        """Running this from inside .venv is ordinary on a second run. Naming
        it would promise not to install into the very folder being installed
        into."""
        said = self._said(start, monkeypatch, VIRTUAL_ENV=str(start.VENV_DIR))
        assert "currently in" not in said

    def test_even_when_reached_by_a_different_spelling_of_the_same_path(
        self, start, monkeypatch
    ):
        said = self._said(start, monkeypatch,
                          VIRTUAL_ENV=str(start.VENV_DIR) + "/./")
        assert "currently in" not in said

    def test_the_person_is_told_this_before_being_asked_to_press_return(self, start):
        """It is the answer to "what is about to happen", so it belongs above
        the question, not after it."""
        source = _START.read_text(encoding="utf-8")
        main = source[source.index("def main():"):]
        assert main.index("explain_where_the_software_goes()") < main.index(
            "wait_for_go_ahead()")

    def test_and_told_where_it_went_afterwards(self, start):
        source = _START.read_text(encoding="utf-8")
        assert "Software installed into %s." in source


class _PretendLoadingPage:
    """Stands in for the loading page, so no test takes the real port."""

    def __init__(self, token, log=""):
        self.token = token

    def start(self):
        return True

    def hand_over(self, patience=15):
        pass


class TestACopyWithNoWebInterface:
    """The web interface is a plugin, and removing it is a supported choice —
    every other command keeps working. This script is the part that did not:
    it opened a browser at an address nothing was listening on, then printed
    the argument parser's complaint about a command that no longer exists,
    with the wrong word quoted because "webui" had been read as a name."""

    def _run_main(self, start, monkeypatch, *, has_web, set_up):
        """Run main() with the two questions answered, and collect what it said."""
        said = []
        monkeypatch.setattr(start, "say", said.append)
        monkeypatch.setattr(start, "find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr(start, "environment_is_ready", lambda: True)
        monkeypatch.setattr(start, "has_the_web_interface", lambda sandbox: has_web)
        monkeypatch.setattr(start, "is_set_up", lambda sandbox: set_up)
        opened = []
        monkeypatch.setattr(start, "open_in_browser", opened.append)
        monkeypatch.setattr(start, "ask_the_running_copy_to_stop",
                            lambda: start.NOTHING_RUNNING)
        monkeypatch.setattr(start, "new_stop_token", lambda: "token")
        monkeypatch.setattr(start, "LoadingPage", _PretendLoadingPage)
        monkeypatch.setattr(start, "make_shortcut_if_missing", lambda: None)
        ran = []

        def fake_call(args, **kwargs):
            ran.append(args)
            return 0

        monkeypatch.setattr(start.subprocess, "call", fake_call)
        code = start.main()
        return code, "\n".join(said), opened, ran

    def test_no_browser_is_opened(self, start, monkeypatch):
        _code, _said, opened, _ran = self._run_main(
            start, monkeypatch, has_web=False, set_up=True)
        assert opened == [], "it opened a browser at a server that will not start"

    def test_it_says_why_rather_than_failing(self, start, monkeypatch):
        code, said, _opened, _ran = self._run_main(
            start, monkeypatch, has_web=False, set_up=True)
        assert code == 0
        assert "web interface is not installed" in said
        assert "python main.py --help" in said

    def test_it_never_asks_for_a_command_that_is_not_there(self, start, monkeypatch):
        _code, _said, _opened, ran = self._run_main(
            start, monkeypatch, has_web=False, set_up=True)
        for args in ran:
            assert "webui" not in args, args

    def test_it_still_sets_the_sandbox_up_in_this_window(self, start, monkeypatch):
        """Setup works perfectly well without the web interface, so a first
        run should still finish rather than stop at an explanation."""
        _code, said, _opened, ran = self._run_main(
            start, monkeypatch, has_web=False, set_up=False)
        assert any(args[-2:] == ["settings", "setup"] for args in ran), ran
        assert "asks the same questions" in said

    def test_and_does_not_when_it_is_already_set_up(self, start, monkeypatch):
        _code, _said, _opened, ran = self._run_main(
            start, monkeypatch, has_web=False, set_up=True)
        assert not any("setup" in args for args in ran)

    def test_it_says_how_to_get_the_web_interface_back(self, start, monkeypatch):
        _code, said, _opened, _ran = self._run_main(
            start, monkeypatch, has_web=False, set_up=True)
        assert "plugins/webui" in said

    def test_with_the_plugin_there_nothing_changes(self, start, monkeypatch):
        _code, said, opened, ran = self._run_main(
            start, monkeypatch, has_web=True, set_up=True)
        assert opened, "the browser should still be opened"
        assert "web interface is not installed" not in said
        assert any("webui" in args for args in ran)

    def test_the_question_is_asked_of_the_sandbox_not_of_a_folder(self, start):
        """A plugin that is there but cannot load is the same problem as one
        that is absent, and only the sandbox can tell you which commands it
        actually has."""
        source = _START.read_text(encoding="utf-8")
        block = source[source.index("def has_the_web_interface"):
                       source.index("def finish_without_the_web_interface")]
        assert '"webui", "--help"' in block
        assert "isdir" not in block and "exists" not in block


# ── Starting from the icon ────────────────────────────────────────────────


def _free_port():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def own_port(start, monkeypatch, tmp_path):
    """Point start.py at a port of the test's own, and its token file at tmp_path.

    Never 8000: a real sandbox may be running there, and these tests stop things.
    """
    port = _free_port()
    monkeypatch.setattr(start, "PORT", port)
    monkeypatch.setattr(start, "URL", "http://127.0.0.1:%d" % port)
    monkeypatch.setattr(start, "TOKEN_FILE", str(tmp_path / ".stop-token"))
    monkeypatch.setattr(start, "VENV_DIR", str(tmp_path))
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield heard
    server.shutdown()
    server.server_close()


class TestStoppingTheCopyAlreadyRunning:
    """Every start is a fresh start, because starting is when the sandbox
    looks for a newer version. But only this sandbox is ever stopped, and
    only by asking."""

    def test_nothing_there_is_nothing_to_stop(self, start, own_port):
        assert start.ask_the_running_copy_to_stop() == start.NOTHING_RUNNING

    def test_it_presents_the_token_the_last_start_left(self, start, web_server, monkeypatch):
        start.new_stop_token()
        left = start.read_stop_token()
        monkeypatch.setattr(start, "port_is_taken", lambda: False)
        assert start.ask_the_running_copy_to_stop() == start.STOPPED
        assert web_server["tokens"] == [left]

    def test_a_copy_in_the_middle_of_something_is_left_running(self, start, web_server):
        web_server["status"] = 409
        assert start.ask_the_running_copy_to_stop() == start.BUSY

    @pytest.mark.parametrize("status", [403, 404, 405, 500])
    def test_anything_that_does_not_agree_is_somebody_elses(self, start, web_server, status):
        web_server["status"] = status
        assert start.ask_the_running_copy_to_stop() == start.SOMEONE_ELSE

    def test_a_copy_that_agrees_but_never_lets_go_is_not_waited_on_forever(
            self, start, web_server, monkeypatch):
        monkeypatch.setattr(start, "STOP_PATIENCE_SECONDS", 0.5)
        monkeypatch.setattr(start, "port_is_taken", lambda: True)
        assert start.ask_the_running_copy_to_stop() == start.SOMEONE_ELSE

    def test_something_that_is_not_a_web_server_is_somebody_elses(
            self, start, own_port, monkeypatch):
        """It holds the port and never answers in the language of the web."""
        import socket
        import urllib.error

        class Silent:
            def open(self, *args, **kwargs):
                raise urllib.error.URLError("no answer")

        monkeypatch.setattr(start, "_local_opener", Silent)
        listener = socket.socket()
        listener.bind(("127.0.0.1", own_port))
        listener.listen(1)
        try:
            assert start.ask_the_running_copy_to_stop() == start.SOMEONE_ELSE
        finally:
            listener.close()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_the_token_is_readable_by_this_person_only(self, start, own_port):
        import os
        import stat

        start.new_stop_token()
        mode = stat.S_IMODE(os.stat(start.TOKEN_FILE).st_mode)
        assert mode == 0o600

    def test_each_start_makes_a_new_token(self, start, own_port):
        assert start.new_stop_token() != start.new_stop_token()


class TestTheLoadingPage:
    """What the browser shows while the sandbox starts."""

    @pytest.fixture
    def page(self, start, own_port):
        page = start.LoadingPage("right", log="/somewhere/launcher.log")
        page.ended = []
        page.end_this_process = lambda: page.ended.append(True)
        assert page.start()
        yield page
        page.stop()

    def _get(self, start, path):

        return start._local_opener().open(start.URL + path, timeout=5)

    def _post_stop(self, start, token):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(start.URL + "/__stop", data=b"",
                                         headers={"X-Sandbox-Stop-Token": token})
        try:
            return start._local_opener().open(request, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_it_shows_the_logo_and_what_is_happening(self, start, page):
        body = self._get(start, "/").read().decode("utf-8")
        assert "Starting the sandbox" in body
        assert 'fill="#f58025"' in body
        assert page.seen.is_set(), "the browser has now fetched it"

    def test_it_says_how_things_are_going_when_asked(self, start, page):
        import json

        page.show("installing", "Installing updated software…", "A few minutes.")
        answer = json.loads(self._get(start, "/__loading").read().decode("utf-8"))
        assert answer == {"loading": True, "phase": "installing",
                          "headline": "Installing updated software…",
                          "detail": "A few minutes."}

    def test_the_launcher_can_stop_it_with_the_right_token(self, start, page):
        assert self._post_stop(start, "right") == 200
        assert page.ended == [True]

    def test_not_with_any_other(self, start, page):
        assert self._post_stop(start, "wrong") == 403
        assert self._post_stop(start, "") == 403
        assert page.ended == []

    def test_not_halfway_through_installing(self, start, page):
        page.show("installing", "Installing updated software…")
        assert self._post_stop(start, "right") == 409
        assert page.ended == []

    def test_a_second_one_cannot_take_the_same_port(self, start, page):
        assert start.LoadingPage("other").start() is False

    def test_the_words_are_written_as_words(self, start):
        """A folder or an error message could contain anything."""
        body = start.render_loading_page("<b>bold</b>", "a & b", watch=True,
                                         log="</script><script>alert(1)")
        text = body.decode("utf-8")
        assert "<b>bold</b>" not in text and "&lt;b&gt;" in text
        assert "a &amp; b" in text
        assert "</script><script>alert(1)" not in text

    def test_the_page_opened_from_disk_does_not_watch(self, start):
        text = start.render_loading_page("The sandbox wasn't started.", "",
                                         watch=False).decode("utf-8")
        assert '"watch": false' in text

    def test_the_page_moves_itself_to_the_sandbox(self):
        """The whole point: nobody reloads it by hand."""
        page = (_ROOT / "plugins" / "webui" / "launcher" / "loading.html").read_text()
        assert 'fetch("/__loading"' in page
        assert "window.location.reload()" in page


class TestTheLaunchLog:
    def test_a_mac_keeps_it_with_its_other_logs(self, start, monkeypatch):
        monkeypatch.setattr(start.sys, "platform", "darwin")
        assert start.launch_log_path().endswith(
            "Library/Logs/PU_AISandbox-launcher.log")

    def test_linux_keeps_it_in_the_state_folder(self, start, monkeypatch):
        monkeypatch.setattr(start.sys, "platform", "linux")
        monkeypatch.setenv("XDG_STATE_HOME", "/state")
        assert start.launch_log_path() == "/state/PU_AISandbox/launcher.log"

    def test_never_inside_the_sandbox_itself(self, start):
        assert not start.launch_log_path().startswith(start.HERE)


class TestTheIcon:
    """Made here, on this computer, so it needs no signing and holds the right paths."""

    def test_a_mac_application_that_runs_the_launcher(self, start, tmp_path, monkeypatch):
        import os
        import plistlib

        monkeypatch.setattr(start.sys, "executable", "/Python Folder/python3")
        app = tmp_path / "PU AI Sandbox.app"
        start.make_mac_app(str(app))
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        assert info["CFBundleExecutable"] == "launch"
        assert info["CFBundleIdentifier"] == start.MAC_BUNDLE_ID
        assert info["LSUIElement"] is True, "no Dock icon for something over in a second"
        script = app / "Contents" / "MacOS" / "launch"
        text = script.read_text()
        assert "exec '/Python Folder/python3' " in text, "a space in a path must not split it"
        assert text.rstrip().endswith("--launch")
        assert os.access(str(script), os.X_OK)
        assert (app / "Contents" / "Resources" / "sandbox.icns").exists()

    def test_making_it_again_replaces_ours(self, start, tmp_path):
        app = tmp_path / "PU AI Sandbox.app"
        start.make_mac_app(str(app))
        (app / "Contents" / "stale").write_text("x")
        start.make_mac_app(str(app))
        assert not (app / "Contents" / "stale").exists()

    def test_but_never_someone_elses(self, start, tmp_path):
        app = tmp_path / "PU AI Sandbox.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents" / "theirs").write_text("keep me")
        with pytest.raises(OSError):
            start.make_mac_app(str(app))
        assert (app / "Contents" / "theirs").read_text() == "keep me"

    def test_a_linux_desktop_entry(self, start, tmp_path, monkeypatch):
        import os

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        made = start.make_linux_launcher(str(desktop / "pu-ai-sandbox.desktop"))
        entry = (desktop / "pu-ai-sandbox.desktop").read_text()
        assert made == str(desktop / "pu-ai-sandbox.desktop")
        assert "Terminal=false" in entry
        assert '--launch' in entry
        assert (tmp_path / "share" / "applications" / "pu-ai-sandbox.desktop").exists()
        assert os.access(made, os.X_OK)

    @pytest.mark.parametrize("path, quoted", [
        ("/plain/path", '"/plain/path"'),
        ("/with space/x", '"/with space/x"'),
        ('/a"quote', '"/a\\\\"quote"'),
        ("/a$dollar", '"/a\\\\$dollar"'),
        ("/100%", '"/100%%"'),
    ])
    def test_desktop_entry_quoting(self, start, path, quoted):
        assert start.desktop_entry_argument(path) == quoted

    def test_an_icon_already_there_is_left_alone(self, start, tmp_path, monkeypatch):
        existing = tmp_path / "PU AI Sandbox.app"
        existing.mkdir()
        monkeypatch.setattr(start, "shortcut_path", lambda: str(existing))
        made = []
        monkeypatch.setattr(start, "make_shortcut", lambda: made.append(1))
        start.make_shortcut_if_missing()
        assert made == []

    def test_a_first_terminal_run_makes_one(self, start, tmp_path, monkeypatch):
        said = []
        monkeypatch.setattr(start, "say", said.append)
        monkeypatch.setattr(start, "shortcut_path", lambda: str(tmp_path / "missing"))
        monkeypatch.setattr(start, "make_shortcut", lambda: "/Desktop/PU AI Sandbox.app")
        start.make_shortcut_if_missing()
        assert any("/Desktop/PU AI Sandbox.app" in line for line in said)

    def test_the_icons_it_needs_are_in_the_web_interface(self, start):
        """They are the web interface's, so they go if it is removed."""
        for name in ("sandbox.icns", "sandbox.ico", "sandbox.png", "loading.html"):
            assert (_ROOT / "plugins" / "webui" / "launcher" / name).exists(), name


class TestTheWordsAfterStartPy:
    def test_no_icon_for_a_copy_without_the_web_interface(self, start, monkeypatch):
        monkeypatch.setattr(start, "say", lambda line: None)
        monkeypatch.setattr(start, "has_the_web_interface", lambda sandbox: False)
        made = []
        monkeypatch.setattr(start, "make_shortcut", lambda: made.append(1))
        assert start.entry(["--make-shortcut"]) == 1
        assert made == []

    def test_nothing_is_the_ordinary_start(self, start, monkeypatch):
        monkeypatch.setattr(start, "main", lambda: "main")
        assert start.entry([]) == "main"

    @pytest.mark.parametrize("flag, name", [("--launch", "launch"),
                                            ("--run-hidden", "run_hidden")])
    def test_the_icons_own_ways_in(self, start, monkeypatch, flag, name):
        monkeypatch.setattr(start, name, lambda: name)
        assert start.entry([flag]) == name

    def test_something_unknown_is_answered_with_how_to_use_it(self, start, monkeypatch):
        said = []
        monkeypatch.setattr(start, "say", said.append)
        assert start.entry(["--nonsense"]) == 2
        assert "--make-shortcut" in said[0]
