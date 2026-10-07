"""Tests for plugins/webui/src/app.py's conversation routes: listing, renaming, exporting, and documents supplied to one."""

from __future__ import annotations

import sys

import pytest

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestConversations:
    def test_new_conversation_then_list(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        assert create.status_code == 200
        conv_id = create.json()["id"]

        listed = unlocked_client.get("/api/conversations", params={"professor": "heller"})
        assert conv_id in {c["id"] for c in listed.json()["conversations"]}

    def test_get_single_conversation(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        got = unlocked_client.get(f"/api/conversations/{conv_id}", params={"professor": "heller"})
        assert got.status_code == 200
        assert got.json()["id"] == conv_id

    def test_get_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.get("/api/conversations/c_missing", params={"professor": "heller"})
        assert resp.status_code == 404

    def test_delete_conversation(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.delete(f"/api/conversations/{conv_id}", params={"professor": "heller"})
        assert resp.status_code == 200
        assert unlocked_client.get(f"/api/conversations/{conv_id}", params={"professor": "heller"}).status_code == 404

    def test_a_model_named_with_its_provider_is_the_same_model(self, unlocked_client):
        """A model can be named anthropic/claude-fable-5 — that is how one is
        added, and the model box goes on accepting it — while the catalog
        files it as claude-fable-5 and every reply comes back under that. Two
        entries for one model in the filter is the visible half of that."""
        created = unlocked_client.post(
            "/api/conversations",
            json={"professor": "heller", "model": "openai/gpt-4o"}).json()
        assert created["model"] == "gpt-4o"
        (listed,) = unlocked_client.get(
            "/api/conversations", params={"professor": "heller"}).json()["conversations"]
        assert listed["models"] == ["gpt-4o"]

    def test_a_name_the_catalog_does_not_hold_is_left_as_it_was_written(
            self, unlocked_client):
        """Somebody's own spelling, and not ours to rewrite."""
        created = unlocked_client.post(
            "/api/conversations",
            json={"professor": "heller", "model": "someone/their-model"}).json()
        assert created["model"] == "someone/their-model"

    def test_a_model_on_your_own_endpoint_keeps_its_whole_name(self, unlocked_client):
        """There the endpoint and the model together are the name."""
        created = unlocked_client.post(
            "/api/conversations",
            json={"professor": "heller", "model": "della:alibaba/qwen35"}).json()
        assert created["model"] == "della:alibaba/qwen35"

    def test_the_name_is_settled_the_same_way_wherever_it_arrives(self, settings_env):
        """A conversation's model is also set by sending a message with a
        different one chosen, which is a whole chat turn away from here."""
        settle = sys.modules["_pu_webui_app"]._as_the_catalog_knows_it
        assert settle("openai/gpt-4o") == "gpt-4o"
        assert settle("gpt-4o") == "gpt-4o"
        assert settle("someone/their-model") == "someone/their-model"
        assert settle("della:alibaba/qwen35") == "della:alibaba/qwen35"

    def test_the_list_says_where_each_model_ran_and_whose_it_is(
            self, unlocked_client, monkeypatch):
        """Worked out here: the name a reply came back under is often not in
        the catalog, and only the server can read whose it is."""
        from src import settings

        monkeypatch.setattr(settings, "ENDPOINTS", {"my_mac": {"name": "My Mac"}})
        unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        unlocked_client.post("/api/conversations",
                             json={"professor": "heller", "model": "my_mac:qwen3.8:27b-mlx"})
        groups = unlocked_client.get(
            "/api/conversations", params={"professor": "heller"}).json()["model_groups"]
        assert groups["gpt-4o"] == {"service": None, "company": "OpenAI"}
        assert groups["my_mac:qwen3.8:27b-mlx"] == {"service": "My Mac", "company": "Qwen"}

    def test_the_list_carries_what_the_filter_narrows_by(self, unlocked_client):
        """The filter works in the browser, over the list it already has, so
        each entry has to arrive with everything it can be filtered on."""
        unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        (listed,) = unlocked_client.get(
            "/api/conversations", params={"professor": "heller"}).json()["conversations"]
        assert listed["models"] == ["gpt-4o"]
        assert listed["cost"] == 0
        assert listed["tokens"] == 0
        assert listed["has_job"] is False

    def test_conversations_isolated_per_professor(self, unlocked_client):
        unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        smith_list = unlocked_client.get("/api/conversations", params={"professor": "smith"})
        assert smith_list.json()["conversations"] == []


class TestRenameConversation:
    def test_renames_conversation(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.patch(f"/api/conversations/{conv_id}", json={
            "professor": "heller", "title": "My Custom Title",
        })
        assert resp.status_code == 200
        assert resp.json()["title"] == "My Custom Title"

        got = unlocked_client.get(f"/api/conversations/{conv_id}", params={"professor": "heller"})
        assert got.json()["title"] == "My Custom Title"

    def test_rename_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.patch("/api/conversations/c_missing", json={
            "professor": "heller", "title": "Anything",
        })
        assert resp.status_code == 404

    def test_blank_title_rejected(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.patch(f"/api/conversations/{conv_id}", json={
            "professor": "heller", "title": "   ",
        })
        assert resp.status_code == 400

    def test_title_is_trimmed_and_truncated(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.patch(f"/api/conversations/{conv_id}", json={
            "professor": "heller", "title": "  " + ("x" * 100) + "  ",
        })
        assert resp.json()["title"] == "x" * 80

    def test_rename_requires_unlock(self, client):
        resp = client.patch("/api/conversations/c_anything", json={"professor": "heller", "title": "x"})
        assert resp.status_code == 401


class TestUploadAttachment:
    def test_uploads_and_extracts_text(self, unlocked_client):
        resp = unlocked_client.post(
            "/api/attachments",
            data={"professor": "heller"},
            files={"file": ("notes.txt", b"Hello from an uploaded file.", "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "notes.txt"
        assert "Hello from an uploaded file." in body["text"]
        assert body["char_count"] == len(body["text"])

    def test_unsupported_file_type_returns_400(self, unlocked_client):
        resp = unlocked_client.post(
            "/api/attachments",
            data={"professor": "heller"},
            files={"file": ("virus.exe", b"whatever", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "supported file type" in resp.json()["detail"]

    def test_oversized_attachment_returns_400(self, unlocked_client, monkeypatch):
        attachments = sys.modules["_pu_webui_attachments"]
        monkeypatch.setattr(attachments, "MAX_ATTACHMENT_CHARS", 5)
        resp = unlocked_client.post(
            "/api/attachments",
            data={"professor": "heller"},
            files={"file": ("notes.txt", b"This is definitely longer than five characters.", "text/plain")},
        )
        assert resp.status_code == 400
        assert "too long to attach" in resp.json()["detail"]

    def test_requires_unlock(self, client):
        resp = client.post(
            "/api/attachments",
            data={"professor": "heller"},
            files={"file": ("notes.txt", b"hi", "text/plain")},
        )
        assert resp.status_code == 401

    def test_unknown_professor_rejected(self, unlocked_client):
        resp = unlocked_client.post(
            "/api/attachments",
            data={"professor": "nobody"},
            files={"file": ("notes.txt", b"hi", "text/plain")},
        )
        assert resp.status_code == 400


class TestExportConversation:
    @pytest.fixture
    def conv_id(self, unlocked_client):
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        return create.json()["id"]

    @pytest.mark.parametrize(("fmt", "content_type"), [
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf", "application/pdf"),
        ("md", "text/markdown"),
        ("txt", "text/plain"),
    ])
    def test_exports_each_supported_format(self, unlocked_client, conv_id, fmt, content_type):
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/export",
            params={"professor": "heller", "format": fmt},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(content_type)
        assert len(resp.content) > 0

    def test_missing_conversation_404s(self, unlocked_client):
        resp = unlocked_client.get(
            "/api/conversations/c_missing/export",
            params={"professor": "heller", "format": "docx"},
        )
        assert resp.status_code == 404

    def test_unsupported_format_400s(self, unlocked_client, conv_id):
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/export",
            params={"professor": "heller", "format": "exe"},
        )
        assert resp.status_code == 400

    def test_requires_unlock(self, client):
        resp = client.get(
            "/api/conversations/c_1/export", params={"professor": "heller", "format": "docx"}
        )
        assert resp.status_code == 401

    def test_plain_text_really_is_plain_text(self, unlocked_client, conv_id):
        """Not Markdown with a different extension.

        The writer used to be chosen by a chain ending in a bare else that
        meant Markdown, so any format added without touching it would have
        produced a Markdown file under whatever name was asked for.
        """
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/export",
            params={"professor": "heller", "format": "txt"},
        )
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert "Princeton University AI Sandbox — Conversation Transcript" in body
        # save_to_markdown writes a heading for the label; the text writer
        # writes the transcript and nothing else.
        assert not body.lstrip().startswith("#")

    def test_every_format_has_a_writer_of_its_own(self):
        """A format in the table with no branch to match it is the fault this
        guards: it would be written by whichever branch happened to be last."""
        export_module = sys.modules["_pu_webui_export"]
        import inspect

        source = inspect.getsource(export_module.export_conversation)
        for fmt in export_module.FORMATS:
            assert f'fmt == "{fmt}"' in source, f"{fmt} has no writer named for it"


class TestOpeningAConversationFolder:
    """The way in to everything a conversation is made of."""

    def _page(self):

        from fastapi.templating import Jinja2Templates

        here = WEBUI_SRC / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None, can_reveal=True))

    def test_the_menu_offers_it(self):
        page = self._page()
        assert "Open this conversation's folder" in page

    def test_it_is_not_offered_where_there_is_nothing_to_open_it_with(self):

        from fastapi.templating import Jinja2Templates

        here = WEBUI_SRC / "templates"
        page = (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None, can_reveal=False))
        assert 'data-can-reveal="false"' in page

    def test_the_chat_page_is_told_whether_it_can(self, unlocked_client):
        """The template defaults to false, so a route that forgot would be silent."""
        page = unlocked_client.get("/").text
        assert 'data-can-reveal="true"' in page or 'data-can-reveal="false"' in page

    def test_it_opens_the_conversations_own_folder(self, unlocked_client, monkeypatch):
        import sys

        opened = []
        picker = sys.modules["_pu_webui_file_picker"]
        monkeypatch.setattr(picker, "reveal", lambda p: opened.append(str(p)) or True)
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        r = unlocked_client.post(
            f"/api/conversations/{conv_id}/reveal?professor=heller"
        )
        assert r.status_code == 200
        assert len(opened) == 1
        assert opened[0].endswith(conv_id)

    def test_a_conversation_that_does_not_exist_is_refused(self, unlocked_client):
        r = unlocked_client.post(
            "/api/conversations/c_" + "f" * 16 + "/reveal?professor=heller"
        )
        assert r.status_code == 404

    def test_a_malformed_id_is_refused_rather_than_used_as_a_path(self, unlocked_client):
        r = unlocked_client.post("/api/conversations/..%2F..%2Fetc/reveal?professor=heller")
        assert r.status_code in (404, 400)

    def test_it_is_behind_the_unlock_gate(self, client):
        r = client.post("/api/conversations/c_" + "f" * 16 + "/reveal?professor=heller")
        assert r.status_code in (401, 403, 302, 303)

    def test_a_browser_on_another_computer_cannot_open_a_window_here(
        self, unlocked_client, monkeypatch
    ):
        """It would open on the server's screen, not the person's."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_SAME_COMPUTER", frozenset())
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        r = unlocked_client.post(f"/api/conversations/{conv_id}/reveal?professor=heller")
        assert r.status_code == 403


class TestKeepingSuppliedDocuments:
    """Off by default; on, the documents sit with the conversation."""

    def _upload(self, client, conversation_id, name=b"source.txt"):
        return client.post(
            "/api/attachments",
            files={"file": (name.decode(), b"hello", "text/plain")},
            data={"professor": "heller", "conversation_id": conversation_id},
        )

    def _conv(self, client):
        return client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]

    def _set(self, preference_file, on):
        """Turn the setting on or off the way the settings page does."""
        from src.plugin_preferences import set_live
        set_live(preference_file, "webui", "keep_supplied_documents",
                 "true" if on else "false")

    def test_nothing_is_kept_unless_it_is_asked_for(
        self, unlocked_client, preference_file
    ):

        self._set(preference_file, False)
        conv_id = self._conv(unlocked_client)
        assert self._upload(unlocked_client, conv_id).json()["saved_as"] is None

    def test_when_asked_for_it_sits_with_the_conversation(
        self, unlocked_client, preference_file
    ):
        import sys

        self._set(preference_file, True)
        conv_id = self._conv(unlocked_client)
        assert self._upload(unlocked_client, conv_id).json()["saved_as"] == "source.txt"
        store = sys.modules["_pu_webui_conversation"].ConversationStore("heller")
        assert (store.attachments_dir(conv_id) / "source.txt").read_bytes() == b"hello"

    def test_a_second_document_of_the_same_name_does_not_replace_the_first(
        self, unlocked_client, preference_file
    ):
        self._set(preference_file, True)
        conv_id = self._conv(unlocked_client)
        self._upload(unlocked_client, conv_id)
        second = self._upload(unlocked_client, conv_id)
        assert second.json()["saved_as"] == "source (2).txt"

    def test_a_name_that_is_a_path_cannot_write_outside_the_folder(
        self, unlocked_client, preference_file
    ):
        self._set(preference_file, True)
        conv_id = self._conv(unlocked_client)
        saved = self._upload(unlocked_client, conv_id, b"../../escaped.txt").json()["saved_as"]
        assert saved == "escaped.txt"
