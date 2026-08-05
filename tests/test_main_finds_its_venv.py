"""Tests for main.py handing commands to the sandbox's own Python.

start.py builds an environment in .venv and installs everything the sandbox
needs into it. Nothing outside that environment has those packages, so a
command run without activating it used to fail on a missing module naming a
package nobody had heard of. main.py hands over by itself now.

Loaded the way start.py's tests load theirs: the top of the file only, which
is written to run on the Python macOS ships and must keep doing so.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_MAIN = _ROOT / "main.py"
_HANDED_OVER = "PU_AISANDBOX_PYTHON_CHOSEN"


@pytest.fixture
def handover():
    """The handover functions, loaded without running the rest of main.py."""
    source = _MAIN.read_text()
    prefix = source.split("_use_the_sandboxes_own_python()\n")[0]
    namespace: dict = {"__file__": str(_MAIN)}
    exec(compile(prefix, str(_MAIN), "exec"), namespace)
    return namespace


class TestItStillRunsOnTheOldPython:
    """The property the whole thing rests on: the handover has to happen
    before anything modern is parsed, on the Python that shipped with the Mac."""

    def test_the_file_compiles_for_python_3_9(self):
        compile(_MAIN.read_text(), str(_MAIN), "exec", dont_inherit=True)

    def test_a_command_works_from_the_system_python(self):
        """The whole point, end to end, with no environment activated."""
        system = Path("/usr/bin/python3")
        if not system.exists():
            pytest.skip("no system python to try this with")
        done = subprocess.run(
            [str(system), str(_MAIN), "--help"],
            capture_output=True, text=True, cwd=_ROOT,
            env={k: v for k, v in os.environ.items() if k != _HANDED_OVER},
        )
        assert done.returncode == 0, done.stderr
        assert "Princeton University AI Sandbox" in done.stdout


class TestWhenItHandsOver:
    def test_it_does_nothing_when_already_in_that_environment(self, handover, monkeypatch):
        """Somebody who activated it is running that same Python."""
        own = handover["_sandboxes_own_python"]()
        assert own, "this checkout has no .venv to test against"
        monkeypatch.setattr(sys, "executable", own)
        called = []
        monkeypatch.setattr(os, "execv", lambda *a: called.append(a))
        handover["_use_the_sandboxes_own_python"]()
        assert called == []

    def test_it_hands_over_when_run_from_somewhere_else(self, handover, monkeypatch):
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
        monkeypatch.delenv(_HANDED_OVER, raising=False)
        called = []
        monkeypatch.setattr(os, "execv", lambda *a: called.append(a))
        handover["_use_the_sandboxes_own_python"]()
        assert called, "it should have handed the command over"
        program, argv = called[0]
        assert program.endswith("python") or program.endswith("python.exe")
        assert argv[1] == str(_MAIN)

    def test_it_carries_the_command_across(self, handover, monkeypatch):
        """Otherwise the handover would run a different command."""
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
        monkeypatch.delenv(_HANDED_OVER, raising=False)
        monkeypatch.setattr(sys, "argv", ["main.py", "jh43", "usage", "report", "--all-time"])
        called = []
        monkeypatch.setattr(os, "execv", lambda *a: called.append(a))
        handover["_use_the_sandboxes_own_python"]()
        assert called[0][1][2:] == ["jh43", "usage", "report", "--all-time"]

    def test_it_never_hands_over_twice(self, handover, monkeypatch):
        """A second handover is a loop, and a loop here never says anything."""
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
        monkeypatch.setenv(_HANDED_OVER, "1")
        called = []
        monkeypatch.setattr(os, "execv", lambda *a: called.append(a))
        handover["_use_the_sandboxes_own_python"]()
        assert called == []

    def test_setting_it_yourself_keeps_you_where_you_are(self, handover, monkeypatch):
        """The same guard is the way to run against another environment."""
        monkeypatch.setattr(sys, "executable", "/somewhere/else/python")
        monkeypatch.setenv(_HANDED_OVER, "1")
        monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("handed over anyway"))
        handover["_use_the_sandboxes_own_python"]()

    def test_no_environment_means_carry_on_quietly(self, handover, monkeypatch, tmp_path):
        """A copy nobody has run start.py on. The version check below says more."""
        monkeypatch.setitem(handover, "__file__", str(tmp_path / "main.py"))
        assert handover["_sandboxes_own_python"]() is None

    def test_an_environment_that_will_not_run_is_not_fatal(self, handover, monkeypatch):
        """Half-built, or carried over from another computer."""
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
        monkeypatch.delenv(_HANDED_OVER, raising=False)

        def refuses(*_a):
            raise OSError("cannot run this")

        monkeypatch.setattr(os, "execv", refuses)
        handover["_use_the_sandboxes_own_python"]()
        # And the guard is cleared, so the next thing to try is not blocked.
        assert _HANDED_OVER not in os.environ
