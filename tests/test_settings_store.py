"""
Tests for src/settings_store.py:
  - get_value / set_value / unset_value (generic dotted-path access)
  - get_shared_settings_path
  - get_professors / add_professor / remove_professor
  - get_source_id / set_source_id
  - get_configured_sources / set_professor_usage_source /
    clear_professor_usage_source / get_shared_write_source

Every test redirects settings_store.SETTINGS_PATH to a tmp_path location, so
nothing here ever touches the real repo-root settings.toml file.
"""

from unittest.mock import patch

import pytest

from src import settings_store
from src.settings_store import ExternalSource


@pytest.fixture(autouse=True)
def _isolate_settings_path(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.toml")
    yield


# ---------------------------------------------------------------------------
# get_value / set_value / unset_value
# ---------------------------------------------------------------------------

class TestGenericValues:

    def test_get_missing_value_returns_none(self):
        assert settings_store.get_value("webui.session_secret") is None

    def test_set_then_get_top_level_key(self):
        settings_store.set_value("source_id", "toms-mac")
        assert settings_store.get_value("source_id") == "toms-mac"

    def test_set_then_get_nested_key_creates_table(self):
        settings_store.set_value("webui.session_secret", "abc123")
        assert settings_store.get_value("webui.session_secret") == "abc123"

    def test_set_deeply_nested_key(self):
        settings_store.set_value("endpoints.hpc_cluster.key", "sk-cluster")
        assert settings_store.get_value("endpoints.hpc_cluster.key") == "sk-cluster"

    def test_set_persists_to_disk(self):
        settings_store.set_value("webui.session_secret", "abc123")
        content = settings_store.SETTINGS_PATH.read_text()
        assert "session_secret" in content
        assert "abc123" in content

    def test_set_preserves_other_existing_values(self):
        settings_store.set_value("webui.passphrase_hash", "hash1")
        settings_store.set_value("webui.session_secret", "secret1")
        assert settings_store.get_value("webui.passphrase_hash") == "hash1"
        assert settings_store.get_value("webui.session_secret") == "secret1"

    def test_unset_removes_value(self):
        settings_store.set_value("webui.session_secret", "abc123")
        settings_store.unset_value("webui.session_secret")
        assert settings_store.get_value("webui.session_secret") is None

    def test_unset_missing_value_does_not_raise(self):
        settings_store.unset_value("never.set.this")

    def test_unset_leaves_sibling_keys_intact(self):
        settings_store.set_value("webui.passphrase_hash", "hash1")
        settings_store.set_value("webui.session_secret", "secret1")
        settings_store.unset_value("webui.session_secret")
        assert settings_store.get_value("webui.passphrase_hash") == "hash1"
        assert settings_store.get_value("webui.session_secret") is None

    def test_preserves_comments_on_partial_rewrite(self):
        settings_store.SETTINGS_PATH.write_text(
            "# a hand-written comment\n[webui]\npassphrase_hash = \"existing\"\n"
        )
        settings_store.set_value("webui.session_secret", "new-secret")
        content = settings_store.SETTINGS_PATH.read_text()
        assert "# a hand-written comment" in content
        assert "existing" in content
        assert "new-secret" in content


class TestSharedSettingsPath:

    def test_returns_none_when_unset(self):
        assert settings_store.get_shared_settings_path() is None

    def test_returns_expanded_path(self):
        settings_store.set_value("shared_settings.path", "~/shared/shared-settings.toml")
        result = settings_store.get_shared_settings_path()
        assert result is not None
        assert "~" not in str(result)
        assert str(result).endswith("shared-settings.toml")

    @pytest.mark.parametrize("name", [
        "shared-settings.toml",
        "nurikabe-lab.toml",
        "our group's rules.toml",   # spaces are fine
        "settings",                 # so is no extension at all
    ])
    def test_the_filename_is_not_special(self, name):
        """A path is stored and read back; nothing looks for a particular name.

        Worth pinning because the docs necessarily use *some* example name, and
        an example is easy to read as a requirement.
        """
        settings_store.set_value("shared_settings.path", f"/somewhere/{name}")
        assert str(settings_store.get_shared_settings_path()) == f"/somewhere/{name}"


# ---------------------------------------------------------------------------
# Professors
# ---------------------------------------------------------------------------

class TestProfessors:

    def test_no_professors_returns_empty(self):
        assert settings_store.get_professors() == {}

    def test_add_and_get_professor(self):
        netid = settings_store.add_professor("jh43", "Jeff Heller", "sk-primary")
        assert netid == "jh43"
        profs = settings_store.get_professors()
        assert profs["jh43"]["name"] == "Jeff Heller"
        assert profs["jh43"]["key"] == "sk-primary"
        assert profs["jh43"]["backup_key"] is None
        assert profs["jh43"]["netid"] == "jh43"

    def test_add_professor_with_backup_key(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary", "sk-backup")
        assert settings_store.get_professors()["jh43"]["backup_key"] == "sk-backup"

    def test_add_professor_persists_to_disk(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary")
        content = settings_store.SETTINGS_PATH.read_text()
        assert "Jeff Heller" in content
        assert "sk-primary" in content

    def test_blank_name_raises(self):
        with pytest.raises(ValueError, match="blank"):
            settings_store.add_professor("jh43", "   ", "sk-primary")

    def test_blank_key_raises(self):
        with pytest.raises(ValueError, match="blank"):
            settings_store.add_professor("jh43", "Jeff Heller", "  ")

    def test_duplicate_professor_raises(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-1")
        with pytest.raises(ValueError, match="already configured"):
            settings_store.add_professor("jh43", "Jeff Heller", "sk-2")

    def test_two_professors_both_present(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-1")
        settings_store.add_professor("as12", "Alice Smith", "sk-2")
        profs = settings_store.get_professors()
        assert set(profs.keys()) == {"jh43", "as12"}

    def test_remove_by_netid(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary")
        removed = settings_store.remove_professor("jh43")
        assert removed == "Jeff Heller"
        assert settings_store.get_professors() == {}

    def test_remove_by_display_name_case_insensitive(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary")
        assert settings_store.remove_professor("JEFF HELLER") == "Jeff Heller"

    def test_remove_unknown_raises(self):
        with pytest.raises(ValueError, match="No configured professor matches"):
            settings_store.remove_professor("nobody")

    def test_remove_leaves_others_intact(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-1")
        settings_store.add_professor("as12", "Alice Smith", "sk-2")
        settings_store.remove_professor("jh43")
        assert set(settings_store.get_professors().keys()) == {"as12"}


class TestSetProfessorKey:

    def test_replaces_primary_key(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-old")
        settings_store.set_professor_key("jh43", "sk-new")
        assert settings_store.get_professors()["jh43"]["key"] == "sk-new"

    def test_blank_key_raises(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-old")
        with pytest.raises(ValueError, match="blank"):
            settings_store.set_professor_key("jh43", "   ")

    def test_unknown_professor_raises(self):
        with pytest.raises(ValueError, match="Nobody is configured"):
            settings_store.set_professor_key("nobody", "sk-new")

    def test_leaves_backup_key_and_other_professors_intact(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-old", "sk-backup")
        settings_store.add_professor("as12", "Alice Smith", "sk-alice")
        settings_store.set_professor_key("jh43", "sk-new")
        profs = settings_store.get_professors()
        assert profs["jh43"]["backup_key"] == "sk-backup"
        assert profs["as12"]["key"] == "sk-alice"


class TestSetProfessorBackupKey:

    def test_sets_new_backup_key(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary")
        settings_store.set_professor_backup_key("jh43", "sk-backup")
        assert settings_store.get_professors()["jh43"]["backup_key"] == "sk-backup"

    def test_replaces_existing_backup_key(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary", "sk-old-backup")
        settings_store.set_professor_backup_key("jh43", "sk-new-backup")
        assert settings_store.get_professors()["jh43"]["backup_key"] == "sk-new-backup"

    def test_blank_clears_backup_key(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary", "sk-old-backup")
        settings_store.set_professor_backup_key("jh43", "")
        assert settings_store.get_professors()["jh43"]["backup_key"] is None

    def test_none_clears_backup_key(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary", "sk-old-backup")
        settings_store.set_professor_backup_key("jh43", None)
        assert settings_store.get_professors()["jh43"]["backup_key"] is None

    def test_clearing_when_unset_does_not_raise(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary")
        settings_store.set_professor_backup_key("jh43", "")
        assert settings_store.get_professors()["jh43"]["backup_key"] is None

    def test_unknown_professor_raises(self):
        with pytest.raises(ValueError, match="Nobody is configured"):
            settings_store.set_professor_backup_key("nobody", "sk-backup")

    def test_leaves_primary_key_and_other_professors_intact(self):
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary")
        settings_store.add_professor("as12", "Alice Smith", "sk-alice")
        settings_store.set_professor_backup_key("jh43", "sk-backup")
        profs = settings_store.get_professors()
        assert profs["jh43"]["key"] == "sk-primary"
        assert profs["as12"]["backup_key"] is None


# ---------------------------------------------------------------------------
# get_source_id / set_source_id
# ---------------------------------------------------------------------------

class TestSourceId:

    def test_defaults_to_hostname_when_unconfigured(self):
        with patch("platform.node", return_value="toms-mac"):
            assert settings_store.get_source_id() == "toms-mac"

    def test_falls_back_to_unknown_machine_when_hostname_empty(self):
        with patch("platform.node", return_value=""):
            assert settings_store.get_source_id() == "unknown-machine"

    def test_explicit_source_id_overrides_hostname(self):
        settings_store.set_source_id("custom-id")
        with patch("platform.node", return_value="toms-mac"):
            assert settings_store.get_source_id() == "custom-id"


# ---------------------------------------------------------------------------
# Usage sources
# ---------------------------------------------------------------------------

def _person(netid="smith", name="Prof. Smith"):
    """Add somebody, since a usage folder now belongs to a person."""
    settings_store.add_professor(netid, name, "sk-test")


class TestConfiguredSources:

    def test_empty_when_no_file(self):
        assert settings_store.get_configured_sources() == []

    def test_a_person_with_no_folder_is_not_a_source(self):
        _person()
        assert settings_store.get_configured_sources() == []

    def test_returns_the_folder_set_on_a_person(self):
        _person()
        settings_store.set_professor_usage_source(
            "smith", "/tmp/smith-shared", mode="shared-write")
        sources = settings_store.get_configured_sources()
        assert len(sources) == 1
        assert sources[0].path == "/tmp/smith-shared"
        assert sources[0].mode == "shared-write"
        assert sources[0].professor == "smith"

    def test_the_label_is_the_persons_name(self):
        """Nobody types a label any more, so it is what they are already called."""
        _person("jh43", "Jeff Heller")
        settings_store.set_professor_usage_source("jh43", "/tmp/jh43")
        assert settings_store.get_configured_sources()[0].label == "Jeff Heller"

    def test_one_folder_each(self):
        _person("smith")
        _person("johnson", "Prof. Johnson")
        settings_store.set_professor_usage_source("smith", "/tmp/smith")
        settings_store.set_professor_usage_source("johnson", "/tmp/johnson")
        assert sorted(s.path for s in settings_store.get_configured_sources()) == [
            "/tmp/johnson", "/tmp/smith"]

    def test_setting_a_second_folder_replaces_the_first(self):
        _person()
        settings_store.set_professor_usage_source("smith", "/tmp/smith-v1")
        settings_store.set_professor_usage_source("smith", "/tmp/smith-v2")
        sources = settings_store.get_configured_sources()
        assert len(sources) == 1
        assert sources[0].path == "/tmp/smith-v2"

    def test_read_only_is_what_you_get_without_saying(self):
        _person()
        settings_store.set_professor_usage_source("smith", "/tmp/smith")
        assert settings_store.get_configured_sources()[0].mode == "read-only"


class TestSourcesWrittenTheOldWay:
    """An installation set up before folders belonged to people keeps working."""

    def _write_old_shape(self, **extra):
        lines = ['[usage_sources]', '[[usage_sources.external]]',
                 'label = "Prof. Smith"', 'path = "/tmp/old-smith"',
                 'mode = "shared-write"', 'professor = "smith"']
        for key, value in extra.items():
            lines.append(f'{key} = "{value}"')
        settings_store.SETTINGS_PATH.write_text("\n".join(lines) + "\n")

    def test_an_old_entry_is_still_read(self):
        self._write_old_shape()
        sources = settings_store.get_configured_sources()
        assert len(sources) == 1
        assert sources[0].path == "/tmp/old-smith"
        assert sources[0].label == "Prof. Smith"

    def test_a_folder_on_the_person_wins_over_an_old_entry_for_them(self):
        """Otherwise the same person would be counted twice, from two folders."""
        self._write_old_shape()
        _person()
        settings_store.set_professor_usage_source("smith", "/tmp/new-smith")
        sources = settings_store.get_configured_sources()
        assert [s.path for s in sources] == ["/tmp/new-smith"]

    def test_an_old_entry_for_somebody_else_is_kept(self):
        self._write_old_shape()
        _person("johnson", "Prof. Johnson")
        settings_store.set_professor_usage_source("johnson", "/tmp/johnson")
        assert sorted(s.path for s in settings_store.get_configured_sources()) == [
            "/tmp/johnson", "/tmp/old-smith"]

    def test_label_defaults_to_path_when_missing(self):
        settings_store.SETTINGS_PATH.write_text(
            '[usage_sources]\n[[usage_sources.external]]\npath = "/some/path"\n')
        assert settings_store.get_configured_sources()[0].label == "/some/path"

    def test_entries_missing_path_are_skipped(self):
        settings_store.SETTINGS_PATH.write_text(
            '[usage_sources]\n[[usage_sources.external]]\nlabel = "no path here"\n'
        )
        assert settings_store.get_configured_sources() == []


class TestSharedWriteSource:

    def test_returns_none_when_no_sources(self):
        assert settings_store.get_shared_write_source("smith") is None

    def test_finds_matching_shared_write_source(self):
        _person()
        settings_store.set_professor_usage_source(
            "smith", "/tmp/smith-shared", mode="shared-write")
        result = settings_store.get_shared_write_source("smith")
        assert result is not None
        assert result.path == "/tmp/smith-shared"

    def test_matches_case_insensitively(self):
        _person("Smith")
        settings_store.set_professor_usage_source(
            "Smith", "/tmp/smith-shared", mode="shared-write")
        assert settings_store.get_shared_write_source("SMITH") is not None

    def test_read_only_source_never_matches(self):
        _person("johnson", "Prof. Johnson")
        settings_store.set_professor_usage_source(
            "johnson", "/tmp/johnson", mode="read-only")
        assert settings_store.get_shared_write_source("johnson") is None

    def test_source_for_different_professor_does_not_match(self):
        _person()
        settings_store.set_professor_usage_source(
            "smith", "/tmp/smith-shared", mode="shared-write")
        assert settings_store.get_shared_write_source("johnson") is None


class TestSetProfessorUsageSource:

    def test_invalid_mode_raises(self):
        _person()
        with pytest.raises(ValueError, match="mode"):
            settings_store.set_professor_usage_source(
                "smith", "/tmp/smith", mode="read-write")

    def test_a_folder_is_needed(self):
        _person()
        with pytest.raises(ValueError, match="location"):
            settings_store.set_professor_usage_source("smith", "   ")

    def test_somebody_who_was_never_added_raises(self):
        """The netID would otherwise become a new, keyless person nobody meant."""
        with pytest.raises(ValueError, match="added yet"):
            settings_store.set_professor_usage_source("nobody", "/tmp/nobody")

    def test_matches_however_it_was_capitalised(self):
        _person("smith")
        assert settings_store.set_professor_usage_source("SMITH", "/tmp/x") == "smith"

    def test_surrounding_space_is_dropped(self):
        _person()
        settings_store.set_professor_usage_source("smith", "  /tmp/smith  ")
        assert settings_store.get_configured_sources()[0].path == "/tmp/smith"

    def test_persists_to_disk(self):
        _person()
        settings_store.set_professor_usage_source("smith", "/tmp/smith")
        assert "usage_path" in settings_store.SETTINGS_PATH.read_text()

    def test_their_key_is_left_alone(self):
        _person()
        settings_store.set_professor_usage_source("smith", "/tmp/smith")
        assert settings_store.get_professors()["smith"]["key"] == "sk-test"


class TestClearProfessorUsageSource:

    def test_clearing_returns_true_and_forgets_the_folder(self):
        _person()
        settings_store.set_professor_usage_source("smith", "/tmp/smith")
        assert settings_store.clear_professor_usage_source("smith") is True
        assert settings_store.get_configured_sources() == []

    def test_clearing_when_there_was_nothing_returns_false(self):
        _person()
        assert settings_store.clear_professor_usage_source("smith") is False

    def test_clearing_for_somebody_unknown_returns_false(self):
        assert settings_store.clear_professor_usage_source("nobody") is False

    def test_the_person_is_not_removed_with_their_folder(self):
        _person()
        settings_store.set_professor_usage_source("smith", "/tmp/smith")
        settings_store.clear_professor_usage_source("smith")
        assert settings_store.get_professors()["smith"]["key"] == "sk-test"

    def test_clearing_one_leaves_another_persons_folder_alone(self):
        _person("smith")
        _person("johnson", "Prof. Johnson")
        settings_store.set_professor_usage_source("smith", "/tmp/smith")
        settings_store.set_professor_usage_source("johnson", "/tmp/johnson")
        settings_store.clear_professor_usage_source("smith")
        assert [s.path for s in settings_store.get_configured_sources()] == ["/tmp/johnson"]


class TestExternalSourceResolvedPath:

    def test_expands_user_home(self):
        src = ExternalSource(label="x", path="~/data", mode="read-only")
        resolved = src.resolved_path()
        assert "~" not in str(resolved)
