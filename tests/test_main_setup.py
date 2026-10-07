"""Tests for main.py's checks before any command runs: the Python version, and first-time setup.

main.py does these as it is loaded, so each test loads a fresh copy of it, with
the hand-over to the sandbox's own Python already marked as done (that is
tested in test_main_finds_its_venv.py) and ``__name__`` not ``"__main__"``, so
no command actually runs.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from src import paths

_MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _load_main(monkeypatch):
    monkeypatch.setenv("PU_AISANDBOX_PYTHON_CHOSEN", "1")
    spec = importlib.util.spec_from_file_location("_main_under_test", _MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestThePythonVersion:
    def test_an_old_python_is_told_what_to_do_rather_than_failing_on_an_import(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "version_info", (3, 9, 6, "final", 0))
        with pytest.raises(SystemExit) as stopped:
            _load_main(monkeypatch)
        assert stopped.value.code == 1
        err = capsys.readouterr().err
        assert "needs Python 3.11 or newer" in err
        assert "You are running: Python 3.9.6" in err
        assert "python.org/downloads" in err

    def test_a_new_enough_python_carries_on(self, monkeypatch):
        assert hasattr(_load_main(monkeypatch), "_set_up_if_needed")


class TestFirstTimeSetup:
    @pytest.fixture
    def main(self, monkeypatch):
        module = _load_main(monkeypatch)
        monkeypatch.setattr(paths, "is_installed", lambda: False)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        asked = []
        monkeypatch.setattr("src.setup_prompts.run_interactive_setup", lambda: asked.append(True))
        module.asked = asked
        return module

    def _with_argv(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, "argv", ["main.py", *argv])

    def test_a_copy_already_set_up_goes_straight_on(self, main, monkeypatch):
        monkeypatch.setattr(paths, "is_installed", lambda: True)
        self._with_argv(monkeypatch, "heller", "usage", "report")
        assert main._set_up_if_needed() is False
        assert main.asked == []

    def test_help_is_never_held_up_by_setup(self, main, monkeypatch):
        self._with_argv(monkeypatch, "--help")
        assert main._set_up_if_needed() is False

    @pytest.mark.parametrize("argv", [["settings", "setup"], ["webui", "setup"], ["--verbose", "settings", "setup"]])
    def test_asking_for_setup_is_not_answered_with_setup_first(self, main, monkeypatch, argv):
        self._with_argv(monkeypatch, *argv)
        assert main._set_up_if_needed() is False
        assert main.asked == []

    def test_any_other_command_asks_the_setup_questions_first(self, main, monkeypatch, capsys):
        self._with_argv(monkeypatch, "heller", "usage", "report")
        assert main._set_up_if_needed() is True
        assert main.asked == [True]
        assert "run your command again" in capsys.readouterr().out

    def test_with_nobody_at_the_keyboard_it_says_what_to_run(self, main, monkeypatch, capsys):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        self._with_argv(monkeypatch, "heller", "usage", "report")
        with pytest.raises(SystemExit) as stopped:
            main._set_up_if_needed()
        assert stopped.value.code == 1
        assert "python main.py settings setup" in capsys.readouterr().err
        assert main.asked == []
