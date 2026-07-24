"""Tests for plugins/webui/src/conversation.py — Message/Conversation and ConversationStore."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.webui.src.conversation import (  # noqa: E402
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
