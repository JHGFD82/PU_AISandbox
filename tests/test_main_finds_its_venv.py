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


def _a_checkout_with_an_environment(root: Path, python_is: Path | None = None) -> Path:
    """Build a copy of the sandbox with a ``.venv`` beside it, and return it.

    These tests used to read the ``.venv`` of whoever ran them. That passed on
    a machine where somebody had run ``start.py`` and failed everywhere else —
    continuous integration installs into the Python it is already running, so
    there was no ``.venv`` there at all and every one of these was quietly
    testing nothing. The environment is built here instead, so the tests hold
    the thing they are about rather than borrowing it.

    Args:
        root: Where to build it.
        python_is: What ``.venv``'s Python should be. ``None`` leaves a stub
                   that is never run, which is all a test needs when it has
                   replaced ``os.execv``. Give a real Python for the one test
                   that runs the command through for real — it is put there as
                   a two-line script that runs it, not as a link to it, because
                   a Python reached through a link in a folder with no
                   ``pyvenv.cfg`` beside it decides it is not in an environment
                   at all and looks for its packages in the wrong place.

    Returns:
        The folder built, with ``main.py`` in it.
    """
    root.mkdir(parents=True, exist_ok=True)
    # Linked rather than copied: the point is to test this checkout's main.py,
    # and everything it reaches on the way to answering --help.
    for name in ("main.py", "src", "plugins", "templates", "settings.default.toml"):
        source = _ROOT / name
        if source.exists():
            (root / name).symlink_to(source)

    bin_dir = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    if python_is is None:
        python.write_text("")
    else:
        python.write_text(f'#!/bin/sh\nexec "{python_is}" "$@"\n')
    python.chmod(0o755)
    return root


@pytest.fixture
def sandbox(tmp_path):
    """A checkout with an environment of its own, and no Python worth running."""
    return _a_checkout_with_an_environment(tmp_path / "sandbox")


@pytest.fixture
def handover(sandbox):
    """The handover functions, loaded without running the rest of main.py.

    Loaded as though they were sitting in *sandbox*, since where main.py is
    is exactly what decides which environment it hands over to.
    """
    main = sandbox / "main.py"
    prefix = main.read_text().split("_use_the_sandboxes_own_python()\n")[0]
    namespace: dict = {"__file__": str(main)}
    exec(compile(prefix, str(main), "exec"), namespace)
    return namespace


class TestItStillRunsOnTheOldPython:
    """The property the whole thing rests on: the handover has to happen
    before anything modern is parsed, on the Python that shipped with the Mac."""

    def test_the_file_compiles_for_python_3_9(self):
        compile(_MAIN.read_text(), str(_MAIN), "exec", dont_inherit=True)

    def test_a_command_works_from_the_system_python(self, tmp_path):
        """The whole point, end to end, with no environment activated.

        The environment this hands over to is the Python running these tests,
        which has everything installed — so what is being tested is that the
        handover happens and carries the command, not that pip works.
        """
        system = Path("/usr/bin/python3")
        if not system.exists():
            pytest.skip("no system python to try this with")
        if Path(sys.executable).resolve() == system.resolve():
            pytest.skip("these tests are running on the system python already")
        root = _a_checkout_with_an_environment(
            tmp_path / "sandbox", python_is=Path(sys.executable))
        done = subprocess.run(
            [str(system), str(root / "main.py"), "--help"],
            capture_output=True, text=True, cwd=root,
            env={k: v for k, v in os.environ.items() if k != _HANDED_OVER},
        )
        assert done.returncode == 0, done.stderr
        assert "Princeton University AI Sandbox" in done.stdout


class TestWhenItHandsOver:
    def test_it_does_nothing_when_already_in_that_environment(self, handover, monkeypatch):
        """Somebody who activated it is running that same Python."""
        own = handover["_sandboxes_own_python"]()
        assert own, "the checkout built for this test has no .venv in it"
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
        assert argv[1] == handover["__file__"]

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
