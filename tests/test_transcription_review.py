"""Tests for the transcription_review feature.

Covers:
- TranscriptionReviewPromptSpec (prompt assembly, note injection, kanbun flag)
- TranscriptionReviewService._inject_model_and_validate (JSON post-processing)
- TranscriptionReviewService.build_prompts (dry-run / note preview interface)
- TranscriptionReviewService.review_transcription (API call path via mock)
- CLI subparser wiring for the transcription_review command
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from pathlib import Path
from src.services.prompts.transcription_review import TranscriptionReviewPromptSpec
from src.services.transcription_review_service import TranscriptionReviewService
from src.cli import create_argument_parser
from src.runtime.plugin_loader import load_plugins

_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"


def _make_parser():
    return create_argument_parser(load_plugins(_PLUGINS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_service(**kwargs) -> TranscriptionReviewService:
    """Return a service with a dummy key and mocked token tracker."""
    return TranscriptionReviewService(
        api_key="test-key",
        token_tracker=MagicMock(),
        **kwargs,
    )


def _minimal_report(language: str = "Japanese") -> dict:
    """Minimal valid JSON report with no model field (service injects it)."""
    return {
        "meta": {
            "language": language,
            "identified_source": "test",
            "source_confidence": "high",
            "overall_quality": "good",
            "assessment": "Looks fine.",
            "error_count": 0,
        },
        "corrections": [],
    }


# ===========================================================================
# TranscriptionReviewPromptSpec
# ===========================================================================

class TestTranscriptionReviewPromptSpecDefaults:
    def test_returns_strings(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese")
        assert isinstance(spec.system_prompt(), str)
        assert isinstance(spec.user_prompt(), str)

    def test_language_appears_in_system_prompt(self):
        spec = TranscriptionReviewPromptSpec(language="Korean")
        assert "Korean" in spec.system_prompt()

    def test_language_appears_in_user_prompt(self):
        spec = TranscriptionReviewPromptSpec(language="Korean")
        assert "Korean" in spec.user_prompt()

    def test_no_kanbun_note_by_default(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese")
        assert "kanbun" not in spec.system_prompt().lower()

    def test_placeholder_text_in_default_user_prompt(self):
        spec = TranscriptionReviewPromptSpec(language="Chinese")
        # Default placeholder shows intent when build_prompts is called for dry-run
        assert "[" in spec.user_prompt()

    def test_custom_text_injected_into_user_prompt(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese")
        assert "MY_OCR_TEXT" in spec.user_prompt("MY_OCR_TEXT")


class TestTranscriptionReviewPromptSpecKanbun:
    def test_kanbun_note_present_when_flag_set(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese", kanbun=True)
        assert "kanbun" in spec.system_prompt().lower()

    def test_kanbun_note_absent_when_flag_false(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese", kanbun=False)
        assert "kanbun" not in spec.system_prompt().lower()


class TestTranscriptionReviewPromptSpecNoteInjection:
    def test_system_note_in_system_prompt(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese", system_note="MYSYS")
        assert "MYSYS" in spec.system_prompt()
        assert "MYSYS" not in spec.user_prompt()

    def test_user_note_in_user_prompt(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese", user_note="MYUSR")
        assert "MYUSR" in spec.user_prompt()
        assert "MYUSR" not in spec.system_prompt()

    def test_no_note_labels_when_notes_absent(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese")
        assert "ADDITIONAL INSTRUCTIONS" not in spec.system_prompt()
        assert "ADDITIONAL NOTES" not in spec.user_prompt()

    def test_system_note_label_present_when_set(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese", system_note="x")
        assert "ADDITIONAL INSTRUCTIONS" in spec.system_prompt()

    def test_user_note_label_present_when_set(self):
        spec = TranscriptionReviewPromptSpec(language="Japanese", user_note="x")
        assert "ADDITIONAL NOTES" in spec.user_prompt()


# ===========================================================================
# TranscriptionReviewService._inject_model_and_validate
# ===========================================================================

class TestInjectModelAndValidate:
    def test_injects_model_name(self):
        data = _minimal_report()
        raw = json.dumps(data)
        result = TranscriptionReviewService._inject_model_and_validate(raw, "gpt-4o", "Japanese")
        parsed = json.loads(result)
        assert parsed["meta"]["model"] == "gpt-4o"

    def test_preserves_existing_fields(self):
        data = _minimal_report()
        raw = json.dumps(data)
        result = TranscriptionReviewService._inject_model_and_validate(raw, "gpt-4o", "Japanese")
        parsed = json.loads(result)
        assert parsed["meta"]["error_count"] == 0
        assert parsed["corrections"] == []

    def test_strips_json_markdown_fences(self):
        data = _minimal_report()
        fenced = f"```json\n{json.dumps(data)}\n```"
        result = TranscriptionReviewService._inject_model_and_validate(fenced, "gpt-4o", "Japanese")
        parsed = json.loads(result)
        assert parsed["meta"]["model"] == "gpt-4o"

    def test_strips_plain_fences(self):
        data = _minimal_report()
        fenced = f"```\n{json.dumps(data)}\n```"
        result = TranscriptionReviewService._inject_model_and_validate(fenced, "gpt-4o", "Japanese")
        parsed = json.loads(result)
        assert parsed["meta"]["model"] == "gpt-4o"

    def test_non_json_returns_raw_string(self):
        raw = "This is not JSON at all."
        result = TranscriptionReviewService._inject_model_and_validate(raw, "gpt-4o", "Japanese")
        assert result == raw

    def test_output_is_pretty_printed(self):
        data = _minimal_report()
        raw = json.dumps(data)
        result = TranscriptionReviewService._inject_model_and_validate(raw, "gpt-4o", "Japanese")
        # Pretty-printed JSON contains newlines and indented braces
        assert "\n" in result
        assert "  " in result

    def test_language_preserved_if_already_set(self):
        data = _minimal_report(language="Korean")
        raw = json.dumps(data)
        result = TranscriptionReviewService._inject_model_and_validate(raw, "gpt-4o", "Japanese")
        parsed = json.loads(result)
        # Existing language value should not be overwritten
        assert parsed["meta"]["language"] == "Korean"

    def test_language_injected_if_absent(self):
        data = _minimal_report()
        del data["meta"]["language"]
        raw = json.dumps(data)
        result = TranscriptionReviewService._inject_model_and_validate(raw, "gpt-4o", "Japanese")
        parsed = json.loads(result)
        assert parsed["meta"]["language"] == "Japanese"


# ===========================================================================
# TranscriptionReviewService.build_prompts (dry-run interface)
# ===========================================================================

class TestBuildPrompts:
    def test_returns_two_strings(self):
        svc = make_service()
        sys_p, usr_p = svc.build_prompts("Japanese")
        assert isinstance(sys_p, str)
        assert isinstance(usr_p, str)

    def test_language_in_both_prompts(self):
        svc = make_service()
        sys_p, usr_p = svc.build_prompts("Chinese")
        assert "Chinese" in sys_p
        assert "Chinese" in usr_p

    def test_kanbun_in_system_when_set(self):
        svc = make_service()
        sys_p, _ = svc.build_prompts("Japanese", kanbun=True)
        assert "kanbun" in sys_p.lower()

    def test_text_injected_into_user_prompt(self):
        svc = make_service()
        _, usr_p = svc.build_prompts("Japanese", text="SAMPLE_TEXT")
        assert "SAMPLE_TEXT" in usr_p

    def test_system_note_reflected(self):
        svc = make_service()
        svc.system_note = "SYS_NOTE"
        sys_p, _ = svc.build_prompts("Japanese")
        assert "SYS_NOTE" in sys_p

    def test_user_note_reflected(self):
        svc = make_service()
        svc.user_note = "USR_NOTE"
        _, usr_p = svc.build_prompts("Japanese")
        assert "USR_NOTE" in usr_p


# ===========================================================================
# TranscriptionReviewService.review_transcription (mocked API)
# ===========================================================================

class TestReviewTranscription:
    def _make_api_response(self, content: str) -> MagicMock:
        """Build a mock that looks like a Portkey/OpenAI completion response."""
        choice = MagicMock()
        choice.message.content = content
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage.prompt_tokens = 100
        resp.usage.completion_tokens = 50
        resp.usage.total_tokens = 150
        return resp

    def test_returns_json_string(self):
        svc = make_service()
        data = _minimal_report()
        mock_resp = self._make_api_response(json.dumps(data))
        with patch.object(svc, "_create_completion", return_value=mock_resp), \
             patch.object(svc, "_record_response_usage"), \
             patch("src.services.base_service.resolve_model", return_value="gpt-4o"), \
             patch("src.services.base_service.maybe_sync_model_pricing"), \
             patch("src.services.transcription_review_service.get_model_system_role", return_value="system"), \
             patch("src.services.transcription_review_service.get_model_max_completion_tokens", return_value=4000):
            result = svc.review_transcription("some text", "Japanese")
        parsed = json.loads(result)
        assert "meta" in parsed
        assert parsed["meta"]["model"] == "gpt-4o"

    def test_model_name_injected(self):
        svc = make_service()
        data = _minimal_report()
        mock_resp = self._make_api_response(json.dumps(data))
        with patch.object(svc, "_create_completion", return_value=mock_resp), \
             patch.object(svc, "_record_response_usage"), \
             patch("src.services.base_service.resolve_model", return_value="gpt-4o-mini"), \
             patch("src.services.base_service.maybe_sync_model_pricing"), \
             patch("src.services.transcription_review_service.get_model_system_role", return_value="system"), \
             patch("src.services.transcription_review_service.get_model_max_completion_tokens", return_value=4000):
            result = svc.review_transcription("some text", "Korean")
        parsed = json.loads(result)
        assert parsed["meta"]["model"] == "gpt-4o-mini"

    def test_kanbun_passed_to_prompt(self):
        svc = make_service()
        data = _minimal_report()
        mock_resp = self._make_api_response(json.dumps(data))
        with patch.object(svc, "_create_completion", return_value=mock_resp) as mock_call, \
             patch.object(svc, "_record_response_usage"), \
             patch("src.services.base_service.resolve_model", return_value="gpt-4o"), \
             patch("src.services.base_service.maybe_sync_model_pricing"), \
             patch("src.services.transcription_review_service.get_model_system_role", return_value="system"), \
             patch("src.services.transcription_review_service.get_model_max_completion_tokens", return_value=4000):
            svc.review_transcription("some text", "Japanese", kanbun=True)
        # The system message (first message) should mention kanbun
        messages = mock_call.call_args[0][1]
        sys_content = messages[0]["content"]
        assert "kanbun" in sys_content.lower()


# ===========================================================================
# CLI subparser wiring
# ===========================================================================

class TestTranscriptionReviewCLI:
    """Verify the transcription_review subparser is correctly wired."""

    @pytest.fixture
    def parser(self):
        return _make_parser()

    def test_subparser_exists(self, parser):
        """transcription_review is a recognised professor sub-command."""
        args = parser.parse_args(["heller", "transcription_review", "J", "-c"])
        assert args.command == "transcription_review"

    def test_language_code_parsed(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "J", "-c"])
        assert args.language_code == "Japanese"

    def test_kanbun_flag_default_false(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "J", "-c"])
        assert args.kanbun is False

    def test_kanbun_flag_set(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "J", "-c", "--kanbun"])
        assert args.kanbun is True

    def test_input_file_flag(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "J", "-i", "trans.txt"])
        assert args.input_file == "trans.txt"

    def test_output_flag(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "J", "-i", "t.txt", "-o", "out.json"])
        assert args.output_file == "out.json"

    def test_mutual_exclusion_of_i_and_c(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["heller", "transcription_review", "J", "-i", "t.txt", "-c"])

    def test_dry_run_flag(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "J", "-c", "--dry-run"])
        assert args.dry_run is True

    def test_model_flag(self, parser):
        args = parser.parse_args(["heller", "transcription_review", "J", "-c", "-m", "gpt-4o-mini"])
        assert args.model == "gpt-4o-mini"


# ===========================================================================
# review_transcription — exception and empty-choices paths (lines 158-160, 169)
# ===========================================================================

class TestReviewTranscriptionExceptionPaths:
    """Cover lines 158-160 (API exception re-raised) and 169 (empty choices → returns '')."""

    def _make_svc(self):
        from src.services.transcription_review_service import TranscriptionReviewService
        svc = TranscriptionReviewService(api_key="fake-key", token_tracker=MagicMock())
        return svc

    def _patch_catalog(self):
        return [
            patch("src.services.base_service.resolve_model", return_value="gpt-4o"),
            patch("src.services.base_service.maybe_sync_model_pricing"),
            patch("src.services.transcription_review_service.get_model_system_role", return_value="system"),
            patch("src.services.transcription_review_service.get_model_max_completion_tokens", return_value=4000),
        ]

    def test_api_exception_is_re_raised(self):
        """_call_api raising should call handle_api_errors and then re-raise (lines 158-160)."""
        svc = self._make_svc()
        patches = self._patch_catalog()
        for p in patches:
            p.start()
        try:
            with patch.object(svc, "_call_api", side_effect=RuntimeError("boom")), \
                 patch("src.services.transcription_review_service.handle_api_errors") as mock_handle:
                with pytest.raises(RuntimeError, match="boom"):
                    svc.review_transcription("text", "Japanese")
            mock_handle.assert_called_once()
        finally:
            for p in patches:
                p.stop()

    def test_empty_choices_returns_empty_string(self):
        """When response has no choices, review_transcription should return '' (line 169)."""
        svc = self._make_svc()
        no_choices_resp = MagicMock()
        no_choices_resp.choices = []
        patches = self._patch_catalog()
        for p in patches:
            p.start()
        try:
            with patch.object(svc, "_call_api", return_value=no_choices_resp), \
                 patch.object(svc, "_record_response_usage"):
                result = svc.review_transcription("text", "Japanese")
            assert result == ""
        finally:
            for p in patches:
                p.stop()
