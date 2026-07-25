"""Tests for plugins/transcription/src/runtime/image_handler.py (registered as src.runtime.image_handler).

Covers image OCR: process_image, process_image_folder (sequential and
parallel paths).
"""

import os
from unittest.mock import MagicMock

import pytest

from src.errors import CLIError
from src.runtime.sandbox_processor import SandboxProcessor


# ---------------------------------------------------------------------------
# Helpers — build a SandboxProcessor bypassing real service init
# ---------------------------------------------------------------------------

def _make_processor(monkeypatch) -> SandboxProcessor:
    """Create a SandboxProcessor with all real services replaced by MagicMocks."""
    monkeypatch.setattr("src.runtime.sandbox_processor.get_api_key",
                        lambda name: ("fake-key", "Professor Fake"))

    for svc_path in (
        "src.services.base_service.resolve_model",
        "src.services.base_service.maybe_sync_model_pricing",
        "src.services.base_service.get_model_system_role",
        "src.services.base_service.get_model_max_completion_tokens",
    ):
        monkeypatch.setattr(svc_path, MagicMock(return_value="gpt-4o"), raising=False)

    monkeypatch.setattr(
        "src.tracking.token_tracker.TokenTracker.__init__",
        lambda self, professor: None,
    )

    proc = SandboxProcessor.__new__(SandboxProcessor)
    proc.professor_name = "fake"
    proc.professor_display_name = "Professor Fake"
    proc.token_tracker = MagicMock()
    proc.token_tracker.usage_data = {"total_usage": {"total_tokens": 0, "total_cost": 0.0}}
    proc.image_processor_service = MagicMock()
    proc.image_processor = MagicMock()
    proc.pdf_processor = MagicMock()
    proc.file_output = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# process_image / process_image_folder
# ---------------------------------------------------------------------------

class TestProcessImage:

    def test_delegates_to_image_processor_service(self, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)
        proc.image_processor_service.process_image_ocr.return_value = "extracted text"
        proc.process_image("/fake/path/img.jpg", "English")
        out = capsys.readouterr().out
        assert "extracted text" in out

    def test_saves_to_output_file(self, monkeypatch, tmp_path):
        proc = _make_processor(monkeypatch)
        proc.image_processor_service.process_image_ocr.return_value = "ocr result"
        out_file = str(tmp_path / "result.txt")
        proc.process_image("/fake/img.jpg", "English", output_file=out_file)
        proc.file_output.save_translation_output.assert_called_once()

    def test_exception_wrapped_as_cli_error(self, monkeypatch):
        proc = _make_processor(monkeypatch)
        proc.image_processor_service.process_image_ocr.side_effect = RuntimeError("vision fail")
        with pytest.raises(CLIError, match="Error processing image"):
            proc.process_image("/fake/img.jpg", "English")


class TestProcessImageFolder:

    def test_empty_folder_raises_cli_error(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        with pytest.raises(CLIError, match="No image files found"):
            proc.process_image_folder(str(tmp_path), "English")

    def test_sequential_processing_prints_results(self, tmp_path, monkeypatch, capsys):
        proc = _make_processor(monkeypatch)
        img = tmp_path / "scan.jpg"
        img.write_bytes(b"fake-jpg")
        proc.image_processor_service.process_image_ocr.return_value = "OCR output"
        proc.process_image_folder(str(tmp_path), "English")
        out = capsys.readouterr().out
        assert "scan.jpg" in out
        assert "OCR output" in out

    def test_saves_output_file_after_processing(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        img = tmp_path / "scan.jpg"
        img.write_bytes(b"fake")
        proc.image_processor_service.process_image_ocr.return_value = "text"
        proc.process_image_folder(str(tmp_path), "English", output_file="out.txt")
        proc.file_output.save_translation_output.assert_called_once()

    def test_on_progress_called_per_image_in_order(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            (tmp_path / name).write_bytes(b"fake")
        proc.image_processor_service.process_image_ocr.return_value = "text"
        calls: list = []
        proc.process_image_folder(
            str(tmp_path), "English",
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert calls == [(1, 3), (2, 3), (3, 3)]

    def test_on_progress_called_even_when_an_image_errors(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        (tmp_path / "err.jpg").write_bytes(b"fake")
        (tmp_path / "ok.jpg").write_bytes(b"fake")

        def flaky(path, *a, **kw):
            if "err" in path:
                raise RuntimeError("boom")
            return "text"

        proc.image_processor_service.process_image_ocr = flaky
        calls: list = []
        proc.process_image_folder(
            str(tmp_path), "English",
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert calls == [(1, 2), (2, 2)]

    def test_on_progress_none_by_default(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        (tmp_path / "a.jpg").write_bytes(b"fake")
        proc.image_processor_service.process_image_ocr.return_value = "text"
        # Must not raise (i.e. must not try calling None()).
        proc.process_image_folder(str(tmp_path), "English")

    def test_on_page_text_called_per_image_in_order_with_content(self, tmp_path, monkeypatch):
        # Regression coverage mirroring the translation plugin's
        # on_page_text fix — a professor should see each image's actual
        # transcribed text as it's produced, not just a numeric progress
        # ping. See process_image_translation_folder's equivalent
        # parameter in the translation plugin for the mirrored behavior.
        proc = _make_processor(monkeypatch)
        (tmp_path / "a.jpg").write_bytes(b"fake")
        (tmp_path / "b.jpg").write_bytes(b"fake")

        def ocr(path, *a, **kw):
            return "text from a" if "a.jpg" in path else "text from b"

        proc.image_processor_service.process_image_ocr = ocr
        calls: list = []
        proc.process_image_folder(
            str(tmp_path), "English",
            on_page_text=lambda idx, text: calls.append((idx, text)),
        )
        assert calls == [(1, "text from a"), (2, "text from b")]

    def test_on_page_text_called_even_when_an_image_errors(self, tmp_path, monkeypatch):
        # Same as on_progress: an image's own error placeholder text is
        # still reported, so a professor watching the conversation sees
        # every image accounted for rather than a silent gap.
        proc = _make_processor(monkeypatch)
        (tmp_path / "err.jpg").write_bytes(b"fake")
        (tmp_path / "ok.jpg").write_bytes(b"fake")

        def flaky(path, *a, **kw):
            if "err" in path:
                raise RuntimeError("boom")
            return "text"

        proc.image_processor_service.process_image_ocr = flaky
        calls: list = []
        proc.process_image_folder(
            str(tmp_path), "English",
            on_page_text=lambda idx, text: calls.append((idx, text)),
        )
        assert calls[0][0] == 1
        assert "Error processing" in calls[0][1]
        assert calls[1] == (2, "text")

    def test_on_page_text_none_by_default(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        (tmp_path / "a.jpg").write_bytes(b"fake")
        proc.image_processor_service.process_image_ocr.return_value = "text"
        # Must not raise (i.e. must not try calling None()).
        proc.process_image_folder(str(tmp_path), "English")

    def test_on_page_text_not_called_on_parallel_path(self, tmp_path, monkeypatch):
        # Same sequential-only restriction as on_progress — page order
        # can't be guaranteed once more than one worker is running.
        proc = _make_processor(monkeypatch)
        img = tmp_path / "scan.jpg"
        img.write_bytes(b"fake")
        monkeypatch.setattr(
            "src.runtime.image_handler.run_folder_parallel",
            lambda *a, **k: {0: ("scan.jpg", "OCR result")},
        )
        monkeypatch.setattr("src.runtime.image_handler.cap_worker_count", lambda *a, **k: 2)
        proc.image_processor_service._get_model.return_value = "gpt-4o"
        calls: list = []
        proc.process_image_folder(
            str(tmp_path), "English", workers=2,
            on_page_text=lambda idx, text: calls.append((idx, text)),
        )
        assert calls == []

    def test_parallel_path_saves_output_file(self, tmp_path, monkeypatch):
        """Parallel workers > 1 with output_file set exercises the parallel output branch."""
        proc = _make_processor(monkeypatch)
        img = tmp_path / "scan.jpg"
        img.write_bytes(b"fake")
        # Stub out run_folder_parallel so we don't spin up real threads
        fake_results = {0: ("scan.jpg", "OCR result")}
        monkeypatch.setattr(
            "src.runtime.image_handler.run_folder_parallel",
            lambda *a, **k: fake_results,
        )
        monkeypatch.setattr(
            "src.runtime.image_handler.cap_worker_count",
            lambda *a, **k: 2,
        )
        proc.image_processor_service._get_model.return_value = "gpt-4o"
        proc.process_image_folder(str(tmp_path), "English", output_file="out.txt", workers=2)
        proc.file_output.save_translation_output.assert_called_once()


class TestProcessImageFolderParallel:

    def test_parallel_ocr_processes_all_images(self, monkeypatch, tmp_path, capsys):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "ocr_imgs"
        folder.mkdir()
        for name in ["x.jpg", "y.jpg"]:
            (folder / name).write_bytes(b"fake")

        proc.image_processor_service.process_image_ocr = MagicMock(
            return_value="Extracted text"
        )

        proc.process_image_folder(str(folder), "English", workers=2)
        assert proc.image_processor_service.process_image_ocr.call_count == 2

    def test_parallel_ocr_output_uses_natural_filename_order(self, monkeypatch, tmp_path):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "ocr_imgs_order"
        folder.mkdir()
        for name in ["page_1.jpg", "page_2.jpg", "page_10.jpg"]:
            (folder / name).write_bytes(b"fake")

        def _ocr_side_effect(file_path, *_args, **_kwargs):
            return f"OCR({os.path.basename(file_path)})"

        proc.image_processor_service.process_image_ocr = MagicMock(side_effect=_ocr_side_effect)

        proc.process_image_folder(str(folder), "English", output_file="out.txt", workers=2)

        saved_text = proc.file_output.save_translation_output.call_args.args[0]
        i1 = saved_text.index("=== page_1.jpg ===")
        i2 = saved_text.index("=== page_2.jpg ===")
        i10 = saved_text.index("=== page_10.jpg ===")
        assert i1 < i2 < i10

    def test_on_progress_called_on_the_real_parallel_path(self, monkeypatch, tmp_path):
        # Regression coverage for the "progress bar frozen with workers > 1"
        # bug: on_progress previously wasn't forwarded to run_folder_parallel
        # at all, so a webui transcribe job run with more than one worker
        # never produced a single progress update.
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "ocr_imgs_progress"
        folder.mkdir()
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            (folder / name).write_bytes(b"fake")
        proc.image_processor_service.process_image_ocr = MagicMock(return_value="text")

        calls: list = []
        proc.process_image_folder(
            str(folder), "English", workers=2,
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) == 3
        assert all(total == 3 for _done, total in calls)
        assert sorted(done for done, _total in calls) == [1, 2, 3]
        assert calls[-1] == (3, 3)


class TestProcessImageFolderSequentialException:
    """Sequential process_image_folder catches exception from process_image_ocr."""

    def test_ocr_exception_continues_processing(self, monkeypatch, tmp_path, capsys):
        proc = _make_processor(monkeypatch)
        folder = tmp_path / "ocr_err"
        folder.mkdir()
        (folder / "bad.jpg").write_bytes(b"fake")
        (folder / "good.jpg").write_bytes(b"fake")

        call_count = [0]
        def flaky_ocr(path, language, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("OCR failed on bad image")
            return "Good OCR text"

        proc.image_processor_service.process_image_ocr = flaky_ocr
        # Should complete without raising, printing an error for the first image
        proc.process_image_folder(str(folder), "English", workers=1)
        out = capsys.readouterr().out
        assert "ERROR" in out or "error" in out.lower()
