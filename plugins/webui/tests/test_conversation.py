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
        # A real-shaped id ("c_" + 16 hex characters), because one of the
        # tests below saves this through ConversationStore, which rejects
        # anything that isn't shaped like an id it could have issued itself.
        conv = Conversation(
            id="c_0011223344556677", title="Test", created_at="t", updated_at="t", model="gpt-4o",
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
    """Conversation.active_job_id + Message.kind/job_id/output_*."""

    def test_message_defaults_to_kind_message_with_no_job_fields(self):
        m = Message(role="user", content="hi", timestamp="t")
        assert m.kind == "message"
        assert m.job_id is None
        assert m.output_filename is None
        assert m.output_path is None
        assert m.progress_done is None
        assert m.progress_total is None
        assert m.page_number is None

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

    def test_job_page_message_round_trips_with_page_number(self):
        # job_page messages carry a page's actual translated text (unlike
        # job_progress, which only ever carries counts) — see the "no
        # messages from each page" bug this was built to fix.
        conv = Conversation(id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o")
        conv.messages.append(Message(
            role="assistant", content="Translated page text.", timestamp="t",
            kind="job_page", job_id="job_abc123", page_number=3,
        ))
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.messages[0].kind == "job_page"
        assert restored.messages[0].content == "Translated page text."
        assert restored.messages[0].page_number == 3

    def test_page_number_defaults_none_for_old_records(self):
        data = {
            "role": "assistant", "content": "Translated page text.", "timestamp": "t",
            "kind": "job_page", "job_id": "job_abc123",
        }
        m = Message.from_dict(data)
        assert m.page_number is None

    def test_job_notice_message_round_trips(self):
        # job_notice: a one-off aside about how the job will behave (e.g.
        # no per-page preview above 1 worker) — no extra fields of its own,
        # unlike job_page/job_progress/job_result, just role/content/kind/job_id.
        conv = Conversation(id="c_1", title="T", created_at="t", updated_at="t", model="gpt-4o")
        conv.messages.append(Message(
            role="assistant",
            content="Preview of the translation is turned off while running with more than one worker.",
            timestamp="t", kind="job_notice", job_id="job_abc123",
        ))
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.messages[0].kind == "job_notice"
        assert restored.messages[0].job_id == "job_abc123"

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
            role="assistant", content="Translated page 1.", timestamp="t",
            kind="job_page", job_id="job_1", page_number=1,
        ))
        conv.messages.append(Message(
            role="assistant", content="Done.", timestamp="t",
            kind="job_result", job_id="job_1", output_filename="out.docx", output_path="/tmp/out.docx",
        ))
        conv.messages.append(Message(
            role="assistant", content="No preview with multiple workers.", timestamp="t",
            kind="job_notice", job_id="job_1",
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
        store.create(model="gpt-4o", title="Good one")
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


class TestConversationIdValidation:
    """The store must never turn a browser-supplied id into a path outside itself.

    A conversation id arrives from the browser and is used as a filename.
    Without a shape check, an id containing '../' walks out of the
    professor's own folder — which made it possible to read, overwrite, or
    delete any .json file the server could reach.
    """

    def _store(self, tmp_path):
        return ConversationStore("heller", base_dir=tmp_path)

    @pytest.mark.parametrize("bad_id", [
        "../../victim",
        "../victim",
        "c_../../victim",
        "c_0011223344556677/../../victim",
        "/etc/passwd",
        "c_SHORT",
        "c_00112233445566778899",      # too long
        "c_00112233445566ZZ",          # not hexadecimal
        "",
    ])
    def test_malformed_ids_cannot_escape_the_store(self, tmp_path, bad_id):
        store = self._store(tmp_path / "convs")
        victim = tmp_path / "victim.json"
        victim.write_text('{"id": "victim", "title": "SECRET", "created_at": "t", '
                          '"updated_at": "t", "model": "gpt-4o", "messages": []}')

        # Lookups report "not found" rather than raising, so routes keep
        # returning their usual 404 instead of a server error.
        assert store.load(bad_id) is None
        assert store.delete(bad_id) is False
        assert victim.exists(), "a file outside the store was reachable"

    def test_save_refuses_a_malformed_id_outright(self, tmp_path):
        """Unlike load/delete, saving with a bad id is a programming error, so it raises."""
        store = self._store(tmp_path / "convs")
        conv = Conversation(
            id="../escape", title="T", created_at="t", updated_at="t", model="gpt-4o",
        )
        with pytest.raises(ValueError, match="Malformed conversation id"):
            store.save(conv)

    def test_ordinary_conversations_still_work(self, tmp_path):
        """The check must not get in the way of the ids the store issues itself."""
        store = self._store(tmp_path / "convs")
        conv = store.create(model="gpt-4o")
        conv.title = "Renamed"
        store.save(conv)
        assert store.load(conv.id).title == "Renamed"
        assert [c["id"] for c in store.list_conversations()] == [conv.id]
        assert store.delete(conv.id) is True
        assert store.load(conv.id) is None


class TestEachConversationHasAFolder:
    """A conversation, what it was given, what it produced, and how — in one place."""

    def _store(self, tmp_path):
        from plugins.webui.src.conversation import ConversationStore

        return ConversationStore("jh43", base_dir=tmp_path)

    def test_a_conversation_is_saved_in_a_folder_of_its_own(self, tmp_path):
        store = self._store(tmp_path)
        conv = store.create(model="gpt-4o")
        assert (tmp_path / "jh43" / conv.id / "conversation.json").exists()

    def test_the_folder_is_named_after_the_conversation(self, tmp_path):
        store = self._store(tmp_path)
        conv = store.create(model="gpt-4o")
        assert store.folder(conv.id).name == conv.id
        assert conv.id.startswith("c_")

    def test_a_malformed_id_cannot_reach_outside_the_persons_own_folder(self, tmp_path):
        """The id comes from the browser and becomes part of a path."""
        import pytest

        store = self._store(tmp_path)
        for bad in ["../../etc", "c_../../x", "c_zzz", "", "c_" + "f" * 15]:
            with pytest.raises(ValueError):
                store.folder(bad)
            with pytest.raises(ValueError):
                store.attachments_dir(bad)
            with pytest.raises(ValueError):
                store.outputs_dir(bad)

    def test_the_settings_that_produced_it_are_written_beside_it(self, tmp_path):
        store = self._store(tmp_path)
        conv = store.create(model="gpt-4o")
        conv.temperature = 0.2
        conv.system_prompt = "Answer in French."
        store.save(conv)
        note = (store.folder(conv.id) / "settings.txt").read_text()
        assert "gpt-4o" in note
        assert "0.2" in note
        assert "Answer in French." in note

    def test_the_note_records_the_value_actually_used(self, tmp_path):
        """Not the word "default", which names no value — and is not even true.

        A conversation that sets nothing is not sent without these: the sandbox
        fills in its own. Someone reading this months later, or citing it, needs
        the number the answer was produced with.
        """
        from src.settings import PROMPT_TEMPERATURE, PROMPT_TOP_P

        store = self._store(tmp_path)
        conv = store.create(model="gpt-4o")
        note = (store.folder(conv.id) / "settings.txt").read_text()
        assert str(PROMPT_TEMPERATURE) in note
        assert str(PROMPT_TOP_P) in note
        assert "default" not in note.lower(), "the note describes a value instead of giving it"

    def test_a_chosen_value_and_a_filled_in_one_read_the_same(self, tmp_path):
        """An archive states what the settings were, not who settled on them."""
        from src.settings import PROMPT_TOP_P

        store = self._store(tmp_path)
        conv = store.create(model="gpt-4o")
        conv.temperature = 0.2
        store.save(conv)
        note = (store.folder(conv.id) / "settings.txt").read_text()
        lines = {ln.split(":")[0]: ln for ln in note.splitlines() if ":" in ln}
        assert lines["Temperature"].split(":")[1].strip() == "0.2"
        assert lines["Top-p"].split(":")[1].strip() == str(PROMPT_TOP_P)

    def test_a_model_missing_from_the_catalogue_still_gets_a_note(self, tmp_path):
        """A conversation outlives the model it used."""
        store = self._store(tmp_path)
        conv = store.create(model="a-model-that-was-retired")
        note = (store.folder(conv.id) / "settings.txt").read_text()
        assert "Max response tokens:" in note
        assert "(none)" in note

    def test_deleting_takes_the_whole_folder(self, tmp_path):
        store = self._store(tmp_path)
        conv = store.create(model="gpt-4o")
        store.attachments_dir(conv.id).mkdir(parents=True)
        (store.attachments_dir(conv.id) / "source.pdf").write_bytes(b"x")
        assert store.delete(conv.id) is True
        assert not store.folder(conv.id).exists()


class TestMovingOlderConversationsIntoFolders:
    """Conversations saved as loose files must survive the change untouched."""

    def _loose(self, tmp_path, conversation_id, title="Older work"):
        import json

        folder = tmp_path / "jh43"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{conversation_id}.json").write_text(json.dumps({
            "id": conversation_id, "title": title,
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-02T00:00:00",
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello", "timestamp": "2026-01-01T00:00:00"}],
        }))

    def _store(self, tmp_path):
        from plugins.webui.src.conversation import ConversationStore

        return ConversationStore("jh43", base_dir=tmp_path)

    def test_an_older_conversation_is_moved_and_still_reads(self, tmp_path):
        self._loose(tmp_path, "c_" + "a" * 16)
        store = self._store(tmp_path)
        conv = store.load("c_" + "a" * 16)
        assert conv is not None
        assert conv.title == "Older work"
        assert conv.messages[0].content == "Hello"

    def test_nothing_is_left_behind_to_be_read_twice(self, tmp_path):
        cid = "c_" + "b" * 16
        self._loose(tmp_path, cid)
        self._store(tmp_path)
        assert not (tmp_path / "jh43" / f"{cid}.json").exists()
        assert (tmp_path / "jh43" / cid / "conversation.json").exists()

    def test_it_still_appears_in_the_list(self, tmp_path):
        cid = "c_" + "c" * 16
        self._loose(tmp_path, cid, title="Findable")
        store = self._store(tmp_path)
        assert [s["title"] for s in store.list_conversations()] == ["Findable"]

    def test_a_conversation_already_moved_is_left_alone(self, tmp_path):
        """Running twice must not overwrite the newer copy with a stale one."""
        import json

        cid = "c_" + "d" * 16
        self._loose(tmp_path, cid, title="Stale loose copy")
        folder = tmp_path / "jh43" / cid
        folder.mkdir(parents=True)
        (folder / "conversation.json").write_text(json.dumps({
            "id": cid, "title": "The one being used", "created_at": "x", "updated_at": "y",
            "model": "gpt-4o", "messages": [],
        }))
        store = self._store(tmp_path)
        assert store.load(cid).title == "The one being used"

    def test_a_file_that_is_not_a_conversation_is_not_touched(self, tmp_path):
        folder = tmp_path / "jh43"
        folder.mkdir(parents=True)
        (folder / "notes.json").write_text("{}")
        (folder / "c_nonsense.json").write_text("{}")
        self._store(tmp_path)
        assert (folder / "notes.json").exists()
        assert (folder / "c_nonsense.json").exists()


class TestWhereAPersonsConversationsGo:
    """A person with a shared folder keeps their conversations in it, beside
    the record of what their work cost."""

    @pytest.fixture
    def settings(self, tmp_path, monkeypatch):
        from src import settings_store

        monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.toml")
        settings_store.add_professor("smith", "Prof. Smith", "sk-test")
        return settings_store

    def _where(self, professor):
        from plugins.webui.src.conversation import conversations_dir_for

        return conversations_dir_for(professor)

    def test_without_a_shared_folder_they_stay_here_under_their_netid(
        self, settings, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("src.paths.data_root", lambda: tmp_path / "data")
        assert self._where("smith") == tmp_path / "data" / "conversations" / "smith"

    def test_a_shared_folder_holds_them_instead(self, settings, tmp_path):
        settings.set_professor_usage_source(
            "smith", str(tmp_path / "dropbox"), mode="shared-write")
        assert self._where("smith") == tmp_path / "dropbox" / "conversations"

    def test_nothing_in_a_shared_folder_is_filed_under_a_netid(self, settings, tmp_path):
        """The folder is already theirs; saying so again inside it is one more
        thing able to disagree with the settings that named it."""
        settings.set_professor_usage_source(
            "smith", str(tmp_path / "dropbox"), mode="shared-write")
        assert "smith" not in self._where("smith").relative_to(tmp_path / "dropbox").parts

    def test_a_folder_only_being_watched_is_never_written_to(
        self, settings, tmp_path, monkeypatch
    ):
        """Read-only means read-only. Conversations are work, and writing them
        into somebody's folder is doing work in it."""
        monkeypatch.setattr("src.paths.data_root", lambda: tmp_path / "data")
        settings.set_professor_usage_source(
            "smith", str(tmp_path / "dropbox"), mode="read-only")
        assert self._where("smith") == tmp_path / "data" / "conversations" / "smith"

    def test_it_follows_the_same_setting_the_spending_does(self, settings, tmp_path):
        """One folder per person, holding both — that is the whole point of
        setting it in one place."""
        from src.tracking.token_tracker import _shared_call_dir

        settings.set_professor_usage_source(
            "smith", str(tmp_path / "dropbox"), mode="shared-write")
        source = settings.get_shared_write_source("smith")
        assert source is not None
        assert _shared_call_dir(source, "2026-08").parent.parent == \
               self._where("smith").parent

    def test_two_people_with_shared_folders_do_not_share_one(self, settings, tmp_path):
        settings.add_professor("jones", "Prof. Jones", "sk-test")
        settings.set_professor_usage_source(
            "smith", str(tmp_path / "smith-dropbox"), mode="shared-write")
        settings.set_professor_usage_source(
            "jones", str(tmp_path / "jones-dropbox"), mode="shared-write")
        assert self._where("smith") != self._where("jones")

    def test_the_store_puts_its_conversations_where_that_says(self, settings, tmp_path):
        settings.set_professor_usage_source(
            "smith", str(tmp_path / "dropbox"), mode="shared-write")
        store = ConversationStore("smith")
        conv = store.create(title="In the shared folder", model="gpt-4o")
        assert (tmp_path / "dropbox" / "conversations" / conv.id).is_dir()

    def test_a_test_that_named_a_folder_still_gets_one_each(self, tmp_path):
        """The base_dir override holds everybody, so it keeps the netID layer."""
        smith = ConversationStore("smith", base_dir=tmp_path)
        jones = ConversationStore("jones", base_dir=tmp_path)
        assert (tmp_path / "smith").is_dir() and (tmp_path / "jones").is_dir()
        assert smith._dir != jones._dir
