"""Fixtures for the launcher's tests: start.py and the launcher, loaded the way they load each other."""

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]


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
