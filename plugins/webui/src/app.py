"""FastAPI application for the webui plugin: the browser-based chat interface.

Built by ``create_app()`` and started by ``run_server()``, both called from
``plugin.py``. See ``docs/webui-plugin-plan.md`` sections 2-7 for the full
design (one shared unlock gate, a professor switcher instead of per-professor
login, and the model/conversation/usage endpoints the front-end in
``templates/chat.html`` calls).

This module is registered into ``sys.modules`` under the flat, dot-free name
``_pu_webui_app`` by ``plugin.py`` — not a real dotted package path — the
same trick used for its own two internal dependencies, ``auth.py`` and
``conversation.py`` (registered as ``_pu_webui_auth`` and
``_pu_webui_conversation``, imported below via a plain ``import
_pu_webui_auth`` style statement). This is deliberate, not an oversight: the
plugin loader in this project (``src/runtime/plugin_loader.py``) loads each
plugin's ``plugin.py`` as a module with a *fabricated* name
(``pu_plugin.<name>.plugin``) whose parent package never actually exists, so
literal relative imports (``from .auth import ...``) or deeper fabricated
dotted paths (``pu_plugin.webui.auth``) would fail the moment Python's real
import machinery tried to resolve their non-existent parent packages. A
flat, single-segment name has no parent to resolve — it's found directly in
``sys.modules``, which is exactly how every other plugin's *service* modules
already work (e.g. ``src.services.prompt_service``), just without also
needing a real package two levels up like ``src.services`` provides for
free. ``src.services.chat_service`` (this plugin's actual AI-calling code)
does use the normal dotted convention, since ``src.services`` genuinely
exists on disk and that's what lets ``SandboxProcessor`` find it.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from dataclasses import asdict

from src import settings_store
from src.config import load_professor_config
from src.models import (
    DEFAULT_FALLBACK_MODEL,
    get_available_models,
    get_monthly_limit,
    model_supports_vision,
)
from src.runtime.info_commands import list_optional_env_fields
from src.services.api_config import credential_path_for_endpoint
from src.settings import ENDPOINTS, WEBUI_SESSION_COOKIE_NAME
from src.tracking.token_tracker import TokenTracker

auth = sys.modules["_pu_webui_auth"]
conversation = sys.modules["_pu_webui_conversation"]
attachments = sys.modules["_pu_webui_attachments"]
jobs = sys.modules["_pu_webui_jobs"]
export = sys.modules["_pu_webui_export"]

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# One backend instance for the life of the process — see auth.get_configured_backend().
_auth_backend = auth.get_configured_backend()

# One JobStore for the life of the process, same lifetime reasoning as
# _auth_backend above — see jobs.py's module docstring for why this is
# deliberately in-memory only (docs/webui-plugin-plan.md section 10).
_job_store = jobs.JobStore()

# Plugins are loaded once, lazily, on first use rather than at import time —
# this module is itself registered by plugins/webui/plugin.py *during* the
# CLI's own top-level load_plugins() call (see this file's own module
# docstring), so calling load_plugins() again here at import time would be
# reentrant while that outer scan is still iterating the same directory.
# Harmless (a plain read-only directory listing), but wasteful — deferring
# to first actual use means it only ever runs once, well after the CLI's
# own startup has fully finished.
_plugins_cache: dict | None = None


def _get_plugins() -> dict:
    """Return the command-name-to-plugin mapping, loading it once and caching it."""
    global _plugins_cache
    if _plugins_cache is None:
        from src.runtime import load_plugins
        plugins_dir = Path(__file__).resolve().parent.parent.parent.parent / "plugins"
        _plugins_cache = load_plugins(plugins_dir)
    return _plugins_cache

# Section order for the /settings page. On first run (no professors yet)
# getting a professor added is the blocking step, with usage-source sharing
# as a natural next thought for someone managing multiple installations;
# once at least one professor exists, install-wide settings (shared
# defaults, alternate endpoints) are what someone is more likely returning
# to tweak, so they lead instead.
_SETTINGS_ORDER_FIRST_RUN = ["professors", "external_sources", "webui", "shared"]
_SETTINGS_ORDER_REPEAT = ["shared", "professors", "webui", "external_sources"]


class ActiveProfessorBody(BaseModel):
    professor: str


class NewConversationBody(BaseModel):
    professor: str
    model: str | None = None


class ChatAttachmentBody(BaseModel):
    """A previously extracted document, as returned by POST /api/attachments and sent back on the next chat turn."""

    filename: str
    text: str
    char_count: int


class ChatBody(BaseModel):
    professor: str
    conversation_id: str
    message: str
    model: str | None = None
    attachment: ChatAttachmentBody | None = None


class RenameConversationBody(BaseModel):
    professor: str
    title: str


class AddProfessorBody(BaseModel):
    name: str
    key: str
    backup_key: str | None = None


class ProfessorKeyBody(BaseModel):
    key: str


class ProfessorBackupKeyBody(BaseModel):
    backup_key: str | None = None


class PassphraseBody(BaseModel):
    passphrase: str
    confirm: str


class SettingValueBody(BaseModel):
    path: str
    value: str


class GenerateValueBody(BaseModel):
    path: str


class SourceBody(BaseModel):
    label: str
    path: str
    mode: str = "read-only"
    professor: str | None = None


# Dotted paths the /settings page may write directly via the generic
# value/generate/unset endpoints. webui.passphrase_hash is deliberately
# excluded — a passphrase must only ever be written pre-hashed, through the
# dedicated /api/settings/passphrase endpoint, never as a raw string via the
# generic one.
def _directly_editable_paths() -> set[str]:
    return {
        path for path, _label, _section, _secret in list_optional_env_fields()
        if path != "webui.passphrase_hash"
    }


def _settings_snapshot() -> dict:
    """Build the full payload the /settings page renders from.

    Recomputed on every request rather than cached — this data changes
    exactly when someone submits one of the forms on this same page, and
    there's no meaningful volume of traffic here to make caching worth the
    staleness risk.
    """
    professors = load_professor_config()
    has_professors = bool(professors)

    field_status = {
        path: bool(settings_store.get_value(path))
        for path, _label, _section, _secret in list_optional_env_fields()
    }

    endpoints = []
    for api_name, raw in ENDPOINTS.items():
        cred_path = credential_path_for_endpoint(api_name)
        endpoints.append({
            "name": api_name,
            "display_name": raw.get("name", api_name),
            "base_url": raw.get("base_url", ""),
            "openai_compatible": bool(raw.get("openai_compatible", False)),
            "default_model": raw.get("default_model"),
            "timeout": raw.get("timeout", 30),
            "credential_path": cred_path,
            "key_set": field_status.get(cred_path, False),
        })

    sources = settings_store.get_configured_sources()

    return {
        "has_professors": has_professors,
        "order": _SETTINGS_ORDER_FIRST_RUN if not has_professors else _SETTINGS_ORDER_REPEAT,
        "professors": [
            {
                "safe_name": safe_name,
                "name": prof["name"],
                "has_key": bool(prof.get("key")),
                "has_backup_key": bool(prof.get("backup_key")),
            }
            for safe_name, prof in professors.items()
        ],
        "webui": {
            "passphrase_configured": bool(settings_store.get_value("webui.passphrase_hash")),
            "session_secret_set": field_status.get("webui.session_secret", False),
        },
        "shared": {
            "shared_settings_path": settings_store.get_value("shared_settings.path"),
            "endpoints": endpoints,
        },
        "sources": {
            "source_id": settings_store.get_source_id(),
            "external": [
                {"label": s.label, "path": s.path, "mode": s.mode, "professor": s.professor}
                for s in sources
            ],
        },
    }


def _validated_professor(safe_name: str | None) -> str:
    """Confirm *safe_name* is a professor configured in .env, or raise a 400 error.

    Every route that accepts a professor name from the browser calls this
    first — the value ends up in file paths (usage data, conversation
    storage), so it must be one of the professors this installation actually
    knows about, not arbitrary user input.
    """
    professors = load_professor_config()
    if not safe_name or safe_name not in professors:
        raise HTTPException(400, f"Unknown professor '{safe_name}'.")
    return safe_name


def _require_unlocked(request: Request) -> None:
    """Raise a 401 error unless this browser session has already unlocked the app."""
    if not request.session.get("unlocked"):
        raise HTTPException(401, "Not unlocked.")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application for the local web interface.

    Called once by ``run_server()``. Kept as a separate function (rather
    than a module-level ``app = FastAPI()``) so tests can build a fresh app
    instance without starting a real server.
    """
    app = FastAPI(title="Princeton AI Sandbox")

    # A random secret is fine for this session-cookie's purpose (signing,
    # not encrypting) — worst case on restart is everyone has to unlock
    # again. Set webui.session_secret in .settings (e.g. via
    # `python main.py env set webui.session_secret --generate`) to keep
    # sessions alive across restarts instead.
    secret = settings_store.get_value("webui.session_secret") or secrets.token_hex(32)
    app.add_middleware(
        SessionMiddleware, secret_key=secret, session_cookie=WEBUI_SESSION_COOKIE_NAME
    )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        if not request.session.get("unlocked"):
            return templates.TemplateResponse(request, "unlock.html", {"error": None})
        if not load_professor_config():
            # Nothing to chat with yet — send a first-time visitor straight
            # to setup instead of an empty, broken-looking chat screen.
            return RedirectResponse("/settings", status_code=303)
        return templates.TemplateResponse(request, "chat.html")

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        if not request.session.get("unlocked"):
            return templates.TemplateResponse(request, "unlock.html", {"error": None})
        return templates.TemplateResponse(request, "settings.html")

    @app.post("/unlock")
    async def unlock(request: Request):
        ok = await _auth_backend.authenticate(request)
        if ok:
            request.session["unlocked"] = True
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request,
            "unlock.html",
            {"error": "Incorrect passphrase."},
            status_code=401,
        )

    @app.post("/lock")
    async def lock(request: Request):
        request.session.clear()
        return JSONResponse({"ok": True})

    @app.get("/api/professors")
    async def api_professors(request: Request):
        _require_unlocked(request)
        professors = load_professor_config()
        active = request.session.get("active_professor")
        if active not in professors:
            active = next(iter(professors), None)
            if active:
                request.session["active_professor"] = active
        return {
            "professors": [{"safe_name": k, "name": v["name"]} for k, v in professors.items()],
            "active": active,
        }

    @app.post("/api/active-professor")
    async def api_set_active_professor(request: Request, body: ActiveProfessorBody):
        _require_unlocked(request)
        professor = _validated_professor(body.professor)
        request.session["active_professor"] = professor
        return {"ok": True}

    # ── Settings page ────────────────────────────────────────────────────────
    # Everything below backs /settings — professors, this plugin's own
    # secrets, the shared-settings pointer and alternate-endpoint
    # credentials, and external usage-data sources. See docs/configuration.md
    # for what each of these means; this is just a browser front-end over
    # the same src/settings_store.py the `env`/`usage sources` CLI commands
    # already use.

    @app.get("/api/settings")
    async def api_settings(request: Request):
        _require_unlocked(request)
        return _settings_snapshot()

    @app.post("/api/settings/professors")
    async def api_add_professor(request: Request, body: AddProfessorBody):
        _require_unlocked(request)
        try:
            safe_name = settings_store.add_professor(body.name, body.key, body.backup_key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "safe_name": safe_name}

    @app.delete("/api/settings/professors/{safe_name}")
    async def api_remove_professor(request: Request, safe_name: str):
        _require_unlocked(request)
        try:
            removed_name = settings_store.remove_professor(safe_name)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True, "removed": removed_name}

    @app.post("/api/settings/professors/{safe_name}/key")
    async def api_set_professor_key(request: Request, safe_name: str, body: ProfessorKeyBody):
        _require_unlocked(request)
        try:
            settings_store.set_professor_key(safe_name, body.key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/settings/professors/{safe_name}/backup-key")
    async def api_set_professor_backup_key(
        request: Request, safe_name: str, body: ProfessorBackupKeyBody
    ):
        _require_unlocked(request)
        try:
            settings_store.set_professor_backup_key(safe_name, body.backup_key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/settings/passphrase")
    async def api_set_passphrase(request: Request, body: PassphraseBody):
        _require_unlocked(request)
        if body.passphrase != body.confirm:
            raise HTTPException(400, "Passphrases did not match.")
        if not body.passphrase:
            raise HTTPException(400, "Passphrase cannot be empty.")
        hashed = auth.hash_passphrase(body.passphrase)
        settings_store.set_value("webui.passphrase_hash", hashed)
        return {"ok": True}

    @app.delete("/api/settings/passphrase")
    async def api_clear_passphrase(request: Request):
        _require_unlocked(request)
        settings_store.unset_value("webui.passphrase_hash")
        return {"ok": True}

    @app.post("/api/settings/values")
    async def api_set_setting_value(request: Request, body: SettingValueBody):
        _require_unlocked(request)
        if body.path not in _directly_editable_paths():
            raise HTTPException(400, f"'{body.path}' is not an editable setting.")
        value = body.value.strip()
        if not value:
            raise HTTPException(400, "Value cannot be blank — use the remove action instead.")
        settings_store.set_value(body.path, value)
        return {"ok": True}

    @app.post("/api/settings/values/generate")
    async def api_generate_setting_value(request: Request, body: GenerateValueBody):
        _require_unlocked(request)
        if body.path not in _directly_editable_paths():
            raise HTTPException(400, f"'{body.path}' is not an editable setting.")
        settings_store.set_value(body.path, secrets.token_urlsafe(32))
        return {"ok": True}

    @app.delete("/api/settings/values")
    async def api_unset_setting_value(request: Request, path: str):
        _require_unlocked(request)
        if path not in _directly_editable_paths():
            raise HTTPException(400, f"'{path}' is not an editable setting.")
        settings_store.unset_value(path)
        return {"ok": True}

    @app.post("/api/settings/sources")
    async def api_add_source(request: Request, body: SourceBody):
        _require_unlocked(request)
        try:
            settings_store.add_source(body.label, body.path, mode=body.mode, professor=body.professor)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.delete("/api/settings/sources")
    async def api_remove_source(request: Request, label: str):
        _require_unlocked(request)
        if not settings_store.remove_source(label):
            raise HTTPException(404, f"No configured source named '{label}'.")
        return {"ok": True}

    @app.get("/api/models")
    async def api_models(request: Request, professor: str):
        _require_unlocked(request)
        _validated_professor(professor)
        names = sorted(get_available_models())
        models = [{"name": m, "supports_vision": model_supports_vision(m)} for m in names]
        default = DEFAULT_FALLBACK_MODEL if DEFAULT_FALLBACK_MODEL in names else (names[0] if names else None)
        return {"models": models, "default": default}

    @app.get("/api/usage")
    async def api_usage(request: Request, professor: str):
        _require_unlocked(request)
        _validated_professor(professor)
        tracker = TokenTracker(professor=professor)
        month = tracker.get_monthly_usage()
        all_time = tracker.get_all_time_usage()
        monthly_limit = get_monthly_limit()
        usage_pct = (month["total_cost"] / monthly_limit * 100) if monthly_limit > 0 else 0.0
        return {
            "month": month,
            "all_time": all_time,
            "model_usage": tracker.usage_data.get("model_usage", {}),
            "budget": {
                "monthly_limit": monthly_limit,
                "usage_percentage": usage_pct,
                "remaining_budget": max(0.0, monthly_limit - month["total_cost"]),
            },
        }

    @app.get("/api/conversations")
    async def api_list_conversations(request: Request, professor: str):
        _require_unlocked(request)
        _validated_professor(professor)
        store = conversation.ConversationStore(professor)
        return {"conversations": store.list_conversations()}

    @app.post("/api/conversations")
    async def api_create_conversation(request: Request, body: NewConversationBody):
        _require_unlocked(request)
        professor = _validated_professor(body.professor)
        store = conversation.ConversationStore(professor)
        model = body.model or DEFAULT_FALLBACK_MODEL
        conv = store.create(model=model)
        return conv.to_dict()

    @app.get("/api/conversations/{conversation_id}")
    async def api_get_conversation(request: Request, conversation_id: str, professor: str):
        _require_unlocked(request)
        professor = _validated_professor(professor)
        store = conversation.ConversationStore(professor)
        conv = store.load(conversation_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found.")
        return conv.to_dict()

    @app.delete("/api/conversations/{conversation_id}")
    async def api_delete_conversation(request: Request, conversation_id: str, professor: str):
        _require_unlocked(request)
        professor = _validated_professor(professor)
        store = conversation.ConversationStore(professor)
        if not store.delete(conversation_id):
            raise HTTPException(404, "Conversation not found.")
        return {"deleted": True}

    @app.patch("/api/conversations/{conversation_id}")
    async def api_rename_conversation(request: Request, conversation_id: str, body: RenameConversationBody):
        _require_unlocked(request)
        professor = _validated_professor(body.professor)
        store = conversation.ConversationStore(professor)
        conv = store.load(conversation_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found.")
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "Title cannot be blank.")
        conv.title = title[:80]
        store.save(conv)
        return conv.to_dict()

    @app.get("/api/conversations/{conversation_id}/export")
    async def api_export_conversation(
        request: Request, conversation_id: str, professor: str, format: str = "docx"
    ):
        """Download a conversation as a formatted transcript (Word, PDF, or Markdown)."""
        _require_unlocked(request)
        professor = _validated_professor(professor)
        store = conversation.ConversationStore(professor)
        conv = store.load(conversation_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found.")
        if format not in export.FORMATS:
            supported = ", ".join(sorted(export.FORMATS))
            raise HTTPException(400, f"Unsupported export format '{format}'. Supported: {supported}.")

        content_type, ext = export.FORMATS[format]
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in conv.title).strip()
        safe_title = safe_title or "conversation"
        tmp_dir = tempfile.mkdtemp(prefix="pu_webui_export_")
        output_path = os.path.join(tmp_dir, f"{safe_title}.{ext}")
        try:
            export.export_conversation(conv, format, output_path)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        # The temp directory is only safe to remove once the file has
        # actually finished streaming to the browser — FileResponse's
        # background task runs after the response body is fully sent, not
        # before, unlike a plain try/finally around the return.
        return FileResponse(
            output_path,
            media_type=content_type,
            filename=f"{safe_title}.{ext}",
            background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
        )

    @app.post("/api/attachments")
    async def api_upload_attachment(
        request: Request, professor: str = Form(...), file: UploadFile = File(...)
    ):
        """Extract text from an uploaded document so it can be attached to a chat turn.

        The AI gateway this project uses has no native file-upload support
        (see attachments.py's module docstring), so the browser uploads the
        file here first; the extracted text this returns is what the
        front end then sends along with the next /api/chat call, not the
        original file.
        """
        _require_unlocked(request)
        _validated_professor(professor)
        if not file.filename:
            raise HTTPException(400, "No filename was provided with the upload.")

        data = await file.read()
        fd, tmp_path = tempfile.mkstemp(prefix="pu_webui_upload_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            doc = attachments.extract_text(tmp_path, file.filename)
        except attachments.AttachmentError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            os.unlink(tmp_path)

        return {"filename": doc.filename, "text": doc.text, "char_count": doc.char_count}

    # ── Plugin composer actions (docs/webui-plugin-plan.md section 10) ────────
    # Background jobs (translate/transcribe/...) triggered from a
    # conversation's composer, distinct from the ordinary chat turn above —
    # see jobs.py's module docstring for the full design.

    @app.get("/api/plugin-actions")
    async def api_plugin_actions(request: Request):
        """List every installed plugin's declared composer action."""
        _require_unlocked(request)
        actions = jobs.list_ui_actions(_get_plugins())
        return {"actions": [asdict(a) for a in actions]}

    @app.get("/api/languages")
    async def api_languages(request: Request):
        """List every registered language, for populating a 'language'-kind UiField as a dropdown.

        Plugins call ``register_language()`` at import time (see
        ``docs/plugin-authoring-guide.md``), so loading the plugin registry
        at least once (``_get_plugins()``, cached after the first call) is
        what guarantees ``LANGUAGE_MAP`` is fully populated before this
        reads it.
        """
        _require_unlocked(request)
        _get_plugins()
        from src.config import LANGUAGE_MAP
        languages = [{"code": code, "name": name} for code, name in LANGUAGE_MAP.items()]
        languages.sort(key=lambda lang: lang["name"])
        return {"languages": languages}

    @app.post("/api/plugin-actions/{action_id}/preview")
    async def api_plugin_action_preview(request: Request, action_id: str):
        """Build the live system/user prompt preview for one composer action's form.

        Called by the two-pane preview panel after every change to the
        form — see ``UiPromptPreview``'s docstring
        (``src/runtime/ui_action.py``) for the full design. Returns
        ``{"available": False}`` rather than an error if the action's
        plugin doesn't implement ``preview_ui_action`` at all, so the panel
        can show "no live preview for this action" instead of a broken
        request.
        """
        _require_unlocked(request)
        body = await request.json()
        professor = _validated_professor(body.get("professor"))
        model = body.get("model") or None
        fields = body.get("fields") or {}
        if not isinstance(fields, dict):
            raise HTTPException(400, "fields must be a JSON object.")

        plugin = jobs.find_plugin_for_action(_get_plugins(), action_id)
        if plugin is None:
            raise HTTPException(404, f"No installed plugin offers the '{action_id}' action.")
        if not hasattr(plugin, "preview_ui_action"):
            return {"available": False}

        try:
            preview = plugin.preview_ui_action(fields, professor, model)
        except Exception as e:
            return {"available": False, "error": str(e)}
        return {"available": True, **asdict(preview)}

    @app.post("/api/jobs")
    async def api_start_job(
        request: Request,
        professor: str = Form(...),
        conversation_id: str = Form(...),
        action_id: str = Form(...),
        model: str | None = Form(None),
        fields_json: str = Form("{}"),
        file: UploadFile | None = File(None),
    ):
        """Start a plugin background job (translate/transcribe/...) in one conversation.

        The submitted form's non-file values arrive JSON-encoded in
        ``fields_json`` — which fields exist is entirely plugin-defined
        (see ``UiAction``/``UiField``), not a fixed set this route could
        declare individual ``Form(...)`` parameters for. An uploaded file,
        if this action needs one, arrives as an ordinary multipart file
        part and is saved into this job's own output directory before
        ``run_ui_action`` ever sees it — that directory is created here
        (via a job id generated up front) specifically so the upload has
        somewhere durable to live before the job even starts.
        """
        _require_unlocked(request)
        professor = _validated_professor(professor)
        try:
            fields = json.loads(fields_json)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid fields_json: {e}") from e
        if not isinstance(fields, dict):
            raise HTTPException(400, "fields_json must decode to a JSON object.")

        job_id = jobs.new_job_id()
        if file is not None and file.filename:
            output_dir = jobs.job_output_dir(professor, job_id)
            upload_path = output_dir / file.filename
            data = await file.read()
            with open(upload_path, "wb") as f:
                f.write(data)
            fields["file_path"] = str(upload_path)
            fields["file_name"] = file.filename

        store = conversation.ConversationStore(professor)
        try:
            job = jobs.start_job(
                plugins=_get_plugins(), action_id=action_id, fields=fields,
                professor=professor, model=model, conversation_id=conversation_id,
                conversation_store=store, job_store=_job_store, job_id=job_id,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

        return {"job_id": job.id, "status": job.status}

    @app.get("/api/conversations/{conversation_id}/job-outputs/{job_id}")
    async def api_download_job_output(
        request: Request, conversation_id: str, job_id: str, professor: str
    ):
        """Download a finished job's one output file.

        Looks the file up server-side from the conversation's own
        ``job_result`` message rather than trusting a path from the
        browser — see ``Message.output_path``'s docstring in
        conversation.py.
        """
        _require_unlocked(request)
        professor = _validated_professor(professor)
        store = conversation.ConversationStore(professor)
        conv = store.load(conversation_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found.")
        match = next(
            (m for m in conv.messages if m.kind == "job_result" and m.job_id == job_id), None
        )
        if match is None or not match.output_path or not os.path.exists(match.output_path):
            raise HTTPException(404, "Job output not found.")
        return FileResponse(match.output_path, filename=match.output_filename)

    @app.post("/api/chat")
    async def api_chat(request: Request, body: ChatBody):
        """Stream one chat turn back to the browser as Server-Sent Events.

        Emits a ``{"type": "delta", "text": ...}`` event per piece of new
        text as the model generates it, then exactly one final event: either
        ``{"type": "done", "conversation": {...}}`` with the fully updated
        conversation, or ``{"type": "error", "message": ...}`` if the model
        call failed. A plain ``fetch()`` + stream reader on the front end
        parses these — not the browser's native ``EventSource``, since that
        only supports GET requests and this needs a POST body.
        """
        _require_unlocked(request)
        professor = _validated_professor(body.professor)
        store = conversation.ConversationStore(professor)
        conv = store.load(body.conversation_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found.")
        if conv.active_job_id:
            raise HTTPException(
                409,
                "A background job is running in this conversation — start a new "
                "conversation if you'd like to keep chatting while it finishes.",
            )
        if body.model:
            conv.model = body.model

        # An attached document's text rides along in api_content (what the
        # model actually reads) rather than content (what the transcript
        # shows) — see Message's docstring in conversation.py. The typed
        # message can be blank (attach a document and just hit send).
        msg_attachments: list = []
        api_content: str | None = None
        if body.attachment:
            msg_attachments = [
                conversation.Attachment(
                    filename=body.attachment.filename, char_count=body.attachment.char_count
                )
            ]
            doc_block = (
                f"[The professor attached a document named '{body.attachment.filename}'. "
                "Its extracted text follows, then their message below it.]\n\n"
                f"--- {body.attachment.filename} ---\n{body.attachment.text}\n"
                f"--- end of {body.attachment.filename} ---"
            )
            api_content = f"{doc_block}\n\n{body.message}" if body.message.strip() else doc_block

        conv.messages.append(
            conversation.Message(
                role="user",
                content=body.message,
                timestamp=datetime.now().isoformat(),
                attachments=msg_attachments,
                api_content=api_content,
            )
        )
        # Saved immediately, before the model has replied at all — unlike
        # the old one-shot version, a page refresh (or a failed call below)
        # no longer loses the message that was actually sent.
        store.save(conv)
        # Falls back to the attached filename when the professor attached a
        # document without typing anything alongside it, so a title-
        # generation failure still produces something more useful than an
        # empty string (see the "New conversation" fallback below).
        title_source = body.message.strip() or (body.attachment.filename if body.attachment else "")

        def event_stream():
            # A plain (non-async) generator, deliberately — everything in
            # this body is a blocking synchronous call (SandboxProcessor's
            # network requests to the AI gateway), and there's no real
            # awaiting to be done. Starlette's StreamingResponse detects
            # that this isn't an async generator and runs it in a background
            # thread via iterate_in_threadpool() instead of driving it
            # directly on the event loop. That distinction actually matters
            # here: an async generator with a blocking body never yields
            # control back to the loop between chunks, so the transport's
            # writes just pile up and all flush at once when the generator
            # finally finishes — which is exactly the "whole reply arrives
            # in one burst instead of streaming" bug this was.
            #
            # SandboxProcessor is imported here, not at module level, for an
            # unrelated reason: its class statement discovers every
            # installed plugin's registered mixins from sys.modules the
            # first time this module is imported — safe only once
            # load_plugins() has finished registering every plugin, which is
            # guaranteed by the time a request is being handled, but NOT
            # while this file itself is first executed during plugin loading
            # (see the module docstring above and plugins/prompt/plugin.py
            # for the same pattern).
            from src.runtime.sandbox_processor import SandboxProcessor

            final: dict | None = None
            try:
                sandbox = SandboxProcessor(professor, model=conv.model)
                for event in sandbox.chat_service.stream_message(conv.api_messages()):
                    if event["type"] == "delta":
                        yield f"data: {json.dumps(event)}\n\n"
                    else:
                        final = event
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Chat request failed: {e}'})}\n\n"
                return

            if final is None:
                # stream_message() always yields exactly one "done" event
                # once it stops iterating without raising — this only fires
                # if that contract is ever broken.
                yield f"data: {json.dumps({'type': 'error', 'message': 'Chat request failed: no reply received.'})}\n\n"
                return

            conv.messages.append(conversation.Message(
                role="assistant",
                content=final["content"],
                timestamp=datetime.now().isoformat(),
                model=final["model"],
                prompt_tokens=final["prompt_tokens"],
                completion_tokens=final["completion_tokens"],
                cost=final["cost"],
            ))
            if conv.title == "New conversation" and len(conv.messages) >= 2:
                # Ask the model for a real title first (see
                # ChatService.generate_title()'s docstring for why this is
                # deliberately a separate, cheap call rather than the
                # conversation's own possibly-expensive model) — only fall
                # back to the literal opening words if that call itself
                # failed for any reason. Uses display_messages() rather than
                # api_messages() so an attached document's full extracted
                # text isn't resent (and billed) just to name the chat —
                # a filename hint is enough context for a title.
                generated_title = sandbox.chat_service.generate_title(conv.display_messages())
                conv.title = generated_title or title_source.strip()[:60] or conv.title
            store.save(conv)

            yield f"data: {json.dumps({'type': 'done', 'conversation': conv.to_dict()})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            # X-Accel-Buffering only matters if this ever runs behind nginx
            # or another buffering reverse proxy (it doesn't today, as a
            # local single-user tool) — harmless to set regardless.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Startup sweep (docs/webui-plugin-plan.md section 10): a fresh process
    # always starts with an empty _job_store, so any conversation whose
    # active_job_id is still set from before this process started is
    # unambiguously orphaned — clear it now rather than leaving that
    # conversation's composer locked forever with nothing actually running.
    jobs.sweep_stale_jobs(
        list(load_professor_config().keys()),
        lambda professor: conversation.ConversationStore(professor),
    )

    return app


def run_server(host: str, port: int) -> None:
    """Start the local web interface and block until interrupted (Ctrl-C).

    Args:
        host: The network address to listen on. ``127.0.0.1`` (the default)
              means only this computer can reach it — see
              docs/webui-plugin-plan.md section 8 for what changes if this
              needs to be reachable from another device.
        port: The port to listen on.
    """
    import uvicorn
    uvicorn.run(create_app(), host=host, port=port)
