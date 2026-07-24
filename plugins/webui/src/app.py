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
import secrets
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

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

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# One backend instance for the life of the process — see auth.get_configured_backend().
_auth_backend = auth.get_configured_backend()

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


class ChatBody(BaseModel):
    professor: str
    conversation_id: str
    message: str
    model: str | None = None


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
        if body.model:
            conv.model = body.model
        conv.messages.append(
            conversation.Message(role="user", content=body.message, timestamp=datetime.now().isoformat())
        )
        # Saved immediately, before the model has replied at all — unlike
        # the old one-shot version, a page refresh (or a failed call below)
        # no longer loses the message that was actually sent.
        store.save(conv)
        title_source = body.message

        async def event_stream():
            # Imported here, not at module level. SandboxProcessor's class
            # statement discovers every installed plugin's registered mixins
            # from sys.modules the first time this module is imported —
            # safe only once load_plugins() has finished registering every
            # plugin, which is guaranteed by the time a request is being
            # handled, but NOT while this file itself is first executed
            # during plugin loading (see the module docstring above and
            # plugins/prompt/plugin.py for the same pattern).
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
                conv.title = title_source.strip()[:60] or conv.title
            store.save(conv)

            yield f"data: {json.dumps({'type': 'done', 'conversation': conv.to_dict()})}\n\n"

        return StreamingResponse(
            event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
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
