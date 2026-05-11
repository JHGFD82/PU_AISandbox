"""
Tests for the base transcription plugin CLI flag parsing and validation logic.

This plugin registers English only.  Tests verify:
  - transcribe and transcription_review commands work with 'en'
  - EA-only flags (--kanbun, --kanbun-main, --spread, --vertical,
    --passes, --preserve-tables, --workers) are NOT present
  - Language codes outside 'en' are rejected
"""

from pathlib import Path

import pytest

from src.cli import create_argument_parser
from src.runtime.plugin_loader import load_plugins

# Load plugins from this plugin's own directory (not the whole plugins/ folder).
# This ensures the test exercises the base plugin in isolation.
_PLUGIN_DIR = Path(__file__).resolve().parents[2]


def _make_parser():
    return create_argument_parser(load_plugins(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# transcribe — basic flag defaults
# ---------------------------------------------------------------------------

class TestTranscribeFlagDefaults:

    @pytest.fixture
    def parser(self):
        return _make_parser()

    def test_input_file_default_is_none(self, parser):
        args = parser.parse_args(["heller", "transcribe", "en"])
        assert args.input_file is None

    def test_language_code_resolves_to_full_name(self, parser):
        args = parser.parse_args(["heller", "transcribe", "en"])
        assert args.language_code == "English"

    def test_input_long_flag(self, parser):
        args = parser.parse_args(["heller", "transcribe", "en", "--input", "img.png"])
        assert args.input_file == "img.png"

    def test_input_short_flag(self, parser):
        args = parser.parse_args(["heller", "transcribe", "en", "-i", "img.png"])
        assert args.input_file == "img.png"


# ---------------------------------------------------------------------------
# transcribe — EA flags are absent
# ---------------------------------------------------------------------------

class TestTranscribeNoEAFlags:

    @pytest.fixture
    def parser(self):
        return _make_parser()

    def test_kanbun_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcribe", "en", "-i", "img.png", "--kanbun"])

    def test_kanbun_main_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcribe", "en", "-i", "img.png", "--kanbun-main"])

    def test_spread_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcribe", "en", "-i", "img.png", "--spread"])

    def test_vertical_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcribe", "en", "-i", "img.png", "--vertical"])

    def test_passes_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcribe", "en", "-i", "img.png", "--passes", "2"])

    def test_preserve_tables_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcribe", "en", "-i", "img.png", "--preserve-tables"])

    def test_workers_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcribe", "en", "-i", "img.png", "--workers", "4"])


# ---------------------------------------------------------------------------
# transcribe — unknown codes are always rejected
# ---------------------------------------------------------------------------

class TestTranscribeLanguageRestriction:

    def test_unknown_code_rejected(self):
        parser = _make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcribe", "xx", "-i", "img.png"])


# ---------------------------------------------------------------------------
# transcription_review — basic flag defaults
# ---------------------------------------------------------------------------

class TestTranscriptionReviewFlagDefaults:

    @pytest.fixture
    def parser(self):
        return _make_parser()

    def test_input_file_default_is_none(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "en"])
        assert args.input_file is None

    def test_custom_text_default_is_false(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "en"])
        assert args.custom_text is False

    def test_language_code_resolves_to_full_name(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "en"])
        assert args.language_code == "English"


# ---------------------------------------------------------------------------
# transcription_review — flag parsing
# ---------------------------------------------------------------------------

class TestTranscriptionReviewFlagParsing:

    @pytest.fixture
    def parser(self):
        return _make_parser()

    def test_input_long_flag(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "en", "--input", "text.txt"])
        assert args.input_file == "text.txt"

    def test_input_short_flag(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "en", "-i", "text.txt"])
        assert args.input_file == "text.txt"

    def test_custom_short_flag(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "en", "-c"])
        assert args.custom_text is True

    def test_custom_long_flag(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "en", "--custom"])
        assert args.custom_text is True

    def test_input_and_custom_are_mutually_exclusive(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args([
                "heller", "transcription_review", "en",
                "-i", "text.txt", "-c",
            ])


# ---------------------------------------------------------------------------
# transcription_review — EA flags are absent
# ---------------------------------------------------------------------------

class TestTranscriptionReviewNoEAFlags:

    @pytest.fixture
    def parser(self):
        return _make_parser()

    def test_kanbun_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcription_review", "en", "-i", "text.txt", "--kanbun"])

    def test_kanbun_main_flag_not_recognised(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcription_review", "en", "-i", "text.txt", "--kanbun-main"])


# ---------------------------------------------------------------------------
# Cross-command — flags do not bleed between subcommands
# ---------------------------------------------------------------------------

class TestFlagIsolation:

    @pytest.fixture
    def parser(self):
        return _make_parser()

    def test_ea_flags_not_present_on_transcribe(self, parser):
        args = parser.parse_args(["heller", "transcribe", "en", "-i", "img.png"])
        for flag in ("vertical", "spread", "kanbun", "kanbun_main", "passes",
                     "preserve_tables", "workers"):
            assert not hasattr(args, flag), f"EA flag '{flag}' should not exist on base transcribe args"

    def test_ea_flags_not_present_on_review(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "en", "-i", "text.txt"])
        for flag in ("kanbun", "kanbun_main"):
            assert not hasattr(args, flag), f"EA flag '{flag}' should not exist on base transcription_review args"
