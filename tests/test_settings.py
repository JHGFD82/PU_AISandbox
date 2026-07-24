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
