"""
Tests for small utility modules:
- src/console.py (print_pass_result and other formatting functions)
- src/services/parallel_utils.py (tqdm_logging, update_pbar_postfix)
- src/processors/base_text_processor.py (split_text_into_pages, parse_text_into_paragraphs)
"""

import logging
import pytest
from unittest.mock import MagicMock, patch


# ===========================================================================
# src/console.py
# ===========================================================================

from src.console import (
    print_section,
    print_banner,
    print_subsection,
    print_pass_result,
)


class TestPrintSection:
    def test_outputs_title_and_content(self, capsys):
        print_section("Translation", "Hello world")
        out = capsys.readouterr().out
        assert "Translation" in out
        assert "Hello world" in out
        assert "===" in out


class TestPrintBanner:
    def test_outputs_title_between_lines(self, capsys):
        print_banner("TOKEN REPORT")
        out = capsys.readouterr().out
        assert "TOKEN REPORT" in out
        assert "=" * 10 in out

    def test_custom_width(self, capsys):
        print_banner("TITLE", width=30)
        out = capsys.readouterr().out
        assert "=" * 30 in out


class TestPrintSubsection:
    def test_outputs_label_and_rule(self, capsys):
        print_subsection("Model Breakdown")
        out = capsys.readouterr().out
        assert "Model Breakdown" in out
        assert "---" in out


class TestPrintPassResult:
    def test_outputs_label_and_content(self, capsys):
        print_pass_result("Pass 1/3 result", "transcribed text")
        out = capsys.readouterr().out
        assert "Pass 1/3 result" in out
        assert "transcribed text" in out
        assert "---" in out

    def test_outputs_empty_content_without_error(self, capsys):
        print_pass_result("label", "")
        out = capsys.readouterr().out
        assert "label" in out


# ===========================================================================
# src/services/parallel_utils.py
# ===========================================================================

from src.services.parallel_utils import tqdm_logging, update_pbar_postfix


class TestTqdmLogging:
    def test_context_manager_restores_handlers(self):
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        with tqdm_logging():
            pass
        assert root.handlers == original_handlers

    def test_handler_swapped_inside_context(self):
        root = logging.getLogger()
        original_count = len(root.handlers)
        with tqdm_logging():
            # Inside the context, exactly one handler (the tqdm one) should be active
            assert len(root.handlers) == 1

    def test_logging_works_inside_context(self):
        """Ensure the tqdm handler doesn't raise on emit."""
        with tqdm_logging():
            # Should not raise
            logging.getLogger().info("test message inside tqdm_logging")

    def test_handler_emit_calls_handleError_on_tqdm_write_failure(self):
        """When tqdm.write raises, _TqdmLoggingHandler.emit should call handleError."""
        from src.services.parallel_utils import _TqdmLoggingHandler
        from unittest.mock import patch, MagicMock as _MM
        handler = _TqdmLoggingHandler()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        handle_error_calls = []
        handler.handleError = lambda r: handle_error_calls.append(r)
        with patch("src.services.parallel_utils.tqdm.write", side_effect=RuntimeError("boom")):
            handler.emit(record)
        assert handle_error_calls


class TestUpdatePbarPostfix:
    def test_sets_postfix_with_correct_values(self):
        pbar = MagicMock()
        usage_data = {"total_usage": {"total_tokens": 500, "total_cost": 0.05}}
        update_pbar_postfix(pbar, usage_data, 100, 0.01)
        pbar.set_postfix.assert_called_once()
        kwargs = pbar.set_postfix.call_args.kwargs
        assert "tokens" in kwargs
        assert "cost" in kwargs
        assert "400" in kwargs["tokens"]  # 500 - 100

    def test_handles_none_baseline_gracefully(self):
        pbar = MagicMock()
        usage_data = {"total_usage": {"total_tokens": None, "total_cost": None}}
        # Should not raise
        update_pbar_postfix(pbar, usage_data, None, None)
        pbar.set_postfix.assert_not_called()

    def test_handles_type_error_gracefully(self):
        pbar = MagicMock()
        usage_data = {"total_usage": {"total_tokens": "abc", "total_cost": "xyz"}}
        update_pbar_postfix(pbar, usage_data, 0, 0.0)
        pbar.set_postfix.assert_not_called()


# ===========================================================================
# src/processors/base_text_processor.py
# ===========================================================================

from src.processors.base_text_processor import BaseTextProcessor


class TestSplitTextIntoPages:
    def test_empty_list_returns_empty_string(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = BaseTextProcessor.split_text_into_pages([])
        assert result == [""]
        assert any("No paragraphs" in r.message for r in caplog.records)

    def test_splits_large_content_into_pages(self):
        # Create 10 paragraphs each 500 chars — target 2000 chars/page means ~4 per page
        paras = ["A" * 500] * 10
        pages = BaseTextProcessor.split_text_into_pages(paras, target_page_size=2000)
        assert len(pages) > 1

    def test_single_small_para_stays_on_one_page(self):
        pages = BaseTextProcessor.split_text_into_pages(["hello"], target_page_size=2000)
        assert pages == ["hello"]

    def test_very_small_paras_create_one_page(self):
        paras = ["a", "b", "c"]
        pages = BaseTextProcessor.split_text_into_pages(paras, target_page_size=100)
        # All tiny paras fit in one page
        assert len(pages) >= 1
        assert "a" in pages[0]

    def test_multiple_paras_joined_with_double_newline(self):
        paras = ["First", "Second"]
        pages = BaseTextProcessor.split_text_into_pages(paras, target_page_size=10000)
        assert pages[0] == "First\n\nSecond"


class TestParseTextIntoParagraphs:
    def test_splits_on_double_newlines(self):
        content = "Para one\n\nPara two\n\nPara three"
        result = BaseTextProcessor.parse_text_into_paragraphs(content)
        assert result == ["Para one", "Para two", "Para three"]

    def test_empty_or_blank_string_returns_empty_list(self):
        assert BaseTextProcessor.parse_text_into_paragraphs("") == []
        assert BaseTextProcessor.parse_text_into_paragraphs("   ") == []

    def test_no_double_newline_returns_whole_string(self):
        content = "line one\nline two\nline three"
        result = BaseTextProcessor.parse_text_into_paragraphs(content)
        # No double newline → treated as a single paragraph
        assert len(result) == 1
        assert "line one" in result[0]

    def test_no_newlines_returns_full_content(self):
        content = "Single paragraph no breaks"
        result = BaseTextProcessor.parse_text_into_paragraphs(content)
        assert result == ["Single paragraph no breaks"]

    def test_strips_whitespace_from_paragraphs(self):
        content = "  Para one  \n\n  Para two  "
        result = BaseTextProcessor.parse_text_into_paragraphs(content)
        assert result == ["Para one", "Para two"]


# ===========================================================================
# src/services/parallel_utils.py — cap_worker_count
# ===========================================================================

from src.services.parallel_utils import cap_worker_count


class TestCapWorkerCount:

    def test_no_cap_when_workers_within_limits(self, caplog):
        with caplog.at_level(logging.INFO):
            result = cap_worker_count(3, 10, 20)
        assert result == 3
        assert not any(r.levelno == logging.INFO for r in caplog.records
                       if "capped" in r.message)

    def test_workers_capped_by_item_count(self, caplog):
        """actual == item_count triggers the item-count reason branch."""
        with caplog.at_level(logging.INFO):
            result = cap_worker_count(10, 3, 20, item_label="image", container_label="folder")
        assert result == 3
        assert any("3 image(s)" in r.message for r in caplog.records)

    def test_workers_capped_by_max_workers(self, caplog):
        """actual == max_workers (not item_count) triggers the settings-cap reason branch."""
        with caplog.at_level(logging.INFO):
            result = cap_worker_count(10, 20, 5)
        assert result == 5
        assert any("max_parallel_workers=5" in r.message for r in caplog.records)

    def test_returns_min_of_all_three(self):
        assert cap_worker_count(8, 4, 6) == 4
        assert cap_worker_count(8, 6, 4) == 4
        assert cap_worker_count(3, 6, 4) == 3

