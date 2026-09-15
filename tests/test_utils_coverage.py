"""
Tests for small utility modules:
- src/console.py (print_pass_result and other formatting functions)
- src/services/parallel_utils.py (tqdm_logging, update_pbar_postfix)
- src/processors/base_text_processor.py (split_text_into_pages, parse_text_into_paragraphs)
"""

import logging
from unittest.mock import MagicMock, patch

import pytest


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


@pytest.fixture(autouse=True)
def no_logging_redirection_left_behind():
    """Report a leaked logging redirection against the test that leaked it.

    ``tqdm_logging()`` keeps its count of how many runs are relying on the
    redirection in module-level state, so a test that leaves that count raised
    makes the *next* test fail instead — which sends whoever is reading the
    output looking in entirely the wrong place. Checking here names the
    culprit, and clearing it keeps one failure from becoming several.
    """
    yield
    import src.services.parallel_utils as pu

    depth, undo = pu._swap_depth, pu._undo_swap
    pu._swap_depth, pu._undo_swap = 0, None
    if undo is not None:
        undo()
    assert depth == 0, (
        f"this test left tqdm_logging()'s count at {depth} rather than 0, so the "
        "redirection would never be undone — or never set up again — for the rest "
        "of the process"
    )


class TestTqdmLogging:
    def test_context_manager_restores_handlers(self):
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        with tqdm_logging():
            pass
        assert root.handlers == original_handlers

    def test_handler_swapped_inside_context(self):
        root = logging.getLogger()
        len(root.handlers)
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
        handler = _TqdmLoggingHandler()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        handle_error_calls = []
        handler.handleError = lambda r: handle_error_calls.append(r)
        with patch("src.services.parallel_utils.tqdm.write", side_effect=RuntimeError("boom")):
            handler.emit(record)
        assert handle_error_calls


class TestTqdmLoggingWithMoreThanOneRun:
    """Two runs sharing the redirection, which is what the web interface does.

    Each test arranges one shape of sharing and checks the same thing
    afterwards: logging is exactly as it was before, with no handler of ours
    left attached and no logger left quietened. A leftover handler is not a
    tidiness problem — it prints every later line in the program a second
    time, for as long as the process lives.
    """

    @staticmethod
    def _quiet_logger_levels():
        from src.services.parallel_utils import _QUIET_LOGGERS
        return {name: logging.getLogger(name).level for name in _QUIET_LOGGERS}

    @staticmethod
    def _tqdm_handlers_on_root():
        from src.services.parallel_utils import _TqdmLoggingHandler
        return [h for h in logging.getLogger().handlers
                if isinstance(h, _TqdmLoggingHandler)]

    def test_nested_use_restores_only_once(self):
        root = logging.getLogger()
        before_handlers = root.handlers[:]
        before_levels = self._quiet_logger_levels()

        with tqdm_logging():
            with tqdm_logging():
                assert self._tqdm_handlers_on_root()
            # The inner block finishing must not put the original handlers
            # back while the outer block is still drawing a progress bar.
            assert self._tqdm_handlers_on_root()

        assert root.handlers == before_handlers
        assert self._quiet_logger_levels() == before_levels

    def test_overlapping_runs_leave_no_handler_behind(self):
        """The shape that used to break: in, in, out, out — not properly nested.

        Two background jobs in different conversations start and finish
        independently, so neither one's block contains the other's.
        """
        import threading

        root = logging.getLogger()
        before_handlers = root.handlers[:]
        before_levels = self._quiet_logger_levels()

        first_is_in = threading.Event()
        second_is_in = threading.Event()
        first_is_out = threading.Event()

        def first():
            with tqdm_logging():
                first_is_in.set()
                second_is_in.wait(timeout=5)
            first_is_out.set()

        def second():
            first_is_in.wait(timeout=5)
            with tqdm_logging():
                second_is_in.set()
                first_is_out.wait(timeout=5)

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            assert not t.is_alive()

        assert self._tqdm_handlers_on_root() == []
        assert root.handlers == before_handlers
        assert self._quiet_logger_levels() == before_levels

    def test_a_run_that_raises_still_restores(self):
        root = logging.getLogger()
        before_handlers = root.handlers[:]
        before_levels = self._quiet_logger_levels()

        with pytest.raises(RuntimeError):
            with tqdm_logging():
                raise RuntimeError("the job failed")

        assert root.handlers == before_handlers
        assert self._quiet_logger_levels() == before_levels


class TestTqdmLoggingWhenTheRedirectionCannotBeMade:
    """A failure to set the redirection up must not disable it for good.

    The count says how many runs are relying on the redirection. Raising it for
    a run that never managed to make the change would leave every later run
    reading that count as "somebody else has already done this" — so the
    redirection would silently never happen again, and progress bars would be
    broken up by log lines for the rest of the process's life.
    """

    def test_a_failed_setup_leaves_nothing_behind(self):
        import src.services.parallel_utils as pu

        with patch.object(pu, "_send_logging_through_tqdm",
                          side_effect=RuntimeError("could not install")):
            with pytest.raises(RuntimeError):
                with tqdm_logging():
                    pass

        assert pu._swap_depth == 0
        assert pu._undo_swap is None

    def test_the_next_run_still_gets_its_redirection(self):
        import src.services.parallel_utils as pu
        from src.services.parallel_utils import _TqdmLoggingHandler

        with patch.object(pu, "_send_logging_through_tqdm",
                          side_effect=RuntimeError("could not install")):
            with pytest.raises(RuntimeError):
                with tqdm_logging():
                    pass

        root = logging.getLogger()
        before = root.handlers[:]
        with tqdm_logging():
            assert any(isinstance(h, _TqdmLoggingHandler) for h in root.handlers)
        assert root.handlers == before


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


# ===========================================================================
# src/services/parallel_utils.py — collect_image_files / natural_sort_key
# ===========================================================================

import os

from src.services.parallel_utils import collect_image_files


class TestCollectImageFiles:

    def test_returns_sorted_image_paths(self, tmp_path):
        (tmp_path / "b.jpg").write_bytes(b"")
        (tmp_path / "a.png").write_bytes(b"")
        (tmp_path / "z.txt").write_text("")  # not an image
        result = collect_image_files(str(tmp_path))
        names = [os.path.basename(p) for p in result]
        assert names == ["a.png", "b.jpg"]

    def test_empty_folder_returns_empty_list(self, tmp_path):
        assert collect_image_files(str(tmp_path)) == []

    def test_non_image_files_excluded(self, tmp_path):
        (tmp_path / "doc.pdf").write_bytes(b"")
        (tmp_path / "img.jpg").write_bytes(b"")
        result = collect_image_files(str(tmp_path))
        names = [os.path.basename(p) for p in result]
        assert "doc.pdf" not in names
        assert "img.jpg" in names

    def test_numeric_filenames_use_natural_order(self, tmp_path):
        (tmp_path / "page_1.jpg").write_bytes(b"")
        (tmp_path / "page_2.jpg").write_bytes(b"")
        (tmp_path / "page_10.jpg").write_bytes(b"")
        result = collect_image_files(str(tmp_path))
        names = [os.path.basename(p) for p in result]
        assert names == ["page_1.jpg", "page_2.jpg", "page_10.jpg"]


# ===========================================================================
# src/services/parallel_utils.py — run_folder_parallel's on_progress callback
# (regression coverage for the "progress bar frozen with workers > 1" bug:
# this function previously had no callback mechanism at all — only a local
# console tqdm bar, invisible to a caller running it on a background
# thread, like the webui's job runner does. Every plugin's own parallel
# path — transcription's process_image_folder, translation's
# process_image_translation_folder and _translate_pages_parallel — funnels
# through here or mirrors this exact pattern.)
# ===========================================================================

from src.services.parallel_utils import run_folder_parallel


class TestRunFolderParallelOnProgress:

    def _usage_data(self):
        return {"total_usage": {"total_tokens": 0, "total_cost": 0.0}}

    def test_called_once_per_item_reaching_the_full_total(self):
        files = ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
        calls: list = []
        run_folder_parallel(
            files,
            worker_fn=lambda i, path: (i, path, "ok"),
            make_error_result=lambda fname, e: (fname, "error"),
            usage_data=self._usage_data(),
            actual_workers=2,
            desc="Testing",
            on_progress=lambda done, total: calls.append((done, total)),
        )
        # Threads can finish in any order, so only the count and final call
        # are guaranteed, not which file happened to finish when.
        assert len(calls) == 4
        assert all(total == 4 for _done, total in calls)
        assert sorted(done for done, _total in calls) == [1, 2, 3, 4]
        assert calls[-1] == (4, 4)

    def test_called_even_when_a_worker_raises(self):
        files = ["ok.jpg", "err.jpg"]

        def worker(i, path):
            if "err" in path:
                raise RuntimeError("boom")
            return i, path, "ok"

        calls: list = []
        run_folder_parallel(
            files,
            worker_fn=worker,
            make_error_result=lambda fname, e: (fname, "error"),
            usage_data=self._usage_data(),
            actual_workers=2,
            desc="Testing",
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) == 2
        assert calls[-1] == (2, 2)

    def test_default_none_means_no_progress_reporting(self):
        # Must not raise (i.e. must not try calling None()).
        results = run_folder_parallel(
            ["a.jpg"],
            worker_fn=lambda i, path: (i, path, "ok"),
            make_error_result=lambda fname, e: (fname, "error"),
            usage_data=self._usage_data(),
            actual_workers=1,
            desc="Testing",
        )
        assert len(results) == 1

