"""Tests for plugins/webui/src/app.py's chat routes: sending a message, the model menu, and what a reply streams back."""

from __future__ import annotations

import json
import re
import sys
from unittest.mock import MagicMock

import pytest

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
    _parse_sse,
    _rendered_chat,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestModelsEndpoint:
    def test_includes_accepts_sampling_params_flag(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "models_in_reading_order", lambda: ["gpt-4o", "o3-mini"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: m == "gpt-4o")
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: m != "o3-mini")
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr(app_module, "model_owner", lambda m: "Test")
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "gpt-4o")

        resp = unlocked_client.get("/api/models", params={"professor": "heller"})
        assert resp.status_code == 200
        by_name = {m["name"]: m for m in resp.json()["models"]}
        assert by_name["gpt-4o"]["accepts_sampling_params"] is True
        assert by_name["o3-mini"]["accepts_sampling_params"] is False

    def test_omit_sampling_params_also_hides_the_controls(self, unlocked_client, monkeypatch):
        # A model can be "not fully fixed" but still on a provider route
        # that refuses temperature/top-p — see model_accepts_sampling_params's
        # docstring. Either flag alone should hide the controls.
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "models_in_reading_order", lambda: ["some-model"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: False)
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: False)
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr(app_module, "model_owner", lambda m: "Test")
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "some-model")

        resp = unlocked_client.get("/api/models", params={"professor": "heller"})
        assert resp.json()["models"][0]["accepts_sampling_params"] is False


class TestChat:
    def test_chat_turn_streams_deltas_then_done_with_usage(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {"type": "delta", "text": "Hello "},
            {"type": "delta", "text": "back!"},
            {
                "type": "done", "content": "Hello back!", "model": "gpt-4o",
                "prompt_tokens": 5, "completion_tokens": 7, "cost": 0.001,
            },
        ])
        # No title-generation call configured -> falls back to the literal
        # opening words, same as generate_title() failing for a real reason.
        # See test_first_turn_uses_generated_title_when_available for the
        # AI-generated-title path.
        fake_sandbox.chat_service.generate_title.return_value = None
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi there", "model": "gpt-4o",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        deltas = [e for e in events if e["type"] == "delta"]
        assert [d["text"] for d in deltas] == ["Hello ", "back!"]

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        conv = done_events[0]["conversation"]
        assert conv["messages"][-2] == {
            "role": "user", "content": "Hi there", "timestamp": conv["messages"][-2]["timestamp"],
            "model": None, "prompt_tokens": None, "completion_tokens": None, "cost": None,
            "attachments": [], "api_content": None,
            "kind": "message", "job_id": None, "output_filename": None, "output_path": None,
            "progress_done": None, "progress_total": None, "page_number": None,
            "incomplete": False,
        }
        assert conv["messages"][-1]["content"] == "Hello back!"
        assert conv["messages"][-1]["cost"] == 0.001
        assert conv["title"] == "Hi there"

    def test_a_reply_from_an_endpoint_is_saved_under_the_endpoints_name_for_it(
            self, unlocked_client, monkeypatch):
        """The endpoint answers as qwen3.8:27b-mlx alone, which on its own reads
        as a second, different model, and one on the built-in service."""
        from src import settings

        monkeypatch.setattr(settings, "ENDPOINTS", {"my_mac": {"name": "My Mac"}})
        conv_id = unlocked_client.post("/api/conversations", json={
            "professor": "heller", "model": "my_mac:qwen3.8:27b-mlx"}).json()["id"]
        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {"type": "done", "content": "ok", "model": "qwen3.8:27b-mlx",
             "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0},
        ])
        fake_sandbox.chat_service.generate_title.return_value = None
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor",
                            lambda *a, **kw: fake_sandbox)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi"})
        (done,) = [e for e in _parse_sse(resp.text) if e["type"] == "done"]
        assert done["conversation"]["messages"][-1]["model"] == "my_mac:qwen3.8:27b-mlx"
        (listed,) = unlocked_client.get(
            "/api/conversations", params={"professor": "heller"}).json()["conversations"]
        assert listed["models"] == ["my_mac:qwen3.8:27b-mlx"]

    def test_sampling_overrides_persist_and_are_passed_to_sandbox(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {"type": "done", "content": "ok", "model": "gpt-4o",
             "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0001},
        ])
        fake_sandbox.chat_service.generate_title.return_value = None
        sandbox_cls = MagicMock(return_value=fake_sandbox)
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", sandbox_cls)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
            "temperature": 0.3, "top_p": 0.85, "max_tokens": 1500,
        })
        assert resp.status_code == 200
        sandbox_cls.assert_called_once_with(
            "heller", model="gpt-4o", temperature=0.3, top_p=0.85, max_tokens=1500,
        )

        # And it's saved on the conversation, not just used for this one call.
        conv = unlocked_client.get(
            "/api/conversations/" + conv_id, params={"professor": "heller"}
        ).json()
        assert conv["temperature"] == 0.3
        assert conv["top_p"] == 0.85
        assert conv["max_tokens"] == 1500

    def test_first_turn_uses_generated_title_when_available(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {
                "type": "done", "content": "Sure, here's a plan.", "model": "gpt-4o",
                "prompt_tokens": 5, "completion_tokens": 7, "cost": 0.001,
            },
        ])
        fake_sandbox.chat_service.generate_title.return_value = "Weekend Trip Planning"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id,
            "message": "Help me plan a weekend trip", "model": "gpt-4o",
        })
        events = _parse_sse(resp.text)
        conv = [e for e in events if e["type"] == "done"][0]["conversation"]
        assert conv["title"] == "Weekend Trip Planning"

    def test_later_turn_does_not_regenerate_title(self, unlocked_client, monkeypatch):
        """Only the first exchange should trigger title generation — once a
        conversation has a real title (from either the AI or the fallback),
        later turns must not silently overwrite it."""
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        # side_effect (not return_value) so each call gets its own fresh
        # iterator — a shared return_value would be exhausted by the first
        # /api/chat call, leaving the second call's stream_message() loop
        # with nothing left to iterate.
        fake_sandbox.chat_service.stream_message.side_effect = lambda *a, **kw: iter([
            {
                "type": "done", "content": "ok", "model": "gpt-4o",
                "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0001,
            },
        ])
        fake_sandbox.chat_service.generate_title.return_value = "First Title"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "first", "model": "gpt-4o",
        })
        fake_sandbox.chat_service.generate_title.return_value = "Should Not Be Used"
        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "second", "model": "gpt-4o",
        })
        events = _parse_sse(resp.text)
        conv = [e for e in events if e["type"] == "done"][0]["conversation"]
        assert conv["title"] == "First Title"

    def test_chat_on_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": "c_missing", "message": "Hi", "model": "gpt-4o",
        })
        assert resp.status_code == 404

    def test_chat_service_failure_emits_error_event(self, unlocked_client, monkeypatch):
        """Once streaming has begun the HTTP status can't change, so a failure
        partway through the model call surfaces as an in-band SSE error event
        instead of an HTTP error status (unlike the old one-shot /api/chat).

        An unexpected error's own text is deliberately *not* forwarded: it's
        written for whoever maintains the installation and can quote internal
        details back to the browser. The professor gets a plain message and a
        reference code instead.
        """
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        def _boom(*a, **kw):
            raise RuntimeError("upstream API error at /etc/secret/path")
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _boom)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        message = events[0]["message"]
        assert "upstream API error" not in message
        assert "/etc/secret/path" not in message
        # A reference code the professor can quote to whoever helps them.
        assert re.search(r"reference [0-9a-f]{8}", message)

    def test_chat_user_facing_error_is_shown_verbatim(self, unlocked_client, monkeypatch):
        """A CLIError is wording meant for the person using the tool (e.g. a
        rate limit or a model they can't access), so it reaches the browser
        unchanged rather than being replaced by the generic message."""
        from src.errors import CLIError

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        def _boom(*a, **kw):
            raise CLIError("Rate limit exceeded: please wait a moment and try again.")
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _boom)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
        })
        events = _parse_sse(resp.text)
        assert events[0]["type"] == "error"
        assert events[0]["message"] == "Rate limit exceeded: please wait a moment and try again."

    def test_user_message_is_saved_even_if_the_model_call_fails(self, unlocked_client, monkeypatch):
        """The user's message is persisted before the model is called at all,
        so a failed turn doesn't lose what was actually sent."""
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        def _boom(*a, **kw):
            raise RuntimeError("upstream API error")
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _boom)

        unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "Hi", "model": "gpt-4o",
        })

        got = unlocked_client.get(f"/api/conversations/{conv_id}", params={"professor": "heller"})
        assert got.json()["messages"][-1] == {
            "role": "user", "content": "Hi", "timestamp": got.json()["messages"][-1]["timestamp"],
            "model": None, "prompt_tokens": None, "completion_tokens": None, "cost": None,
            "attachments": [], "api_content": None,
            "kind": "message", "job_id": None, "output_filename": None, "output_path": None,
            "progress_done": None, "progress_total": None, "page_number": None,
            "incomplete": False,
        }

    def test_attachment_becomes_message_attachment_and_api_content(self, unlocked_client, monkeypatch):
        """An attachment sent with a chat turn shows up as a chip (attachments)
        on the saved message, while the actual document text only reaches the
        model via api_content — never the displayed content."""
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {
                "type": "done", "content": "Here's a summary.", "model": "gpt-4o",
                "prompt_tokens": 50, "completion_tokens": 10, "cost": 0.002,
            },
        ])
        fake_sandbox.chat_service.generate_title.return_value = "Report Summary"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id,
            "message": "Summarize this", "model": "gpt-4o",
            "attachment": {"filename": "report.pdf", "text": "Q3 revenue rose 12%.", "char_count": 20},
        })
        events = _parse_sse(resp.text)
        conv = [e for e in events if e["type"] == "done"][0]["conversation"]
        user_msg = conv["messages"][-2]
        assert user_msg["content"] == "Summarize this"
        assert user_msg["attachments"] == [{"filename": "report.pdf", "char_count": 20}]
        assert "Q3 revenue rose 12%." not in user_msg["content"]

        # The model itself must have received the document text — check what
        # was actually passed to stream_message().
        sent_messages = fake_sandbox.chat_service.stream_message.call_args[0][0]
        assert "Q3 revenue rose 12%." in sent_messages[-1]["content"]
        assert "Summarize this" in sent_messages[-1]["content"]

        # Title generation must NOT have received the full document text —
        # only display_messages()'s filename hint (see generate_title()'s
        # docstring on why: no reason to bill a long document just to name
        # the chat).
        # generate_title() is called after the assistant's reply has already
        # been appended, so the user's turn (with the attachment hint) is
        # the second-to-last message, not the last.
        title_messages = fake_sandbox.chat_service.generate_title.call_args[0][0]
        assert not any("Q3 revenue rose 12%." in m["content"] for m in title_messages)
        assert "report.pdf" in title_messages[-2]["content"]

    def test_attachment_only_message_with_blank_text_is_allowed(self, unlocked_client, monkeypatch):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {
                "type": "done", "content": "Sure, here's what it says.", "model": "gpt-4o",
                "prompt_tokens": 30, "completion_tokens": 8, "cost": 0.001,
            },
        ])
        fake_sandbox.chat_service.generate_title.return_value = None
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            lambda *a, **kw: fake_sandbox,
        )

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "", "model": "gpt-4o",
            "attachment": {"filename": "notes.txt", "text": "Some notes.", "char_count": 11},
        })
        events = _parse_sse(resp.text)
        conv = [e for e in events if e["type"] == "done"][0]["conversation"]
        assert conv["messages"][-2]["content"] == ""
        assert conv["messages"][-2]["attachments"][0]["filename"] == "notes.txt"
        # Falls back to the attachment's filename when there's no typed text
        # to use as a title source.
        assert conv["title"] == "notes.txt"


class TestChatBlockedWhileJobRunning:
    def test_chat_returns_409_when_conversation_has_active_job(self, unlocked_client):
        conversation = sys.modules["_pu_webui_conversation"]
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        store = conversation.ConversationStore("heller", base_dir=conversation.CONVERSATIONS_DIR)
        conv = store.load(conv_id)
        conv.active_job_id = "job_running"
        store.save(conv)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id, "message": "hi", "model": "gpt-4o",
        })
        assert resp.status_code == 409


class TestSystemPromptPerConversation:
    """Standing instructions belong to a conversation and apply to every turn.

    A model is handed the whole conversation afresh on each message and
    remembers nothing of its own, so instructions sent once would quietly stop
    applying from the second message onwards.
    """

    def _page(self):

        from fastapi.templating import Jinja2Templates

        here = WEBUI_SRC / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))

    def test_a_conversation_remembers_its_instructions(self, tmp_path):
        from plugins.webui.src.conversation import Conversation

        conv = Conversation(
            id="c1", title="t", created_at="2026-07-30T10:00:00",
            updated_at="2026-07-30T10:00:00", model="gpt-4o",
            system_prompt="Answer in French.",
        )
        assert Conversation.from_dict(conv.to_dict()).system_prompt == "Answer in French."

    def test_a_conversation_saved_before_this_existed_still_loads(self, tmp_path):
        from plugins.webui.src.conversation import Conversation

        old = {
            "id": "c1", "title": "t", "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00", "model": "gpt-4o", "messages": [],
        }
        assert Conversation.from_dict(old).system_prompt is None

    def test_two_conversations_keep_different_instructions(self, tmp_path, monkeypatch):
        from plugins.webui.src import conversation as conv_mod
        from plugins.webui.src.conversation import ConversationStore

        monkeypatch.setattr(conv_mod, "_conversations_dir", lambda: tmp_path)
        store = ConversationStore("heller")
        a = store.create(model="gpt-4o")
        b = store.create(model="gpt-4o")
        a.system_prompt = "Answer in French."
        store.save(a)
        assert store.load(a.id).system_prompt == "Answer in French."
        assert store.load(b.id).system_prompt is None, "instructions leaked between conversations"

    def _chat(self, client, monkeypatch, conv_id, message, **body):
        """Send one message and hand back what the model was asked."""
        fake_sandbox = MagicMock()
        fake_sandbox.chat_service.stream_message.return_value = iter([
            {"type": "done", "content": "ok", "model": "gpt-4o",
             "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0001},
        ])
        fake_sandbox.chat_service.generate_title.return_value = None
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor",
                            lambda *a, **kw: fake_sandbox)
        resp = client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id,
            "message": message, "model": "gpt-4o", **body,
        })
        assert resp.status_code == 200
        return fake_sandbox.chat_service.stream_message.call_args

    def test_the_prompt_is_sent_on_every_turn_not_just_the_first(self, unlocked_client, monkeypatch):
        """The whole point of keeping it: it has to be resent each time."""
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]

        first = self._chat(unlocked_client, monkeypatch, conv_id, "Bonjour",
                           system_prompt="Answer in French.")
        assert first.kwargs["system_prompt"] == "Answer in French."

        # Second turn sends no instructions of its own; the conversation's must
        # still reach the model, or they silently stop applying after one message.
        second = self._chat(unlocked_client, monkeypatch, conv_id, "Et maintenant ?")
        assert second.kwargs["system_prompt"] == "Answer in French."

    def test_blank_instructions_are_the_same_as_none(self, unlocked_client, monkeypatch):
        """Spaces sent on every turn would say nothing and cost tokens."""
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        call = self._chat(unlocked_client, monkeypatch, conv_id, "Hi", system_prompt="   ")
        assert call.kwargs["system_prompt"] is None

    def test_clearing_the_box_clears_the_instructions(self, unlocked_client, monkeypatch):
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        self._chat(unlocked_client, monkeypatch, conv_id, "Hi", system_prompt="Answer in French.")
        call = self._chat(unlocked_client, monkeypatch, conv_id, "Hi again", system_prompt=None)
        assert call.kwargs["system_prompt"] is None

    def test_the_box_is_offered_with_the_other_model_settings(self):
        page = self._page()
        assert 'id="sampling-system-prompt"' in page
        assert "Instructions for this conversation" in page

    def test_the_box_grows_and_can_be_dragged_taller(self):
        """Instructions run to a paragraph; the three settings above are numbers."""
        page = self._page()
        assert "resize: vertical" in page
        assert "scrollHeight" in page

    def test_the_page_says_it_applies_to_every_message(self):
        page = self._page()
        assert "before every message in this conversation" in page

    def test_what_is_typed_reaches_the_model_unchanged(self):
        """No quoting or escaping for a person to get wrong.

        The shared settings editor needs quotation marks because a settings file
        spells text that way. A conversation is stored as JSON, which quotes for
        itself, so instructions are stored exactly as written.
        """
        import json

        from plugins.webui.src.conversation import Conversation

        typed = 'Say "hello" first.\nThen answer in French.'
        conv = Conversation(
            id="c1", title="t", created_at="x", updated_at="x", model="gpt-4o",
            system_prompt=typed,
        )
        assert Conversation.from_dict(json.loads(json.dumps(conv.to_dict()))).system_prompt == typed


class TestTheInterfaceNamesTheValue:
    """A blank box stands for a real number, and says which."""

    def test_the_page_is_given_the_numbers_it_will_send(self, unlocked_client):
        from src.settings import PROMPT_TEMPERATURE, PROMPT_TOP_P

        page = unlocked_client.get("/").text
        assert "defaultSampling" in page
        assert str(PROMPT_TEMPERATURE) in page
        assert str(PROMPT_TOP_P) in page

    def test_no_box_claims_the_model_decides(self, unlocked_client):
        """It does not: a value is always sent, and the sandbox chooses it."""
        page = unlocked_client.get("/").text
        assert "Model default" not in page
        assert "model default" not in page
        assert "model's default" not in page


class TestTheModelMenuReadsInOrder:
    def test_the_menu_uses_the_shared_ordering(self, unlocked_client, monkeypatch):
        """Sorting at each display site is how two lists went wrong the same way."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(
            app_module, "models_in_reading_order",
            lambda: ["claude-sonnet-5", "gpt-4o", "Llama-3.3-70B-Instruct"],
        )
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: True)
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr(app_module, "model_owner", lambda m: "Test")
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "gpt-4o")
        names = [m["name"] for m in
                 unlocked_client.get("/api/models?professor=heller").json()["models"]]
        assert names == ["claude-sonnet-5", "gpt-4o", "Llama-3.3-70B-Instruct"]

    def test_the_page_shows_them_in_the_order_it_is_given(self):
        """Grouped, but not reordered: the server decides which model comes
        before which, and the page only decides which heading they sit under."""

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        assert "(state.models || []).forEach" in page
        # Group headings are sorted here; the models under them are not, so
        # there is one authority on which model comes before which.
        renderer = page.split("function renderModelList")[1].split("\nfunction ")[0]
        sorts = renderer.count(".sort(")
        assert sorts == 1, f"{sorts} sorts in the menu builder — the models are being reordered"
        assert "groups.get(owner).forEach" in renderer


class TestTheModelSaysWhatItCanDo:
    """Shown beside the settings, for reference while choosing."""

    def _page(self):

        return (WEBUI_SRC / "templates" / "chat.html").read_text()

    def test_the_menu_reports_how_long_an_answer_a_model_can_give(
        self, unlocked_client, monkeypatch
    ):
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "models_in_reading_order", lambda: ["o3-mini"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: False)
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: False)
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: 16000)
        monkeypatch.setattr(app_module, "model_owner", lambda m: "Test")
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "o3-mini")
        model = unlocked_client.get("/api/models?professor=heller").json()["models"][0]
        assert model["max_response_tokens"] == 16000
        assert model["supports_vision"] is False
        assert model["accepts_sampling_params"] is False

    def test_it_is_shown_wherever_the_settings_are(self):
        page = self._page()
        assert 'id="model-capabilities"' in page
        assert "What this model can do" in page

    def test_it_refreshes_when_the_model_changes(self):
        """It sits beside controls that appear and disappear with the model."""
        page = self._page()
        visibility = page.split("function applySamplingVisibility")[1].split("\n}")[0]
        assert "showModelCapabilities(model)" in visibility

    def test_it_says_nothing_a_field_below_already_says(self):
        """A field's presence answers whether a model takes that setting.

        Saying it again is one more thing to read and one more thing to keep
        true. What has no field — reading images — is what belongs here.
        """
        page = self._page()
        block = page.split("function showModelCapabilities")[1].split("\n}")[0]
        assert "images" in block
        assert "temperature" not in block.lower()
        assert "max_response_tokens" not in block

    def test_the_response_cap_is_shown_in_the_box_it_is_about(self):
        page = self._page()
        defaults = page.split("function showSamplingDefaults")[1].split("\n}")[0]
        assert "model.max_response_tokens" in defaults

    def test_the_boxes_are_refreshed_when_the_model_changes(self):
        """The cap differs by model, so it cannot be settled once at load."""
        page = self._page()
        visibility = page.split("function applySamplingVisibility")[1].split("\n}")[0]
        assert "showSamplingDefaults(model)" in visibility


class TestTheModelMenuIsGrouped:
    def _chat(self):
        return _rendered_chat()

    def test_the_menu_is_told_whose_each_model_is(self, unlocked_client, monkeypatch):
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "models_in_reading_order", lambda: ["gpt-4o"])
        monkeypatch.setattr(app_module, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(app_module, "model_accepts_sampling_params", lambda m: True)
        monkeypatch.setattr(app_module, "get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr(app_module, "model_owner", lambda m: "OpenAI")
        monkeypatch.setattr(app_module, "resolve_model", lambda **kw: "gpt-4o")
        model = unlocked_client.get("/api/models?professor=heller").json()["models"][0]
        assert model["owner"] == "OpenAI"

    def test_a_model_on_your_own_service_is_offered_here(self):
        """Typed as endpoint:model — the same shape the command line takes."""
        chat = self._chat()
        assert "della:alibaba/qwen35" in chat, "the box does not say the syntax is allowed"
        assert "rememberEndpointModel" in chat

    def test_those_are_remembered_rather_than_retyped(self):
        chat = self._chat()
        assert 'localStorage.setItem("endpoint-models"' in chat

    def test_a_damaged_note_of_them_does_not_empty_the_menu(self):
        """It is a convenience kept in the browser, so it must not be load-bearing."""
        chat = self._chat()
        remembered = chat.split("function rememberedEndpointModels")[1].split("\n}")[0]
        assert "catch" in remembered


class TestACutOffStreamEndToEnd:
    """The whole path, from a stream that stops early to what is saved."""

    def test_a_reply_that_stops_early_is_saved_as_incomplete_and_unpriced(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        """No usage chunk and no finish_reason — which is what a dropped
        connection looks like from this end."""
        create = unlocked_client.post("/api/conversations",
                                      json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        noted = {}

        class _FakeTracker:
            def record_usage(self, **kw):
                raise AssertionError("an unpriced call was recorded as if it had a price")

            def record_unreported_call(self, model, note=""):
                noted["model"] = model
                noted["note"] = note

        class _FakeChat:
            token_tracker = _FakeTracker()

            def stream_message(self, messages, system_prompt=None):
                # Exactly what the real service yields for a stream that
                # stopped before the provider said it had finished.
                yield {"type": "delta", "text": "Half a sen"}
                self.token_tracker.record_unreported_call(
                    "gpt-4o", note="the provider sent no usage and never said it had finished")
                yield {
                    "type": "done", "content": "Half a sen", "model": "gpt-4o",
                    "prompt_tokens": None, "completion_tokens": None, "cost": None,
                    "incomplete": True, "finish_reason": None,
                }

            def generate_title(self, messages):
                return None

        class _FakeSandbox:
            def __init__(self, *a, **kw):
                self.chat_service = _FakeChat()

        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", _FakeSandbox)

        resp = unlocked_client.post("/api/chat", json={
            "professor": "heller", "conversation_id": conv_id,
            "message": "Tell me something", "model": "gpt-4o",
        })
        assert resp.status_code == 200
        done = [json.loads(line[6:]) for line in resp.text.splitlines()
                if line.startswith("data: ") and '"done"' in line]
        saved = done[-1]["conversation"]["messages"][-1]

        assert saved["content"] == "Half a sen"
        assert saved["cost"] is None
        assert saved["incomplete"] is True
        # And it was counted as spending nobody could measure.
        assert noted["model"] == "gpt-4o"
        assert "no usage" in noted["note"]
