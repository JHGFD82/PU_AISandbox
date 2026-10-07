"""Tests for plugins/translation/src/services/image_translation_service.py: translating the text in a picture."""

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest


ImageTranslationService = sys.modules["src.services.image_translation_service"].ImageTranslationService


def _make_img_svc(monkeypatch) -> ImageTranslationService:
    """Return an ImageTranslationService with API + catalog patched."""
    monkeypatch.setattr(
        "src.services.base_service.get_model_max_completion_tokens",
        lambda m, d: d,
    )
    svc = ImageTranslationService("fake-api-key", professor="test")
    return svc


# ---------------------------------------------------------------------------
# ImageTranslationService — __init__, build_prompts, _build_system/user_prompt
# ---------------------------------------------------------------------------

class TestImageTranslationService:

    def test_instantiates_without_error(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        assert svc is not None
        assert svc.tables is False

    def test_build_system_prompt(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        result = svc._build_system_prompt("Japanese", "English")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_user_prompt(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        result = svc._build_user_prompt("Japanese", "English")
        assert "Japanese" in result

    def test_build_prompts_returns_tuple(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        system, user = svc.build_prompts("Japanese", "English")
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_build_prompts_vertical_flag(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        _, user_normal = svc.build_prompts("Japanese", "English", vertical=False)
        _, user_vertical = svc.build_prompts("Japanese", "English", vertical=True)
        assert len(user_vertical) > len(user_normal)


# ---------------------------------------------------------------------------
# ImageTranslationService — _get_model / _get_max_tokens
# ---------------------------------------------------------------------------

class TestImageTranslationServiceModel:

    def test_get_max_tokens_uses_custom(self, monkeypatch):
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        svc = ImageTranslationService("fake-key", max_tokens=999)
        assert svc._get_max_tokens("gpt-4o") == 999

    def test_get_max_tokens_uses_catalog_default(self, monkeypatch):
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        from plugins.translation.src.settings import IMAGE_TRANSLATION_MAX_TOKENS
        svc = ImageTranslationService("fake-key")
        result = svc._get_max_tokens("gpt-4o")
        assert result == IMAGE_TRANSLATION_MAX_TOKENS

    def test_get_model_returns_string(self, monkeypatch):
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        svc = ImageTranslationService("fake-key")
        model = svc._get_model()
        assert isinstance(model, str)
        assert len(model) > 0


# ---------------------------------------------------------------------------
# ImageTranslationService — _parse_response
# ---------------------------------------------------------------------------

class TestImageParseResponse:

    def test_parses_both_sections(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        transcript, translation = svc._parse_response(
            "[TRANSCRIPT]\nhello world\n[TRANSLATION]\nhow are you"
        )
        assert transcript == "hello world"
        assert translation == "how are you"

    def test_translation_only(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        transcript, translation = svc._parse_response("[TRANSLATION]\nsome translation")
        assert transcript == ""
        assert translation == "some translation"

    def test_fallback_when_no_sections(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        transcript, translation = svc._parse_response("plain response text without sections")
        assert transcript == ""
        assert translation == "plain response text without sections"


# ---------------------------------------------------------------------------
# ImageTranslationService — _call_api
# ---------------------------------------------------------------------------

class TestImageCallApi:

    def test_call_api_builds_multipart_message(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        captured: dict = {}

        def fake_completion(model, messages, max_tokens, **kw):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            return MagicMock()

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        svc._call_api("gpt-4o", "system", "sys prompt", "user prompt",
                      "data:image/jpeg;base64,abc", 1000)
        assert captured["max_tokens"] == 1000
        user_msg = captured["messages"][1]
        assert user_msg["role"] == "user"
        assert any(c.get("type") == "image_url" for c in user_msg["content"])

    def test_call_api_uses_custom_temperature(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        svc = ImageTranslationService("fake-key", temperature=0.3)
        captured: dict = {}

        def fake_completion(model, messages, max_tokens, **kw):
            captured["temperature"] = kw.get("temperature")
            return MagicMock()

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        svc._call_api("gpt-4o", "system", "sys prompt", "user prompt",
                      "data:image/jpeg;base64,abc", 500)
        assert captured["temperature"] == 0.3


# ---------------------------------------------------------------------------
# ImageTranslationService — _get_model warning branch (line 59)
# ---------------------------------------------------------------------------

class TestImageGetModelWarning:

    def test_logs_warning_when_preferred_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "fallback-model")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        svc = ImageTranslationService("fake-key")
        model = svc._get_model()
        assert model == "fallback-model"


# ---------------------------------------------------------------------------
# ImageTranslationService — process_image_translation
# ---------------------------------------------------------------------------

class TestProcessImageTranslation:

    def _make_api_response(self, content: str) -> Any:
        class _Usage:
            prompt_tokens = 5
            completion_tokens = 20
            total_tokens = 25

        class _Message:
            def __init__(self, c: str) -> None:
                self.content = c

        class _Choice:
            def __init__(self, c: str) -> None:
                self.message = _Message(c)
                self.finish_reason = "stop"

        class _Resp:
            def __init__(self, c: str) -> None:
                self.id = "resp-img-1"
                self.model = "gpt-4o"
                self.usage = _Usage()
                self.choices = [_Choice(c)]

        return _Resp(content)

    def _patch_svc(self, monkeypatch) -> ImageTranslationService:
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(its_mod, "get_model_system_role", lambda m: "system")
        svc = ImageTranslationService("fake-key")
        monkeypatch.setattr(svc.image_processor, "local_image_to_data_url",
                            lambda path: "data:image/jpeg;base64,abc")
        return svc

    def test_returns_transcript_and_translation(self, monkeypatch):
        svc = self._patch_svc(monkeypatch)
        content = "[TRANSCRIPT]\nオリジナル\n[TRANSLATION]\nOriginal"
        monkeypatch.setattr(svc, "_create_completion",
                            lambda *a, **kw: self._make_api_response(content))
        transcript, translation = svc.process_image_translation(
            "dummy.jpg", "Japanese", "English"
        )
        assert transcript == "オリジナル"
        assert translation == "Original"

    def test_raises_value_error_when_model_lacks_vision(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "text-only-model")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "model_supports_vision", lambda m: False)
        monkeypatch.setattr(
            its_mod, "cannot_read_images_message", lambda m: f"'{m}' cannot read images."
        )
        svc = ImageTranslationService("fake-key")
        with pytest.raises(ValueError, match="cannot read images"):
            svc.process_image_translation("dummy.jpg", "Japanese", "English")

    def test_raises_when_image_file_unreadable(self, monkeypatch):
        svc = self._patch_svc(monkeypatch)

        def fail_read(path: str) -> str:
            raise IOError("File not found")

        monkeypatch.setattr(svc.image_processor, "local_image_to_data_url", fail_read)
        with pytest.raises(IOError):
            svc.process_image_translation("missing.jpg", "Japanese", "English")

    def test_no_choices_response_exhausts_retries(self, monkeypatch):
        import time as _time

        import src.services.base_service as bsm
        svc = self._patch_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 1)
        monkeypatch.setattr(_time, "sleep", lambda x: None)

        class _NoChoicesResp:
            id = "r"
            model = "gpt-4o"

            class _Usage:
                prompt_tokens = 5
                completion_tokens = 10
                total_tokens = 15

            usage = _Usage()
            choices: list = []

        monkeypatch.setattr(svc, "_create_completion", lambda *a, **kw: _NoChoicesResp())
        with pytest.raises(RuntimeError):
            svc.process_image_translation("dummy.jpg", "Japanese", "English")


# ---------------------------------------------------------------------------
# ImageTranslationService — process_image_translation retry body branches
# ---------------------------------------------------------------------------

class TestProcessImageTranslationRetryPaths:

    def _make_patch_svc(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(its_mod, "get_model_system_role", lambda m: "system")
        svc = ImageTranslationService("fake-key")
        monkeypatch.setattr(svc.image_processor, "local_image_to_data_url",
                            lambda path: "data:image/jpeg;base64,abc")
        return svc

    def _resp_with_content(self, content):
        class _Usage:
            prompt_tokens = 5
            completion_tokens = 10
            total_tokens = 15

        class _Msg:
            def __init__(self, c): self.content = c

        class _Choice:
            def __init__(self, c):
                self.message = _Msg(c)
                self.finish_reason = "stop"

        class _Resp:
            def __init__(self, c):
                self.id = "r"
                self.model = "gpt-4o"
                self.usage = _Usage()
                self.choices = [_Choice(c)]

        return _Resp(content)

    def test_none_content_then_valid_returns_translation(self, monkeypatch):
        import time as _time

        import src.services.base_service as bsm
        svc = self._make_patch_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 3)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        call_count = [0]

        def fake_completion(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._resp_with_content(None)  # content is None → retry
            return self._resp_with_content("[TRANSCRIPT]\nhello\n[TRANSLATION]\nworld")

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        transcript, translation = svc.process_image_translation("dummy.jpg", "Japanese", "English")
        assert translation == "world"
        assert call_count[0] == 2

    def test_whitespace_content_then_valid_returns_translation(self, monkeypatch):
        import time as _time

        import src.services.base_service as bsm
        svc = self._make_patch_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 3)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        call_count = [0]

        def fake_completion(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._resp_with_content("   ")  # whitespace → retry
            return self._resp_with_content("[TRANSCRIPT]\nhello\n[TRANSLATION]\nworld")

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        transcript, translation = svc.process_image_translation("dummy.jpg", "Japanese", "English")
        assert translation == "world"
        assert call_count[0] == 2

    def test_non_string_content_then_valid_returns_translation(self, monkeypatch):
        import time as _time

        import src.services.base_service as bsm
        svc = self._make_patch_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 3)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        call_count = [0]

        def fake_completion(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._resp_with_content(42)  # int, not str → retry
            return self._resp_with_content("[TRANSCRIPT]\nhello\n[TRANSLATION]\nworld")

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        transcript, translation = svc.process_image_translation("dummy.jpg", "Japanese", "English")
        assert translation == "world"
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# ImageTranslationService — blank image short-circuit
# ---------------------------------------------------------------------------

class TestProcessImageTranslationBlankShortCircuit:

    def _patch_svc(self, monkeypatch) -> "ImageTranslationService":
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(its_mod, "get_model_system_role", lambda m: "system")
        svc = ImageTranslationService("fake-key")
        monkeypatch.setattr(svc.image_processor, "local_image_to_data_url",
                            lambda path: "data:image/jpeg;base64,abc")
        return svc

    def test_blank_image_returns_empty_tuple_without_api_call(self, monkeypatch):
        """process_image_translation must return ('', '') immediately for blank images."""
        svc = self._patch_svc(monkeypatch)
        monkeypatch.setattr(svc.image_processor, "is_blank_image", lambda *a, **kw: True)
        api_called = [False]

        def _no_api(*a, **kw):
            api_called[0] = True
            raise AssertionError("API should not be called for blank images")

        monkeypatch.setattr(svc, "_create_completion", _no_api)
        transcript, translation = svc.process_image_translation("blank.png", "Japanese", "English")
        assert transcript == ""
        assert translation == ""
        assert not api_called[0]

    def test_non_blank_image_still_calls_api(self, monkeypatch):
        """process_image_translation must proceed normally when image is not blank."""
        svc = self._patch_svc(monkeypatch)
        monkeypatch.setattr(svc.image_processor, "is_blank_image", lambda *a, **kw: False)

        class _Usage:
            prompt_tokens = 5
            completion_tokens = 20
            total_tokens = 25

        class _Msg:
            content = "[TRANSCRIPT]\n文字\n[TRANSLATION]\nText"

        class _Choice:
            message = _Msg()
            finish_reason = "stop"

        class _Resp:
            id = "r"
            model = "gpt-4o"
            usage = _Usage()
            choices = [_Choice()]

        monkeypatch.setattr(svc, "_create_completion", lambda *a, **kw: _Resp())
        transcript, translation = svc.process_image_translation("content.png", "Japanese", "English")
        assert transcript == "文字"
        assert translation == "Text"
