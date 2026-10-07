"""Tests for plugins/transcription/src/services/transcription_review_service.py: checking a transcription for misreadings.

The model is never really asked: ``_create_completion`` is replaced by a stand-in
that hands back the reply a test scripts, and records what it was sent.
"""

import json
import sys
from types import SimpleNamespace

import pytest

svc_mod = sys.modules["src.services.transcription_review_service"]
TranscriptionReviewService = svc_mod.TranscriptionReviewService


def _reply(content):
    """A reply shaped like the API's, carrying *content* as the model's text."""
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=10, total_tokens=15)
    message = SimpleNamespace(content=content)
    return SimpleNamespace(id="r", model="gpt-4o", usage=usage,
                           choices=[SimpleNamespace(message=message, finish_reason="stop")])


@pytest.fixture
def service(monkeypatch):
    """A review service on gpt-4o."""
    monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
    monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
    monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
    monkeypatch.setattr(svc_mod, "get_model_system_role", lambda m: "system")
    return TranscriptionReviewService("fake-key")


def _answering(monkeypatch, svc, content) -> list:
    """Make the model reply with *content*; return the list its requests are kept in."""
    sent = []

    def model(model, messages, max_tokens, **params):
        sent.append({"messages": messages, "max_tokens": max_tokens, **params})
        return _reply(content)

    monkeypatch.setattr(svc, "_create_completion", model)
    return sent


REPORT = {"meta": {"language": "English", "quality": "good"}, "errors": []}


class TestTheModel:
    def test_it_is_the_one_chosen_for_reviews(self, service):
        assert service._get_model() == "gpt-4o"


class TestThePrompts:
    def test_they_carry_the_language_and_the_text_under_review(self, service):
        system, user = service.build_prompts("English", "Teh quick brown fox")
        assert "English" in system and "English" in user
        assert "Teh quick brown fox" in user

    def test_notes_go_where_they_were_aimed(self, service):
        service.system_note = "standing instruction"
        service.user_note = "about this text"
        system, user = service.build_prompts("English")
        assert "standing instruction" in system and "standing instruction" not in user
        assert "about this text" in user and "about this text" not in system


class TestReviewing:
    def test_both_prompts_are_sent(self, monkeypatch, service):
        sent = _answering(monkeypatch, service, json.dumps(REPORT))
        service.review_transcription("some text", "English")

        system, user = service.build_prompts("English", "some text")
        assert sent[0]["messages"] == [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def test_the_review_settings_are_used(self, monkeypatch, service):
        sent = _answering(monkeypatch, service, json.dumps(REPORT))
        service.review_transcription("some text", "English")
        assert sent[0]["temperature"] == svc_mod.TRANSCRIPTION_REVIEW_TEMPERATURE
        assert sent[0]["top_p"] == svc_mod.TRANSCRIPTION_REVIEW_TOP_P
        assert sent[0]["max_tokens"] == svc_mod.TRANSCRIPTION_REVIEW_MAX_TOKENS

    def test_the_report_names_the_model_that_actually_wrote_it(self, monkeypatch, service):
        """Not whatever the model says it is: models are unreliable about their own names."""
        claimed = {"meta": {"model": "I am GPT-5", "language": "English"}, "errors": []}
        _answering(monkeypatch, service, json.dumps(claimed))
        report = json.loads(service.review_transcription("some text", "English"))
        assert report["meta"]["model"] == "gpt-4o"

    def test_a_report_missing_its_language_is_given_it(self, monkeypatch, service):
        _answering(monkeypatch, service, json.dumps({"meta": {}, "errors": []}))
        report = json.loads(service.review_transcription("some text", "English"))
        assert report["meta"]["language"] == "English"

    def test_a_language_the_model_gave_is_kept(self, monkeypatch, service):
        _answering(monkeypatch, service, json.dumps(REPORT | {"meta": {"language": "Middle English"}}))
        report = json.loads(service.review_transcription("some text", "English"))
        assert report["meta"]["language"] == "Middle English"

    def test_a_report_wrapped_in_a_code_block_is_unwrapped(self, monkeypatch, service):
        _answering(monkeypatch, service, "```json\n" + json.dumps(REPORT) + "\n```")
        report = json.loads(service.review_transcription("some text", "English"))
        assert report["errors"] == [] and report["meta"]["model"] == "gpt-4o"

    def test_text_that_is_not_a_report_is_shown_as_it_came(self, monkeypatch, service, caplog):
        """Something to read beats nothing, even when it is not the expected shape."""
        _answering(monkeypatch, service, "The transcription looks fine to me.")
        assert service.review_transcription("some text", "English") == "The transcription looks fine to me."
        assert any("non-JSON" in r.message for r in caplog.records)

    def test_a_report_without_a_meta_section_is_left_as_it_is(self, monkeypatch, service):
        _answering(monkeypatch, service, json.dumps({"errors": ["x"]}))
        assert json.loads(service.review_transcription("some text", "English")) == {"errors": ["x"]}
