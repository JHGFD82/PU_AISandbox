"""Tests for plugins/transcription/plugin.py::_run_transcription_review: running a review and showing or saving its report."""

import sys
from unittest.mock import MagicMock

import pytest

from src.errors import CLIError
from src.runtime.sandbox_processor import SandboxProcessor

from plugins.transcription.plugin import _run_transcription_review

TranscriptionReviewService = sys.modules["src.services.transcription_review_service"].TranscriptionReviewService


def _make_sandbox() -> SandboxProcessor:
    """Return a bare SandboxProcessor with transcription_review_service mocked."""
    proc = SandboxProcessor.__new__(SandboxProcessor)
    proc.transcription_review_service = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# _run_transcription_review
# ---------------------------------------------------------------------------

class TestRunTranscriptionReview:

    def test_delegates_to_service_and_prints(self, capsys):
        sandbox = _make_sandbox()
        sandbox.transcription_review_service.review_transcription.return_value = '{"errors": []}'
        _run_transcription_review(sandbox, "some text", "Japanese")
        out = capsys.readouterr().out
        assert '{"errors": []}' in out

    def test_saves_to_output_file(self, tmp_path):
        sandbox = _make_sandbox()
        sandbox.transcription_review_service.review_transcription.return_value = '{"errors": []}'
        out_file = str(tmp_path / "report.json")
        _run_transcription_review(sandbox, "text", "Japanese", output_file=out_file)
        assert (tmp_path / "report.json").read_text() == '{"errors": []}'

    def test_exception_wrapped_as_cli_error(self):
        sandbox = _make_sandbox()
        sandbox.transcription_review_service.review_transcription.side_effect = RuntimeError("API fail")
        with pytest.raises(CLIError, match="Error during transcription review"):
            _run_transcription_review(sandbox, "text", "Japanese")
