"""FastAPI application for the webui plugin: the browser-based chat interface.

Built by ``create_app()`` and started by ``run_server()``, both called from
``plugin.py``. One shared unlock gate, a professor switcher rather than
per-professor login, and the model/conversation/usage endpoints the
front-end in ``templates/chat.html`` calls.

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
import logging
import os
import secrets
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
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
from src.errors import CLIError
from src.models import (
    get_available_models,
    model_accepts_sampling_params,
    model_supports_vision,
    resolve_model,
)
from src.runtime.info_commands import list_optional_settings
from src.services.api_config import credential_path_for_endpoint
from src.settings import (
    CHAT_ROLE,
    ENDPOINTS,
    WEBUI_KEEP_SUPPLIED_DOCUMENTS,
    WEBUI_SESSION_COOKIE_NAME,
)
from src.tracking.token_tracker import TokenTracker

auth = sys.modules["_pu_webui_auth"]
file_picker = sys.modules["_pu_webui_file_picker"]
conversation = sys.modules["_pu_webui_conversation"]
attachments = sys.modules["_pu_webui_attachments"]
jobs = sys.modules["_pu_webui_jobs"]
export = sys.modules["_pu_webui_export"]

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# One backend instance for the life of the process — see auth.get_configured_backend().
_auth_backend = auth.get_configured_backend()

# Tracks wrong unlock attempts per computer. Process-lifetime, like the
# backend above: a restart forgets them, which is fine because restarting
# means access to the machine itself.
_attempt_limiter = auth.AttemptLimiter()

# One JobStore for the life of the process, same lifetime reasoning as
# _auth_backend above — see jobs.py's module docstring for why this is
# deliberately in-memory only.
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
    # Sampling overrides — None means "leave the conversation's current
    # value alone" (which itself may be None, meaning "use the model's
    # default"), the same one-sided-update pattern as `model` just above.
    # There's no separate "clear this override" signal yet; a professor
    # clears one by re-opening the options popover and blanking the field,
    # which the front end sends as an explicit None.
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    # Standing instructions for this conversation. Applied on the same
    # one-sided terms as the three above: whatever the options panel is
    # showing is what gets stored, so blanking the box clears it.
    system_prompt: str | None = None


class RenameConversationBody(BaseModel):
    professor: str
    title: str


class AddProfessorBody(BaseModel):
    netid: str
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


class SharedDraftBody(BaseModel):
    """The settings someone ticked in the guided editor.

    ``chosen`` maps a section to the settings to write live in it, each value as
    the TOML text the person typed (``0.2``, ``"developer"``, ``["gpt-4o"]``).
    Kept as text rather than parsed here so what they wrote is what gets
    checked, and a value TOML can't express is refused with their own input
    quoted back at them.
    """

    chosen: dict = {}


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


class PickPathBody(BaseModel):
    kind: str = "folder"
    start: str | None = None
    prompt: str = "Choose a folder"


# Dotted paths the /settings page may write directly via the generic
# value/generate/unset endpoints. webui.passphrase_hash is deliberately
# excluded — a passphrase must only ever be written pre-hashed, through the
# dedicated /api/settings/passphrase endpoint, never as a raw string via the
# generic one.
def _directly_editable_paths() -> set[str]:
    return {
        path for path, _label, _section, _secret in list_optional_settings()
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
        for path, _label, _section, _secret in list_optional_settings()
    }

    endpoints = []
    for api_name, raw in ENDPOINTS.items():
        cred_path = credential_path_for_endpoint(api_name)
        endpoints.append({
            "name": api_name,
            "display_name": raw.get("name", api_name),
            "base_url": raw.get("base_url", ""),
            # Same default as load_api_config(): true unless said otherwise.
            # A second, different default here would have this page describing
            # endpoints differently from the way they actually behave.
            "openai_compatible": bool(raw.get("openai_compatible", True)),
            "default_model": raw.get("default_model"),
            "timeout": raw.get("timeout", 30),
            "credential_path": cred_path,
            "key_set": field_status.get(cred_path, False),
        })

    sources = settings_store.get_configured_sources()

    return {
        "has_professors": has_professors,
        "order": _SETTINGS_ORDER_FIRST_RUN if not has_professors else _SETTINGS_ORDER_REPEAT,
        # Whether to draw the "Browse…" buttons at all. A computer with no
        # file chooser the sandbox can open gets typeable boxes and no
        # button, rather than a button that does nothing when pressed.
        "can_browse": file_picker.available(),
        "professors": [
            {
                "netid": netid,
                "name": prof["name"],
                "has_key": bool(prof.get("key")),
                "has_backup_key": bool(prof.get("backup_key")),
            }
            for netid, prof in professors.items()
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


def _validated_professor(netid: str | None) -> str:
    """Confirm *netid* is a professor configured in .env, or raise a 400 error.

    Every route that accepts a professor name from the browser calls this
    first — the value ends up in file paths (usage data, conversation
    storage), so it must be one of the professors this installation actually
    knows about, not arbitrary user input.
    """
    professors = load_professor_config()
    if not netid or netid not in professors:
        raise HTTPException(400, f"Unknown professor '{netid}'.")
    return netid


def _require_unlocked(request: Request) -> None:
    """Raise a 401 error unless this browser session has already unlocked the app."""
    if not request.session.get("unlocked"):
        raise HTTPException(401, "Not unlocked.")


# Addresses that mean the browser asking is on this same computer.
_SAME_COMPUTER = frozenset({"127.0.0.1", "::1", "localhost"})


def _require_same_computer(request: Request) -> None:
    """Raise a 403 error unless the browser making this request is on this computer.

    Only the file chooser needs this, and it needs it because of what it
    does: a window opens on the screen of whoever is running the server. If
    the browser is somewhere else — the sandbox started with ``--host
    0.0.0.0`` and reached from an iPad — then a "Browse…" button there would
    pop a window on someone else's desk and hand back a folder from a
    computer the person clicking has never seen. That isn't a browse button;
    it's a way of reading someone else's disk. So the button is refused
    rather than made to work.
    """
    client = request.client.host if request.client else None
    if client not in _SAME_COMPUTER:
        raise HTTPException(
            403,
            "The Browse button only works in a browser on the same computer as "
            "the sandbox. Type the path instead.",
        )


def _keep_supplied_document(
    professor: str, conversation_id: str, filename: str, data: bytes
) -> str | None:
    """Put a copy of a supplied document in its conversation's folder, if asked to.

    The text is read out of a document and kept in the conversation whatever
    this does; what this decides is whether the document *itself* is kept too,
    so that a conversation's folder holds the things it was given and not only
    what was read from them. Off unless someone turns it on — see
    ``keep_supplied_documents`` in the webui settings.

    Args:
        professor: Whose conversation this is, already validated.
        conversation_id: The conversation the document was supplied to. Empty
                         when the browser didn't say, in which case there is no
                         folder to put it in and nothing is kept.
        filename: The name the document was uploaded under.
        data: The document itself.

    Returns:
        The name it was saved under, or ``None`` if nothing was saved. A name
        already in use gets a number added rather than being overwritten:
        supplying two different documents that happen to share a name is
        ordinary, and losing the first one silently would not be.
    """
    if not WEBUI_KEEP_SUPPLIED_DOCUMENTS or not conversation_id:
        return None
    store = conversation.ConversationStore(professor)
    try:
        folder = store.attachments_dir(conversation_id)
    except ValueError:
        return None
    if store.load(conversation_id) is None:
        return None
    # basename() because the name comes from the browser: it is the one part of
    # this path not built from something already checked.
    name = os.path.basename(filename) or "document"
    folder.mkdir(parents=True, exist_ok=True)
    stem, suffix = os.path.splitext(name)
    candidate, n = name, 2
    while (folder / candidate).exists():
        candidate, n = f"{stem} ({n}){suffix}", n + 1
    try:
        (folder / candidate).write_bytes(data)
    except OSError as e:
        # A document that could not be filed away is a disappointment, not a
        # reason to lose the answer that was about to be given about it.
        logging.warning("Could not keep %s with conversation %s: %s", name, conversation_id, e)
        return None
    return candidate


def _chat_error_message(error: Exception) -> str:
    """Turn an exception from a chat turn into something safe to show in the browser.

    Two kinds of thing can go wrong during a chat turn, and they deserve
    different treatment:

    * Problems the professor can do something about — the model isn't
      available to them, the rate limit was hit, the API key was rejected.
      These are raised as ``CLIError`` precisely because their wording is
      meant to be read by the person using the tool (see
      ``src/services/api_errors.py``), so they're shown as-is.

    * Everything else: a bug, a broken configuration file, an unexpected
      response shape. The raw text of these is written for whoever maintains
      the installation, not for a professor, and it can quote back internal
      details such as file paths or the metadata a provider attached to its
      error. Those get a short, plain reply instead.

    Either way the full error and its traceback are written to the server log
    — which previously wasn't happening at all, so a failed chat turn left no
    trace anywhere for whoever had to explain it afterwards. Unrecognised
    errors are given a short reference code that appears both in the log line
    and in the browser message, so a professor can quote it and the person
    helping them can find the exact traceback.

    Args:
        error: Whatever the chat turn raised.

    Returns:
        The message to send to the browser.
    """
    if isinstance(error, CLIError):
        logging.warning("Chat turn failed with a user-facing error: %s", error)
        return str(error)

    reference = uuid.uuid4().hex[:8]
    logging.exception("Chat turn failed unexpectedly [ref %s]", reference)
    return (
        "Something went wrong on the server while answering — this is a fault in "
        "the sandbox, not something you did. Please try again. If it keeps "
        f"happening, quote reference {reference} to whoever looks after this "
        "installation; it will let them find the details in the server log."
    )


def create_app() -> FastAPI:
    """Build and configure the FastAPI application for the local web interface.

    Called once by ``run_server()``. Kept as a separate function (rather
    than a module-level ``app = FastAPI()``) so tests can build a fresh app
    instance without starting a real server.
    """
    app = FastAPI(title="Princeton AI Sandbox")

    # A random secret is fine for this session-cookie's purpose (signing,
    # not encrypting) — worst case on restart is everyone has to unlock
    # again. Set webui.session_secret in settings.toml (e.g. via
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
        from src.settings import PROMPT_MAX_TOKENS, PROMPT_TEMPERATURE, PROMPT_TOP_P

        return templates.TemplateResponse(
            request, "chat.html",
            # Whether to offer "Open this conversation's folder" at all. A
            # computer with no file browser to open gets no menu item, rather
            # than one that does nothing when pressed.
            {
                "can_reveal": file_picker.can_reveal(),
                # The actual numbers a message is sent with when a conversation
                # sets none of its own. Shown rather than described, because
                # "the model's default" names no value and is not even true —
                # the sandbox always sends these.
                "default_sampling": {
                    "temperature": PROMPT_TEMPERATURE,
                    "top_p": PROMPT_TOP_P,
                    "max_tokens": PROMPT_MAX_TOKENS,
                },
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        if not request.session.get("unlocked"):
            return templates.TemplateResponse(request, "unlock.html", {"error": None})
        return templates.TemplateResponse(request, "settings.html")

    @app.get("/shared-settings", response_class=HTMLResponse)
    async def shared_settings_page(request: Request):
        """The guided editor for whoever looks after a group's shared settings."""
        if not request.session.get("unlocked"):
            return templates.TemplateResponse(request, "unlock.html", {"error": None})
        return templates.TemplateResponse(request, "shared_settings.html")

    @app.post("/unlock")
    async def unlock(request: Request):
        # Repeated wrong guesses from one computer are slowed down, so the
        # passphrase can't simply be worked through one attempt at a time —
        # see AttemptLimiter in auth.py. Checked before the passphrase itself
        # so that a computer in its cooling-off period learns nothing about
        # whether its latest guess was right.
        wait = _attempt_limiter.seconds_remaining(request)
        if wait > 0:
            return templates.TemplateResponse(
                request,
                "unlock.html",
                {"error": f"Too many incorrect attempts. Please wait {wait} seconds and try again."},
                status_code=429,
            )
        ok = await _auth_backend.authenticate(request)
        if ok:
            _attempt_limiter.record_success(request)
            request.session["unlocked"] = True
            return RedirectResponse("/", status_code=303)
        _attempt_limiter.record_failure(request)
        logging.warning(
            "Failed webui unlock attempt from %s.",
            getattr(getattr(request, "client", None), "host", "unknown"),
        )
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
            "professors": [{"netid": k, "name": v["name"]} for k, v in professors.items()],
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

    # Deliberately not `async`: opening the chooser waits for a person to
    # finish looking through their files, and FastAPI gives an ordinary
    # function its own thread to do that on. Written as `async` it would
    # hold up every other request in the browser until the window was
    # answered.
    @app.post("/api/pick-path")
    def api_pick_path(request: Request, body: PickPathBody):
        """Open this computer's file chooser and report back what was picked."""
        _require_unlocked(request)
        _require_same_computer(request)
        try:
            chosen = file_picker.choose(
                kind=body.kind, start=body.start, prompt=body.prompt
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except file_picker.PickerUnavailable as e:
            raise HTTPException(503, str(e)) from e
        # No path and no error means the window was closed without choosing,
        # which is an answer in itself — the page leaves the box alone.
        return {"path": str(chosen) if chosen else None, "cancelled": chosen is None}

    @app.post("/api/settings/professors")
    async def api_add_professor(request: Request, body: AddProfessorBody):
        _require_unlocked(request)
        try:
            netid = settings_store.add_professor(body.netid, body.name, body.key, body.backup_key)
        except (ValueError, CLIError) as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "netid": netid}

    @app.delete("/api/settings/professors/{netid}")
    async def api_remove_professor(request: Request, netid: str):
        _require_unlocked(request)
        try:
            removed_name = settings_store.remove_professor(netid)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True, "removed": removed_name}

    @app.post("/api/settings/professors/{netid}/key")
    async def api_set_professor_key(request: Request, netid: str, body: ProfessorKeyBody):
        _require_unlocked(request)
        try:
            settings_store.set_professor_key(netid, body.key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/settings/professors/{netid}/backup-key")
    async def api_set_professor_backup_key(
        request: Request, netid: str, body: ProfessorBackupKeyBody
    ):
        _require_unlocked(request)
        try:
            settings_store.set_professor_backup_key(netid, body.backup_key)
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

    @app.get("/api/settings/shared-inventory")
    async def api_shared_settings_inventory(request: Request):
        """Every setting a group could share, and what their file already says.

        What the guided editor is built from: the same gathering the plain draft
        comes from, as data rather than a file, so it can be shown as a form
        instead of a hundred commented lines to read through.
        """
        _require_unlocked(request)
        from src.paths import PACKAGE_ROOT
        from src.shared_settings import inventory

        configured = settings_store.get_shared_settings_path()
        existing = configured if configured is not None and configured.exists() else None
        return {
            "sections": inventory(
                plugins_dir=PACKAGE_ROOT / "plugins",
                package_defaults=PACKAGE_ROOT / "settings.default.toml",
                existing=existing,
            ),
            "existing_path": str(existing) if existing is not None else None,
        }

    @app.post("/api/settings/shared-draft")
    async def api_build_shared_settings(request: Request, body: SharedDraftBody):
        """Build a shared settings file from what someone chose in the editor.

        Handed back to download, never saved: the sandbox does not write shared
        settings files, and has no idea where a group keeps theirs.
        """
        _require_unlocked(request)
        from src.paths import PACKAGE_ROOT
        from src.shared_settings import render_chosen

        try:
            text = render_chosen(
                plugins_dir=PACKAGE_ROOT / "plugins",
                package_defaults=PACKAGE_ROOT / "settings.default.toml",
                chosen=body.chosen,
            )
        except ValueError as error:
            # A value the person typed, so the message is for them.
            raise HTTPException(400, str(error)) from error
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="shared-settings.toml"'},
        )

    @app.get("/api/settings/shared-draft")
    async def api_shared_settings_draft(request: Request):
        """Hand back a shared settings draft to download.

        The same file ``python main.py settings export-shared`` writes, for
        whoever looks after a group's settings but doesn't work at a terminal.
        Downloaded rather than saved anywhere: the sandbox never writes a shared
        settings file, and it has no idea where this group keeps theirs.

        Carries across whatever the shared file already decides, if one is
        configured, and marks anything that has appeared since — the same as
        ``--from`` on the command line, which is what makes coming back for a
        second draft worth doing.
        """
        _require_unlocked(request)
        from src.paths import PACKAGE_ROOT
        from src.shared_settings import build_shared_settings

        configured = settings_store.get_shared_settings_path()
        existing = configured if configured is not None and configured.exists() else None
        text = build_shared_settings(
            plugins_dir=PACKAGE_ROOT / "plugins",
            package_defaults=PACKAGE_ROOT / "settings.default.toml",
            existing=existing,
        )
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="shared-settings.toml"'},
        )

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
        models = [
            {
                "name": m,
                "supports_vision": model_supports_vision(m),
                # Whether this model accepts temperature/top-p at all — some
                # reasoning models refuse them entirely (see
                # model_accepts_sampling_params's docstring). The front end
                # uses this to hide those two controls rather than let a
                # professor set them and have the request fail.
                "accepts_sampling_params": model_accepts_sampling_params(m),
            }
            for m in names
        ]
        # Which model a new conversation starts on. Resolved from the catalog's
        # config.defaults rather than named here, so that a provider retiring a
        # model doesn't leave every professor picking a new default by hand:
        # the next choice in the list takes over, and failing that the cheapest
        # model that can read an image (a chat question may carry a document).
        try:
            default = resolve_model(role=CHAT_ROLE)
        except ValueError:
            # No vision-capable model in the catalog at all. Better to open on
            # something than to refuse to render the picker.
            default = names[0] if names else None
        return {"models": models, "default": default}

    @app.get("/api/usage")
    async def api_usage(request: Request, professor: str):
        _require_unlocked(request)
        _validated_professor(professor)
        tracker = TokenTracker(professor=professor)
        # The same answer the terminal report prints, from the same method,
        # rather than a second copy of the arithmetic here. The copy this
        # replaced read the budget from the global setting instead of from
        # this professor's tracker, and left out the two warning flags — so
        # the sidebar had no way to say someone was over budget.
        budget = tracker.get_monthly_budget_status()
        return {
            "month": budget["monthly_usage"],
            "all_time": tracker.get_all_time_usage(),
            "model_usage": tracker.usage_data.get("model_usage", {}),
            # Alternate services, each on its own, carrying tokens and no money.
            # Kept out of the figures above on purpose: those are what the
            # university is billed for and what the budget is measured against.
            # Usually empty — only someone using an endpoint of their own from
            # the command line has anything here.
            "endpoint_usage": tracker.usage_data.get("endpoint_usage", {}),
            "budget": {
                "monthly_limit": tracker.monthly_limit,
                "usage_percentage": budget["usage_percentage"],
                "remaining_budget": budget["remaining_budget"],
                "is_exceeded": budget["is_exceeded"],
                "approaching_limit": budget["approaching_limit"],
            },
        }

    @app.post("/api/conversations/{conversation_id}/reveal")
    async def api_reveal_conversation_folder(
        conversation_id: str, request: Request, professor: str
    ):
        """Open one conversation's folder in this computer's file browser.

        Everything belonging to the conversation is in there — the
        conversation itself, a readable note of the settings that produced it,
        the documents supplied to it, and any files a job produced — so that a
        whole piece of work can be looked at, kept or cited as one thing.
        """
        _require_unlocked(request)
        _validated_professor(professor)
        _require_same_computer(request)
        store = conversation.ConversationStore(professor)
        if store.load(conversation_id) is None:
            raise HTTPException(404, "No such conversation.")
        folder = store.folder(conversation_id)
        if not file_picker.reveal(folder):
            raise HTTPException(
                500,
                "This computer could not open a file browser. The folder is at "
                f"{folder}",
            )
        return {"opened": True, "path": str(folder)}

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
        model = body.model or resolve_model(role=CHAT_ROLE)
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
        request: Request, professor: str = Form(...), file: UploadFile = File(...),
        conversation_id: str = Form(""),
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

        kept = _keep_supplied_document(professor, conversation_id, file.filename, data)
        return {
            "filename": doc.filename, "text": doc.text, "char_count": doc.char_count,
            # Where the document itself was put, if it was kept at all. The
            # browser shows nothing for this; it is here so that what happened
            # to somebody's file is answerable rather than invisible.
            "saved_as": kept,
        }

    # ── Plugin composer actions ──────────────────────────────────────────────
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

    @app.get("/api/plugin-actions/{action_id}/extension-fields")
    async def api_plugin_action_extension_fields(request: Request, action_id: str, target_language: str = ""):
        """List any extra composer fields a language-extension plugin contributes for one destination language.

        See ``ExtensionUiHooks``'s docstring (``src/runtime/ui_action.py``):
        a language-extension plugin (e.g. an East-Asian translation or transcription extension)
        never gets its own composer entry, but can still register extra
        fields that appear as a subsection once its language is picked —
        this is the endpoint the composer polls (on every relevant-language
        change) to know what to render. ``action_id`` IS used here (unlike
        earlier, before both ``translate`` and ``transcribe`` used this
        mechanism) — the registry is keyed by ``(action_id, token)``, not
        by token alone, because two different actions can register the
        same language token for entirely unrelated fields (translate's
        Kanbun checkbox and transcribe's vertical/spread/passes fields both
        apply to ``'jp'``, for instance); passing the wrong action_id here
        would silently return the other action's fields instead. Always
        returns an empty list (never an error) when no extension is
        installed for the given action/language pair, which is the normal
        case for most installations.
        """
        _require_unlocked(request)
        from src.runtime.ui_action import get_extension_ui_fields
        fields = get_extension_ui_fields(action_id, target_language)
        return {"fields": [asdict(f) for f in fields]}

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
        files: list[UploadFile] = File(default_factory=list),
    ):
        """Start a plugin background job (translate/transcribe/...) in one conversation.

        The submitted form's non-file values arrive JSON-encoded in
        ``fields_json`` — which fields exist is entirely plugin-defined
        (see ``UiAction``/``UiField``), not a fixed set this route could
        declare individual ``Form(...)`` parameters for. Any uploaded files
        arrive as ordinary multipart file parts under the shared ``files``
        key.

        A browser sends the *contents* of a chosen file and never its
        location on disk, so those bytes have to be written down before a
        plugin can open them. They go to scratch space that the job deletes
        as soon as it finishes (see ``jobs.discard_scratch_dir()``), and
        never into the person's own data: the file already exists wherever
        they chose to keep it, and a second copy filed inside their usage
        history would grow with every job, forever, for no purpose either of
        them would recognise.

        One uploaded file puts its path in ``fields['file_path']``. Several
        — a professor picking a set of images, or a whole folder on a
        browser that supports it — arrive together in one directory, and
        ``fields['file_path']`` points at that directory instead: exactly
        the shape a plugin's ``run_ui_action`` already expects from a CLI
        user pointing ``-i`` at a folder (e.g.
        ``TranscriptionPlugin.run_ui_action``'s ``os.path.isdir(file_path)``
        branch). Either way, a plugin should read what it needs while it
        runs and not expect the files to still be there afterwards.
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
        # A multipart part with no filename isn't a file — an empty file input
        # posts one — so those are dropped here. Each surviving part is paired
        # with its name so that the filter stays visible further down, where
        # the name is used to build a path. Reading it back off the part there
        # would give something that might be missing, which is not what this
        # list contains.
        uploaded: list[tuple[UploadFile, str]] = [(f, f.filename) for f in files if f.filename]
        if uploaded:
            # A browser sends the *contents* of a chosen file, never its
            # location on disk — that boundary is deliberate and not
            # something a web page can ask past. So the bytes have to be
            # written somewhere for the plugin to open.
            #
            # Somewhere, but not here: they go to a scratch folder the
            # operating system already cleans up after, and the job deletes
            # it as soon as it finishes. Keeping every one by default would
            # mean each translated document quietly stored twice — once where
            # the professor put it, once inside their usage data, growing
            # forever, for no purpose either of them would recognise.
            #
            # Unless someone asks for it. Turning on keep_supplied_documents
            # puts a copy in the conversation's own folder as well, so that the
            # documents a piece of work started from sit with the conversation
            # and the results, and the whole of it can be kept or cited. Off
            # unless chosen, so nobody pays that cost without meaning to.
            upload_dir = Path(tempfile.mkdtemp(prefix="pu_webui_job_"))
            for part, filename in uploaded:
                data = await part.read()
                with open(upload_dir / os.path.basename(filename), "wb") as out:
                    out.write(data)
                _keep_supplied_document(professor, conversation_id, filename, data)
            fields["_scratch_dir"] = str(upload_dir)
            if len(uploaded) == 1:
                only_name = os.path.basename(uploaded[0][1])
                fields["file_path"] = str(upload_dir / only_name)
                fields["file_name"] = uploaded[0][1]
            else:
                fields["file_path"] = str(upload_dir)
                fields["file_name"] = f"{len(uploaded)} images"

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
        if match is None:
            raise HTTPException(404, "Job output not found.")
        # Rebuilt from the netID, job id and filename rather than read back
        # from the path recorded when the job ran — see resolve_output_path().
        # That path stops being true whenever the data folder moves, and
        # rebuilding also means the result can only ever land inside this
        # person's own job-output directory.
        path = jobs.resolve_output_path(
            professor, job_id, match.output_filename, match.output_path,
            conversation_id=conv.id,
        )
        if path is None or not path.exists():
            raise HTTPException(404, "Job output not found.")
        return FileResponse(str(path), filename=match.output_filename)

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
        # Unlike `model` above, these three are applied unconditionally,
        # not gated on truthiness — the options popover always sends the
        # sampling values it currently shows (any of which may legitimately
        # be None, meaning "nothing chosen here, use the sandbox's own"), so this
        # is how a professor clears a previously-set override rather than
        # only ever being able to add one.
        conv.temperature = body.temperature
        conv.top_p = body.top_p
        conv.max_tokens = body.max_tokens
        # Unlike the three above, this is changed only when the request
        # actually mentions it. Those are numbers the options panel always
        # sends, so treating "absent" as "clear it" costs nothing; standing
        # instructions are a paragraph somebody wrote, and a request that
        # simply didn't mention them — an older tab, a script, a page that
        # hasn't finished loading — would otherwise wipe them silently and
        # the conversation would quietly stop following them mid-way.
        # Sending it explicitly as blank or null still clears it, which is
        # what emptying the box does.
        if "system_prompt" in body.model_fields_set:
            conv.system_prompt = (body.system_prompt or "").strip() or None

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
                sandbox = SandboxProcessor(
                    professor, model=conv.model,
                    temperature=conv.temperature, top_p=conv.top_p, max_tokens=conv.max_tokens,
                )
                # Passed on every turn, not just the first. The model is given
                # the conversation afresh each time and remembers nothing of its
                # own, so instructions sent once would stop applying the moment
                # a second message was sent.
                for event in sandbox.chat_service.stream_message(
                    conv.api_messages(), system_prompt=conv.system_prompt
                ):
                    if event["type"] == "delta":
                        yield f"data: {json.dumps(event)}\n\n"
                    else:
                        final = event
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': _chat_error_message(e)})}\n\n"
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

    # Startup sweep: a fresh process
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
              means only this computer can reach it; anything else requires a
              passphrase to be set first (``webui set-passphrase``).
        port: The port to listen on.
    """
    import uvicorn
    uvicorn.run(create_app(), host=host, port=port)
