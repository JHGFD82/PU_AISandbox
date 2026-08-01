"""
Tests for src/settings_store.py:
  - get_value / set_value / unset_value (generic dotted-path access)
  - get_shared_settings_path
  - get_professors / add_professor / remove_professor
  - get_source_id / set_source_id
  - get_configured_sources / add_source / remove_source / get_shared_write_source

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

class TestConfiguredSources:

    def test_empty_when_no_file(self):
        assert settings_store.get_configured_sources() == []

    def test_returns_added_sources(self):
        settings_store.add_source("Prof. Smith", "/tmp/smith-shared", mode="shared-write", professor="smith")
        sources = settings_store.get_configured_sources()
        assert len(sources) == 1
        assert sources[0].label == "Prof. Smith"
        assert sources[0].mode == "shared-write"
        assert sources[0].professor == "smith"

    def test_label_defaults_to_path_when_missing(self):
        settings_store.set_value("usage_sources.source_id", "")  # ensure table exists
        doc_path = settings_store.SETTINGS_PATH
        doc_path.write_text('[usage_sources]\n[[usage_sources.external]]\npath = "/some/path"\n')
        sources = settings_store.get_configured_sources()
        assert sources[0].label == "/some/path"

    def test_entries_missing_path_are_skipped(self):
        settings_store.SETTINGS_PATH.write_text(
            '[usage_sources]\n[[usage_sources.external]]\nlabel = "no path here"\n'
        )
        assert settings_store.get_configured_sources() == []


class TestSharedWriteSource:

    def test_returns_none_when_no_sources(self):
        assert settings_store.get_shared_write_source("smith") is None

    def test_finds_matching_shared_write_source(self):
        settings_store.add_source("Prof. Smith", "/tmp/smith-shared", mode="shared-write", professor="smith")
        result = settings_store.get_shared_write_source("smith")
        assert result is not None
        assert result.label == "Prof. Smith"

    def test_matches_case_insensitively(self):
        settings_store.add_source("Prof. Smith", "/tmp/smith-shared", mode="shared-write", professor="Smith")
        assert settings_store.get_shared_write_source("SMITH") is not None

    def test_read_only_source_never_matches(self):
        settings_store.add_source("Prof. Johnson", "/tmp/johnson", mode="read-only", professor="smith")
        assert settings_store.get_shared_write_source("johnson") is None

    def test_source_for_different_professor_does_not_match(self):
        settings_store.add_source("Prof. Smith", "/tmp/smith-shared", mode="shared-write", professor="smith")
        assert settings_store.get_shared_write_source("johnson") is None


class TestAddSource:

    def test_a_read_only_source_needs_a_professor_too(self):
        """Whose spending is being followed is a decision per person: one may
        be content for work to be done from a shared folder while another
        wants only their spending seen from it."""
        with pytest.raises(ValueError, match="professor"):
            settings_store.add_source("Johnson", "/tmp/johnson", mode="read-only")

    def test_shared_write_without_professor_raises(self):
        with pytest.raises(ValueError, match="professor"):
            settings_store.add_source("Smith", "/tmp/smith", mode="shared-write")

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode"):
            settings_store.add_source("Smith", "/tmp/smith", mode="read-write", professor="smith")

    def test_adding_same_label_twice_replaces(self):
        settings_store.add_source("Smith", "/tmp/smith-v1", mode="read-only", professor="smith")
        settings_store.add_source("Smith", "/tmp/smith-v2", mode="read-only", professor="smith")
        sources = settings_store.get_configured_sources()
        assert len(sources) == 1
        assert sources[0].path == "/tmp/smith-v2"

    def test_persists_to_disk(self):
        settings_store.add_source("Smith", "/tmp/smith", mode="read-only", professor="smith")
        content = settings_store.SETTINGS_PATH.read_text()
        assert "Smith" in content


class TestRemoveSource:

    def test_remove_existing_returns_true(self):
        settings_store.add_source("Smith", "/tmp/smith", mode="read-only", professor="smith")
        assert settings_store.remove_source("Smith") is True
        assert settings_store.get_configured_sources() == []

    def test_remove_missing_returns_false(self):
        assert settings_store.remove_source("Nobody") is False

    def test_remove_leaves_other_sources_intact(self):
        settings_store.add_source("Smith", "/tmp/smith", mode="read-only", professor="smith")
        settings_store.add_source("Johnson", "/tmp/johnson", mode="read-only", professor="johnson")
        settings_store.remove_source("Smith")
        labels = [s.label for s in settings_store.get_configured_sources()]
        assert labels == ["Johnson"]


class TestExternalSourceResolvedPath:

    def test_expands_user_home(self):
        src = ExternalSource(label="x", path="~/data", mode="read-only")
        resolved = src.resolved_path()
        assert "~" not in str(resolved)
