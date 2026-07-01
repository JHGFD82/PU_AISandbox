"""Tests for plugins/transcription/plugin.py::_run_transcription_review and
plugins/transcription/src/services/transcription_review_service.py::TranscriptionReviewService._get_model.
"""

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

    def test_kanbun_kwargs_passed_through(self):
        """kanbun/kanbun_main are forwarded to the service for extension-plugin compatibility."""
        sandbox = _make_sandbox()
        sandbox.transcription_review_service.review_transcription.return_value = '{}'
        _run_transcription_review(sandbox, "text", "Japanese", kanbun=True, kanbun_main=False)
        sandbox.transcription_review_service.review_transcription.assert_called_once_with(
            "text", "Japanese", kanbun=True, kanbun_main=False
        )


# ---------------------------------------------------------------------------
# TranscriptionReviewService — _get_model / review_transcription signature
# ---------------------------------------------------------------------------

class TestTranscriptionReviewServiceModel:

    def _make_svc(self, monkeypatch) -> "TranscriptionReviewService":
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        return TranscriptionReviewService("fake-key")

    def test_get_model_returns_string(self, monkeypatch):
        svc = self._make_svc(monkeypatch)
        from src.services import transcription_review_service as trs_mod
        monkeypatch.setattr(trs_mod, "resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr(trs_mod, "maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(trs_mod, "get_default_model", lambda _: "gpt-4o")
        model = svc._get_model()
        assert isinstance(model, str)
        assert len(model) > 0

    def test_review_transcription_accepts_kanbun_kwargs_without_error(self, monkeypatch):
        """Base service must not crash when called with kanbun kwargs (EA-plugin compatibility)."""
        svc = self._make_svc(monkeypatch)
        monkeypatch.setattr(svc, "_get_model", lambda: "gpt-4o")
        monkeypatch.setattr(svc, "_call_api", lambda *a, **kw: MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"meta": {}}'))]
        ))
        monkeypatch.setattr(svc, "_record_response_usage", lambda *a, **kw: None)
        result = svc.review_transcription("text", "English", kanbun=False, kanbun_main=False)
        assert isinstance(result, str)
