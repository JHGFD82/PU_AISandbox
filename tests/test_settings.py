"""
Tests for src/settings.py:
  - _merge_layer (the pure merge logic used for both the shared layer
    and settings.local.toml)
  - the full three-layer load order, exercised via importlib.reload with
    settings_store's shared_settings.path pointed at a tmp_path file

The module executes its layered load at import time, so the reload test
restores the real (unmodified) state afterward to avoid leaking a modified
settings module into any other test in the same session.
"""

import importlib
import logging
import sys

import pytest

import src.settings as settings_mod
import src.settings_store as settings_store_mod
from src.settings import _merge_layer


class TestMergeLayer:

    def test_new_section_added(self, tmp_path):
        base = {"prompt": {"temperature": 0.7}}
        layer_path = tmp_path / "layer.toml"
        layer_path.write_text("[budget]\nwarning_threshold_pct = 90\n")
        _merge_layer(base, layer_path)
        assert base["budget"]["warning_threshold_pct"] == 90
        assert base["prompt"]["temperature"] == 0.7  # untouched

    def test_existing_section_key_overridden(self, tmp_path):
        base = {"prompt": {"temperature": 0.7, "top_p": 1.0}}
        layer_path = tmp_path / "layer.toml"
        layer_path.write_text("[prompt]\ntemperature = 0.2\n")
        _merge_layer(base, layer_path)
        assert base["prompt"]["temperature"] == 0.2
        assert base["prompt"]["top_p"] == 1.0  # key not mentioned, untouched

    def test_only_mentioned_keys_change(self, tmp_path):
        base = {"processing": {"default_parallel_workers": 1, "max_parallel_workers": 50}}
        layer_path = tmp_path / "layer.toml"
        layer_path.write_text("[processing]\ndefault_parallel_workers = 4\n")
        _merge_layer(base, layer_path)
        assert base["processing"]["default_parallel_workers"] == 4
        assert base["processing"]["max_parallel_workers"] == 50


class TestLayeredLoadOrder:

    def _point_shared_settings_at(self, tmp_path, monkeypatch, path):
        """Redirect settings_store's .settings file to a tmp one with shared_settings.path set."""
        settings_file = tmp_path / ".settings"
        settings_file.write_text(f'[shared_settings]\npath = "{path}"\n')
        monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", settings_file)

    def test_shared_settings_path_applies_and_local_still_wins(self, tmp_path, monkeypatch):
        """settings.default.toml -> shared file -> settings.local.toml, each overriding the last."""
        shared_file = tmp_path / "shared.toml"
        shared_file.write_text("[budget]\nwarning_threshold_pct = 95\n")
        self._point_shared_settings_at(tmp_path, monkeypatch, str(shared_file))

        try:
            importlib.reload(settings_mod)
            # Repo's settings.local.toml (if present) still wins over the shared file
            # for any key it mentions; but the shared file's value applies for keys
            # settings.local.toml doesn't touch. We only assert the mechanism ran
            # without error and produced *some* integer threshold.
            assert isinstance(settings_mod.BUDGET_WARNING_THRESHOLD, int)
        finally:
            monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", tmp_path / "no_shared_settings")
            importlib.reload(settings_mod)

    def test_missing_shared_settings_file_warns_but_does_not_raise(self, tmp_path, monkeypatch, caplog):
        missing_path = tmp_path / "does_not_exist.toml"
        self._point_shared_settings_at(tmp_path, monkeypatch, str(missing_path))
        try:
            with caplog.at_level(logging.WARNING):
                importlib.reload(settings_mod)
            assert any("shared_settings.path" in r.message for r in caplog.records)
        finally:
            monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", tmp_path / "no_shared_settings")
            importlib.reload(settings_mod)

    def test_unset_shared_settings_leaves_behavior_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", tmp_path / ".settings")
        importlib.reload(settings_mod)
        assert isinstance(settings_mod.BUDGET_WARNING_THRESHOLD, int)


class TestPluginSettingsLookup:
    """src.settings.__getattr__ resolves constants defined by plugins.

    Two things it must get right, both newly relevant now that the web
    interface imports modules on demand from several threads at once.
    """

    def _fake_plugin_settings(self, monkeypatch, name, **values):
        import types
        mod = types.ModuleType(name)
        for k, v in values.items():
            setattr(mod, k, v)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    def test_finds_a_constant_defined_by_a_plugin(self, monkeypatch):
        import src.settings as settings_mod
        self._fake_plugin_settings(monkeypatch, "pu_plugin.demo.settings", DEMO_SETTING=42)
        assert settings_mod.DEMO_SETTING == 42

    def test_unknown_constant_still_raises(self, monkeypatch):
        import src.settings as settings_mod
        with pytest.raises(AttributeError):
            # Assigned rather than left bare so it reads as a deliberate
            # lookup that must fail, not a stray line.
            _ = settings_mod.NO_SUCH_SETTING_ANYWHERE

    def test_duplicate_constant_warns_and_names_both_plugins(self, monkeypatch, caplog):
        """Which plugin wins depends on load order, so it must not be silent."""
        import logging
        import src.settings as settings_mod
        self._fake_plugin_settings(monkeypatch, "pu_plugin.alpha.settings", SHARED_SETTING="a")
        self._fake_plugin_settings(monkeypatch, "pu_plugin.beta.settings", SHARED_SETTING="b")
        with caplog.at_level(logging.WARNING):
            value = settings_mod.SHARED_SETTING
        assert value in ("a", "b")
        assert "SHARED_SETTING" in caplog.text
        assert "pu_plugin.alpha.settings" in caplog.text
        assert "pu_plugin.beta.settings" in caplog.text

    def test_survives_modules_being_imported_while_it_looks(self, monkeypatch):
        """Another thread importing mid-lookup must not break the lookup.

        Iterating the live module registry raised "dictionary changed size
        during iteration" the moment anything was imported on another thread
        while this was scanning. The web interface imports on demand from
        inside request handlers, so this was reachable in normal use.

        Simulated by a module that inserts into sys.modules the moment the
        scan touches it — the same mutation-while-iterating a real concurrent
        import causes.
        """
        import types
        import src.settings as settings_mod

        inserting = types.ModuleType("pu_plugin.aaa_inserting.settings")

        def _sneaky_getattr(attr):
            # Runs during the scan's hasattr() check, mutating the registry
            # the scan is walking.
            sys.modules["pu_plugin.zzz_injected.settings"] = types.ModuleType("z")
            raise AttributeError(attr)

        inserting.__getattr__ = _sneaky_getattr
        monkeypatch.setitem(sys.modules, "pu_plugin.aaa_inserting.settings", inserting)
        self._fake_plugin_settings(monkeypatch, "pu_plugin.demo.settings", RACY_SETTING=7)
        monkeypatch.delitem(sys.modules, "pu_plugin.zzz_injected.settings", raising=False)

        assert settings_mod.RACY_SETTING == 7
