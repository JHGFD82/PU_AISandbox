"""Coverage tests for src/services/translation_service.py — uncovered branches."""

import os
import pytest
from unittest.mock import MagicMock, patch, call

from src.services.translation_service import TranslationService
from src.services.api_errors import APISignal
from src.models.output_options import OutputOptions


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def svc():
    tracker = MagicMock()
    tracker.record_usage.return_value = MagicMock(total_cost=0.001)
    tracker.usage_data = {"total_usage": {"total_tokens": 0, "total_cost": 0.0}}
    return TranslationService(api_key="fake-key", token_tracker=tracker)


# ---------------------------------------------------------------------------
# generate_text — blank page path (lines 170-175)
# ---------------------------------------------------------------------------

class TestGenerateTextBlankPage:

    def test_blank_page_text_skipped_without_api_call(self, svc):
        """Empty or whitespace-only page_text short-circuits to a blank-page header."""
        with patch.object(svc, "translate_page_text") as mock_translate:
            result = svc.generate_text("", "   ", "", 3, "Japanese", "English")
        mock_translate.assert_not_called()
        assert "-- Page 4 --" in result

    def test_blank_page_increments_counter(self, svc):
        """Blank page increments the internal _blank_page_count."""
        svc._blank_page_count = 0
        with patch.object(svc, "translate_page_text"):
            svc.generate_text("", "", "", 0, "Japanese", "English")
        assert svc._blank_page_count == 1

    def test_blank_page_with_only_newlines(self, svc):
        """Newline-only page_text also triggers the blank-page path."""
        with patch.object(svc, "translate_page_text") as mock_translate:
            svc.generate_text("", "\n\n\t\n", "", 0, "Japanese", "English")
        mock_translate.assert_not_called()


# ---------------------------------------------------------------------------
# _translate_pages_parallel — OSError when writing error temp file (lines 238-242)
# ---------------------------------------------------------------------------

class TestTranslatePagesParallelOSError:

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr("src.services.translation_service.time.sleep", lambda s: None)

    def test_oserror_writing_error_temp_file_is_swallowed(self, svc, tmp_path):
        """When the error temp file cannot be written, the OSError is caught silently."""
        # Make generate_text raise so a worker error is triggered
        svc._get_model = MagicMock(return_value="gpt-4o")
        svc.token_tracker.usage_data = {"total_usage": {"total_tokens": 0, "total_cost": 0.0}}

        all_triples = [(0, "page text", "")]

        real_open = open

        def patched_open(path, mode="r", **kwargs):
            # Fail writes to .tmp files to simulate OSError on error-file write
            if "w" in mode and ".tmp" in str(path):
                raise OSError("disk full")
            return real_open(path, mode, **kwargs)

        with patch.object(svc, "generate_text", side_effect=RuntimeError("API down")), \
             patch("builtins.open", side_effect=patched_open):
            # Should not raise — OSError is caught
            result = svc._translate_pages_parallel(
                all_triples, abstract_text="", source_language="Japanese",
                target_language="English", output_format="console",
                unit_label="page", workers=1, opts=OutputOptions(),
            )
        # Even if temp file write failed, result has a fallback entry
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _translate_pages_parallel — missing result fallback (line 348)
# ---------------------------------------------------------------------------

class TestTranslatePagesParallelMissingResult:

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr("src.services.translation_service.time.sleep", lambda s: None)

    def test_missing_tmp_path_produces_error_message(self, svc):
        """When a worker errors and temp-file write also fails, the fallback
        'Missing result' message is inserted."""
        svc._get_model = MagicMock(return_value="gpt-4o")
        svc.token_tracker.usage_data = {"total_usage": {"total_tokens": 0, "total_cost": 0.0}}

        all_triples = [(0, "page text", "")]

        # Worker raises AND temp file write raises → tmp_paths stays empty for idx=0
        def always_fail_open(path, mode="r", **kwargs):
            if "w" in mode and ".tmp" in str(path):
                raise OSError("cannot write")
            raise OSError("cannot read")

        with patch.object(svc, "generate_text", side_effect=RuntimeError("boom")), \
             patch("builtins.open", side_effect=always_fail_open):
            result = svc._translate_pages_parallel(
                all_triples, abstract_text="", source_language="Japanese",
                target_language="English", output_format="console",
                unit_label="page", workers=1, opts=OutputOptions(),
            )
        assert len(result) == 1
        assert "Missing result" in result[0]


# ---------------------------------------------------------------------------
# _translate_page_sequence — post-run blank-page summary (lines 443-448)
# ---------------------------------------------------------------------------

class TestTranslatePageSequenceBlankSummary:

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr("src.services.translation_service.time.sleep", lambda s: None)

    def test_blank_page_count_printed_after_run(self, svc, capsys):
        """When blank pages were skipped, a summary line is printed and counter reset."""
        # Pre-set the blank page counter
        svc._blank_page_count = 2
        triples = [(0, "real text", "")]

        with patch.object(svc, "generate_text", return_value="Result"):
            svc._translate_page_sequence(
                iter(triples), "", "Japanese", "English", "console",
                0, "page", OutputOptions(), input_file_path=None,
            )
        out = capsys.readouterr().out
        assert "image-only" in out
        # Counter should be reset to 0
        assert svc._blank_page_count == 0


# ---------------------------------------------------------------------------
# _translate_page_sequence — post-run API error summary (lines 454-459)
# ---------------------------------------------------------------------------

class TestTranslatePageSequenceApiErrorSummary:

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr("src.services.translation_service.time.sleep", lambda s: None)

    def test_api_error_count_printed_after_run(self, svc, capsys):
        """When API errors occurred, a summary line is printed and counter reset."""
        svc._api_error_count = 3
        triples = [(0, "text", "")]

        with patch.object(svc, "generate_text", return_value="Result"):
            svc._translate_page_sequence(
                iter(triples), "", "Japanese", "English", "console",
                0, "page", OutputOptions(), input_file_path=None,
            )
        out = capsys.readouterr().out
        assert "API/connection errors" in out
        assert svc._api_error_count == 0


# ---------------------------------------------------------------------------
# _translate_page_sequence — progressive_save + exception path (line 423)
# ---------------------------------------------------------------------------

class TestTranslatePageSequenceProgressiveSaveError:

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr("src.services.translation_service.time.sleep", lambda s: None)

    def test_progressive_save_error_page_is_saved(self, svc, tmp_path):
        """When a page raises an exception in sequential mode with progressive_save,
        the error message is also written to the progressive file."""
        out_file = str(tmp_path / "out.txt")
        opts = OutputOptions(output_file=out_file, progressive_save=True)
        triples = [(0, "text", "")]

        save_calls = []
        with patch.object(svc, "generate_text", side_effect=RuntimeError("bang")), \
             patch("src.services.translation_service.FileOutputHandler.save_page_progressively",
                   side_effect=lambda *a, **kw: save_calls.append(a) or out_file):
            result = svc._translate_page_sequence(
                iter(triples), "", "Chinese", "English", "txt",
                0, "page", opts, input_file_path=None,
            )
        # Error message was appended and the progressive saver was called
        assert any("Translation error" in r for r in result)
        assert len(save_calls) >= 1


# ---------------------------------------------------------------------------
# translate_table_grid — row count mismatch fallback (lines 545-546)
# ---------------------------------------------------------------------------

class TestTranslateTableGridRowCountMismatch:

    def test_row_count_mismatch_returns_original_rows(self, svc):
        """When the translated table has a different row count, the original is returned."""
        original_rows = [["Header A", "Header B"], ["R1C1", "R1C2"], ["R2C1", "R2C2"]]
        # API returns a 2-row table but we expect 3
        two_row_md = "| X | Y |\n|---|---|\n| a | b |"

        svc._get_model = MagicMock(return_value="gpt-4o")

        with patch.object(svc, "_run_with_retry", return_value=two_row_md), \
             patch.object(svc, "_call_translation_api", return_value=MagicMock()), \
             patch.object(svc, "_record_response_usage"), \
             patch.object(svc, "_extract_response_content", return_value=two_row_md):
            result = svc.translate_table_grid(original_rows, "English", "Japanese")

        assert result == original_rows

    def test_empty_rows_returns_early(self, svc):
        """translate_table_grid with empty input returns it unchanged without an API call."""
        with patch.object(svc, "_run_with_retry") as mock_retry:
            result = svc.translate_table_grid([], "English", "Japanese")
        mock_retry.assert_not_called()
        assert result == []

    def test_unparseable_response_returns_original(self, svc):
        """When the API returns content that can't be parsed as a table, original is returned."""
        rows = [["A", "B"], ["1", "2"]]
        svc._get_model = MagicMock(return_value="gpt-4o")

        with patch.object(svc, "_run_with_retry", return_value="Not a table at all"):
            result = svc.translate_table_grid(rows, "English", "Japanese")
        assert result == rows

    def test_api_signal_returns_original(self, svc):
        """When the API returns an error signal, original rows are returned."""
        rows = [["A", "B"]]
        svc._get_model = MagicMock(return_value="gpt-4o")

        with patch.object(svc, "_run_with_retry", return_value=APISignal.CONTEXT_LENGTH_EXCEEDED):
            result = svc.translate_table_grid(rows, "English", "Japanese")
        assert result == rows
