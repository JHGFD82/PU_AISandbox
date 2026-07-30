"""Tests for src/shared_settings.py — the draft a group's settings keeper edits.

The sandbox never writes a shared settings file: it belongs to a group and
usually lives somewhere that syncs, so several installations writing it is how
conflicted copies happen. Whoever looks after it asks for a draft instead, edits
it, and places it deliberately.
"""

import tomllib

import pytest

from src.shared_settings import build_shared_settings, count_new


def _plugin(plugins, name, body):
    d = plugins / name
    d.mkdir(parents=True)
    (d / "settings.toml").write_text(body)


@pytest.fixture
def world(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    defaults = tmp_path / "settings.default.toml"
    defaults.write_text("[retry]\n# how many times to try again\nmax_retries = 10\n")
    return plugins, defaults


class TestAFirstDraft:

    def test_it_is_valid_toml(self, world):
        plugins, defaults = world
        _plugin(plugins, "demo", '[demo]\nmodels = ["gpt-4o"]\n')
        tomllib.loads(build_shared_settings(plugins, defaults))

    def test_placing_it_unedited_changes_nothing(self, world):
        """Everything commented, so a draft dropped in place is inert."""
        plugins, defaults = world
        _plugin(plugins, "demo", '[demo]\nmodels = ["gpt-4o"]\n')
        assert tomllib.loads(build_shared_settings(plugins, defaults)) == {}

    def test_it_offers_the_packages_own_settings(self, world):
        plugins, defaults = world
        assert "# max_retries = 10" in build_shared_settings(plugins, defaults)

    def test_it_offers_every_plugins_settings(self, world):
        plugins, defaults = world
        _plugin(plugins, "alpha", "[alpha]\nx = 1\n")
        _plugin(plugins, "beta", "[beta]\ny = 2\n")
        text = build_shared_settings(plugins, defaults)
        assert "# x = 1" in text and "# y = 2" in text

    def test_the_authors_explanations_come_across(self, world):
        plugins, defaults = world
        _plugin(plugins, "demo", "[demo]\n# why this one matters\nx = 1\n")
        text = build_shared_settings(plugins, defaults)
        assert "# why this one matters" in text
        assert "# how many times to try again" in text

    def test_each_section_says_where_it_came_from(self, world):
        plugins, defaults = world
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        assert "the demo plugin" in build_shared_settings(plugins, defaults)

    def test_nothing_is_marked_new_without_an_existing_file(self, world):
        """There is nothing to be new relative to."""
        plugins, defaults = world
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        assert count_new(build_shared_settings(plugins, defaults)) == 0


class TestTwoPluginsSharingASection:
    """An extension and the plugin it extends can both use [ocr].

    A section written twice is a file no TOML reader will accept — which is
    exactly what an earlier version of this produced, so it is pinned here.
    """

    def test_the_result_is_still_valid_toml(self, world):
        plugins, defaults = world
        _plugin(plugins, "base", "[ocr]\ntemperature = 0.0\n")
        _plugin(plugins, "base-ea", "[ocr]\ntemperature = 0.0\n")
        tomllib.loads(build_shared_settings(plugins, defaults))

    def test_the_section_appears_once(self, world):
        plugins, defaults = world
        _plugin(plugins, "base", "[ocr]\ntemperature = 0.0\n")
        _plugin(plugins, "base-ea", "[ocr]\ntemperature = 0.0\n")
        text = build_shared_settings(plugins, defaults)
        assert text.count("[ocr]") == 1

    def test_both_contributors_are_named(self, world):
        plugins, defaults = world
        _plugin(plugins, "base", "[ocr]\ntemperature = 0.0\n")
        _plugin(plugins, "base-ea", "[ocr]\ntemperature = 0.0\n")
        text = build_shared_settings(plugins, defaults)
        assert "the base plugin" in text and "the base-ea plugin" in text

    def test_it_stays_valid_when_the_shared_section_is_decided(self, world, tmp_path):
        """The live-header path is where the duplicate was actually produced."""
        plugins, defaults = world
        _plugin(plugins, "base", "[ocr]\ntemperature = 0.0\nmax_tokens = 4000\n")
        _plugin(plugins, "base-ea", "[ocr]\ntemperature = 0.0\n")
        existing = tmp_path / "lab.toml"
        existing.write_text("[ocr]\ntemperature = 0.05\n")
        text = build_shared_settings(plugins, defaults, existing=existing)
        assert tomllib.loads(text)["ocr"]["temperature"] == 0.05
        assert text.count("[ocr]") == 1


class TestASecondDraft:
    """A group already has a file; settings have appeared since it was made."""

    @pytest.fixture
    def existing(self, tmp_path):
        f = tmp_path / "lab.toml"
        f.write_text("# the lab's file\n[retry]\nmax_retries = 3\n")
        return f

    def test_decisions_already_made_are_carried_across_live(self, world, existing):
        plugins, defaults = world
        text = build_shared_settings(plugins, defaults, existing=existing)
        assert tomllib.loads(text)["retry"]["max_retries"] == 3

    def test_they_are_carried_across_exactly_as_written(self, world, tmp_path):
        """Including a trailing comment the keeper added — that is their note."""
        plugins, defaults = world
        f = tmp_path / "lab.toml"
        f.write_text("[retry]\nmax_retries = 3   # agreed at the March meeting\n")
        text = build_shared_settings(plugins, defaults, existing=f)
        assert "max_retries = 3   # agreed at the March meeting" in text

    def test_settings_the_file_does_not_mention_are_marked_new(self, world, existing):
        plugins, defaults = world
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        text = build_shared_settings(plugins, defaults, existing=existing)
        assert "# NEW: x = 1" in text
        assert count_new(text) >= 1

    def test_a_decided_setting_is_not_marked_new(self, world, existing):
        plugins, defaults = world
        text = build_shared_settings(plugins, defaults, existing=existing)
        assert "# NEW: max_retries" not in text

    def test_new_settings_stay_commented_so_the_draft_is_still_inert(self, world, existing):
        """Marked for attention, not switched on behind the keeper's back."""
        plugins, defaults = world
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        parsed = tomllib.loads(build_shared_settings(plugins, defaults, existing=existing))
        assert "demo" not in parsed

    def test_the_result_is_valid_toml(self, world, existing):
        plugins, defaults = world
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        tomllib.loads(build_shared_settings(plugins, defaults, existing=existing))


class TestAwkwardInput:

    def test_a_plugin_with_unparseable_settings_is_skipped(self, world):
        plugins, defaults = world
        _plugin(plugins, "broken", "[demo\nnot = = toml")
        _plugin(plugins, "fine", "[fine]\nx = 1\n")
        text = build_shared_settings(plugins, defaults)
        assert "# x = 1" in text
        tomllib.loads(text)

    def test_a_missing_plugins_folder_still_gives_the_packages_settings(self, world):
        plugins, defaults = world
        text = build_shared_settings(plugins / "nope", defaults)
        assert "# max_retries = 10" in text

    def test_a_plugin_with_no_settings_file_is_ignored(self, world):
        plugins, defaults = world
        (plugins / "codeonly").mkdir()
        tomllib.loads(build_shared_settings(plugins, defaults))

    def test_the_header_explains_what_to_do_with_it(self, world):
        """A draft nobody understands is a draft nobody places."""
        plugins, defaults = world
        text = build_shared_settings(plugins, defaults)
        assert "settings set shared_settings.path" in text
        assert "Uncomment" in text

    def test_the_header_says_the_file_may_be_renamed(self, world):
        """The name it comes out as is easy to mistake for a required one.

        Said in the file itself, not only in the docs, because the file is what
        travels to whoever ends up looking after it.
        """
        plugins, defaults = world
        text = build_shared_settings(plugins, defaults)
        assert "Rename this file" in text
        assert "points at a path, not a name" in text


class TestWhatTheCommandProduces:
    """The name the draft comes out as, and the advice that comes with it."""

    def test_the_default_filename_is_the_documented_one(self, tmp_path, monkeypatch):
        """docs/configuration.md names this file, so a change here contradicts it."""
        import argparse

        from src import paths as paths_mod
        from src import settings_store as store_mod
        from src.runtime.info_commands import _settings_export_shared

        extras = tmp_path / "extras"
        extras.mkdir()
        monkeypatch.setattr(paths_mod, "extras_root", lambda: extras)
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: None)
        _settings_export_shared(argparse.Namespace(output=None, from_existing=None))
        assert (extras / "shared-settings.toml").exists()

    def test_output_puts_it_where_asked_instead(self, tmp_path, monkeypatch):
        import argparse

        from src import paths as paths_mod
        from src import settings_store as store_mod
        from src.runtime.info_commands import _settings_export_shared

        monkeypatch.setattr(paths_mod, "extras_root", lambda: tmp_path)
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: None)
        chosen = tmp_path / "our-group.toml"
        _settings_export_shared(argparse.Namespace(output=str(chosen), from_existing=None))
        assert chosen.exists()
        assert not (tmp_path / "shared-settings.toml").exists()

    def test_a_from_path_that_does_not_exist_is_refused_clearly(self, tmp_path, monkeypatch):
        import argparse

        import pytest as _pytest

        from src import paths as paths_mod
        from src.errors import CLIError
        from src.runtime.info_commands import _settings_export_shared

        monkeypatch.setattr(paths_mod, "extras_root", lambda: tmp_path)
        with _pytest.raises(CLIError, match="No shared settings file"):
            _settings_export_shared(
                argparse.Namespace(output=None, from_existing=str(tmp_path / "nope.toml"))
            )
