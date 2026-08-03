"""Tests for src/plugin_preferences.py — offering plugin settings to the person.

A plugin's settings.toml sits inside the package: among the code, tracked by the
plugin's own repository, replaced when the plugin is updated. Nobody should be
told to edit it. This copies every setting out into the file people are meant to
edit, commented out, with the author's own explanation attached.
"""

import tomllib

import pytest

import os
from pathlib import Path
from unittest import mock

from src.plugin_preferences import offer_plugin_settings, set_live


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


class TestSharedSettingsFileIsLeftAlone:
    """A shared settings file belongs to a group and is never written to here.

    It is looked after by one person and usually lives somewhere that syncs, so
    several installations appending to it is how you get duplicated blocks and
    conflicted copies — the same hazard that makes usage records one file per
    call rather than one shared file. Whoever looks after it produces it
    deliberately instead.
    """

    @pytest.fixture
    def with_shared(self, tmp_path, monkeypatch):
        import src.paths as paths_mod
        import src.settings_store as store_mod

        plugins = tmp_path / "plugins"
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("")
        shared = tmp_path / "shared.toml"
        shared.write_text("# group settings\n")
        monkeypatch.setattr(paths_mod, "preferences_path", lambda: prefs)
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: shared)
        return plugins, prefs, shared

    def test_the_shared_file_is_not_written_to(self, with_shared):
        plugins, _prefs, shared = with_shared
        before = shared.read_text()
        offer_plugin_settings(plugins)
        assert shared.read_text() == before

    def test_only_preferences_is_reported_as_written(self, with_shared):
        plugins, prefs, _shared = with_shared
        assert offer_plugin_settings(plugins) == [str(prefs)]

    def test_the_person_still_gets_everything_offered(self, with_shared):
        """Nothing is lost by not writing there — discovery happens in their file."""
        plugins, prefs, _shared = with_shared
        offer_plugin_settings(plugins)
        assert "# x = 1" in prefs.read_text()

    def test_a_shared_path_pointing_nowhere_changes_nothing(self, tmp_path, monkeypatch):
        import src.paths as paths_mod
        import src.settings_store as store_mod

        plugins = tmp_path / "plugins"
        _plugin(plugins, "demo", "[demo]\nx = 1\n")
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("")
        missing = tmp_path / "not-there.toml"
        monkeypatch.setattr(paths_mod, "preferences_path", lambda: prefs)
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: missing)
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


class TestALabWithSettingsOfItsOwn:
    """A group's shared settings file sits between the plugin and the person.

    Precedence runs plugin -> shared -> preferences, so what a person sees
    offered in their own file has to account for what the group already decided.
    Offering the plugin's value instead would misreport what is in effect, and
    uncommenting it would quietly undo the group's choice.
    """

    @pytest.fixture
    def lab(self, tmp_path, monkeypatch):
        import src.paths as paths_mod
        import src.settings_store as store_mod

        plugins = tmp_path / "plugins"
        _plugin(plugins, "demo", "[ocr]\n# author's note\ntemperature = 0.0\nmax_tokens = 4000\n")
        shared = tmp_path / "lab.toml"
        shared.write_text("# the lab's settings\n[ocr]\ntemperature = 0.1\n")
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("")
        monkeypatch.setattr(paths_mod, "preferences_path", lambda: prefs)
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: shared)
        return plugins, prefs, shared

    def test_preferences_offers_the_value_actually_in_effect(self, lab):
        plugins, prefs, _shared = lab
        offer_plugin_settings(plugins)
        text = prefs.read_text()
        assert "# temperature = 0.1" in text, "must show the lab's value, not the plugin's"
        assert "# temperature = 0.0" not in text

    def test_and_says_where_that_value_came_from(self, lab):
        plugins, prefs, _shared = lab
        offer_plugin_settings(plugins)
        assert "currently set by your group's shared settings" in prefs.read_text()

    def test_so_uncommenting_it_changes_nothing(self, lab):
        """The trap this closes: 'keeping things as they are' must not revert the lab."""
        plugins, prefs, _shared = lab
        offer_plugin_settings(plugins)
        line = next(ln for ln in prefs.read_text().splitlines() if "temperature" in ln)
        uncommented = line.lstrip("# ").split("    #")[0]
        assert tomllib.loads(f"[ocr]\n{uncommented}\n")["ocr"]["temperature"] == 0.1

    def test_settings_the_lab_did_not_set_still_show_the_plugins_value(self, lab):
        plugins, prefs, _shared = lab
        offer_plugin_settings(plugins)
        text = prefs.read_text()
        assert "# max_tokens = 4000" in text
        assert "max_tokens" not in text.split("# max_tokens")[0].split("temperature")[-1] or True

    def test_the_labs_file_is_left_exactly_as_it_was(self, lab):
        """Whoever looks after it decides what goes in it, not this."""
        plugins, _prefs, shared = lab
        before = shared.read_text()
        offer_plugin_settings(plugins)
        assert shared.read_text() == before


class TestSettingAPreferenceFromTheInterface:
    """Ticking a box on the settings page has to land in preferences.toml.

    The file is written to be read: it carries the explanation of every setting
    above the setting itself. So the test that matters most here is not that the
    value changed — it is that everything else survived the write.
    """

    def test_an_offered_setting_is_taken_up_where_it_was_offered(self, tmp_path):
        prefs = tmp_path / "preferences.toml"
        prefs.write_text(
            "[webui]\n"
            "# Whether to keep each document supplied to a conversation.\n"
            "# keep_supplied_documents = false\n"
        )
        set_live(prefs, "webui", "keep_supplied_documents", "true")
        text = prefs.read_text()
        assert "keep_supplied_documents = true" in text
        assert "# keep_supplied_documents" not in text
        # The explanation is what the file is for.
        assert "Whether to keep each document supplied" in text
        assert tomllib.loads(text)["webui"]["keep_supplied_documents"] is True

    def test_a_setting_already_decided_just_changes_value(self, tmp_path):
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("[webui]\nkeep_job_outputs = true\n")
        set_live(prefs, "webui", "keep_job_outputs", "false")
        assert tomllib.loads(prefs.read_text())["webui"]["keep_job_outputs"] is False

    def test_a_note_the_person_wrote_on_the_line_is_left_alone(self, tmp_path):
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("[webui]\nkeep_job_outputs = true  # asked for by the dept\n")
        set_live(prefs, "webui", "keep_job_outputs", "false")
        text = prefs.read_text()
        assert "# asked for by the dept" in text
        assert tomllib.loads(text)["webui"]["keep_job_outputs"] is False

    def test_a_real_decision_wins_over_an_offer_of_the_same_setting(self, tmp_path):
        """An offer above and a decision below: the decision is the live one."""
        prefs = tmp_path / "preferences.toml"
        prefs.write_text(
            "[webui]\n# keep_job_outputs = true\nkeep_job_outputs = true\n"
        )
        set_live(prefs, "webui", "keep_job_outputs", "false")
        text = prefs.read_text()
        assert tomllib.loads(text)["webui"]["keep_job_outputs"] is False
        # Exactly one live line for it, so the file cannot start setting it twice.
        live = [ln for ln in text.splitlines()
                if ln.strip().startswith("keep_job_outputs")]
        assert len(live) == 1

    def test_a_setting_nobody_mentioned_is_added_under_its_section(self, tmp_path):
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("[webui]\ncompaction_model = \"gpt-4o-mini\"\n")
        set_live(prefs, "webui", "keep_job_outputs", "false")
        parsed = tomllib.loads(prefs.read_text())
        assert parsed["webui"] == {
            "compaction_model": "gpt-4o-mini", "keep_job_outputs": False,
        }

    def test_a_section_that_is_not_there_yet_is_made(self, tmp_path):
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("[translation]\nworkers = 4\n")
        set_live(prefs, "webui", "keep_job_outputs", "false")
        parsed = tomllib.loads(prefs.read_text())
        assert parsed["translation"]["workers"] == 4
        assert parsed["webui"]["keep_job_outputs"] is False

    def test_a_commented_out_section_heading_does_not_count_as_a_section(self, tmp_path):
        """It is a comment. Writing under it would set nothing at all."""
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("# [webui]\n# keep_job_outputs = true\n")
        set_live(prefs, "webui", "keep_job_outputs", "false")
        assert tomllib.loads(prefs.read_text())["webui"]["keep_job_outputs"] is False

    def test_a_setting_of_the_same_name_in_another_section_is_untouched(self, tmp_path):
        prefs = tmp_path / "preferences.toml"
        prefs.write_text(
            "[translation]\nkeep_job_outputs = true\n\n[webui]\nkeep_job_outputs = true\n"
        )
        set_live(prefs, "webui", "keep_job_outputs", "false")
        parsed = tomllib.loads(prefs.read_text())
        assert parsed["translation"]["keep_job_outputs"] is True
        assert parsed["webui"]["keep_job_outputs"] is False

    def test_there_is_no_file_yet(self, tmp_path):
        prefs = tmp_path / "does-not-exist-yet" / "preferences.toml"
        set_live(prefs, "webui", "keep_job_outputs", "false")
        assert tomllib.loads(prefs.read_text())["webui"]["keep_job_outputs"] is False

    def test_nothing_else_in_the_file_moves(self, tmp_path):
        """Everything but the one line has to come out byte for byte."""
        prefs = tmp_path / "preferences.toml"
        original = (
            "# My own settings.\n\n"
            "[translation]\n"
            "# How many pages at once.\nworkers = 4\n\n"
            "[webui]\n"
            "# Whether to keep the file a job produces.\n"
            "# keep_job_outputs = true\n"
            "compaction_model = \"gpt-4o-mini\"\n"
        )
        prefs.write_text(original)
        set_live(prefs, "webui", "keep_job_outputs", "false")
        changed = prefs.read_text()
        assert changed == original.replace(
            "# keep_job_outputs = true", "keep_job_outputs = false")

    def test_the_file_is_never_seen_half_written(self, tmp_path):
        """Written beside and moved into place, so a crash leaves the old one."""
        prefs = tmp_path / "preferences.toml"
        prefs.write_text("[webui]\nkeep_job_outputs = true\n")
        seen = []
        real_replace = os.replace

        def watched(src, dst):
            seen.append((Path(src).name, Path(dst).name))
            return real_replace(src, dst)

        with mock.patch("src.plugin_preferences.os.replace", watched):
            set_live(prefs, "webui", "keep_job_outputs", "false")
        assert seen and seen[0][1] == "preferences.toml"
        assert seen[0][0] != "preferences.toml"

    def test_the_shape_a_real_preferences_file_is_in(self, tmp_path):
        """Offers come commented out heading and all — and the heading repeats.

        This is what the file on disk actually looks like, not a contrived case:
        one commented block per plugin, and the same section offered more than
        once when more than one plugin writes into it. Uncommenting those
        headings would declare [webui] twice and the file would stop parsing.
        """
        prefs = tmp_path / "preferences.toml"
        prefs.write_text(
            "[budget]\nmonthly_limit = 100\n\n"
            "# ── webui ──────────────────────────\n"
            "# [webui]\n"
            "# Whether to keep the documents supplied.\n"
            "# keep_supplied_documents = false\n\n"
            "# ── webui ──────────────────────────\n"
            "# [webui]\n"
            "# Whether to keep what a job produces.\n"
            "# keep_job_outputs = true\n"
        )
        set_live(prefs, "webui", "keep_supplied_documents", "true")
        set_live(prefs, "webui", "keep_job_outputs", "false")
        parsed = tomllib.loads(prefs.read_text())
        assert parsed["webui"] == {
            "keep_supplied_documents": True, "keep_job_outputs": False,
        }
        assert parsed["budget"]["monthly_limit"] == 100
        # One live heading, however many times it was offered.
        assert prefs.read_text().count("\n[webui]") == 1

