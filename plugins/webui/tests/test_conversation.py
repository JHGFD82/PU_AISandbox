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
        assert restored.temperature is None
        assert restored.top_p is None
        assert restored.max_tokens is None

    def test_sampling_overrides_round_trip(self):
        conv = Conversation(
            id="c_1", title="Test", created_at="t", updated_at="t", model="gpt-4o",
            temperature=0.4, top_p=0.9, max_tokens=2048,
        )
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.temperature == 0.4
        assert restored.top_p == 0.9
        assert restored.max_tokens == 2048

    def test_sampling_overrides_default_none_for_old_records(self):
        # A conversation file saved before these fields existed has none of
        # these keys at all — from_dict must not raise on their absence.
        data = {
            "id": "c_1", "title": "Test", "created_at": "t", "updated_at": "t",
            "model": "gpt-4o", "messages": [],
        }
        restored = Conversation.from_dict(data)
        assert restored.temperature is None
        assert restored.top_p is None
        assert restored.max_tokens is None

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


class TestJobFields:
    """Conversation.active_job_id + Message.kind/job_id/output_* —
    docs/webui-plugin-plan.md section 10."""

    def test_message_defaults_to_kind_message_with_no_job_fields(self):
        m = Message(role="user", content="hi", timestamp="t")
        assert m.kind == "message"
        assert m.job_id is None
        assert m.output_filename is None
        assert m.output_path is None
        assert m.progress_done is None
        assert m.progress_total is None

    def test_message_from_dict_tolerates_records_without_job_fields(self):
        # Conversations saved before this feature existed have none of
        # these keys at all.
        data = {"role": "assistant", "content": "Hello!", "timestamp": "t"}
        m = Message.from_dict(data)
        assert m.kind == "message"
        assert m.job_id is None

    def test_conversation_defaults_to_no_active_job(self):
        conv = Conversation(id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o")
        assert conv.active_job_id is None

    def test_active_job_id_round_trips(self):
        conv = Conversation(
            id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o",
            active_job_id="job_abc123",
        )
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.active_job_id == "job_abc123"

    def test_job_progress_message_round_trips(self):
        conv = Conversation(id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o")
        conv.messages.append(Message(
            role="assistant", content="Page 3 of 12 translated...", timestamp="t",
            kind="job_progress", job_id="job_abc123",
        ))
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.messages[0].kind == "job_progress"
        assert restored.messages[0].job_id == "job_abc123"

    def test_job_progress_numeric_fields_round_trip(self):
        # The webui's progress bar reads these directly to compute a
        # percentage width — must survive a save/load cycle intact.
        conv = Conversation(id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o")
        conv.messages.append(Message(
            role="assistant", content="Translating... 3 of 12 done.", timestamp="t",
            kind="job_progress", job_id="job_abc123", progress_done=3, progress_total=12,
        ))
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.messages[0].progress_done == 3
        assert restored.messages[0].progress_total == 12

    def test_job_progress_numeric_fields_default_none_for_old_records(self):
        data = {
            "role": "assistant", "content": "Translating... 3 of 12 done.", "timestamp": "t",
            "kind": "job_progress", "job_id": "job_abc123",
        }
        m = Message.from_dict(data)
        assert m.progress_done is None
        assert m.progress_total is None

    def test_job_result_message_carries_output_fields(self):
        conv = Conversation(id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o")
        conv.messages.append(Message(
            role="assistant", content="Translated report.docx to English.", timestamp="t",
            kind="job_result", job_id="job_abc123",
            output_filename="report_Japanese_to_English.docx",
            output_path="/data/conversations/heller/_job_outputs/job_abc123/out.docx",
        ))
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.messages[0].output_filename == "report_Japanese_to_English.docx"
        assert restored.messages[0].output_path.endswith("out.docx")

    def test_api_messages_excludes_job_messages(self):
        conv = Conversation(id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o")
        conv.messages.append(Message(role="user", content="translate this", timestamp="t"))
        conv.messages.append(Message(
            role="assistant", content="Page 1 of 2...", timestamp="t",
            kind="job_progress", job_id="job_1",
        ))
        conv.messages.append(Message(
            role="assistant", content="Done.", timestamp="t",
            kind="job_result", job_id="job_1", output_filename="out.docx", output_path="/tmp/out.docx",
        ))
        assert conv.api_messages() == [{"role": "user", "content": "translate this"}]

    def test_display_messages_excludes_job_messages(self):
        conv = Conversation(id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o")
        conv.messages.append(Message(role="user", content="translate this", timestamp="t"))
        conv.messages.append(Message(
            role="assistant", content="Page 1 of 2...", timestamp="t",
            kind="job_progress", job_id="job_1",
        ))
        assert conv.display_messages() == [{"role": "user", "content": "translate this"}]


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

    def test_save_never_leaves_a_stray_temp_file_behind(self, store, tmp_path):
        conv = store.create(model="gpt-4o")
        conv.messages.append(Message(role="user", content="hi", timestamp="t"))
        store.save(conv)
        leftovers = list((tmp_path / "heller").glob("*.tmp"))
        assert leftovers == []

    def test_save_is_atomic_no_reader_ever_sees_a_partial_file(self, store, tmp_path):
        # Regression test for a real race hit while building the webui's
        # background job runner (jobs.py): a background thread saving a
        # conversation (each save rewrites the whole file) at the same
        # moment a request thread is reading it used to be able to observe
        # a truncated, unparseable file, because the old implementation
        # wrote directly with write_text() (truncate-then-write, not
        # atomic). Simulates that race directly by saving many times
        # from one thread while reading many times from another,
        # asserting every read that finds the file at all parses cleanly.
        import threading as _threading

        conv = store.create(model="gpt-4o")
        errors: list[Exception] = []
        stop = _threading.Event()

        def writer():
            for i in range(200):
                conv.messages.append(Message(role="user", content=f"msg {i}", timestamp="t"))
                store.save(conv)
            stop.set()

        def reader():
            path = tmp_path / "heller" / f"{conv.id}.json"
            while not stop.is_set():
                if path.exists():
                    try:
                        import json
                        json.loads(path.read_text())
                    except (json.JSONDecodeError, OSError) as e:
                        errors.append(e)

        w = _threading.Thread(target=writer)
        r = _threading.Thread(target=reader)
        w.start()
        r.start()
        w.join()
        r.join(timeout=1)

        assert errors == []
