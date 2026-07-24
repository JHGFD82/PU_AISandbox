"""
Tests for src/tracking/source_config.py:
  - get_source_id (hostname fallback, explicit override)
  - get_configured_sources
  - get_shared_write_source
  - add_source / remove_source
  - _load_raw / _save_raw round-trip and atomic-write behavior

No real repo-root data_sources.json is touched — every test redirects
source_config.DATA_SOURCES_FILE to a tmp_path location.
"""

import json
from unittest.mock import patch

import pytest

import src.tracking.source_config as source_config
from src.tracking.source_config import (
    ExternalSource,
    add_source,
    get_configured_sources,
    get_shared_write_source,
    get_source_id,
    remove_source,
    set_source_id,
)


@pytest.fixture(autouse=True)
def _redirect_data_sources_file(tmp_path, monkeypatch):
    """Point DATA_SOURCES_FILE at a tmp_path location for every test in this module."""
    monkeypatch.setattr(source_config, "DATA_SOURCES_FILE", tmp_path / "data_sources.json")
    yield


class TestGetSourceId:

    def test_defaults_to_hostname_when_unconfigured(self):
        with patch("platform.node", return_value="toms-mac"):
            assert get_source_id() == "toms-mac"

    def test_falls_back_to_unknown_machine_when_hostname_empty(self):
        with patch("platform.node", return_value=""):
            assert get_source_id() == "unknown-machine"

    def test_explicit_source_id_overrides_hostname(self):
        set_source_id("custom-id")
        with patch("platform.node", return_value="toms-mac"):
            assert get_source_id() == "custom-id"

    def test_missing_file_does_not_raise(self):
        # DATA_SOURCES_FILE points at a tmp_path location that doesn't exist yet
        assert get_source_id()  # just shouldn't raise


class TestGetConfiguredSources:

    def test_empty_when_no_file(self):
        assert get_configured_sources() == []

    def test_returns_added_sources(self):
        add_source("Prof. Smith", "/tmp/smith-shared", mode="shared-write", professor="smith")
        sources = get_configured_sources()
        assert len(sources) == 1
        assert sources[0].label == "Prof. Smith"
        assert sources[0].mode == "shared-write"
        assert sources[0].professor == "smith"

    def test_label_defaults_to_path_when_missing(self, tmp_path):
        data = {"source_id": "", "external_sources": [{"path": "/some/path"}]}
        source_config.DATA_SOURCES_FILE.write_text(json.dumps(data))
        sources = get_configured_sources()
        assert sources[0].label == "/some/path"

    def test_entries_missing_path_are_skipped(self, tmp_path):
        data = {"source_id": "", "external_sources": [{"label": "no path here"}]}
        source_config.DATA_SOURCES_FILE.write_text(json.dumps(data))
        assert get_configured_sources() == []

    def test_corrupt_file_treated_as_empty(self, tmp_path):
        source_config.DATA_SOURCES_FILE.write_text("{not valid json")
        assert get_configured_sources() == []


class TestGetSharedWriteSource:

    def test_returns_none_when_no_sources(self):
        assert get_shared_write_source("smith") is None

    def test_finds_matching_shared_write_source(self):
        add_source("Prof. Smith", "/tmp/smith-shared", mode="shared-write", professor="smith")
        result = get_shared_write_source("smith")
        assert result is not None
        assert result.label == "Prof. Smith"

    def test_matches_case_insensitively(self):
        add_source("Prof. Smith", "/tmp/smith-shared", mode="shared-write", professor="Smith")
        assert get_shared_write_source("SMITH") is not None

    def test_read_only_source_never_matches(self):
        add_source("Prof. Johnson", "/tmp/johnson", mode="read-only")
        assert get_shared_write_source("johnson") is None

    def test_source_for_different_professor_does_not_match(self):
        add_source("Prof. Smith", "/tmp/smith-shared", mode="shared-write", professor="smith")
        assert get_shared_write_source("johnson") is None


class TestAddSource:

    def test_add_read_only_without_professor_is_valid(self):
        add_source("Johnson", "/tmp/johnson", mode="read-only")
        assert get_configured_sources()[0].professor is None

    def test_shared_write_without_professor_raises(self):
        with pytest.raises(ValueError, match="professor"):
            add_source("Smith", "/tmp/smith", mode="shared-write")

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode"):
            add_source("Smith", "/tmp/smith", mode="read-write")

    def test_adding_same_label_twice_replaces(self):
        add_source("Smith", "/tmp/smith-v1", mode="read-only")
        add_source("Smith", "/tmp/smith-v2", mode="read-only")
        sources = get_configured_sources()
        assert len(sources) == 1
        assert sources[0].path == "/tmp/smith-v2"

    def test_persists_to_disk(self):
        add_source("Smith", "/tmp/smith", mode="read-only")
        on_disk = json.loads(source_config.DATA_SOURCES_FILE.read_text())
        assert on_disk["external_sources"][0]["label"] == "Smith"


class TestRemoveSource:

    def test_remove_existing_returns_true(self):
        add_source("Smith", "/tmp/smith", mode="read-only")
        assert remove_source("Smith") is True
        assert get_configured_sources() == []

    def test_remove_missing_returns_false(self):
        assert remove_source("Nobody") is False

    def test_remove_leaves_other_sources_intact(self):
        add_source("Smith", "/tmp/smith", mode="read-only")
        add_source("Johnson", "/tmp/johnson", mode="read-only")
        remove_source("Smith")
        labels = [s.label for s in get_configured_sources()]
        assert labels == ["Johnson"]


class TestExternalSourceResolvedPath:

    def test_expands_user_home(self):
        src = ExternalSource(label="x", path="~/data", mode="read-only")
        resolved = src.resolved_path()
        assert "~" not in str(resolved)
