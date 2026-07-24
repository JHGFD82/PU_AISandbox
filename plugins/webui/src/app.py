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

import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from src.config import load_professor_config
from src.models import (
    DEFAULT_FALLBACK_MODEL,
    get_available_models,
    get_monthly_limit,
    model_supports_vision,
)
from src.settings import WEBUI_SESSION_COOKIE_NAME
from src.tracking.token_tracker import TokenTracker

auth = sys.modules["_pu_webui_auth"]
conversation = sys.modules["_pu_webui_conversation"]

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# One backend instance for the life of the process — see auth.get_configured_backend().
_auth_backend = auth.get_configured_backend()


class ActiveProfessorBody(BaseModel):
    professor: str


class NewConversationBody(BaseModel):
    professor: str
    model: Optional[str] = None


class ChatBody(BaseModel):
    professor: str
    conversation_id: str
    message: str
    model: Optional[str] = None


def _validated_professor(safe_name: Optional[str]) -> str:
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
    # again. Set WEBUI_SESSION_SECRET in .env to keep sessions alive across
    # restarts instead.
    secret = os.environ.get("WEBUI_SESSION_SECRET") or secrets.token_hex(32)
    app.add_middleware(
        SessionMiddleware, secret_key=secret, session_cookie=WEBUI_SESSION_COOKIE_NAME
    )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        if not request.session.get("unlocked"):
            return templates.TemplateResponse(request, "unlock.html", {"error": None})
        return templates.TemplateResponse(request, "chat.html")

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

        # Imported here, not at module level. SandboxProcessor's class
        # statement discovers every installed plugin's registered mixins
        # from sys.modules the first time this module is imported — safe
        # only once load_plugins() has finished registering every plugin,
        # which is guaranteed by the time a request is being handled, but
        # NOT while this file itself is first executed during plugin
        # loading (see the module docstring above and
        # plugins/prompt/plugin.py for the same pattern).
        from src.runtime.sandbox_processor import SandboxProcessor

        try:
            sandbox = SandboxProcessor(professor, model=conv.model)
            result = sandbox.chat_service.send_message(conv.api_messages())
        except Exception as e:
            raise HTTPException(502, f"Chat request failed: {e}") from e

        conv.messages.append(conversation.Message(
            role="assistant",
            content=result["content"],
            timestamp=datetime.now().isoformat(),
            model=result["model"],
            prompt_tokens=result["prompt_tokens"],
            completion_tokens=result["completion_tokens"],
            cost=result["cost"],
        ))
        if conv.title == "New conversation" and len(conv.messages) >= 2:
            conv.title = body.message.strip()[:60] or conv.title
        store.save(conv)

        return {"conversation": conv.to_dict()}

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
