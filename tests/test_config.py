"""Tests for configuration utilities:
  - normalize_netid
  - parse_single_language_code
  - parse_language_code
  - load_professor_config
  - get_api_key

Tests for validate_page_nums live in plugins/translation/tests/test_utils.py.
"""

import argparse

import pytest

import src.config as config_mod
from src.errors import CLIError
from src.config import (
    get_api_key,
    get_registered_settings,
    load_professor_config,
    normalize_netid,
    parse_language_code,
    parse_single_language_code,
    register_setting,
)

# ---------------------------------------------------------------------------
# normalize_netid
# ---------------------------------------------------------------------------

class TestNormalizeNetid:
    """A netID is letters and digits only, which is what makes it safe as a filename.

    That guarantee is the whole reason the sandbox moved to netIDs: the old
    display-name identifiers were made filename-safe in two different ways
    by two different parts of the code, so one person's spending could be
    recorded under two names at once.
    """

    def test_plain_netid_unchanged(self):
        assert normalize_netid("jh43") == "jh43"

    def test_uppercase_folded(self):
        """JH43 and jh43 must be one person, not two sets of totals."""
        assert normalize_netid("JH43") == "jh43"

    def test_surrounding_whitespace_ignored(self):
        assert normalize_netid("  jh43  ") == "jh43"

    def test_letters_only_allowed(self):
        assert normalize_netid("heller") == "heller"

    def test_digits_only_allowed(self):
        assert normalize_netid("12345") == "12345"

    def test_long_netid_allowed(self):
        """Most netIDs are eight characters or fewer, but that isn't guaranteed."""
        assert normalize_netid("abcdefghijkl") == "abcdefghijkl"

    @pytest.mark.parametrize("bad", [
        "jeff heller",   # a space — the display name, entered by mistake
        "jeff_heller",   # the old safe-name form
        "smith-jones",
        "prof.heller",
        "O'Brien",
        "../etc",        # a path, which must never reach a filename
        "a/b",
    ])
    def test_anything_but_letters_and_digits_rejected(self, bad):
        with pytest.raises(CLIError):
            normalize_netid(bad)

    def test_empty_rejected(self):
        with pytest.raises(CLIError):
            normalize_netid("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(CLIError):
            normalize_netid("   ")

    def test_error_mentions_the_value_and_what_a_netid_is(self):
        """The usual cause is a display name typed where a netID was wanted."""
        with pytest.raises(CLIError) as excinfo:
            normalize_netid("Jeff Heller")
        message = str(excinfo.value)
        assert "Jeff Heller" in message
        assert "netID" in message


# ---------------------------------------------------------------------------
# parse_single_language_code  (used by the transcribe subcommand)
# ---------------------------------------------------------------------------

class TestParseSingleLanguageCode:

    @pytest.mark.parametrize("code,expected", [
        ("en", "English"),
        ("zh", "Chinese"),
        ("jp", "Japanese"),
        ("kr", "Korean"),
    ])
    def test_valid_lowercase_codes(self, code, expected):
        assert parse_single_language_code(code) == expected

    def test_uppercase_accepted(self):
        assert parse_single_language_code("JP") == "Japanese"

    def test_invalid_code_raises(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_single_language_code("xx")

    def test_old_single_char_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_single_language_code("J")

    def test_empty_string_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_single_language_code("")


# ---------------------------------------------------------------------------
# parse_language_code  (single code → OCR full name, hyphen pair → code tuple)
# ---------------------------------------------------------------------------

class TestParseLanguageCode:

    # --- single code (OCR mode, returns full name) ---

    @pytest.mark.parametrize("code,expected", [
        ("jp", "Japanese"),
        ("en", "English"),
        ("zh", "Chinese"),
        ("kr", "Korean"),
    ])
    def test_single_code_ocr_mode(self, code, expected):
        assert parse_language_code(code) == expected

    def test_single_uppercase_accepted(self):
        assert parse_language_code("JP") == "Japanese"

    def test_single_invalid_raises(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_language_code("xx")

    # --- hyphen-separated translation pair (returns code tuple) ---

    @pytest.mark.parametrize("code,expected", [
        ("zh-en", ("zh", "en")),
        ("jp-en", ("jp", "en")),
        ("kr-en", ("kr", "en")),
        ("en-jp", ("en", "jp")),
    ])
    def test_translation_pairs(self, code, expected):
        assert parse_language_code(code) == expected

    def test_translation_pair_uppercase_accepted(self):
        assert parse_language_code("ZH-EN") == ("zh", "en")

    def test_translation_pair_mixed_case_accepted(self):
        assert parse_language_code("Zh-En") == ("zh", "en")

    def test_same_source_and_target_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_language_code("jp-jp")

    def test_invalid_source_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_language_code("xx-en")

    def test_invalid_target_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_language_code("jp-xx")

    def test_triple_part_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_language_code("jp-en-kr")

    def test_empty_string_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_language_code("")


# ---------------------------------------------------------------------------
# load_professor_config
# ---------------------------------------------------------------------------

class TestLoadProfessorConfig:

    @pytest.fixture(autouse=True)
    def _isolate_settings_path(self, tmp_path, monkeypatch):
        from src import settings_store
        monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / ".settings")

    def test_empty_settings_returns_empty_dict(self):
        assert load_professor_config() == {}

    def test_single_person_returned(self):
        from src import settings_store
        settings_store.add_professor("jh43", "Jeff Heller", "sk-test-key")
        assert "jh43" in load_professor_config()

    def test_entry_has_expected_keys(self):
        from src import settings_store
        settings_store.add_professor("jh43", "Jeff Heller", "sk-test-key", "sk-backup")
        entry = load_professor_config()["jh43"]
        assert entry["name"] == "Jeff Heller"
        assert entry["netid"] == "jh43"
        assert entry["key"] == "sk-test-key"
        assert entry["backup_key"] == "sk-backup"

    def test_multiple_people_all_returned(self):
        from src import settings_store
        settings_store.add_professor("as12", "Alice Smith", "key-alice")
        settings_store.add_professor("bj34", "Bob Jones", "key-bob")
        result = load_professor_config()
        assert "as12" in result
        assert "bj34" in result
        assert len(result) == 2

    def test_netid_is_the_dict_key_not_the_display_name(self):
        """The display name can be anything; only the netID names files."""
        from src import settings_store
        settings_store.add_professor("ol99", "O'Brien Lee", "sk-obrien")
        result = load_professor_config()
        assert "ol99" in result
        assert "o_brien_lee" not in result

    def test_netid_capitalisation_folded(self):
        from src import settings_store
        settings_store.add_professor("JH43", "Jeff Heller", "sk-test-key")
        assert "jh43" in load_professor_config()

    def test_section_that_is_not_a_netid_is_skipped(self):
        """An old-style name left in .settings must not become a filename."""
        from src import settings_store
        settings_store.set_value("professors.jeff_heller.name", "Jeff Heller")
        settings_store.set_value("professors.jeff_heller.key", "sk-old")
        assert load_professor_config() == {}

    def test_backup_key_none_when_not_set(self):
        from src import settings_store
        settings_store.add_professor("tp01", "Test Prof", "sk-testprof")
        assert load_professor_config()["tp01"]["backup_key"] is None


# ---------------------------------------------------------------------------
# get_api_key
# ---------------------------------------------------------------------------

class TestGetApiKey:

    @pytest.fixture(autouse=True)
    def _isolate_settings_path(self, tmp_path, monkeypatch):
        from src import settings_store
        monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / ".settings")

    def _setup_one_prof(self, *, netid="jh43", name="Jeff Heller", primary="sk-primary", backup=None):
        """Helper: register one person and optionally set their keys."""
        from src import settings_store
        settings_store.add_professor(netid, name, primary, backup)

    def test_returns_primary_key_by_netid(self):
        self._setup_one_prof()
        key, display_name = get_api_key("jh43")
        assert key == "sk-primary"
        assert display_name == "Jeff Heller"

    def test_netid_lookup_is_case_insensitive(self):
        self._setup_one_prof()
        key, _ = get_api_key("JH43")
        assert key == "sk-primary"

    def test_display_name_is_not_accepted(self):
        """Two ways to name one person is how their spending got split in two."""
        self._setup_one_prof()
        with pytest.raises(CLIError):
            get_api_key("Jeff Heller")

    def test_unknown_netid_raises_value_error(self):
        self._setup_one_prof()
        with pytest.raises(ValueError, match="nobody|No one"):
            get_api_key("zz99")

    def test_nobody_configured_raises_value_error(self):
        with pytest.raises(ValueError):
            get_api_key("jh43")

    def test_falls_back_to_backup_key(self):
        from src import settings_store
        settings_store.add_professor("jh43", "Jeff Heller", "sk-primary")
        settings_store.unset_value("professors.jh43.key")
        settings_store.set_value("professors.jh43.backup_key", "sk-backup")
        key, _ = get_api_key("jh43")
        assert key == "sk-backup"

    def test_no_api_keys_at_all_raises_value_error(self):
        from src import settings_store
        settings_store.add_professor("jh43", "Jeff Heller", "placeholder")
        settings_store.unset_value("professors.jh43.key")
        with pytest.raises(ValueError, match="No API key"):
            get_api_key("jh43")

    def test_unknown_netid_mentions_add_professor(self):
        self._setup_one_prof()
        with pytest.raises(ValueError, match="env add-professor"):
            get_api_key("zz99")

    def test_nobody_configured_mentions_add_professor(self):
        with pytest.raises(ValueError, match="env add-professor"):
            get_api_key("jh43")


# ---------------------------------------------------------------------------
# register_setting / get_registered_settings
# ---------------------------------------------------------------------------

class TestSettingFieldRegistry:

    @pytest.fixture(autouse=True)
    def _isolate_registry(self, monkeypatch):
        """Don't let test registrations leak into other tests or the real registry."""
        monkeypatch.setattr(config_mod, "_SETTING_FIELDS", {})

    def test_register_and_retrieve(self):
        register_setting("TEST_VAR", "A test variable", section="Testing", secret=True)
        fields = get_registered_settings()
        assert len(fields) == 1
        assert fields[0].key == "TEST_VAR"
        assert fields[0].label == "A test variable"
        assert fields[0].section == "Testing"
        assert fields[0].secret is True

    def test_defaults_section_other_and_not_secret(self):
        register_setting("TEST_VAR", "A test variable")
        fields = get_registered_settings()
        assert fields[0].section == "Other"
        assert fields[0].secret is False

    def test_registering_same_key_twice_replaces(self):
        register_setting("TEST_VAR", "First label")
        register_setting("TEST_VAR", "Second label")
        fields = get_registered_settings()
        assert len(fields) == 1
        assert fields[0].label == "Second label"

    def test_sorted_by_section_then_key(self):
        register_setting("Z_VAR", "z", section="B")
        register_setting("A_VAR", "a", section="A")
        fields = get_registered_settings()
        assert [f.key for f in fields] == ["A_VAR", "Z_VAR"]

    def test_empty_registry_returns_empty_list(self):
        assert get_registered_settings() == []
