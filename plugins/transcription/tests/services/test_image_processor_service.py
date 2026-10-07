"""Tests for plugins/transcription/src/services/image_processor_service.py: reading the text in a picture.

The model is never really asked: ``_create_completion`` is replaced by a stand-in
that hands back the replies a test scripts, and records what it was sent.
"""

import sys
from types import SimpleNamespace

import pytest

svc_mod = sys.modules["src.services.image_processor_service"]
ImageProcessorService = svc_mod.ImageProcessorService

DATA_URL = "data:image/png;base64,abc"


def _reply(content):
    """A reply shaped like the API's, carrying *content* as the model's text."""
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=10, total_tokens=15)
    message = SimpleNamespace(content=content)
    return SimpleNamespace(id="r", model="gpt-4o", usage=usage,
                           choices=[SimpleNamespace(message=message, finish_reason="stop")])


def _no_choices():
    reply = _reply("unused")
    reply.choices = []
    return reply


class _Model:
    """Stands in for the API: gives back *replies* in order and keeps what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, model, messages, max_tokens, **params):
        self.calls.append({"model": model, "messages": messages,
                           "max_tokens": max_tokens, **params})
        return self.replies.pop(0)


@pytest.fixture
def service(monkeypatch):
    """An OCR service on gpt-4o that can read images, with the picture already read in."""
    # Asking again normally waits a few seconds first; here it need not.
    monkeypatch.setattr("src.services.base_service.RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
    monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
    monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
    monkeypatch.setattr(svc_mod, "model_supports_vision", lambda m: True)
    monkeypatch.setattr(svc_mod, "get_model_system_role", lambda m: "system")
    monkeypatch.setattr(svc_mod, "get_model_max_completion_tokens", lambda m, d: d)
    svc = ImageProcessorService("fake-key")
    monkeypatch.setattr(svc.image_processor, "is_blank_image", lambda path: False)
    monkeypatch.setattr(svc.image_processor, "local_image_to_data_url", lambda path: DATA_URL)
    return svc


def _answering(monkeypatch, svc, *replies) -> _Model:
    model = _Model(*replies)
    monkeypatch.setattr(svc, "_create_completion", model)
    return model


class TestThePrompts:
    def test_they_name_the_language_being_read(self, service):
        system, user = service.build_prompts("English")
        assert "English" in system and "English" in user

    def test_notes_go_where_they_were_aimed(self, service):
        service.system_note = "standing instruction"
        service.user_note = "about this page"
        system, user = service.build_prompts("English")
        assert "standing instruction" in system and "standing instruction" not in user
        assert "about this page" in user and "about this page" not in system

    def test_options_only_an_extension_uses_are_accepted_and_change_nothing(self, service):
        """An East Asian extension passes vertical/spread through the same call."""
        assert service.build_prompts("English", vertical=True, spread=True) == service.build_prompts("English")


class TestReadingOnePicture:
    def test_the_models_text_comes_back(self, monkeypatch, service):
        _answering(monkeypatch, service, _reply("Dear Sir,"))
        assert service.process_image_ocr("page.png", "English") == "Dear Sir,"

    def test_the_picture_and_both_prompts_are_sent(self, monkeypatch, service):
        model = _answering(monkeypatch, service, _reply("text"))
        service.process_image_ocr("page.png", "English")

        system, user = service.build_prompts("English")
        messages = model.calls[0]["messages"]
        assert messages[0] == {"role": "system", "content": system}
        assert messages[1]["content"][0] == {"type": "text", "text": user}
        assert DATA_URL in str(messages[1]["content"][1])

    def test_the_ocr_settings_are_used_unless_some_are_given(self, monkeypatch, service):
        model = _answering(monkeypatch, service, _reply("a"), _reply("b"))
        service.process_image_ocr("page.png", "English")
        assert (model.calls[0]["temperature"], model.calls[0]["top_p"]) == (
            svc_mod.OCR_TEMPERATURE, svc_mod.OCR_TOP_P)
        assert model.calls[0]["max_tokens"] == svc_mod.OCR_MAX_TOKENS

        service.custom_temperature, service.custom_top_p = 0.9, 0.5
        service.process_image_ocr("page.png", "English")
        assert (model.calls[1]["temperature"], model.calls[1]["top_p"]) == (0.9, 0.5)

    def test_a_model_that_cannot_read_pictures_is_refused_before_anything_is_sent(
        self, monkeypatch, service
    ):
        monkeypatch.setattr(svc_mod, "model_supports_vision", lambda m: False)
        model = _answering(monkeypatch, service)
        with pytest.raises(ValueError, match="gpt-4o"):
            service.process_image_ocr("page.png", "English")
        assert model.calls == []

    def test_a_blank_page_costs_nothing(self, monkeypatch, service):
        monkeypatch.setattr(service.image_processor, "is_blank_image", lambda path: True)
        model = _answering(monkeypatch, service)
        assert service.process_image_ocr("blank.png", "English") == ""
        assert model.calls == []

    def test_a_picture_that_cannot_be_opened_says_so(self, monkeypatch, service):
        def unreadable(path):
            raise OSError("not an image")
        monkeypatch.setattr(service.image_processor, "local_image_to_data_url", unreadable)
        with pytest.raises(OSError, match="not an image"):
            service.process_image_ocr("page.png", "English")


class TestWhenTheModelAnswersWithNothing:
    @pytest.mark.parametrize("useless", [
        _reply(None), _reply("   "), _reply(["not", "text"]), _no_choices(),
    ], ids=["none", "blank", "not-text", "no-choices"])
    def test_it_asks_again(self, monkeypatch, service, useless):
        model = _answering(monkeypatch, service, useless, _reply("second time"))
        assert service.process_image_ocr("page.png", "English") == "second time"
        assert len(model.calls) == 2

    def test_it_gives_up_with_a_reason_after_the_last_try(self, monkeypatch, service):
        import src.services.base_service as base

        _answering(monkeypatch, service, *[_reply("") for _ in range(base.MAX_RETRIES)])
        with pytest.raises(RuntimeError, match="OCR returned no content"):
            service.process_image_ocr("page.png", "English")


class TestMoreThanOnePass:
    """Each pass after the first shows the model its last reading and the picture again."""

    def test_the_last_passs_reading_is_returned(self, monkeypatch, service):
        _answering(monkeypatch, service, _reply("first"), _reply("second"), _reply("third"))
        assert service.process_image_ocr("page.png", "English", passes=3) == "third"

    def test_each_pass_is_shown_the_reading_before_it(self, monkeypatch, service):
        model = _answering(monkeypatch, service, _reply("first"), _reply("second"), _reply("third"))
        service.process_image_ocr("page.png", "English", passes=3)

        second, third = model.calls[1]["messages"], model.calls[2]["messages"]
        assert second[2] == {"role": "assistant", "content": "first"}
        assert third[2] == {"role": "assistant", "content": "second"}
        refinement = service._build_refinement_prompt("English")
        assert second[3]["content"][0] == {"type": "text", "text": refinement}
        assert DATA_URL in str(second[3]["content"][1])

    def test_each_reading_is_printed_as_it_arrives(self, monkeypatch, service, capsys):
        _answering(monkeypatch, service, _reply("first"), _reply("second"))
        service.process_image_ocr("page.png", "English", passes=2)
        out = capsys.readouterr().out
        assert "Pass 1/2 result" in out and "first" in out
        assert "Pass 2/2: Refining" in out

    def test_nothing_is_printed_while_running_beside_others(self, monkeypatch, service, capsys):
        """Several pictures at once share one progress bar, which printing would break up."""
        service._suppress_inline_print = True
        _answering(monkeypatch, service, _reply("first"), _reply("second"))
        service.process_image_ocr("page.png", "English", passes=2)
        assert capsys.readouterr().out == ""

    def test_a_refinement_that_comes_back_empty_is_asked_again(self, monkeypatch, service):
        model = _answering(monkeypatch, service, _reply("first"), _reply(""), _reply("better"))
        assert service.process_image_ocr("page.png", "English", passes=2) == "better"
        assert len(model.calls) == 3

    def test_a_refinement_that_never_answers_gives_up(self, monkeypatch, service):
        import src.services.base_service as base

        _answering(monkeypatch, service, _reply("first"),
                   *[_reply(None) for _ in range(base.MAX_RETRIES)])
        with pytest.raises(RuntimeError, match="refinement pass 2"):
            service.process_image_ocr("page.png", "English", passes=2)
