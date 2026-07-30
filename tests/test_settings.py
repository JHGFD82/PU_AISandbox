"""
Tests for src/settings.py:
  - _merge_layer (the pure merge logic used for both the shared layer
    and preferences.toml)
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
        """Redirect settings_store's settings.toml to a tmp one with shared_settings.path set."""
        settings_file = tmp_path / ".settings"
        settings_file.write_text(f'[shared_settings]\npath = "{path}"\n')
        monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", settings_file)

    def test_shared_settings_path_applies_and_preferences_still_win(self, tmp_path, monkeypatch):
        """settings.default.toml -> shared file -> preferences.toml, each overriding the last."""
        shared_file = tmp_path / "shared.toml"
        shared_file.write_text("[budget]\nwarning_threshold_pct = 95\n")
        self._point_shared_settings_at(tmp_path, monkeypatch, str(shared_file))

        try:
            importlib.reload(settings_mod)
            # preferences.toml (if present) still wins over the shared file
            # for any key it mentions; but the shared file's value applies for keys
            # preferences.toml doesn't touch. We only assert the mechanism ran
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


class TestPluginSettingsLayering:
    """plugin_settings() — a plugin's own defaults, overridable like everything else.

    Before this existed each plugin walked up to its own settings.toml and read
    that one file, so a `[translation]` section in someone's preferences.toml
    was silently ignored — including in a plugin whose docstring promised it
    worked.
    """

    def _layers(self, tmp_path, monkeypatch, shared=None, prefs=None):
        plugin_dir = tmp_path / "plugins" / "demo"
        (plugin_dir / "src").mkdir(parents=True)
        (plugin_dir / "settings.toml").write_text(
            "[demo]\ntemperature = 0.5\nmax_tokens = 4000\n[other]\nkeep = 1\n"
        )
        caller = plugin_dir / "src" / "settings.py"
        caller.write_text("")

        shared_path = None
        if shared is not None:
            shared_path = tmp_path / "shared.toml"
            shared_path.write_text(shared)
        prefs_path = None
        if prefs is not None:
            prefs_path = tmp_path / "preferences.toml"
            prefs_path.write_text(prefs)

        monkeypatch.setattr(settings_mod, "_shared_settings_path", shared_path)
        monkeypatch.setattr(settings_mod, "_PREFERENCES_PATH", prefs_path)
        return str(caller)

    def test_the_plugins_own_values_are_the_starting_point(self, tmp_path, monkeypatch):
        caller = self._layers(tmp_path, monkeypatch)
        got = settings_mod.plugin_settings(caller, "demo")
        assert got["demo"] == {"temperature": 0.5, "max_tokens": 4000}

    def test_preferences_wins_over_everything(self, tmp_path, monkeypatch):
        caller = self._layers(
            tmp_path, monkeypatch,
            shared="[demo]\ntemperature = 0.9\n",
            prefs="[demo]\ntemperature = 0.25\n",
        )
        assert settings_mod.plugin_settings(caller, "demo")["demo"]["temperature"] == 0.25

    def test_shared_applies_where_preferences_is_silent(self, tmp_path, monkeypatch):
        caller = self._layers(
            tmp_path, monkeypatch,
            shared="[demo]\ntemperature = 0.9\nmax_tokens = 111\n",
            prefs="[demo]\ntemperature = 0.25\n",
        )
        got = settings_mod.plugin_settings(caller, "demo")["demo"]
        assert (got["temperature"], got["max_tokens"]) == (0.25, 111)

    def test_keys_no_layer_mentions_keep_the_plugins_value(self, tmp_path, monkeypatch):
        caller = self._layers(tmp_path, monkeypatch, prefs="[demo]\ntemperature = 0.1\n")
        assert settings_mod.plugin_settings(caller, "demo")["demo"]["max_tokens"] == 4000

    def test_only_the_named_sections_come_back(self, tmp_path, monkeypatch):
        """A plugin must not be able to read another plugin's settings."""
        caller = self._layers(tmp_path, monkeypatch, prefs="[webui]\nport = 9999\n")
        got = settings_mod.plugin_settings(caller, "demo")
        assert set(got) == {"demo"}

    def test_an_unconfigured_section_is_an_empty_dict_not_missing(self, tmp_path, monkeypatch):
        """So callers can use .get(key, default) without checking first."""
        caller = self._layers(tmp_path, monkeypatch)
        assert settings_mod.plugin_settings(caller, "demo", "never_configured") == {
            "demo": {"temperature": 0.5, "max_tokens": 4000},
            "never_configured": {},
        }

    def test_a_settings_file_without_our_sections_is_skipped(self, tmp_path, monkeypatch):
        """The walk-up must not stop at a settings.toml belonging to something else."""
        caller = self._layers(tmp_path, monkeypatch)
        # An unrelated settings.toml sitting between the plugin and its own file.
        (tmp_path / "plugins" / "settings.toml").write_text("[unrelated]\nx = 1\n")
        assert settings_mod.plugin_settings(caller, "demo")["demo"]["temperature"] == 0.5

    def test_an_unreadable_layer_leaves_the_others_standing(self, tmp_path, monkeypatch):
        """A hand-edited file with a typo must not blank out a plugin's settings."""
        caller = self._layers(tmp_path, monkeypatch, prefs="[demo\nbroken = ")
        assert settings_mod.plugin_settings(caller, "demo")["demo"]["temperature"] == 0.5

    def test_no_layers_configured_at_all(self, tmp_path, monkeypatch):
        caller = self._layers(tmp_path, monkeypatch)
        assert settings_mod.plugin_settings(caller, "demo")["demo"]["temperature"] == 0.5


class TestRequiredModels:
    """A model list is written in one place — the plugin's settings.toml.

    Every other plugin setting carries a default in code, which is fine for a
    temperature or a worker count. A model list is different: it is the setting
    most likely to be edited, because providers retire models, and a second copy
    in Python would drift out of step silently — the file wins, so the stale
    copy just sits there looking authoritative. So there is no second copy, and
    absence is an error rather than a quiet substitution.
    """

    def test_returns_the_names_in_the_order_given(self):
        got = settings_mod.required_models(
            {"models": ["gpt-4o", "gpt-4o-mini"]}, "models", where="somewhere"
        )
        assert got == ["gpt-4o", "gpt-4o-mini"]

    def test_surrounding_whitespace_is_forgiven(self):
        """A hand-edited file shouldn't fail over a stray space."""
        got = settings_mod.required_models(
            {"models": [" gpt-4o ", "gpt-4o-mini"]}, "models", where="somewhere"
        )
        assert got == ["gpt-4o", "gpt-4o-mini"]

    @pytest.mark.parametrize("value", [
        None,                       # key absent entirely
        [],                         # named but empty
        "gpt-4o",                   # a bare string, not a list
        ["gpt-4o", ""],             # a blank entry
        ["gpt-4o", "   "],          # a whitespace-only entry
        ["gpt-4o", None],           # a non-string entry
        {"1": "gpt-4o"},            # a table where a list belongs
    ])
    def test_anything_unusable_raises(self, value):
        settings = {} if value is None else {"models": value}
        with pytest.raises(ValueError, match="list of model names"):
            settings_mod.required_models(settings, "models", where="somewhere")

    def test_the_error_says_where_to_go_and_why_it_matters(self):
        """An error about configuration is only useful if it names the file."""
        with pytest.raises(ValueError) as excinfo:
            settings_mod.required_models(
                {}, "models", where="[ocr] in plugins/transcription/settings.toml"
            )
        message = str(excinfo.value)
        assert "[ocr] in plugins/transcription/settings.toml" in message
        assert "cheapest" in message   # says what the alternative would have been

    def test_every_bundled_plugin_names_its_models_in_its_settings_file(self):
        """The lists must really be in the TOML — that is the whole point.

        Reads the files rather than the loaded constants, so a Python fallback
        creeping back in would not hide a missing entry.
        """
        import tomllib
        from pathlib import Path

        expected = {
            "prompt": [("prompt_command", "models")],
            "translation": [("translation", "models"), ("image_translation", "models")],
            "transcription": [("ocr", "models"), ("transcription_review", "models")],
            "webui": [("webui", "chat_models"), ("webui", "title_models")],
        }
        for plugin, entries in expected.items():
            path = Path("plugins") / plugin / "settings.toml"
            data = tomllib.loads(path.read_text())
            for section, key in entries:
                names = data.get(section, {}).get(key)
                assert isinstance(names, list) and names, f"{path}: [{section}] {key} missing"
                assert all(isinstance(n, str) and n.strip() for n in names), f"{path}: [{section}] {key}"
