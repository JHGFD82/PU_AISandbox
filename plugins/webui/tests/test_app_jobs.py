"""Tests for plugins/webui/src/app.py's job routes: the forms plugins offer, starting a job, and fetching what it made."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
    _fake_plugin,
    _fake_ui_job_result,
    _wait_for_job_done,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestPluginActions:
    def test_lists_actions_from_installed_plugins(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        fake = _fake_plugin(action_id="translate")
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        resp = unlocked_client.get("/api/plugin-actions")
        assert resp.status_code == 200
        actions = resp.json()["actions"]
        assert [a["id"] for a in actions] == ["translate"]
        assert actions[0]["label"] == "Fake action"

    def test_empty_when_no_plugin_declares_one(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {})
        resp = unlocked_client.get("/api/plugin-actions")
        assert resp.json()["actions"] == []

    def test_requires_unlock(self, client):
        resp = client.get("/api/plugin-actions")
        assert resp.status_code == 401


class TestLanguages:
    def test_lists_registered_languages(self, unlocked_client):
        # Real plugins (translation, transcription) register real languages
        # at import time, so this doesn't need a fake — English at least
        # must be present since plugins/translation/plugin.py registers it
        # unconditionally.
        resp = unlocked_client.get("/api/languages")
        assert resp.status_code == 200
        languages = resp.json()["languages"]
        assert {"code": "en", "name": "English"} in languages
        # Sorted by display name, not by code.
        names = [lang["name"] for lang in languages]
        assert names == sorted(names)

    def test_requires_unlock(self, client):
        resp = client.get("/api/languages")
        assert resp.status_code == 401


class TestPluginActionPreview:
    def test_returns_preview_from_plugin(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        from src.runtime.ui_action import UiPromptPreview

        fake = _fake_plugin(
            action_id="translate",
            preview_ui_action=lambda fields, professor, model: UiPromptPreview(
                system_prompt=f"System for {fields.get('target_language')}",
                user_prompt="User prompt text",
                model=model or "default-model",
            ),
        )
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        resp = unlocked_client.post(
            "/api/plugin-actions/translate/preview",
            json={"professor": "heller", "model": "gpt-4o", "fields": {"target_language": "en"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["system_prompt"] == "System for en"
        assert body["user_prompt"] == "User prompt text"
        assert body["model"] == "gpt-4o"

    def test_unavailable_when_plugin_has_no_preview_method(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        fake = _fake_plugin(action_id="translate")  # no preview_ui_action attached
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        resp = unlocked_client.post(
            "/api/plugin-actions/translate/preview",
            json={"professor": "heller", "fields": {}},
        )
        assert resp.status_code == 200
        assert resp.json() == {"available": False}

    def test_unknown_action_id_404s(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {})
        resp = unlocked_client.post(
            "/api/plugin-actions/nope/preview",
            json={"professor": "heller", "fields": {}},
        )
        assert resp.status_code == 404

    def test_preview_exception_reported_as_unavailable_not_500(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]

        def _boom(fields, professor, model):
            raise ValueError("bad field value")

        fake = _fake_plugin(action_id="translate", preview_ui_action=_boom)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        resp = unlocked_client.post(
            "/api/plugin-actions/translate/preview",
            json={"professor": "heller", "fields": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "bad field value" in body["error"]

    def test_requires_unlock(self, client):
        resp = client.post("/api/plugin-actions/translate/preview", json={"professor": "heller", "fields": {}})
        assert resp.status_code == 401


class TestPluginActionExtensionFields:
    """GET /api/plugin-actions/{action_id}/extension-fields — the composer's
    dynamic subsection for whatever a language-extension plugin (e.g.
    translation-ea, transcription-ea) registers via
    register_extension_ui_hooks(), keyed by (action_id, language token) —
    see ExtensionUiHooks's docstring in src/runtime/ui_action.py for why
    action_id is part of the key (two different actions can register the
    same token for unrelated fields)."""

    @pytest.fixture(autouse=True)
    def _isolated_registry(self, monkeypatch):
        from src.runtime import ui_action as ui_action_module
        monkeypatch.setattr(ui_action_module, "_EXTENSION_UI_HOOKS", {})

    def test_returns_empty_list_when_nothing_registered(self, unlocked_client):
        resp = unlocked_client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": "jp"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"fields": []}

    def test_returns_registered_fields_for_matching_token(self, unlocked_client):
        from src.runtime.ui_action import UiField, register_extension_ui_hooks
        register_extension_ui_hooks(
            action_id="translate",
            token="jp",
            fields=[UiField(name="kanbun", label="Use Kanbun conventions", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: None,
        )
        resp = unlocked_client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": "jp"}
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "fields": [{
                "name": "kanbun", "label": "Use Kanbun conventions", "kind": "checkbox",
                "required": False, "choices": None, "group": None, "allow_folder": False,
                "allow_text": False,
            }]
        }

    def test_blank_target_language_returns_empty_list(self, unlocked_client):
        from src.runtime.ui_action import UiField, register_extension_ui_hooks
        register_extension_ui_hooks(
            action_id="translate", token="jp",
            fields=[UiField(name="kanbun", label="Kanbun", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: None,
        )
        resp = unlocked_client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": ""}
        )
        assert resp.json() == {"fields": []}

    def test_unmatched_token_returns_empty_list(self, unlocked_client):
        from src.runtime.ui_action import UiField, register_extension_ui_hooks
        register_extension_ui_hooks(
            action_id="translate", token="jp",
            fields=[UiField(name="kanbun", label="Kanbun", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: None,
        )
        resp = unlocked_client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": "zh"}
        )
        assert resp.json() == {"fields": []}

    def test_different_action_with_the_same_token_returns_empty_list(self, unlocked_client):
        # Regression coverage for the collision this key shape fixes:
        # registering "jp" under "translate" must not leak into a request
        # for "transcribe"'s own extension fields for the same token.
        from src.runtime.ui_action import UiField, register_extension_ui_hooks
        register_extension_ui_hooks(
            action_id="translate", token="jp",
            fields=[UiField(name="kanbun", label="Kanbun", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: None,
        )
        resp = unlocked_client.get(
            "/api/plugin-actions/transcribe/extension-fields", params={"target_language": "jp"}
        )
        assert resp.json() == {"fields": []}

    def test_requires_unlock(self, client):
        resp = client.get(
            "/api/plugin-actions/translate/extension-fields", params={"target_language": "jp"}
        )
        assert resp.status_code == 401


class TestStartJob:
    def test_start_job_returns_running_status(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        # A real run_ui_action always returns a UiJobResult (never None) —
        # matters here even though this test only checks the immediate
        # response, since the background thread runs to completion inside
        # this same process regardless of whether the test waits for it.
        fake = _fake_plugin(
            action_id="translate",
            run_ui_action=lambda fields, professor, model, on_progress, output_dir: _fake_ui_job_result(output_dir),
        )
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        _wait_for_job_done(unlocked_client, conv_id, "heller")

    def test_uploaded_file_saved_and_passed_as_file_path(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        received = {}

        seen_while_running = []

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            seen_while_running.append(Path(fields["file_path"]).read_text())
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
            files={"files": ("doc.txt", b"some document text", "text/plain")},
        )
        assert resp.status_code == 200
        _wait_for_job_done(unlocked_client, conv_id, "heller")
        assert received["file_name"] == "doc.txt"
        assert received["file_path"].endswith("doc.txt")
        # Readable while the job ran — that is all a plugin needs.
        assert seen_while_running == ["some document text"]
        # And gone afterwards: the professor already has this file where
        # they chose to keep it, so a second copy filed inside their usage
        # data would grow forever for no purpose.
        assert not Path(received["file_path"]).exists()

    def test_uploads_never_land_in_the_professors_data(self, unlocked_client, monkeypatch):
        """The whole point: nothing uploaded is stored under data/."""
        app_module = sys.modules["_pu_webui_app"]
        seen = {}

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            seen["file_path"] = fields["file_path"]
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                  "fields_json": json.dumps({})},
            files={"files": ("paper.pdf", b"pdf bytes", "application/pdf")},
        )
        _wait_for_job_done(unlocked_client, conv_id, "heller")

        conversation = sys.modules["_pu_webui_conversation"]
        data_dir = Path(conversation.CONVERSATIONS_DIR)
        assert "paper.pdf" not in [p.name for p in data_dir.rglob("*")]
        assert not str(Path(seen["file_path"])).startswith(str(data_dir))

    def test_no_input_folder_is_ever_created(self, unlocked_client, monkeypatch):
        """`input/` used to hold copies of every uploaded file. It is gone."""
        app_module = sys.modules["_pu_webui_app"]

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            from src.runtime.ui_action import UiJobResult
            return UiJobResult(output_path=None, output_filename=None, summary="Done.")

        fake = _fake_plugin(action_id="transcribe", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"transcribe": fake})
        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]
        unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": conv_id, "action_id": "transcribe",
                  "fields_json": json.dumps({})},
            files=[("files", ("a.jpg", b"one", "image/jpeg")),
                   ("files", ("b.jpg", b"two", "image/jpeg"))],
        )
        _wait_for_job_done(unlocked_client, conv_id, "heller")
        conversation = sys.modules["_pu_webui_conversation"]
        assert "input" not in [p.name for p in Path(conversation.CONVERSATIONS_DIR).rglob("*")]

    def test_upload_filename_cannot_escape_the_job_directory(self, unlocked_client, monkeypatch):
        """An uploaded file is written under its job's own directory, whatever it claims to be called.

        The filename in a multipart upload is supplied by whoever made the
        request, not by the browser's file picker, so it can contain "../".
        The single-file branch used to build the path from it directly while
        the multi-file branch stripped it, which meant a crafted name could
        write outside the job directory entirely. Both strip it now.
        """
        app_module = sys.modules["_pu_webui_app"]
        received = {}

        seen_while_running = []

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            seen_while_running.append(Path(fields["file_path"]).read_text())
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
            files={"files": ("../../../../escaped.txt", b"payload", "text/plain")},
        )
        assert resp.status_code == 200
        _wait_for_job_done(unlocked_client, conv_id, "heller")

        import os
        written = Path(received["file_path"])
        assert written.name == "escaped.txt"
        # It landed directly inside the scratch folder made for this job,
        # not four levels above it.
        assert written.parent.name.startswith("pu_webui_job_")
        assert not os.path.exists("/escaped.txt")
        # And nothing named that reached the professor's own data.
        conversation = sys.modules["_pu_webui_conversation"]
        assert "escaped.txt" not in [
            p.name for p in Path(conversation.CONVERSATIONS_DIR).rglob("*")
        ]

    def test_multiple_uploaded_files_saved_into_one_folder(self, unlocked_client, monkeypatch):
        # A professor picking several images (or a whole folder, on a
        # browser that supports it) at once for an allow_folder field —
        # see UiField.allow_folder's docstring. All uploads land in one
        # directory, and file_path points at that directory the same way
        # it would for a CLI user pointing -i at a folder directly.
        app_module = sys.modules["_pu_webui_app"]
        received = {}
        seen_listing = []

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            seen_listing.extend(sorted(p.name for p in Path(fields["file_path"]).iterdir()))
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="transcribe", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"transcribe": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "transcribe",
                "fields_json": json.dumps({"target_language": "en"}),
            },
            files=[
                ("files", ("page1.jpg", b"fake image bytes 1", "image/jpeg")),
                ("files", ("page2.jpg", b"fake image bytes 2", "image/jpeg")),
            ],
        )
        assert resp.status_code == 200
        _wait_for_job_done(unlocked_client, conv_id, "heller")

        assert received["file_name"] == "2 images"
        # One folder holding both, the same shape a plugin gets from a CLI
        # user pointing -i at a directory — just not inside anyone's data.
        assert seen_listing == ["page1.jpg", "page2.jpg"]
        conversation = sys.modules["_pu_webui_conversation"]
        assert not str(received["file_path"]).startswith(str(conversation.CONVERSATIONS_DIR))

    def test_no_files_leaves_fields_untouched(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        received = {}

        seen_while_running = []

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            received.update(fields)
            seen_while_running.append(Path(fields["file_path"]).read_text())
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/out.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write("done")
            return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]

        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
        )
        assert resp.status_code == 200
        _wait_for_job_done(unlocked_client, conv_id, "heller")
        assert "file_path" not in received
        assert "file_name" not in received

    def test_unknown_action_returns_400(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {})
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": conv_id, "action_id": "translate", "fields_json": "{}"},
        )
        assert resp.status_code == 400

    def test_missing_conversation_returns_404(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        fake = _fake_plugin(run_ui_action=lambda *a: None)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})
        resp = unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": "c_missing", "action_id": "translate", "fields_json": "{}"},
        )
        assert resp.status_code == 404

    def test_conversation_already_busy_returns_409(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        conversation = sys.modules["_pu_webui_conversation"]
        fake = _fake_plugin(run_ui_action=lambda *a: None)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        store = conversation.ConversationStore("heller", base_dir=conversation.CONVERSATIONS_DIR)
        conv = store.load(conv_id)
        conv.active_job_id = "job_existing"
        store.save(conv)

        resp = unlocked_client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": conv_id, "action_id": "translate", "fields_json": "{}"},
        )
        assert resp.status_code == 409

    def test_invalid_fields_json_returns_400(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        fake = _fake_plugin(run_ui_action=lambda *a: None)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})
        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        resp = unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": "not json",
            },
        )
        assert resp.status_code == 400

    def test_requires_unlock(self, client):
        resp = client.post(
            "/api/jobs",
            data={"professor": "heller", "conversation_id": "c_1", "action_id": "translate", "fields_json": "{}"},
        )
        assert resp.status_code == 401


class TestJobOutputDownload:
    def _run_job_to_completion(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]

        def run_ui_action(fields, professor, model, on_progress, output_dir):
            from src.runtime.ui_action import UiJobResult
            out = f"{output_dir}/translated.docx"
            with open(out, "w", encoding="utf-8") as f:
                f.write("translated content")
            return UiJobResult(output_path=out, output_filename="translated.docx", summary="Translated it.")

        fake = _fake_plugin(action_id="translate", run_ui_action=run_ui_action)
        monkeypatch.setattr(app_module, "_get_plugins", lambda: {"translate": fake})

        create = unlocked_client.post("/api/conversations", json={"professor": "heller", "model": "gpt-4o"})
        conv_id = create.json()["id"]
        unlocked_client.post(
            "/api/jobs",
            data={
                "professor": "heller", "conversation_id": conv_id, "action_id": "translate",
                "fields_json": json.dumps({"source_language": "ja", "target_language": "en"}),
            },
        )
        conv = _wait_for_job_done(unlocked_client, conv_id, "heller")
        job_id = next(m["job_id"] for m in conv["messages"] if m["kind"] == "job_result")
        return conv_id, job_id

    def test_downloads_the_finished_file(self, unlocked_client, monkeypatch):
        conv_id, job_id = self._run_job_to_completion(unlocked_client, monkeypatch)
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/job-outputs/{job_id}", params={"professor": "heller"}
        )
        assert resp.status_code == 200
        assert resp.content == b"translated content"

    def test_unknown_job_id_returns_404(self, unlocked_client, monkeypatch):
        conv_id, _ = self._run_job_to_completion(unlocked_client, monkeypatch)
        resp = unlocked_client.get(
            f"/api/conversations/{conv_id}/job-outputs/job_bogus", params={"professor": "heller"}
        )
        assert resp.status_code == 404

    def test_requires_unlock(self, client):
        resp = client.get("/api/conversations/c_1/job-outputs/job_1", params={"professor": "heller"})
        assert resp.status_code == 401


class TestAJobFormNamesItsOwnNumbers:
    """A blank box in a job form has a real value behind it, and shows it.

    The value comes from the plugin's own settings with the group's shared file
    and this person's preferences applied — so somebody who set
    ``[translation] temperature`` sees the number they set, not a description.
    """

    def _page(self):

        from fastapi.templating import Jinja2Templates

        here = WEBUI_SRC / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))

    def test_whatever_an_action_reports_reaches_the_browser(self, unlocked_client):
        """The route hands the values on; each plugin decides what they are."""
        actions = unlocked_client.get("/api/plugin-actions?professor=heller").json()["actions"]
        assert all("sampling" in a for a in actions), (
            "an action's own settings are dropped on the way to the page"
        )

    def test_the_form_shows_the_number_beside_the_box(self):
        page = self._page()
        assert "blank = ${declared[key]}" in page

    def test_a_label_reads_properly_whether_or_not_it_has_a_range(self):
        """Max response tokens has no range, and read "(, blank = 4000)"."""
        page = self._page()
        assert "parts.join(\", \")" in page
        assert 'samplingLabel("Max response tokens", "max_tokens")' in page

    def test_an_action_that_reports_nothing_gets_no_invented_figure(self):
        """A plugin that declares no settings gets a plain label, not a guess."""
        page = self._page()
        assert "declared[key] !== undefined && declared[key] !== null" in page
        assert "parts.length ? " in page


class TestAJobCanRunOnItsOwnModel:
    """Choosable in the form, without changing the conversation."""

    def _page(self):

        return (WEBUI_SRC / "templates" / "chat.html").read_text()

    def test_the_form_offers_a_model(self):
        page = self._page()
        assert 'name: "model", label: "Model", kind: "model"' in page

    def test_the_job_runs_on_what_the_form_says(self):
        """Not on the chat header, which the form only starts from."""
        page = self._page()
        start = page.split("async function startJobFromModal")[1]
        assert "const model = jobModelName();" in start

    def test_the_preview_is_of_the_job_that_would_run(self):
        """A prompt differs by model — see get_model_system_role."""
        page = self._page()
        preview = page.split("async function refreshJobPreview")[1].split("\n}")[0]
        assert "jobModelName()" in preview

    def test_the_model_is_not_passed_off_as_one_of_the_plugins_fields(self):
        """It is the sandbox's field, not something any plugin declared."""
        page = self._page()
        assert "const { model: _chosenModel, ...actionValues } = values;" in page
        assert "fields_json\", JSON.stringify(actionValues)" in page

    def test_the_conversation_keeps_its_own_model(self, unlocked_client, monkeypatch):
        """Running one job on another model is not a decision about the chat."""
        import sys

        conv_id = unlocked_client.post(
            "/api/conversations", json={"professor": "heller", "model": "gpt-4o"}
        ).json()["id"]

        jobs_module = sys.modules["_pu_webui_jobs"]
        started = {}

        def remember(**kwargs):
            started.update(kwargs)
            from unittest.mock import MagicMock

            job = MagicMock()
            job.id = "job_1"
            return job

        monkeypatch.setattr(jobs_module, "start_job", remember)
        resp = unlocked_client.post("/api/jobs", data={
            "professor": "heller", "conversation_id": conv_id,
            "action_id": "translate", "model": "o3-mini", "fields_json": "{}",
        })
        assert resp.status_code in (200, 400, 404), resp.text
        if resp.status_code == 200:
            assert started.get("model") == "o3-mini", "the job did not run on the chosen model"
        conv = unlocked_client.get(
            f"/api/conversations/{conv_id}?professor=heller"
        ).json()
        assert conv["model"] == "gpt-4o", "running a job changed the conversation's model"
