"""Tests for src/runtime/plugin_loader.py — load_plugins and _load_one."""

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.runtime.plugin_loader import load_plugins


# ---------------------------------------------------------------------------
# Helpers — build a fake plugins directory on disk
# ---------------------------------------------------------------------------

def _write_plugin(tmp_path: Path, name: str, src: str) -> Path:
    """Write a plugin.py under tmp_path/<name>/ and return the file path."""
    d = tmp_path / name
    d.mkdir()
    f = d / "plugin.py"
    f.write_text(textwrap.dedent(src))
    return f


# ---------------------------------------------------------------------------
# load_plugins
# ---------------------------------------------------------------------------

class TestLoadPlugins:

    def test_returns_empty_dict_when_dir_absent(self, tmp_path):
        result = load_plugins(tmp_path / "nonexistent")
        assert result == {}

    def test_returns_empty_dict_for_empty_dir(self, tmp_path):
        result = load_plugins(tmp_path)
        assert result == {}

    def test_skips_subdir_without_plugin_file(self, tmp_path):
        (tmp_path / "no_plugin").mkdir()
        result = load_plugins(tmp_path)
        assert result == {}

    def test_loads_valid_plugin(self, tmp_path):
        _write_plugin(tmp_path, "myplugin", """
            class _P:
                commands = ["mycommand"]
                handles = []
                def register_subparsers(self, sp): pass
                def run(self, *a, **k): pass
            plugin = _P()
        """)
        result = load_plugins(tmp_path)
        assert "mycommand" in result

    def test_skips_plugin_with_import_error(self, tmp_path, caplog):
        import logging
        _write_plugin(tmp_path, "badplugin", "raise ImportError('boom')")
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        assert result == {}
        assert "import failed" in caplog.text

    def test_skips_plugin_without_plugin_attribute(self, tmp_path, caplog):
        import logging
        _write_plugin(tmp_path, "nopluginattr", "x = 1")
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        assert result == {}
        assert "no module-level 'plugin' attribute" in caplog.text

    def test_skips_plugin_with_missing_interface(self, tmp_path, caplog):
        import logging
        _write_plugin(tmp_path, "incomplete", """
            class _P:
                commands = ["cmd"]
                # missing register_subparsers and run
            plugin = _P()
        """)
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        assert result == {}
        assert "missing required attributes" in caplog.text

    def test_skips_plugin_with_empty_commands(self, tmp_path, caplog):
        import logging
        _write_plugin(tmp_path, "emptycmds", """
            class _P:
                commands = []
                handles = []
                def register_subparsers(self, sp): pass
                def run(self, *a, **k): pass
            plugin = _P()
        """)
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        assert result == {}
        assert "'commands' list is empty" in caplog.text

    def test_files_in_plugins_dir_are_ignored(self, tmp_path):
        """Non-directory entries at the top level should be silently skipped."""
        (tmp_path / "somefile.txt").write_text("hello")
        result = load_plugins(tmp_path)
        assert result == {}

    def test_plugins_loaded_in_alphabetical_order(self, tmp_path):
        """Alphabetical scan order: 'aaa' should be registered first."""
        for name, cmd in [("zzz", "zcmd"), ("aaa", "acmd")]:
            _write_plugin(tmp_path, name, f"""
                class _P:
                    commands = ["{cmd}"]
                    handles = []
                    def register_subparsers(self, sp): pass
                    def run(self, *a, **k): pass
                plugin = _P()
            """)
        result = load_plugins(tmp_path)
        assert "acmd" in result
        assert "zcmd" in result


# ---------------------------------------------------------------------------
# Dispatch merging via _load_one
# ---------------------------------------------------------------------------

class TestDispatchMerging:

    def test_two_plugins_with_handles_create_dispatcher(self, tmp_path):
        for name, cmd, handles in [
            ("alpha", "translate", ["en"]),
            ("beta",  "translate", ["jp"]),
        ]:
            _write_plugin(tmp_path, name, f"""
                class _P:
                    commands = ["{cmd}"]
                    handles = {handles!r}
                    def register_subparsers(self, sp): pass
                    def run(self, *a, **k): pass
                    def register_command_flags(self, p): pass
                plugin = _P()
            """)
        result = load_plugins(tmp_path)
        from src.runtime.dispatch_plugin import DispatchPlugin
        assert isinstance(result["translate"], DispatchPlugin)
        assert "en" in result["translate"].source_registry
        assert "jp" in result["translate"].source_registry

    def test_command_conflict_without_handles_warns(self, tmp_path, caplog):
        import logging
        for name in ["alpha", "beta"]:
            _write_plugin(tmp_path, name, """
                class _P:
                    commands = ["translate"]
                    # no 'handles' — conflict, not dispatch
                    def register_subparsers(self, sp): pass
                    def run(self, *a, **k): pass
                plugin = _P()
            """)
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        assert "already registered" in caplog.text
        # first plugin wins
        assert "translate" in result

    def test_three_plugins_with_handles_third_warns(self, tmp_path, caplog):
        """Two plugins with handles merge into a DispatchPlugin.  A third plugin
        with handles is not absorbed (DispatchPlugin has no 'handles' attribute)
        and emits a conflict warning instead."""
        import logging
        for name, handles in [("alpha", ["en"]), ("beta", ["jp"]), ("gamma", ["zh"])]:
            _write_plugin(tmp_path, name, f"""
                class _P:
                    commands = ["translate"]
                    handles = {handles!r}
                    def register_subparsers(self, sp): pass
                    def run(self, *a, **k): pass
                    def register_command_flags(self, p): pass
                plugin = _P()
            """)
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        from src.runtime.dispatch_plugin import DispatchPlugin
        assert isinstance(result["translate"], DispatchPlugin)
        assert "en" in result["translate"].source_registry
        assert "jp" in result["translate"].source_registry
        # gamma was already-registered, so it warns rather than absorbs
        assert "already registered" in caplog.text


# ---------------------------------------------------------------------------
# spec=None branch in _load_one
# ---------------------------------------------------------------------------

class TestLoadOneSpecNone:

    def test_skips_plugin_when_spec_is_none(self, tmp_path, caplog, monkeypatch):
        """If spec_from_file_location returns None the plugin is skipped with a warning."""
        import logging
        import importlib.util as _ilu
        _write_plugin(tmp_path, "specnone", "plugin = object()")
        monkeypatch.setattr(_ilu, "spec_from_file_location", lambda *a, **k: None)
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        assert result == {}
        assert "could not create import spec" in caplog.text
