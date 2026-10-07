"""Tests for src/runtime/plugin_loader.py — load_plugins and _load_one."""

import textwrap
from pathlib import Path


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
                model_roles = {}
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
                model_roles = {}
                # missing register_subparsers and run
            plugin = _P()
        """)
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        assert result == {}
        assert "missing required attributes" in caplog.text

    def test_loads_extension_plugin_without_register_subparsers(self, tmp_path):
        """Extension plugins with handles+register_command_flags but no register_subparsers
        must be accepted by the loader (they hook into an existing command)."""
        _write_plugin(tmp_path, "base", """
            class _P:
                commands = ["translate"]
                model_roles = {}
                handles = ["en"]
                def register_subparsers(self, sp): pass
                def run(self, *a, **k): pass
            plugin = _P()
        """)
        _write_plugin(tmp_path, "extension", """
            class _P:
                commands = ["translate"]
                model_roles = {}
                handles = ["jp"]
                def register_command_flags(self, p): pass
                def run(self, *a, **k): pass
            plugin = _P()
        """)
        result = load_plugins(tmp_path)
        from src.runtime.dispatch_plugin import DispatchPlugin
        assert isinstance(result["translate"], DispatchPlugin)
        assert "en" in result["translate"].source_registry
        assert "jp" in result["translate"].source_registry

    def test_skips_extension_plugin_missing_handles(self, tmp_path, caplog):
        """A plugin with register_command_flags but no handles is not a valid extension
        plugin and must be skipped."""
        import logging
        _write_plugin(tmp_path, "broken_ext", """
            class _P:
                commands = ["translate"]
                model_roles = {}
                # has register_command_flags but no handles — invalid
                def register_command_flags(self, p): pass
                def run(self, *a, **k): pass
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
                model_roles = {}
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
                    model_roles = {{}}
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
                    model_roles = {{}}
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
                    model_roles = {}
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

    def test_three_plugins_with_handles_all_merge(self, tmp_path, caplog):
        """A third plugin declaring handles merges into the existing DispatchPlugin.

        This used to fail: the dispatcher exposed no 'handles' of its own, so
        the loader's "do both sides declare handles?" check said no, and the
        third plugin was dropped with a misleading "already registered"
        warning.  DispatchPlugin.handles now reports every token owned so
        far, so a third (or fourth) language extension merges exactly like
        the second did.
        """
        import logging
        for name, handles in [("alpha", ["en"]), ("beta", ["jp"]), ("gamma", ["zh"])]:
            _write_plugin(tmp_path, name, f"""
                class _P:
                    commands = ["translate"]
                    model_roles = {{}}
                    handles = {handles!r}
                    def register_subparsers(self, sp): pass
                    def run(self, *a, **k): pass
                    def register_command_flags(self, p): pass
                plugin = _P()
            """)
        with caplog.at_level(logging.WARNING, logger="src.runtime.plugin_loader"):
            result = load_plugins(tmp_path)
        from src.runtime.dispatch_plugin import DispatchPlugin
        dispatcher = result["translate"]
        assert isinstance(dispatcher, DispatchPlugin)
        assert "en" in dispatcher.source_registry
        assert "jp" in dispatcher.source_registry
        assert "zh" in dispatcher.source_registry
        assert set(dispatcher.handles) == {"en", "jp", "zh"}
        # No plugin was dropped, so nothing should have complained.
        assert "already registered" not in caplog.text


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


# ---------------------------------------------------------------------------
# Required: every plugin must say which models its work should use
# ---------------------------------------------------------------------------

class TestModelRolesRequired:
    """Without a declaration the sandbox silently uses the cheapest model.

    That is how the translate command came to default to a
    four-billion-parameter model with nothing but a line in the terminal to say
    so. Refusing to load is louder, and the message names the fix.
    """

    _BODY = """
        class _P:
            commands = ["cmd"]
            {roles}
            def register_subparsers(self, sp): pass
            def run(self, *a, **k): pass
        plugin = _P()
    """

    def _load(self, tmp_path, roles, caplog):
        import logging
        _write_plugin(tmp_path, "p", self._BODY.format(roles=roles))
        with caplog.at_level(logging.ERROR, logger="src.runtime.plugin_loader"):
            return load_plugins(tmp_path)

    def test_a_plugin_with_no_declaration_is_refused(self, tmp_path, caplog):
        result = self._load(tmp_path, "", caplog)
        assert result == {}
        assert "no 'model_roles' declared" in caplog.text

    def test_the_refusal_names_the_fix(self, tmp_path, caplog):
        """A contract error is only useful if it says what to write."""
        self._load(tmp_path, "", caplog)
        assert "ModelRole" in caplog.text
        assert "model_roles = {}" in caplog.text

    def test_an_empty_declaration_is_accepted(self, tmp_path, caplog):
        """A plugin that calls no AI model says so, explicitly.

        The point is that the decision was made rather than forgotten.
        """
        result = self._load(tmp_path, "model_roles = {}", caplog)
        assert "cmd" in result

    def test_a_declaration_of_the_wrong_type_is_refused(self, tmp_path, caplog):
        result = self._load(tmp_path, 'model_roles = ["translation"]', caplog)
        assert result == {}
        assert "must be a dict" in caplog.text

    def test_a_role_naming_no_models_is_refused(self, tmp_path, caplog):
        """An empty list would resolve to the cheapest model — the thing being prevented."""
        result = self._load(
            tmp_path,
            'model_roles = {"x": type("R", (), {"models": []})()}',
            caplog,
        )
        assert result == {}
        assert "names no models" in caplog.text

    def test_a_valid_role_loads(self, tmp_path, caplog):
        result = self._load(
            tmp_path,
            'model_roles = {"x": type("R", (), {"models": ["gpt-4o"]})()}',
            caplog,
        )
        assert "cmd" in result

    def test_every_bundled_plugin_declares_one(self):
        """The rule has to hold for the plugins that ship with the sandbox."""
        from pathlib import Path as _Path
        loaded = load_plugins(_Path("plugins"))
        assert loaded, "no plugins loaded at all — the declaration check may be over-strict"
        for command, plugin in loaded.items():
            roles = getattr(plugin, "model_roles", None)
            assert roles is not None, f"{command} has no model_roles"
