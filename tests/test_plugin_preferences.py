"""Tests for src/plugin_preferences.py — offering plugin settings to the person.

A plugin's settings.toml sits inside the package: among the code, tracked by the
plugin's own repository, replaced when the plugin is updated. Nobody should be
told to edit it. This copies every setting out into the file people are meant to
edit, commented out, with the author's own explanation attached.
"""

import tomllib

import pytest

from src.plugin_preferences import offer_plugin_settings


def _plugin(plugins_dir, name, body):
    d = plugins_dir / name
    d.mkdir(parents=True)
    (d / "settings.toml").write_text(body)
    return d


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A plugins folder, a preferences file, and no shared settings file."""
    import src.paths as paths_mod
    import src.settings_store as store_mod

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    prefs = tmp_path / "preferences.toml"
    prefs.write_text("# my own preferences\n")
    monkeypatch.setattr(paths_mod, "preferences_path", lambda: prefs)
    monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: None)
    return plugins, prefs


class TestOfferingSettings:

    def test_a_plugins_settings_appear_in_preferences(self, world):
        plugins, prefs = world
        _plugin(plugins, "demo", '[demo]\nmodels = ["gpt-4o"]\ntemperature = 0.5\n')
        offer_plugin_settings(plugins)
        text = prefs.read_text()
        assert "# [demo]" in text
        assert '# models = ["gpt-4o"]' in text
        assert "# temperature = 0.5" in text

    def test_they_are_commented_out(self, world):
        """A live value would pin the setting the moment it was written.

        The plugin could ship a corrected list next month and the frozen copy
        would quietly win. Commented, the plugin's value keeps applying until
        somebody deliberately takes it over.
        """
        plugins, prefs = world
        _plugin(plugins, "demo", '[demo]\nmodels = ["gpt-4o"]\n')
        offer_plugin_settings(plugins)
        assert tomllib.loads(prefs.read_text()) == {}, "nothing should be live"

    def test_the_authors_explanation_comes_with_it(self, world):
        """The comment is the whole reason a setting is understandable."""
        plugins, prefs = world
        _plugin(plugins, "demo", "[demo]\n# Why this matters, in the author's words.\ntemperature = 0.5\n")
        offer_plugin_settings(plugins)
        assert "# Why this matters, in the author's words." in prefs.read_text()

    def test_an_inline_comment_survives_too(self, world):
        plugins, prefs = world
        _plugin(plugins, "demo", "[demo]\nmax_tokens = 4000   # raise for long pages\n")
        offer_plugin_settings(plugins)
        assert "# raise for long pages" in prefs.read_text()

    def test_running_again_changes_nothing(self, world):
        """This runs on every command, so it has to be a no-op after the first."""
        plugins, prefs = world
        _plugin(plugins, "demo", '[demo]\nmodels = ["gpt-4o"]\n')
        offer_plugin_settings(plugins)
        once = prefs.read_text()
        assert offer_plugin_settings(plugins) == []
        assert prefs.read_text() == once

    def test_a_setting_the_person_has_set_is_left_alone(self, world):
        """Never offer, and never touch, something already decided."""
        plugins, prefs = world
        prefs.write_text('[demo]\ntemperature = 0.9   # mine\n')
        _plugin(plugins, "demo", "[demo]\ntemperature = 0.5\n")
        offer_plugin_settings(plugins)
        text = prefs.read_text()
        assert tomllib.loads(text)["demo"]["temperature"] == 0.9
        assert "# mine" in text
        assert "# temperature = 0.5" not in text

    def test_a_new_setting_is_added_beside_settings_already_offered(self, world):
        plugins, prefs = world
        d = _plugin(plugins, "demo", "[demo]\ntemperature = 0.5\n")
        offer_plugin_settings(plugins)
        (d / "settings.toml").write_text("[demo]\ntemperature = 0.5\nmax_tokens = 4000\n")
        offer_plugin_settings(plugins)
        text = prefs.read_text()
        assert text.count("# temperature = 0.5") == 1, "must not be offered twice"
        assert "# max_tokens = 4000" in text

    def test_two_plugins_sharing_a_section_offer_each_setting_once(self, world):
        """An extension and the plugin it extends can both use [ocr]."""
        plugins, prefs = world
        _plugin(plugins, "base", "[ocr]\ntemperature = 0.0\n")
        _plugin(plugins, "base-ea", "[ocr]\ntemperature = 0.0\n")
        offer_plugin_settings(plugins)
        assert prefs.read_text().count("# temperature = 0.0") == 1

    def test_the_result_is_still_valid_toml(self, world):
        plugins, prefs = world
        _plugin(plugins, "demo", '[demo]\nmodels = ["a", "b"]\n[other]\nx = 1\n')
        offer_plugin_settings(plugins)
        tomllib.loads(prefs.read_text())   # raises if not

    def test_each_block_says_which_plugin_it_came_from(self, world):
        plugins, prefs = world
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        offer_plugin_settings(plugins)
        assert "demo" in prefs.read_text().split("# [demo]")[0]


class TestSharedSettingsFile:

    def test_a_configured_shared_file_gets_them_too(self, tmp_path, monkeypatch):
        import src.paths as paths_mod
        import src.settings_store as store_mod

        plugins = tmp_path / "plugins"
        plugins.mkdir()
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("")
        shared = tmp_path / "shared.toml"
        shared.write_text("# group settings\n")
        monkeypatch.setattr(paths_mod, "preferences_path", lambda: prefs)
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: shared)
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        offer_plugin_settings(plugins)
        assert "# x = 1" in shared.read_text()

    def test_a_shared_path_pointing_nowhere_is_skipped(self, tmp_path, monkeypatch):
        """Configured but absent: leave it alone rather than creating it."""
        import src.paths as paths_mod
        import src.settings_store as store_mod

        plugins = tmp_path / "plugins"
        plugins.mkdir()
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("")
        missing = tmp_path / "not-there.toml"
        monkeypatch.setattr(paths_mod, "preferences_path", lambda: prefs)
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: missing)
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        offer_plugin_settings(plugins)
        assert not missing.exists()
        assert "# x = 1" in prefs.read_text()


class TestNeverGetsInTheWay:
    """This is a convenience. It must never stop a command from running."""

    def test_an_unset_up_sandbox_is_a_no_op(self, tmp_path, monkeypatch):
        import src.paths as paths_mod

        def _raise():
            raise paths_mod.NotSetUpError("not set up")

        monkeypatch.setattr(paths_mod, "preferences_path", _raise)
        assert offer_plugin_settings(tmp_path) == []

    def test_a_plugins_folder_that_isnt_there_is_a_no_op(self, world):
        _plugins, _prefs = world
        assert offer_plugin_settings(_plugins / "nope") == []

    def test_a_plugin_with_unreadable_settings_is_skipped_quietly(self, world):
        plugins, prefs = world
        _plugin(plugins, "broken", "[demo\nthis is not = = toml")
        _plugin(plugins, "fine", "[fine]\nx = 1\n")
        offer_plugin_settings(plugins)
        assert "# x = 1" in prefs.read_text()

    def test_an_unwritable_preferences_file_does_not_raise(self, world, monkeypatch):
        plugins, prefs = world
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        prefs.chmod(0o444)
        try:
            assert offer_plugin_settings(plugins) == []
        finally:
            prefs.chmod(0o644)

    def test_a_plugin_without_a_settings_file_is_ignored(self, world):
        plugins, prefs = world
        (plugins / "codeonly").mkdir()
        assert offer_plugin_settings(plugins) == []
