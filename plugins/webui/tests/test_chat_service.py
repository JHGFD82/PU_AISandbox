"""Tests for ChatService (plugins/webui/src/services/chat_service.py, registered as
src.services.chat_service). Mirrors the mocking conventions in
plugins/prompt/tests/test_prompt_service.py — no real API calls are made.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.chat_service import ChatService


class _Usage:
    def __init__(self, prompt_tokens=10, completion_tokens=20, total_tokens=30):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _FakeResponse:
    def __init__(self, content: object = "hello", model: str = "gpt-4o", usage=True):
        self.id = "resp-1"
        self.model = model
        self.usage = _Usage() if usage else None
        self.choices = [_Choice(content)]


class _Delta:
    def __init__(self, content=None):
        self.content = content


class _ChunkChoice:
    def __init__(self, content=None):
        self.delta = _Delta(content)


class _Chunk:
    """A fake OpenAI-style ChatCompletionChunk, as yielded by a streaming response.

    A real final chunk (carrying only usage) has an empty ``choices`` list —
    pass ``no_choices=True`` to model that.
    """

    def __init__(self, content=None, model=None, usage=None, no_choices=False):
        self.model = model
        self.usage = usage
        self.choices = [] if no_choices else [_ChunkChoice(content)]


@pytest.fixture
def svc():
    tracker = MagicMock()
    tracker.record_usage.return_value = MagicMock(total_cost=0.0042)
    return ChatService(api_key="fake-key", token_tracker=tracker)


@pytest.fixture(autouse=True)
def patch_model(monkeypatch):
    monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
    monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
    monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
    monkeypatch.setattr("src.services.chat_service.get_model_system_role", lambda m: "system")


class TestSendMessage:
    def test_returns_reply_content(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("Hi there")):
            result = svc.send_message([{"role": "user", "content": "hello"}])
        assert result["content"] == "Hi there"
        assert result["model"] == "gpt-4o"

    def test_records_usage_and_returns_cost(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("ok")):
            result = svc.send_message([{"role": "user", "content": "hello"}])
        svc.token_tracker.record_usage.assert_called_once()
        assert result["prompt_tokens"] == 10
        assert result["completion_tokens"] == 20
        assert result["cost"] == 0.0042

    def test_no_usage_in_response_leaves_billing_fields_none(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("ok", usage=False)):
            result = svc.send_message([{"role": "user", "content": "hello"}])
        svc.token_tracker.record_usage.assert_not_called()
        assert result["prompt_tokens"] is None
        assert result["cost"] is None

    def test_system_prompt_is_prepended(self, svc):
        captured = []

        def fake_create(model, messages, max_tokens, **kw):
            captured.extend(messages)
            return _FakeResponse("ok")

        with patch.object(svc, "_create_completion", side_effect=fake_create):
            svc.send_message([{"role": "user", "content": "hello"}], system_prompt="Be concise.")
        assert captured[0] == {"role": "system", "content": "Be concise."}
        assert captured[1] == {"role": "user", "content": "hello"}

    def test_no_system_prompt_sends_only_conversation(self, svc):
        captured = []

        def fake_create(model, messages, max_tokens, **kw):
            captured.extend(messages)
            return _FakeResponse("ok")

        with patch.object(svc, "_create_completion", side_effect=fake_create):
            svc.send_message([{"role": "user", "content": "hello"}])
        assert captured == [{"role": "user", "content": "hello"}]

    def test_full_conversation_history_forwarded(self, svc):
        captured = []

        def fake_create(model, messages, max_tokens, **kw):
            captured.extend(messages)
            return _FakeResponse("ok")

        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        with patch.object(svc, "_create_completion", side_effect=fake_create):
            svc.send_message(history)
        assert captured == history

    def test_uses_response_model_over_requested_when_different(self, svc):
        with patch.object(svc, "_create_completion", return_value=_FakeResponse("ok", model="gpt-4o-2024-08-06")):
            result = svc.send_message([{"role": "user", "content": "hi"}])
        assert result["model"] == "gpt-4o-2024-08-06"

    def test_retries_until_content_returned(self, svc, monkeypatch):
        """_create_completion returning empty content should trigger a retry, not a crash."""
        monkeypatch.setattr("src.services.base_service.BASE_RETRY_DELAY", 0)
        calls = {"n": 0}

        def flaky(model, messages, max_tokens, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResponse(content=None)
            return _FakeResponse("recovered")

        with patch.object(svc, "_create_completion", side_effect=flaky):
            result = svc.send_message([{"role": "user", "content": "hi"}])
        assert result["content"] == "recovered"
        assert calls["n"] == 2


class TestStreamMessage:
    def test_yields_delta_events_then_done(self, svc):
        chunks = [
            _Chunk(content="Hel", model="gpt-4o"),
            _Chunk(content="lo"),
            _Chunk(usage=_Usage(prompt_tokens=5, completion_tokens=7, total_tokens=12), no_choices=True),
        ]
        with patch.object(svc, "_create_completion_stream", return_value=iter(chunks)):
            events = list(svc.stream_message([{"role": "user", "content": "hi"}]))
        assert events[0] == {"type": "delta", "text": "Hel"}
        assert events[1] == {"type": "delta", "text": "lo"}
        assert events[-1]["type"] == "done"
        assert events[-1]["content"] == "Hello"
        assert events[-1]["model"] == "gpt-4o"
        assert events[-1]["prompt_tokens"] == 5
        assert events[-1]["completion_tokens"] == 7
        assert events[-1]["cost"] == 0.0042
        svc.token_tracker.record_usage.assert_called_once()

    def test_missing_usage_in_final_chunk_skips_billing(self, svc):
        chunks = [_Chunk(content="hi", model="gpt-4o")]
        with patch.object(svc, "_create_completion_stream", return_value=iter(chunks)):
            events = list(svc.stream_message([{"role": "user", "content": "hi"}]))
        svc.token_tracker.record_usage.assert_not_called()
        assert events[-1]["type"] == "done"
        assert events[-1]["cost"] is None
        assert events[-1]["prompt_tokens"] is None

    def test_role_only_chunk_produces_no_delta(self, svc):
        """A chunk whose delta has no text content (e.g. an opening role-only
        chunk some providers send) should not be yielded as a delta event."""
        chunks = [
            _Chunk(content=None, model="gpt-4o"),
            _Chunk(content="hi"),
            _Chunk(usage=_Usage(), no_choices=True),
        ]
        with patch.object(svc, "_create_completion_stream", return_value=iter(chunks)):
            events = list(svc.stream_message([{"role": "user", "content": "hi"}]))
        deltas = [e for e in events if e["type"] == "delta"]
        assert len(deltas) == 1
        assert deltas[0]["text"] == "hi"

    def test_system_prompt_is_prepended(self, svc):
        captured = []

        def fake_stream(model, messages, max_tokens, **kw):
            captured.extend(messages)
            return iter([_Chunk(content="ok"), _Chunk(usage=_Usage(), no_choices=True)])

        with patch.object(svc, "_create_completion_stream", side_effect=fake_stream):
            list(svc.stream_message([{"role": "user", "content": "hello"}], system_prompt="Be concise."))
        assert captured[0] == {"role": "system", "content": "Be concise."}
        assert captured[1] == {"role": "user", "content": "hello"}

    def test_full_conversation_history_forwarded(self, svc):
        captured = []

        def fake_stream(model, messages, max_tokens, **kw):
            captured.extend(messages)
            return iter([_Chunk(content="ok"), _Chunk(usage=_Usage(), no_choices=True)])

        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        with patch.object(svc, "_create_completion_stream", side_effect=fake_stream):
            list(svc.stream_message(history))
        assert captured == history

    def test_model_access_error_removes_model_and_raises_clean_message(self, svc, monkeypatch):
        """The 'invalid target name found in the query router' error PortKey
        returns when a model's license/access has been revoked should trigger
        the same model_catalog.json cleanup every other service gets via
        handle_api_errors() — see plugins/prompt/src/services/prompt_service.py's
        send_prompt() for the established pattern this mirrors."""
        mock_remove = MagicMock(return_value=True)
        monkeypatch.setattr("src.services.api_errors.remove_model_from_catalog", mock_remove)

        def boom(*a, **kw):
            raise Exception(
                "Error code: 400 - {'status': 'failure', 'message': "
                "'Invalid target name found in the query router: unknown-model'}"
            )

        with patch.object(svc, "_create_completion_stream", side_effect=boom):
            with pytest.raises(ValueError, match="not accessible"):
                list(svc.stream_message([{"role": "user", "content": "hi"}]))
        mock_remove.assert_called_once_with("gpt-4o")

    def test_error_partway_through_stream_still_triggers_cleanup(self, svc, monkeypatch):
        """A model-access error doesn't only happen at stream creation — it
        can surface after some chunks were already yielded. Cleanup must
        still run, and whatever text already streamed to the caller stays
        yielded (it can't be un-sent)."""
        mock_remove = MagicMock(return_value=True)
        monkeypatch.setattr("src.services.api_errors.remove_model_from_catalog", mock_remove)

        def flaky_stream():
            yield _Chunk(content="partial", model="gpt-4o")
            raise Exception("invalid target name found in the query router: unknown-model")

        with patch.object(svc, "_create_completion_stream", return_value=flaky_stream()):
            events = []
            with pytest.raises(ValueError, match="not accessible"):
                for event in svc.stream_message([{"role": "user", "content": "hi"}]):
                    events.append(event)
        assert events == [{"type": "delta", "text": "partial"}]
        mock_remove.assert_called_once_with("gpt-4o")

    def test_unrelated_errors_are_not_swallowed(self, svc):
        def boom(*a, **kw):
            raise RuntimeError("connection reset")

        with patch.object(svc, "_create_completion_stream", side_effect=boom):
            with pytest.raises(RuntimeError, match="connection reset"):
                list(svc.stream_message([{"role": "user", "content": "hi"}]))
