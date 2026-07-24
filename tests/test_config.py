"""Tests for configuration utilities:
  - make_safe_filename
  - parse_single_language_code
  - parse_language_code
  - load_professor_config
  - get_api_key

Tests for validate_page_nums live in plugins/translation/tests/test_utils.py.
"""

import argparse

import pytest

import src.config as config_mod
from src.config import (
    get_api_key,
    get_registered_env_fields,
    load_professor_config,
    make_safe_filename,
    parse_language_code,
    parse_single_language_code,
    register_env_field,
)

# ---------------------------------------------------------------------------
# make_safe_filename
# ---------------------------------------------------------------------------

class TestMakeSafeFilename:

    def test_spaces_become_underscores(self):
        assert make_safe_filename("Jeff Heller") == "jeff_heller"

    def test_already_safe_unchanged(self):
        assert make_safe_filename("heller") == "heller"

    def test_uppercase_lowercased(self):
        assert make_safe_filename("HELLER") == "heller"

    def test_hyphens_preserved(self):
        assert make_safe_filename("smith-jones") == "smith-jones"

    def test_dots_preserved(self):
        assert make_safe_filename("prof.heller") == "prof.heller"

    def test_apostrophe_replaced(self):
        assert make_safe_filename("O'Brien") == "o_brien"

    def test_consecutive_spaces_collapse_to_single_underscore(self):
        # Two spaces → two underscores → collapsed to one
        assert make_safe_filename("a  b") == "a_b"

    def test_leading_special_char_stripped(self):
        assert make_safe_filename("!heller") == "heller"

    def test_trailing_special_char_stripped(self):
        assert make_safe_filename("heller!") == "heller"

    def test_empty_string(self):
        assert make_safe_filename("") == ""

    def test_mixed_name_with_dot_and_hyphen(self):
        # "Prof. Smith-Jones": dot + space + hyphen
        assert make_safe_filename("Prof. Smith-Jones") == "prof._smith-jones"

    def test_underscore_passthrough(self):
        # Underscores in the original name are preserved
        assert make_safe_filename("hello_world") == "hello_world"

    def test_numbers_preserved(self):
        assert make_safe_filename("prof2") == "prof2"


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

    def test_empty_environment_returns_empty_dict(self, monkeypatch):
        monkeypatch.delenv("PROF_1_NAME", raising=False)
        monkeypatch.delenv("PROF_1_KEY", raising=False)
        # Wipe every PROF_* variable that might be inherited from the real .env
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        assert load_professor_config() == {}

    def test_single_professor_returned(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_1_NAME", "Jeff Heller")
        monkeypatch.setenv("PROF_1_KEY", "sk-test-key")
        result = load_professor_config()
        assert "jeff_heller" in result

    def test_professor_entry_has_expected_keys(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_1_NAME", "Jeff Heller")
        monkeypatch.setenv("PROF_1_KEY", "sk-test-key")
        entry = load_professor_config()["jeff_heller"]
        assert entry["name"] == "Jeff Heller"
        assert entry["safe_name"] == "jeff_heller"
        assert entry["primary_key"] == "PROF_1_KEY"
        assert entry["backup_key"] == "PROF_1_BACKUP_KEY"
        assert entry["id"] == "1"

    def test_professor_without_primary_key_excluded(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_1_NAME", "No Key Prof")
        # Intentionally omit PROF_1_KEY
        assert load_professor_config() == {}

    def test_multiple_professors_all_returned(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_1_NAME", "Alice Smith")
        monkeypatch.setenv("PROF_1_KEY", "key-alice")
        monkeypatch.setenv("PROF_2_NAME", "Bob Jones")
        monkeypatch.setenv("PROF_2_KEY", "key-bob")
        result = load_professor_config()
        assert "alice_smith" in result
        assert "bob_jones" in result
        assert len(result) == 2

    def test_safe_name_used_as_dict_key(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_1_NAME", "O'Brien Lee")
        monkeypatch.setenv("PROF_1_KEY", "sk-obrien")
        result = load_professor_config()
        # Apostrophe → underscore, spaces → underscore
        assert "o_brien_lee" in result

    def test_backup_key_var_recorded_even_if_not_set(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_3_NAME", "Test Prof")
        monkeypatch.setenv("PROF_3_KEY", "sk-testprof")
        # PROF_3_BACKUP_KEY is NOT set, but the config entry should still record the var name
        entry = load_professor_config()["test_prof"]
        assert entry["backup_key"] == "PROF_3_BACKUP_KEY"


# ---------------------------------------------------------------------------
# get_api_key
# ---------------------------------------------------------------------------

class TestGetApiKey:

    def _setup_one_prof(self, monkeypatch, *, name="Jeff Heller", primary="sk-primary", backup=None):
        """Helper: register one professor and optionally set their keys."""
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_1_NAME", name)
        monkeypatch.setenv("PROF_1_KEY", primary)
        if backup:
            monkeypatch.setenv("PROF_1_BACKUP_KEY", backup)

    def test_returns_primary_key_by_safe_name(self, monkeypatch):
        self._setup_one_prof(monkeypatch)
        key, display_name = get_api_key("jeff_heller")
        assert key == "sk-primary"
        assert display_name == "Jeff Heller"

    def test_returns_primary_key_by_display_name(self, monkeypatch):
        self._setup_one_prof(monkeypatch)
        key, display_name = get_api_key("Jeff Heller")
        assert key == "sk-primary"

    def test_display_name_lookup_is_case_insensitive(self, monkeypatch):
        self._setup_one_prof(monkeypatch)
        key, _ = get_api_key("jeff heller")
        assert key == "sk-primary"

    def test_unknown_professor_raises_value_error(self, monkeypatch):
        self._setup_one_prof(monkeypatch)
        with pytest.raises(ValueError, match="not found"):
            get_api_key("nobody")

    def test_no_professors_configured_raises_value_error(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        with pytest.raises(ValueError):
            get_api_key("heller")

    def test_falls_back_to_backup_key(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_1_NAME", "Jeff Heller")
        # Set only the backup key variable (primary env var missing)
        monkeypatch.setenv("PROF_1_BACKUP_KEY", "sk-backup")
        # We must also register the primary key var name in env so load_professor_config
        # includes this professor — PROF_1_KEY must have a value (even empty is excluded).
        # So simulate primary key env var pointing to a value that is empty:
        monkeypatch.setenv("PROF_1_KEY", "sk-primary-present")
        # Now remove the primary env var value by patching os.getenv directly
        import os
        original_getenv = os.getenv

        def _patched_getenv(key, default=None):
            if key == "PROF_1_KEY":
                return None          # simulate missing primary
            return original_getenv(key, default)

        monkeypatch.setattr("src.config.os.getenv", _patched_getenv)
        key, _ = get_api_key("jeff_heller")
        assert key == "sk-backup"

    def test_no_api_keys_at_all_raises_value_error(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PROF_1_NAME", "Jeff Heller")
        monkeypatch.setenv("PROF_1_KEY", "placeholder")   # needed to register professor

        import os
        original_getenv = os.getenv

        def _patched_getenv(key, default=None):
            if key in ("PROF_1_KEY", "PROF_1_BACKUP_KEY"):
                return None
            return original_getenv(key, default)

        monkeypatch.setattr("src.config.os.getenv", _patched_getenv)
        with pytest.raises(ValueError, match="No API key"):
            get_api_key("jeff_heller")

    def test_professor_not_found_mentions_add_professor(self, monkeypatch):
        self._setup_one_prof(monkeypatch)
        with pytest.raises(ValueError, match="env add-professor"):
            get_api_key("nobody")

    def test_no_professors_configured_mentions_add_professor(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("PROF_"):
                monkeypatch.delenv(key, raising=False)
        with pytest.raises(ValueError, match="env add-professor"):
            get_api_key("heller")


# ---------------------------------------------------------------------------
# register_env_field / get_registered_env_fields
# ---------------------------------------------------------------------------

class TestEnvFieldRegistry:

    @pytest.fixture(autouse=True)
    def _isolate_registry(self, monkeypatch):
        """Don't let test registrations leak into other tests or the real registry."""
        monkeypatch.setattr(config_mod, "_ENV_FIELDS", {})

    def test_register_and_retrieve(self):
        register_env_field("TEST_VAR", "A test variable", section="Testing", secret=True)
        fields = get_registered_env_fields()
        assert len(fields) == 1
        assert fields[0].key == "TEST_VAR"
        assert fields[0].label == "A test variable"
        assert fields[0].section == "Testing"
        assert fields[0].secret is True

    def test_defaults_section_other_and_not_secret(self):
        register_env_field("TEST_VAR", "A test variable")
        fields = get_registered_env_fields()
        assert fields[0].section == "Other"
        assert fields[0].secret is False

    def test_registering_same_key_twice_replaces(self):
        register_env_field("TEST_VAR", "First label")
        register_env_field("TEST_VAR", "Second label")
        fields = get_registered_env_fields()
        assert len(fields) == 1
        assert fields[0].label == "Second label"

    def test_sorted_by_section_then_key(self):
        register_env_field("Z_VAR", "z", section="B")
        register_env_field("A_VAR", "a", section="A")
        fields = get_registered_env_fields()
        assert [f.key for f in fields] == ["A_VAR", "Z_VAR"]

    def test_empty_registry_returns_empty_list(self):
        assert get_registered_env_fields() == []
