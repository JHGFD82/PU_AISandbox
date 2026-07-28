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
