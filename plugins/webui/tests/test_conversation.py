"""Tests for plugins/webui/src/conversation.py — Message/Conversation and ConversationStore."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.webui.src.conversation import (  # noqa: E402
    Attachment,
    Conversation,
    ConversationStore,
    Message,
    new_conversation_id,
)


class TestNewConversationId:
    def test_starts_with_c_underscore(self):
        assert new_conversation_id().startswith("c_")

    def test_ids_are_unique(self):
        ids = {new_conversation_id() for _ in range(50)}
        assert len(ids) == 50


class TestConversationRoundTrip:
    def test_to_dict_and_from_dict(self):
        conv = Conversation(
            id="c_1", title="Test", created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00", model="gpt-4o",
            messages=[Message(role="user", content="hi", timestamp="2026-01-01T00:00:00")],
        )
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.id == conv.id
        assert restored.messages[0].content == "hi"
        assert restored.compacted_summary is None

    def test_api_messages_shape(self):
        conv = Conversation(
            id="c_1", title="Test", created_at="t", updated_at="t", model="gpt-4o",
            messages=[
                Message(role="user", content="hi", timestamp="t"),
                Message(role="assistant", content="hello", timestamp="t", model="gpt-4o"),
            ],
        )
        assert conv.api_messages() == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]


class TestAttachment:
    def test_round_trips_through_dict(self):
        a = Attachment(filename="paper.pdf", char_count=1234)
        assert Attachment(**a.to_dict()) == a


class TestMessageAttachments:
    def test_message_without_attachment_has_empty_defaults(self):
        m = Message(role="user", content="Hi", timestamp="t")
        assert m.attachments == []
        assert m.api_content is None

    def test_message_from_dict_reconstructs_attachments(self):
        data = {
            "role": "user",
            "content": "What does this say?",
            "timestamp": "t",
            "attachments": [{"filename": "notes.txt", "char_count": 42}],
            "api_content": "[document text]\n\nWhat does this say?",
        }
        m = Message.from_dict(data)
        assert m.attachments == [Attachment(filename="notes.txt", char_count=42)]
        assert m.api_content == "[document text]\n\nWhat does this say?"

    def test_message_from_dict_tolerates_records_without_new_fields(self):
        # Conversations saved before this feature existed have no
        # "attachments"/"api_content" keys at all.
        data = {"role": "assistant", "content": "Hello!", "timestamp": "t"}
        m = Message.from_dict(data)
        assert m.attachments == []
        assert m.api_content is None


class TestConversationAttachmentRoundTrip:
    def _conv_with_attachment(self):
        conv = Conversation(
            id="c_test", title="Test", created_at="t", updated_at="t", model="gpt-4o",
        )
        conv.messages.append(Message(
            role="user", content="What does this document say?", timestamp="t",
            attachments=[Attachment(filename="report.docx", char_count=500)],
            api_content="[The professor attached a document...]\n\nWhat does this document say?",
        ))
        conv.messages.append(Message(
            role="assistant", content="It summarizes quarterly results.", timestamp="t",
            model="gpt-4o", prompt_tokens=100, completion_tokens=20, cost=0.01,
        ))
        return conv

    def test_to_dict_from_dict_round_trip(self):
        conv = self._conv_with_attachment()
        rebuilt = Conversation.from_dict(conv.to_dict())
        assert rebuilt.messages[0].attachments == conv.messages[0].attachments
        assert rebuilt.messages[0].api_content == conv.messages[0].api_content
        assert rebuilt.messages[1].attachments == []

    def test_api_messages_uses_api_content_when_present(self):
        conv = self._conv_with_attachment()
        api_msgs = conv.api_messages()
        assert api_msgs[0]["content"] == "[The professor attached a document...]\n\nWhat does this document say?"
        assert api_msgs[1]["content"] == "It summarizes quarterly results."

    def test_display_messages_never_substitutes_api_content(self):
        conv = self._conv_with_attachment()
        display_msgs = conv.display_messages()
        assert display_msgs[0]["content"] == "What does this document say?\n[Attached: report.docx]"
        assert display_msgs[1]["content"] == "It summarizes quarterly results."

    def test_display_messages_attachment_only_message_shows_just_the_hint(self):
        conv = Conversation(id="c_test2", title="Test", created_at="t", updated_at="t", model="gpt-4o")
        conv.messages.append(Message(
            role="user", content="", timestamp="t",
            attachments=[Attachment(filename="data.xlsx", char_count=800)],
            api_content="[document text]",
        ))
        assert conv.display_messages()[0]["content"] == "[Attached: data.xlsx]"

    def test_persisted_json_round_trips_via_store(self, tmp_path):
        store = ConversationStore("heller", base_dir=tmp_path)
        conv = self._conv_with_attachment()
        store.save(conv)
        reloaded = store.load(conv.id)
        assert reloaded is not None
        assert reloaded.messages[0].attachments[0].filename == "report.docx"
        assert reloaded.api_messages()[0]["content"] == conv.api_messages()[0]["content"]


class TestConversationStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ConversationStore("heller", base_dir=tmp_path)

    def test_create_and_load(self, store):
        conv = store.create(model="gpt-4o")
        loaded = store.load(conv.id)
        assert loaded is not None
        assert loaded.id == conv.id
        assert loaded.model == "gpt-4o"

    def test_load_missing_returns_none(self, store):
        assert store.load("c_does_not_exist") is None

    def test_list_conversations_sorted_newest_first(self, store):
        first = store.create(model="gpt-4o", title="First")
        first.updated_at = "2026-01-01T00:00:00"
        store.save(first)
        second = store.create(model="gpt-4o", title="Second")
        second.updated_at = "2026-02-01T00:00:00"
        store.save(second)

        listed = store.list_conversations()
        assert [c["title"] for c in listed] == ["Second", "First"]

    def test_delete_existing_returns_true(self, store):
        conv = store.create(model="gpt-4o")
        assert store.delete(conv.id) is True
        assert store.load(conv.id) is None

    def test_delete_missing_returns_false(self, store):
        assert store.delete("c_nope") is False

    def test_save_updates_timestamp(self, store):
        conv = store.create(model="gpt-4o")
        original_updated = conv.updated_at
        conv.messages.append(Message(role="user", content="hi", timestamp="t"))
        store.save(conv)
        reloaded = store.load(conv.id)
        assert reloaded is not None
        assert len(reloaded.messages) == 1
        # updated_at is refreshed on every save (may be equal if the test runs
        # within the same microsecond — the important guarantee is it's never stale).
        assert reloaded.updated_at >= original_updated

    def test_corrupted_file_skipped_in_listing(self, store, tmp_path):
        (tmp_path / "heller" / "c_broken.json").write_text("{not valid json")
        conv = store.create(model="gpt-4o", title="Good one")
        listed = store.list_conversations()
        assert [c["title"] for c in listed] == ["Good one"]

    def test_professors_are_isolated(self, tmp_path):
        heller_store = ConversationStore("heller", base_dir=tmp_path)
        smith_store = ConversationStore("smith", base_dir=tmp_path)
        heller_store.create(model="gpt-4o", title="Heller's")
        assert smith_store.list_conversations() == []
        assert len(heller_store.list_conversations()) == 1
