"""Tests for src/first_run.py — what a not-yet-set-up copy of the sandbox should do.

The stakes here are the highest in the project: a wrong turn costs API keys
and months of usage history. So the tests lean on the refusals — the paths
that must be *unreachable*, not merely discouraged.
"""

from pathlib import Path

import pytest

from src import first_run, paths


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Give each test its own package directory, default location and templates."""
    package = tmp_path / "package"
    package.mkdir()
    templates = package / "templates"
    templates.mkdir()
    (templates / "settings.template").write_text("# starting point\n", encoding="utf-8")
    (templates / "model_catalog.template.json").write_text('{"models": {}}\n', encoding="utf-8")

    monkeypatch.setattr(paths, "PACKAGE_ROOT", package)
    monkeypatch.setattr(paths, "TEMPLATES_DIR", templates)
    monkeypatch.setattr(paths, "INSTALL_MARKER", package / ".installation")
    monkeypatch.setattr(paths, "DEFAULT_EXTRAS_ROOT", tmp_path / "PU_AISandbox_data")
    return package


def _make_setup(root: Path, *, people: int = 1, months: int = 0, catalog: bool = True):
    """Write a plausible extras folder to look at."""
    root.mkdir(parents=True, exist_ok=True)
    tables = "\n".join(
        f'[professors.p{i}]\nname = "Person {i}"\nkey = "sk-{i}"\n' for i in range(people)
    )
    (root / paths.SETTINGS_FILENAME).write_text(tables, encoding="utf-8")
    if catalog:
        (root / paths.MODEL_CATALOG_FILENAME).write_text('{"models": {}}', encoding="utf-8")
    data = root / paths.DATA_DIRNAME
    data.mkdir(exist_ok=True)
    for i in range(months):
        archive = data / "archives" / "p0"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / f"2026-{i + 1:02d}.json").write_text("{}", encoding="utf-8")
    return root


class TestInspect:
    def test_reports_what_is_there(self, tmp_path):
        root = _make_setup(tmp_path / "extras", people=3, months=4)
        got = first_run.inspect_extras(root)
        assert got.people == 3
        assert got.months == 4
        assert got.has_catalog is True
        assert got.is_usable is True

    def test_empty_folder_is_not_usable(self, tmp_path):
        got = first_run.inspect_extras(tmp_path / "nothing")
        assert got.settings_file is None
        assert got.is_usable is False

    def test_history_without_settings_still_counts(self, tmp_path):
        """Someone may have moved their keys but left history behind."""
        root = tmp_path / "extras"
        (root / paths.DATA_DIRNAME).mkdir(parents=True)
        (root / paths.DATA_DIRNAME / "token_usage_jh43.json").write_text("{}", encoding="utf-8")
        assert first_run.inspect_extras(root).is_usable is True

    def test_unreadable_settings_counts_nobody_rather_than_raising(self, tmp_path):
        root = tmp_path / "extras"
        root.mkdir()
        (root / paths.SETTINGS_FILENAME).write_text("this is not toml {{{", encoding="utf-8")
        got = first_run.inspect_extras(root)
        assert got.people == 0
        assert got.is_usable is True     # the file is there; it just didn't parse

    def test_finds_the_catalog_in_its_old_place_inside_the_package(self, _isolate):
        (_isolate / "src").mkdir()
        (_isolate / "src" / paths.MODEL_CATALOG_FILENAME).write_text("{}", encoding="utf-8")
        (_isolate / paths.SETTINGS_FILENAME).write_text("", encoding="utf-8")
        assert first_run.inspect_extras(_isolate, in_package=True).has_catalog is True


class TestFindExisting:
    def test_nothing_anywhere_means_a_genuine_first_install(self):
        assert first_run.find_existing() == []

    def test_finds_a_setup_at_the_default_location(self):
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=2)
        found = first_run.find_existing()
        assert len(found) == 1
        assert found[0].people == 2
        assert found[0].in_package is False

    def test_finds_an_older_installation_inside_the_package(self, _isolate):
        """The arrangement that made upgrading destructive."""
        _make_setup(_isolate, people=1, months=2)
        found = first_run.find_existing()
        assert len(found) == 1
        assert found[0].in_package is True

    def test_default_location_is_offered_before_the_package(self, _isolate):
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=1)
        _make_setup(_isolate, people=1)
        found = first_run.find_existing()
        assert [c.in_package for c in found] == [False, True]


class TestInitialize:
    def test_creates_the_folder_and_copies_the_templates(self, tmp_path):
        target = tmp_path / "new"
        copied = first_run.initialize_extras(target)
        assert (target / paths.SETTINGS_FILENAME).is_file()
        assert (target / paths.MODEL_CATALOG_FILENAME).is_file()
        assert (target / paths.DATA_DIRNAME).is_dir()
        assert paths.SETTINGS_FILENAME in copied

    def test_new_settings_file_is_owner_only(self, tmp_path):
        """It holds API keys from the first moment it exists."""
        target = tmp_path / "new"
        first_run.initialize_extras(target)
        mode = (target / paths.SETTINGS_FILENAME).stat().st_mode & 0o777
        assert mode == 0o600

    def test_refuses_to_initialise_over_an_existing_setup(self, tmp_path):
        """The highest-stakes refusal in the project.

        Someone upgrading who picks "first time" by mistake must not be able
        to erase their own keys and history. This is control flow, not a
        confirmation prompt — prompts get clicked past.
        """
        existing = _make_setup(tmp_path / "mine", people=3)
        with pytest.raises(FileExistsError):
            first_run.initialize_extras(existing)
        # and it is genuinely untouched
        assert "sk-0" in (existing / paths.SETTINGS_FILENAME).read_text()


class TestMoveOutOfPackage:
    def test_moves_settings_catalog_and_data(self, _isolate, tmp_path):
        _make_setup(_isolate, people=2, months=1)
        destination = tmp_path / "extras"
        moved = first_run.move_out_of_package(destination)

        assert (destination / paths.SETTINGS_FILENAME).is_file()
        assert (destination / paths.MODEL_CATALOG_FILENAME).is_file()
        assert (destination / paths.DATA_DIRNAME).is_dir()
        assert not (_isolate / paths.SETTINGS_FILENAME).exists()
        assert not (_isolate / paths.DATA_DIRNAME).exists()
        assert any("API keys" in m for m in moved)

    def test_keys_are_owner_only_after_moving(self, _isolate, tmp_path):
        _make_setup(_isolate, people=1)
        (_isolate / paths.SETTINGS_FILENAME).chmod(0o644)
        destination = tmp_path / "extras"
        first_run.move_out_of_package(destination)
        mode = (destination / paths.SETTINGS_FILENAME).stat().st_mode & 0o777
        assert mode == 0o600

    def test_moves_the_catalog_from_its_old_place_under_src(self, _isolate, tmp_path):
        (_isolate / "src").mkdir()
        (_isolate / "src" / paths.MODEL_CATALOG_FILENAME).write_text('{"models":{}}', encoding="utf-8")
        destination = tmp_path / "extras"
        first_run.move_out_of_package(destination)
        assert (destination / paths.MODEL_CATALOG_FILENAME).is_file()

    def test_refuses_to_move_onto_an_existing_settings_file(self, _isolate, tmp_path):
        _make_setup(_isolate, people=1)
        destination = _make_setup(tmp_path / "occupied", people=5)
        with pytest.raises(FileExistsError):
            first_run.move_out_of_package(destination)
        assert "sk-4" in (destination / paths.SETTINGS_FILENAME).read_text()
        assert (_isolate / paths.SETTINGS_FILENAME).is_file()  # nothing moved


class TestCompleteSetup:
    def test_writes_the_marker_and_stops_asking(self, tmp_path):
        extras = tmp_path / "extras"
        assert paths.is_installed() is False
        first_run.complete_setup(extras)
        assert paths.is_installed() is True
        assert paths.extras_root() == extras

    def test_creates_the_folder_if_it_is_not_there(self, tmp_path):
        extras = tmp_path / "made" / "on" / "demand"
        first_run.complete_setup(extras)
        assert extras.is_dir()
