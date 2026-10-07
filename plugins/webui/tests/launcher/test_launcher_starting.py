"""Tests for the launcher's starting of the sandbox: from a terminal, from the icon, and what each says when it cannot.

Nothing here takes a port, opens a browser or starts a process. The loading
page, the browser, the port and the processes are all stand-ins that record
what they were asked, so each test checks one decision: given what is already
running and what this copy needs, what happens next, and what the person is
told.
"""

import sys

import pytest


class _Page:
    """Stands in for the loading page; every page made is kept, in order."""

    made = []

    def __init__(self, token, log="", port=None):
        self.token, self.log, self.port = token, log, port
        self.shown, self.events = [], []
        self.starts = True
        _Page.made.append(self)

    def start(self):
        return self.starts

    def show(self, phase, headline, detail=""):
        self.shown.append((phase, headline))

    def hand_over(self, patience=15):
        self.events.append("hand_over")

    def move_to(self, other, patience=15):
        self.events.append(("move_to", other.port))

    def linger(self, minutes=10):
        self.events.append("linger")


@pytest.fixture
def world(launcher, start, monkeypatch, tmp_path):
    """The launcher with everything outside it replaced. Returns what it said and opened."""
    _Page.made = []
    said, opened = [], []
    monkeypatch.setattr(start, "say", said.append)
    monkeypatch.setattr(launcher, "LoadingPage", _Page)
    monkeypatch.setattr(launcher, "open_in_browser", opened.append)
    monkeypatch.setattr(launcher, "settle_the_port", lambda: True)
    monkeypatch.setattr(launcher, "ask_the_running_copy_to_stop", lambda: launcher.NOTHING_RUNNING)
    monkeypatch.setattr(launcher, "new_stop_token", lambda: "token")
    monkeypatch.setattr(launcher, "launch_log_path", lambda: str(tmp_path / "logs" / "launcher.log"))
    monkeypatch.setattr(start, "is_set_up", lambda sandbox: True)
    launcher.use_port(8765)

    class World:
        pass

    w = World()
    w.said, w.opened, w.launcher, w.start = said, opened, launcher, start
    w.text = lambda: "\n".join(said)
    return w


class TestFromATerminal:
    @pytest.fixture
    def ran(self, world, monkeypatch):
        calls = []
        monkeypatch.setattr(world.launcher, "run_the_web_interface",
                            lambda page, env, needs_setup, in_a_terminal:
                            calls.append((needs_setup, in_a_terminal, env)) or 0)
        return calls

    def test_no_free_port_is_explained(self, world, ran, monkeypatch):
        monkeypatch.setattr(world.launcher, "settle_the_port", lambda: False)
        assert world.launcher.open_from_a_terminal() == 1
        assert world.launcher.explain_no_port() in world.said
        assert ran == []

    def test_a_copy_in_the_middle_of_something_is_opened_rather_than_stopped(self, world, ran, monkeypatch):
        monkeypatch.setattr(world.launcher, "ask_the_running_copy_to_stop", lambda: world.launcher.BUSY)
        assert world.launcher.open_from_a_terminal() == 0
        assert world.opened == ["http://127.0.0.1:8765"] and ran == []

    def test_somebody_elses_program_on_the_port_is_explained(self, world, ran, monkeypatch):
        monkeypatch.setattr(world.launcher, "ask_the_running_copy_to_stop", lambda: world.launcher.SOMEONE_ELSE)
        assert world.launcher.open_from_a_terminal() == 1
        assert world.launcher.explain_the_port_is_taken() in world.said

    def test_a_copy_already_running_is_stopped_and_this_one_starts(self, world, ran, monkeypatch):
        monkeypatch.setattr(world.launcher, "ask_the_running_copy_to_stop", lambda: world.launcher.STOPPED)
        assert world.launcher.open_from_a_terminal() == 0
        assert "starts fresh" in world.text() and len(ran) == 1

    def test_a_loading_page_that_cannot_start_is_explained(self, world, ran, monkeypatch):
        class _Refuses(_Page):
            def start(self):
                return False
        monkeypatch.setattr(world.launcher, "LoadingPage", _Refuses)
        assert world.launcher.open_from_a_terminal() == 1
        assert ran == []

    def test_a_copy_set_up_already_opens_the_web_interface(self, world, ran):
        assert world.launcher.open_from_a_terminal() == 0
        assert "Starting the web interface" in world.text()
        assert world.opened == ["http://127.0.0.1:8765"]
        needs_setup, in_a_terminal, env = ran[0]
        assert (needs_setup, in_a_terminal) == (False, True)
        assert "token" in env.values()

    def test_a_new_copy_says_setup_continues_in_the_browser(self, world, ran, monkeypatch):
        monkeypatch.setattr(world.start, "is_set_up", lambda sandbox: False)
        world.launcher.open_from_a_terminal()
        assert "have your API key ready" in world.text()
        assert ran[0][0] is True


class TestRunningTheWebInterface:
    @pytest.fixture
    def calls(self, world, monkeypatch):
        commands = []
        monkeypatch.setattr(world.launcher.subprocess, "call",
                            lambda command, **kw: commands.append(command[2:]) or 0)
        monkeypatch.setattr(world.launcher, "make_shortcut_if_missing", lambda: commands.append("icon"))
        return commands

    def test_setup_runs_first_on_the_same_port_then_the_sandbox(self, world, calls):
        page = _Page("token")
        assert world.launcher.run_the_web_interface(page, {}, needs_setup=True, in_a_terminal=False) == 0
        assert calls == [["webui", "setup", "--port", "8765"], ["webui", "serve", "--port", "8765"]]
        assert page.events == ["hand_over"]

    def test_setup_that_is_not_finished_stops_there(self, world, calls, monkeypatch):
        monkeypatch.setattr(world.launcher.subprocess, "call", lambda command, **kw: 1)
        assert world.launcher.run_the_web_interface(_Page("t"), {}, needs_setup=True, in_a_terminal=False) == 1

    def test_from_a_terminal_it_makes_the_icon_and_says_how_to_stop(self, world, calls):
        world.launcher.run_the_web_interface(_Page("t"), {}, needs_setup=False, in_a_terminal=True)
        assert calls[0] == "icon"
        assert "press Ctrl-C" in world.text()

    def test_ctrl_c_is_a_way_to_stop_not_a_crash(self, world, monkeypatch):
        def interrupted(command, **kw):
            raise KeyboardInterrupt
        monkeypatch.setattr(world.launcher.subprocess, "call", interrupted)
        assert world.launcher.run_the_web_interface(_Page("t"), {}, needs_setup=False, in_a_terminal=False) == 0
        assert "Stopped." in world.said


class TestFromTheIcon:
    """launch(): starts the hidden part, waits for its loading page, then opens the browser."""

    @pytest.fixture
    def problems(self, world, monkeypatch):
        shown = []
        monkeypatch.setattr(world.launcher, "show_a_problem", lambda headline, detail: shown.append(headline))
        monkeypatch.setattr(world.launcher.time, "sleep", lambda seconds: None)
        return shown

    def _child(self, world, monkeypatch, exits=None, started=None):
        class _Child:
            def poll(self):
                return exits

        def popen(command, **kw):
            if started is not None:
                started.append((command, kw))
            return _Child()

        monkeypatch.setattr(world.launcher.subprocess, "Popen", popen)

    def test_no_free_port_is_shown_in_the_browser(self, world, problems, monkeypatch):
        monkeypatch.setattr(world.launcher, "settle_the_port", lambda: False)
        assert world.launcher.launch() == 1
        assert problems == ["The sandbox wasn't started."]

    def test_a_busy_copy_is_simply_opened(self, world, problems, monkeypatch):
        monkeypatch.setattr(world.launcher, "ask_the_running_copy_to_stop", lambda: world.launcher.BUSY)
        assert world.launcher.launch() == 0 and world.opened == ["http://127.0.0.1:8765"]

    def test_somebody_elses_program_on_the_port_is_shown(self, world, problems, monkeypatch):
        monkeypatch.setattr(world.launcher, "ask_the_running_copy_to_stop", lambda: world.launcher.SOMEONE_ELSE)
        assert world.launcher.launch() == 1 and problems == ["The sandbox wasn't started."]

    def test_the_hidden_part_runs_on_its_own_and_logs_where_it_can_be_read(self, world, problems, monkeypatch, tmp_path):
        started = []
        self._child(world, monkeypatch, started=started)
        monkeypatch.setattr(world.launcher, "the_loading_page_answers", lambda: True)
        assert world.launcher.launch() == 0
        command, kw = started[0]
        assert command[-1] == "--run-hidden"
        assert kw["start_new_session"] is True
        assert (tmp_path / "logs").is_dir()
        assert world.opened == ["http://127.0.0.1:8765"]

    def test_one_that_cannot_be_started_is_shown(self, world, problems, monkeypatch):
        def cannot(command, **kw):
            raise OSError("no python")
        monkeypatch.setattr(world.launcher.subprocess, "Popen", cannot)
        assert world.launcher.launch() == 1 and problems == ["The sandbox couldn't be started."]

    def test_one_that_stops_before_its_page_is_up_is_shown(self, world, problems, monkeypatch):
        self._child(world, monkeypatch, exits=1)
        assert world.launcher.launch() == 1
        assert problems == ["The sandbox stopped before it had started."]

    def test_one_that_never_answers_is_given_up_on(self, world, problems, monkeypatch):
        self._child(world, monkeypatch)
        monkeypatch.setattr(world.launcher, "the_loading_page_answers", lambda: False)
        clock = iter([0, 0, 31])
        monkeypatch.setattr(world.launcher.time, "time", lambda: next(clock))
        assert world.launcher.launch() == 1
        assert problems == ["The sandbox is taking too long to start."]


class TestTheHiddenPart:
    """run_hidden(): what a terminal start does, said on the loading page instead."""

    @pytest.fixture
    def hidden(self, world, monkeypatch):
        monkeypatch.setattr(world.start, "find_python", lambda: "/usr/bin/python3.12")
        monkeypatch.setattr(world.start, "environment_is_ready", lambda: True)
        monkeypatch.setattr(world.start, "has_the_web_interface", lambda sandbox: True)
        monkeypatch.setattr(world.launcher, "run_the_web_interface", lambda *a, **k: 0)
        return world

    def _page(self):
        return _Page.made[0]

    def test_a_clean_start_and_stop(self, hidden):
        assert hidden.launcher.run_hidden() == 0
        assert "exit code 0" in hidden.text()

    def test_no_free_port(self, hidden, monkeypatch):
        monkeypatch.setattr(hidden.launcher, "settle_the_port", lambda: False)
        assert hidden.launcher.run_hidden() == 1

    def test_a_loading_page_that_cannot_start(self, hidden, monkeypatch):
        class _Refuses(_Page):
            def start(self):
                return False
        monkeypatch.setattr(hidden.launcher, "LoadingPage", _Refuses)
        assert hidden.launcher.run_hidden() == 1

    def test_no_python_new_enough_is_said_on_the_page_and_left_there(self, hidden, monkeypatch):
        monkeypatch.setattr(hidden.start, "find_python", lambda: None)
        monkeypatch.setattr(hidden.start, "explain_missing_python", lambda: None)
        assert hidden.launcher.run_hidden() == 1
        assert self._page().shown == [("problem", "The sandbox needs a newer Python.")]
        assert self._page().events == ["linger"]

    def test_new_software_is_installed_with_the_page_saying_so(self, hidden, monkeypatch):
        monkeypatch.setattr(hidden.start, "environment_is_ready", lambda: False)
        monkeypatch.setattr(hidden.start, "build_environment", lambda python: True)
        monkeypatch.setattr(hidden.launcher, "port_from_the_sandbox", lambda: 8765)
        assert hidden.launcher.run_hidden() == 0
        assert [phase for phase, _ in self._page().shown] == ["installing", "starting"]

    def test_software_that_cannot_be_installed_is_said_on_the_page(self, hidden, monkeypatch):
        monkeypatch.setattr(hidden.start, "environment_is_ready", lambda: False)
        monkeypatch.setattr(hidden.start, "build_environment", lambda python: False)
        assert hidden.launcher.run_hidden() == 1
        assert self._page().shown[-1] == ("problem", "The software the sandbox needs couldn't be installed.")

    def test_a_port_that_turns_out_different_moves_the_page(self, hidden, monkeypatch):
        monkeypatch.setattr(hidden.start, "environment_is_ready", lambda: False)
        monkeypatch.setattr(hidden.start, "build_environment", lambda python: True)
        monkeypatch.setattr(hidden.launcher, "port_from_the_sandbox", lambda: 9001)
        hidden.launcher.run_hidden()
        assert self._page().events == [("move_to", 9001)]
        assert hidden.launcher.PORT == 9001

    def test_a_copy_without_the_web_interface_says_what_still_works(self, hidden, monkeypatch):
        monkeypatch.setattr(hidden.start, "has_the_web_interface", lambda sandbox: False)
        assert hidden.launcher.run_hidden() == 1
        assert self._page().shown[-1][1] == "The web interface isn't installed in this copy."

    def test_a_sandbox_that_stops_unexpectedly_leaves_a_page_saying_so(self, hidden, monkeypatch):
        monkeypatch.setattr(hidden.launcher, "run_the_web_interface", lambda *a, **k: 3)
        assert hidden.launcher.run_hidden() == 1
        assert _Page.made[-1].shown == [("problem", "The sandbox stopped unexpectedly.")]


class TestShowingAProblemWithNothingRunning:
    def test_the_page_is_written_to_a_file_and_opened_from_there(self, world, monkeypatch, tmp_path):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        world.launcher.show_a_problem("It broke.", "Try again.")
        page = (tmp_path / "PU_AISandbox-problem.html").read_text(encoding="utf-8")
        assert "It broke." in page and "Try again." in page
        assert world.opened[0].startswith("file:") and world.opened[0].endswith("PU_AISandbox-problem.html")
        assert "It broke. Try again." in world.said

    def test_pythonw_is_only_a_windows_question(self, world):
        assert world.launcher.windowless_python() == sys.executable


class TestTheIconsPlace:
    @pytest.mark.parametrize("platform,osname,name", [
        ("darwin", "posix", "PU AI Sandbox.app"),
        ("linux", "posix", "pu-ai-sandbox.desktop"),
    ])
    def test_each_computer_has_its_own_kind_of_icon(self, world, monkeypatch, tmp_path, platform, osname, name):
        monkeypatch.setattr(world.launcher.sys, "platform", platform)
        monkeypatch.setattr(world.launcher, "desktop_folder", lambda: str(tmp_path))
        assert world.launcher.shortcut_path() == str(tmp_path / name)

    def test_linux_asks_where_the_desktop_is(self, world, monkeypatch):
        monkeypatch.setattr(world.launcher.sys, "platform", "linux")
        monkeypatch.setattr(world.launcher.subprocess, "check_output", lambda command, **kw: b"/home/h/Schreibtisch\n")
        assert world.launcher.desktop_folder() == "/home/h/Schreibtisch"

    def test_and_falls_back_on_the_usual_place(self, world, monkeypatch, tmp_path):
        monkeypatch.setattr(world.launcher.sys, "platform", "linux")
        monkeypatch.setenv("HOME", str(tmp_path))

        def no_tool(command, **kw):
            raise OSError("xdg-user-dir not found")
        monkeypatch.setattr(world.launcher.subprocess, "check_output", no_tool)
        assert world.launcher.desktop_folder() == str(tmp_path / "Desktop")

    def test_an_icon_already_there_is_left_alone(self, world, monkeypatch, tmp_path):
        monkeypatch.setattr(world.launcher, "shortcut_path", lambda: str(tmp_path))
        monkeypatch.setattr(world.launcher, "make_shortcut", lambda: pytest.fail("made a second icon"))
        world.launcher.make_shortcut_if_missing()

    def test_a_new_icon_is_announced(self, world, monkeypatch, tmp_path):
        monkeypatch.setattr(world.launcher, "shortcut_path", lambda: str(tmp_path / "missing.app"))
        monkeypatch.setattr(world.launcher, "make_shortcut", lambda: "/Desktop/PU AI Sandbox.app")
        world.launcher.make_shortcut_if_missing()
        assert "    /Desktop/PU AI Sandbox.app" in world.said

    def test_an_icon_that_cannot_be_made_does_not_stop_the_sandbox(self, world, monkeypatch):
        monkeypatch.setattr(world.launcher.sys, "platform", "linux")

        def cannot(target):
            raise OSError("read-only Desktop")
        monkeypatch.setattr(world.launcher, "make_linux_launcher", cannot)
        monkeypatch.setattr(world.launcher, "shortcut_path", lambda: "/Desktop/x.desktop")
        assert world.launcher.make_shortcut() is None
        assert "The sandbox itself is fine" in world.text()


class TestTheWordsAfterStartPy:
    @pytest.mark.parametrize("word,function", [("--launch", "launch"), ("--run-hidden", "run_hidden")])
    def test_each_goes_to_its_own_function(self, world, monkeypatch, word, function):
        monkeypatch.setattr(world.launcher, function, lambda: 7)
        assert world.launcher.entry([word]) == 7

    def test_making_the_icon_says_where_it_went(self, world, monkeypatch):
        monkeypatch.setattr(world.launcher, "make_shortcut", lambda: "/Desktop/PU AI Sandbox.app")
        assert world.launcher.entry(["--make-shortcut"]) == 0
        assert "The sandbox's icon is at /Desktop/PU AI Sandbox.app" in world.said

    def test_an_icon_that_could_not_be_made_exits_with_an_error(self, world, monkeypatch):
        monkeypatch.setattr(world.launcher, "make_shortcut", lambda: None)
        assert world.launcher.entry(["--make-shortcut"]) == 1

    def test_anything_else_is_not_the_launchers_to_answer(self, world):
        assert world.launcher.entry(["--help"]) is None
