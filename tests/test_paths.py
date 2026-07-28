"""Tests for src/paths.py — where an installation keeps the things that belong to its user.

The point of this module is that upgrading the package must not take a
person's API keys and usage history with it, so most of these tests are
about resolution surviving change: a marker that isn't there yet, an
override, a folder that moves.
"""

from pathlib import Path

import pytest

from src import paths


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the marker file and the environment override somewhere disposable."""
    monkeypatch.setattr(paths, "INSTALL_MARKER", tmp_path / ".installation")
    monkeypatch.delenv(paths.EXTRAS_ENV_VAR, raising=False)


class TestInstallMarker:
    def test_absent_marker_means_not_installed(self):
        """A freshly downloaded package has no marker — that is the whole signal."""
        assert paths.read_install_marker() is None
        assert paths.is_installed() is False

    def test_written_marker_is_read_back(self, tmp_path):
        extras = tmp_path / "PU_AISandbox_data"
        paths.write_install_marker(extras)
        assert paths.read_install_marker() == extras
        assert paths.is_installed() is True

    def test_marker_expands_a_home_relative_path(self, tmp_path):
        paths.INSTALL_MARKER.write_text("~/PU_AISandbox_data\n", encoding="utf-8")
        assert paths.read_install_marker() == Path.home() / "PU_AISandbox_data"

    def test_blank_marker_is_treated_as_absent(self):
        paths.INSTALL_MARKER.write_text("   \n", encoding="utf-8")
        assert paths.read_install_marker() is None

    def test_unreadable_marker_is_treated_as_absent(self, tmp_path):
        """A directory where the marker should be must not crash startup."""
        paths.INSTALL_MARKER.mkdir()
        assert paths.read_install_marker() is None


class TestResolution:
    def test_environment_override_wins(self, tmp_path, monkeypatch):
        paths.write_install_marker(tmp_path / "from_marker")
        monkeypatch.setenv(paths.EXTRAS_ENV_VAR, str(tmp_path / "from_env"))
        assert paths.extras_root() == tmp_path / "from_env"
        assert paths.is_installed() is True

    def test_marker_used_when_no_override(self, tmp_path):
        paths.write_install_marker(tmp_path / "chosen")
        assert paths.extras_root() == tmp_path / "chosen"

    def test_falls_back_to_the_package_when_never_set_up(self):
        """An installation predating all of this keeps working rather than
        behaving as though its data had vanished."""
        assert paths.extras_root() == paths.PACKAGE_ROOT

    def test_everything_hangs_off_one_root(self, tmp_path):
        extras = tmp_path / "extras"
        paths.write_install_marker(extras)
        assert paths.settings_path() == extras / ".settings"
        assert paths.model_catalog_path() == extras / "model_catalog.json"
        assert paths.data_root() == extras / "data"

    def test_catalog_stays_where_it_was_until_set_up(self):
        """Before setup the catalog is still under src/, where it used to live."""
        assert paths.model_catalog_path() == paths.PACKAGE_ROOT / "src" / "model_catalog.json"

    def test_moving_the_extras_folder_moves_everything(self, tmp_path):
        paths.write_install_marker(tmp_path / "before")
        first = paths.data_root()
        paths.write_install_marker(tmp_path / "after")
        assert paths.data_root() != first
        assert paths.data_root() == tmp_path / "after" / "data"


class TestCloudSyncDetection:
    """Warning, never refusing — where someone keeps their files is their call.

    The reason to warn is narrow: the folder holds API keys, so syncing it
    puts those keys on every device signed into that account.
    """

    @pytest.mark.parametrize("relative, expected", [
        ("Library/CloudStorage/OneDrive-PrincetonUniversity/sandbox", "OneDrive"),
        ("Library/CloudStorage/Dropbox/sandbox", "Dropbox"),
        ("Library/CloudStorage/GoogleDrive-me@x.edu/sandbox", "GoogleDrive"),
        ("Library/Mobile Documents/com~apple~CloudDocs/sandbox", "iCloud"),
        ("Dropbox/sandbox", "Dropbox"),
        ("OneDrive/sandbox", "OneDrive"),
        ("Google Drive/My Drive/sandbox", "Google Drive"),
    ])
    def test_detects_common_sync_locations(self, relative, expected):
        got = paths.describe_cloud_sync(Path.home() / relative)
        assert got is not None
        assert expected in got

    @pytest.mark.parametrize("relative", [
        "PU_AISandbox_data",
        "Documents/Code/PU_AISandbox_data",
        "projects/sandbox/data",
    ])
    def test_ordinary_folders_are_not_flagged(self, relative):
        assert paths.describe_cloud_sync(Path.home() / relative) is None

    def test_warning_names_the_service_and_says_why(self):
        """A warning nobody can act on is one everybody dismisses."""
        msg = paths.cloud_sync_warning(Path.home() / "Dropbox" / "sandbox")
        assert msg is not None
        assert "Dropbox" in msg
        assert "API keys" in msg
        assert "budget" in msg

    def test_no_warning_for_an_ordinary_folder(self):
        assert paths.cloud_sync_warning(Path.home() / "PU_AISandbox_data") is None

    def test_names_the_actual_provider_not_a_list_of_candidates(self):
        """macOS names the CloudStorage folder after the service; use that."""
        got = paths.describe_cloud_sync(
            Path.home() / "Library/CloudStorage/OneDrive-PrincetonUniversity/sandbox"
        )
        assert got == "OneDrive"

    def test_detects_icloud_desktop_and_documents_syncing(self, tmp_path, monkeypatch):
        """The dangerous one: enabling this syncs ~/Documents with no change to the path.

        A folder chosen today can start syncing tomorrow, because it is a
        single checkbox in System Settings. Nothing about the path itself
        changes, so this is detected by the container iCloud creates.
        """
        fake_home = tmp_path / "home"
        (fake_home / "Library/Mobile Documents/com~apple~CloudDocs/Documents").mkdir(parents=True)
        (fake_home / "Documents").mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        got = paths.describe_cloud_sync(fake_home / "Documents" / "PU_AISandbox_data")
        assert got is not None
        assert "iCloud" in got

    def test_documents_not_flagged_when_that_syncing_is_off(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        (fake_home / "Documents").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        assert paths.describe_cloud_sync(fake_home / "Documents" / "PU_AISandbox_data") is None
