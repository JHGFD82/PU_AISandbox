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


class TestFindExisting:
    def test_nothing_anywhere_means_a_genuine_first_install(self):
        assert first_run.find_existing() == []

    def test_finds_a_setup_at_the_default_location(self):
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=2)
        found = first_run.find_existing()
        assert len(found) == 1
        assert found[0].people == 2

    def test_a_setup_inside_the_package_is_not_offered(self, _isolate):
        """The package holds code. Anything found there is not someone's data."""
        _make_setup(_isolate, people=1, months=2)
        assert first_run.find_existing() == []


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


class TestAFreshInstallLeavesYouReady:
    """What setup leaves behind, and whether a person can act on it.

    Setup makes a folder and three files, none of which does anything on its
    own: there is nobody to bill and no model to send anything to. What matters
    is that both gaps are named, and that walking into either produces an
    explanation rather than a fault.
    """

    def test_it_creates_a_file_for_every_template_the_package_ships(self, tmp_path):
        """Against the real templates, not this file's stubs.

        The stub fixture above ships two templates; the package ships three, and
        the third — preferences.toml, where every plugin's adjustable settings
        are listed for you — is the one somebody actually opens.
        """
        import shutil

        from src import first_run, paths

        real_templates = Path(__file__).resolve().parent.parent / "templates"
        expected = {
            "settings.template": "settings.toml",
            "preferences.template.toml": "preferences.toml",
            "model_catalog.template.json": "model_catalog.json",
        }
        for template in expected:
            assert (real_templates / template).exists(), template
            shutil.copy(real_templates / template, paths.TEMPLATES_DIR / template)

        extras = tmp_path / "data"
        first_run.initialize_extras(extras)
        first_run.complete_setup(extras)
        made = {f.name for f in extras.rglob("*") if f.is_file()}
        assert made == set(expected.values())

    def test_it_ships_no_models(self):
        """Which models exist depends on the institution, not on this software.

        Shipping a guess hands somebody names their sandbox may not offer, and
        a price that may not be theirs.
        """
        import json
        from pathlib import Path

        template = (Path(__file__).resolve().parent.parent / "templates"
                    / "model_catalog.template.json")
        assert json.loads(template.read_text())["models"] == {}

    def test_it_still_ships_the_things_that_are_not_a_guess(self):
        """Pricing unit, budget and provider names are the same everywhere."""
        import json
        from pathlib import Path

        template = (Path(__file__).resolve().parent.parent / "templates"
                    / "model_catalog.template.json")
        config = json.loads(template.read_text())["config"]
        assert config["pricing_unit"] == 1_000_000
        assert "monthly_limit" in config
        assert config["provider_map"]["google"] == "vertex-ai"

    def test_setup_says_what_is_still_missing_and_what_to_try(self):
        from src.setup_prompts import _next_steps

        text = _next_steps()
        # The two gaps, in the order they are needed.
        assert "settings add-professor" in text
        assert text.index("add-professor") < text.index("model")
        # Where to find out which models to add.
        assert "documentation" in text
        # An external endpoint needs none at all, which is easy to miss.
        assert "external endpoint" in text
        # Something to try, and where to read more.
        assert "prompt" in text and "--dry-run" in text
        assert "usage report" in text
        assert "README.md" in text

    def test_the_readme_says_the_same_thing(self):
        """Setup's message scrolls away; the README is still there tomorrow."""
        from pathlib import Path

        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        assert "A fresh copy has no models in it" in readme
        assert "Your first five minutes" in readme
        assert "--dry-run" in readme


def _with_shared_folder(root: Path, netid: str, folder: Path, *,
                        mode: str = "shared-write", months=(), conversations: int = 0):
    """Configure one person whose work is kept somewhere other than *root*."""
    settings = root / paths.SETTINGS_FILENAME
    settings.write_text(
        (settings.read_text(encoding="utf-8") if settings.exists() else "")
        + f'[professors.{netid}]\nname = "{netid.title()}"\nkey = "sk"\n'
          f'usage_path = "{folder}"\nusage_mode = "{mode}"\n',
        encoding="utf-8")
    (folder / "archives").mkdir(parents=True, exist_ok=True)
    for month in months:
        (folder / "archives" / f"{month}.json").write_text("{}", encoding="utf-8")
    for i in range(conversations):
        (folder / paths.CONVERSATIONS_DIRNAME / f"c_{i:016x}").mkdir(parents=True)
    return folder


class TestItFollowsTheSettingsToWhereTheWorkIs:
    """A settings location is not necessarily where anybody's work is. It says
    where, per person, and until this read those settings it reported a folder
    full of somebody's history as holding nothing at all."""

    def test_a_shared_folder_is_found_and_named(self, tmp_path):
        root = _make_setup(tmp_path / "extras", people=0)
        shared = _with_shared_folder(root, "smith", tmp_path / "Dropbox" / "smith",
                                     months=("2026-05", "2026-06"), conversations=28)
        got = first_run.inspect_extras(root)
        assert [w.netid for w in got.work] == ["smith"]
        assert got.work[0].path == shared
        assert got.work[0].elsewhere is True
        assert got.work[0].months == 2
        assert got.work[0].conversations == 28

    def test_that_history_is_counted_in_the_total(self, tmp_path):
        """It used to report zero months for a folder holding years of them."""
        root = _make_setup(tmp_path / "extras", people=0)
        _with_shared_folder(root, "smith", tmp_path / "Dropbox" / "smith",
                            months=("2026-05", "2026-06", "2026-07"))
        assert first_run.inspect_extras(root).months == 3

    def test_an_open_month_counts_even_with_no_archive_yet(self, tmp_path):
        root = _make_setup(tmp_path / "extras", people=0)
        shared = _with_shared_folder(root, "smith", tmp_path / "Dropbox" / "smith")
        (shared / "calls" / "2026-08").mkdir(parents=True)
        assert first_run.inspect_extras(root).work[0].months == 1

    def test_a_folder_only_being_watched_is_not_theirs(self, tmp_path):
        """Read-only is somebody else's record; their own work stays here."""
        root = _make_setup(tmp_path / "extras", people=0)
        _with_shared_folder(root, "smith", tmp_path / "Dropbox" / "smith",
                            mode="read-only", months=("2026-05",))
        got = first_run.inspect_extras(root)
        assert got.work[0].elsewhere is False
        assert got.work[0].path == root / paths.DATA_DIRNAME

    def test_work_kept_here_is_reported_too(self, tmp_path):
        root = _make_setup(tmp_path / "extras", people=1, months=2)
        data = root / paths.DATA_DIRNAME
        (data / paths.CONVERSATIONS_DIRNAME / "p0" / "c_abc").mkdir(parents=True)
        got = first_run.inspect_extras(root)
        assert got.work[0].netid == "p0"
        assert got.work[0].elsewhere is False
        assert got.work[0].months == 2
        assert got.work[0].conversations == 1

    def test_two_people_in_the_same_month_are_one_month(self, tmp_path):
        """The total is how many months this setup covers, not a sum."""
        root = _make_setup(tmp_path / "extras", people=0)
        _with_shared_folder(root, "a", tmp_path / "d" / "a", months=("2026-05",))
        _with_shared_folder(root, "b", tmp_path / "d" / "b", months=("2026-05",))
        assert first_run.inspect_extras(root).months == 1

    def test_it_says_when_work_is_kept_outside_the_settings_location(self, tmp_path):
        root = _make_setup(tmp_path / "extras", people=1)
        assert first_run.inspect_extras(root).data_is_elsewhere is False
        _with_shared_folder(root, "smith", tmp_path / "Dropbox" / "smith")
        assert first_run.inspect_extras(root).data_is_elsewhere is True

    def test_a_shared_folder_that_is_not_there_is_reported_as_empty(self, tmp_path):
        """A drive not mounted, or a folder Dropbox has not brought down yet.
        Named, with nothing in it, rather than silently dropped."""
        root = _make_setup(tmp_path / "extras", people=0)
        settings = root / paths.SETTINGS_FILENAME
        settings.write_text(
            '[professors.smith]\nname = "Smith"\nkey = "sk"\n'
            f'usage_path = "{tmp_path / "not-mounted"}"\nusage_mode = "shared-write"\n',
            encoding="utf-8")
        got = first_run.inspect_extras(root)
        assert [w.netid for w in got.work] == ["smith"]
        assert got.work[0].is_empty is True

    def test_a_folder_with_history_and_no_settings_still_counts(self, tmp_path):
        """There is nothing to read, so the folder itself is all there is."""
        root = _make_setup(tmp_path / "extras", months=3)
        (root / paths.SETTINGS_FILENAME).unlink()
        got = first_run.inspect_extras(root)
        assert got.work == []
        assert got.months == 3
        assert got.is_usable is True
