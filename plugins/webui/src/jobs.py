"""Background job runner for plugin actions started from the webui composer.

See ``docs/webui-plugin-plan.md`` section 10 for the full design. In short:
a plugin that declares a module-level ``ui_action`` (``src/runtime/ui_action.py``)
and implements ``run_ui_action`` can be triggered from a conversation's
composer (translate, transcribe, and any future plugin that opts in the
same way). Running it is a multi-minute, many-page operation, so it always
happens on a background thread rather than blocking an HTTP request — the
thread appends progress/result/error messages to the conversation as it
goes, and the conversation is "locked" (``active_job_id`` set) to ordinary
chat for as long as it runs.

Registered by ``plugins/webui/plugin.py`` into ``sys.modules`` under the
flat, dot-free name ``_pu_webui_jobs`` — same convention as this plugin's
other internal-only files (``auth.py``, ``conversation.py``, ...), and for
the same reason: see ``app.py``'s module docstring for why a real relative
import doesn't work here.

Job state (the ``JobStore`` below) is deliberately **in-memory only, not
persisted to disk** — your call, recorded in docs/webui-plugin-plan.md
section 10: a webui restart mid-job loses that job's thread and its
tracking entry together, and there's no attempt to resume a job from where
it left off. What restart behavior *does* guarantee: ``create_app()``'s
startup sweep (in ``app.py``) clears any conversation whose
``active_job_id`` survived from before the restart (since it lives on the
conversation, which *is* persisted) and leaves a visible "this job was
interrupted" message, rather than leaving that conversation's composer
locked forever with nothing actually running. The way to pick up
interrupted work is the same page-range field every job form already has
— rerun from wherever it stopped, and combine the output files yourself.
"""

from __future__ import annotations

import logging
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)

conversation = sys.modules["_pu_webui_conversation"]

if TYPE_CHECKING:
    # For type checkers only — never imported at runtime. At runtime this
    # module is registered under the flat name "_pu_webui_conversation" (see
    # the module docstring), which a type checker has no way to follow back
    # to a file, so annotations mentioning ConversationStore resolved to
    # nothing. This import is by file position, which the checker can follow,
    # and the "if" guard means Python never executes it — so the flat-name
    # registration the plugin loader needs is untouched.
    from .conversation import ConversationStore

# plugins/webui/src/jobs.py -> repo root is four parents up (matches
# conversation.py's own CONVERSATIONS_DIR computation).
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CONVERSATIONS_DIR = _ROOT / "data" / "conversations"


def new_job_id() -> str:
    """Generate a short, unique id for a new job, e.g. 'job_4f2a1c9de4b7a501'."""
    return "job_" + uuid.uuid4().hex[:16]


def job_output_dir(professor: str, job_id: str, base_dir: Optional[Path] = None) -> Path:
    """Return the directory a job's one output file should be written into, creating it if needed.

    Lives alongside that professor's conversation storage
    (``data/conversations/{professor}/_job_outputs/{job_id}/``) rather than
    a system temp directory, so a finished job's output survives at least
    as long as the conversation that references it — a temp directory can
    be cleaned up by the OS at any time, which would silently break a
    "download this" link sitting in a saved conversation.

    Args:
        professor: The professor this job runs under.
        job_id: The job's unique id (see ``new_job_id()``).
        base_dir: Override for the conversations root directory. ``None``
                  (the normal case) uses ``data/conversations``; redirected
                  to a temporary directory in tests.

    Returns:
        The absolute path to the (now-existing) output directory.
    """
    base = base_dir if base_dir is not None else _CONVERSATIONS_DIR
    d = base / professor / "_job_outputs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Job:
    """One background plugin job's tracked state, for the lifetime of this process only.

    Args:
        id: The job's unique id.
        professor: Which professor's API key/budget this job runs under.
        conversation_id: The conversation this job was started from, and
                         where its progress/result/error messages land.
        action_id: The ``UiAction.id`` this job is running (e.g.
                   ``'translate'``).
        status: ``'running'``, ``'done'``, or ``'error'``.
        error: The error message, if ``status`` is ``'error'``. ``None``
               otherwise.
        created_at: When this job was started, as an ISO-8601 string.
    """

    id: str
    professor: str
    conversation_id: str
    action_id: str
    status: str = "running"
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class JobStore:
    """In-memory, process-lifetime registry of background jobs.

    See this module's docstring for why this is deliberately not persisted
    — a fresh process always starts with an empty store, which is exactly
    what makes ``app.py``'s startup sweep able to treat "any conversation
    with an ``active_job_id`` right now" as unambiguously stale.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def set_status(self, job_id: str, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = status
                job.error = error


def find_plugin_for_action(plugins: dict, action_id: str) -> Optional[Any]:
    """Return the installed plugin whose declared ``ui_action.id`` matches *action_id*.

    Args:
        plugins: The command-name-to-plugin mapping ``load_plugins()``
                 returns — the same dict this project already builds for
                 CLI dispatch, reused here since it's already "every
                 installed plugin," just keyed by command name instead of
                 by action id.
        action_id: The ``UiAction.id`` requested (e.g. ``'translate'``).

    Returns:
        The plugin object, or ``None`` if no installed plugin declares a
        ``ui_action`` with this id.
    """
    seen: set[int] = set()
    for p in plugins.values():
        if id(p) in seen:
            continue
        seen.add(id(p))
        action = getattr(p, "ui_action", None)
        if action is not None and action.id == action_id:
            return p
    return None


def list_ui_actions(plugins: dict) -> list[Any]:
    """Return every distinct plugin-declared ``UiAction``, for the composer's action picker.

    Args:
        plugins: The command-name-to-plugin mapping ``load_plugins()``
                 returns.

    Returns:
        One ``UiAction`` per *distinct declared action*, in no particular
        order.

    Note:
        Deduplicates by the resolved ``UiAction`` object's own identity, not
        by the plugin object's — a plugin registered under more than one
        command (e.g. the transcription plugin owns both ``transcribe``
        and ``transcription_review``) can be wrapped in a *different*
        ``DispatchPlugin`` instance per command whenever a language
        extension plugin is installed (see ``dispatch_plugin.py``'s
        ``__getattr__``), so ``plugins.values()`` can yield two distinct
        wrapper objects that both proxy to the very same ``ui_action``.
        Deduping by wrapper identity would miss that and list the same
        action twice; deduping by the action itself doesn't.
    """
    seen: set[int] = set()
    actions = []
    for p in plugins.values():
        action = getattr(p, "ui_action", None)
        if action is None or id(action) in seen:
            continue
        seen.add(id(action))
        actions.append(action)
    return actions


def start_job(
    *,
    plugins: dict,
    action_id: str,
    fields: dict,
    professor: str,
    model: Optional[str],
    conversation_id: str,
    conversation_store: "ConversationStore",
    job_store: JobStore,
    job_id: Optional[str] = None,
) -> Job:
    """Validate a job request and start it running on a background thread.

    Returns as soon as the thread is launched — this never blocks for the
    job's actual duration. The conversation is marked busy
    (``active_job_id`` set and saved) before this function returns, so a
    ``/api/chat`` call arriving immediately after still sees the lock.

    Args:
        plugins: The command-name-to-plugin mapping ``load_plugins()`` returns.
        action_id: Which plugin action to run (e.g. ``'translate'``).
        fields: The submitted form's values, keyed by each declared
                ``UiField.name`` — passed straight through to the plugin's
                ``run_ui_action``.
        professor: The professor whose API key/budget this job runs under.
        model: The model explicitly requested by the webui's model picker,
               or ``None`` for the plugin's configured default.
        conversation_id: The conversation this job was started from.
        conversation_store: Already constructed for *professor* by the caller.
        job_store: The process-wide ``JobStore`` to register this job in.
        job_id: Use this id instead of generating a new one. The caller
                needs this when a file has to be saved to this job's own
                output directory (``job_output_dir()``) *before* the job
                can be started — see ``app.py``'s ``/api/jobs`` route.
                ``None`` (the default) generates a fresh id here.

    Returns:
        The new ``Job``, with ``status == 'running'``.

    Raises:
        ValueError: If no installed plugin offers *action_id*.
        LookupError: If *conversation_id* doesn't exist for this professor.
        RuntimeError: If that conversation already has a job running.
    """
    plugin = find_plugin_for_action(plugins, action_id)
    if plugin is None or not hasattr(plugin, "run_ui_action"):
        raise ValueError(f"No installed plugin offers the '{action_id}' action.")

    conv = conversation_store.load(conversation_id)
    if conv is None:
        raise LookupError(f"Conversation '{conversation_id}' not found.")
    if conv.active_job_id:
        raise RuntimeError("This conversation already has a job running.")

    job = Job(
        id=job_id or new_job_id(), professor=professor,
        conversation_id=conversation_id, action_id=action_id,
    )
    job_store.add(job)

    conv.active_job_id = job.id
    conversation_store.save(conv)

    thread = threading.Thread(
        target=_run_job,
        args=(plugin, job, fields, professor, model, conversation_store, job_store),
        daemon=True,
    )
    thread.start()
    return job


def _run_job(
    plugin: Any,
    job: Job,
    fields: dict,
    professor: str,
    model: Optional[str],
    conversation_store: "ConversationStore",
    job_store: JobStore,
) -> None:
    """The background thread's body: run the plugin action and record what happened.

    Not called directly outside this module — see ``start_job()``.
    """

    # UiAction.progress_verb is a plain string a plugin sets itself (e.g.
    # "Translating"), not derived by mechanically appending "ing" to the
    # action id — "translate".capitalize() + "ing" produces the misspelled
    # "Translateing", which is exactly the bug this replaced. Falls back to
    # "Processing" if the plugin's ui_action doesn't set one (or, in the
    # unlikely case getattr fails entirely, doesn't have one at all).
    progress_verb = getattr(getattr(plugin, "ui_action", None), "progress_verb", None) or "Processing"

    # Per-page/per-image messages (on_page_text below) only ever arrive with
    # a single worker — both translate's and transcribe's sequential-vs-
    # parallel split can't guarantee completion order once more than one
    # worker is running, so each silences its own per-item callback entirely
    # above 1 worker (see translation_service.py and image_handler.py). Tell
    # the professor that up front, once, rather than leaving them wondering
    # why nothing is streaming in for a job they can see is still running.
    # This check is plugin-agnostic (just reads the submitted "workers"
    # field, if any) so it applies to any current or future action that
    # declares one — not every plugin does (transcription's transcription_review
    # doesn't, for instance), which is a no-op here.
    try:
        workers_requested = int(str(fields.get("workers", "")).strip() or 1)
    except ValueError:
        workers_requested = 1
    if workers_requested > 1:
        conv = conversation_store.load(job.conversation_id)
        if conv is not None:
            conv.messages.append(conversation.Message(
                role="assistant",
                content=(
                    "Live preview of each item is turned off while running with more than one "
                    "worker. The progress bar below will still update normally, and the finished "
                    "result will be here as soon as the job completes."
                ),
                timestamp=datetime.now().isoformat(),
                kind="job_notice",
                job_id=job.id,
            ))
            conversation_store.save(conv)

    def on_progress(done: int, total: int) -> None:
        conv = conversation_store.load(job.conversation_id)
        if conv is None:
            # Conversation was deleted mid-job — nowhere left to report to.
            return
        conv.messages.append(conversation.Message(
            role="assistant",
            content=f"{progress_verb}... {done} of {total} done.",
            timestamp=datetime.now().isoformat(),
            kind="job_progress",
            job_id=job.id,
            progress_done=done,
            progress_total=total,
        ))
        conversation_store.save(conv)

    def on_page_text(page_number: int, text: str) -> None:
        # A plugin (currently just translate) that reports on_page_text
        # sends this once per page as soon as its translation finishes —
        # see plugins/translation/plugin.py's run_ui_action and
        # PageTextCallback's docstring in src/runtime/ui_action.py for why
        # this is a separate callback from on_progress rather than the same
        # one carrying more data (on_progress only ever passes two ints).
        conv = conversation_store.load(job.conversation_id)
        if conv is None:
            return
        conv.messages.append(conversation.Message(
            role="assistant",
            content=text,
            timestamp=datetime.now().isoformat(),
            kind="job_page",
            job_id=job.id,
            page_number=page_number,
        ))
        conversation_store.save(conv)

    output_dir = job_output_dir(professor, job.id)

    try:
        result = plugin.run_ui_action(
            fields, professor, model, on_progress, str(output_dir), on_page_text=on_page_text,
        )
    except Exception as e:
        logger.error("Job %s (%s) failed: %s", job.id, job.action_id, e, exc_info=True)
        job_store.set_status(job.id, "error", error=str(e))
        conv = conversation_store.load(job.conversation_id)
        if conv is not None:
            conv.messages.append(conversation.Message(
                role="assistant",
                content=f"The {job.action_id} job failed: {e}",
                timestamp=datetime.now().isoformat(),
                kind="job_error",
                job_id=job.id,
            ))
            conv.active_job_id = None
            conversation_store.save(conv)
        return

    job_store.set_status(job.id, "done")
    try:
        conv = conversation_store.load(job.conversation_id)
        if conv is not None:
            conv.messages.append(conversation.Message(
                role="assistant",
                content=result.summary,
                timestamp=datetime.now().isoformat(),
                kind="job_result",
                job_id=job.id,
                output_filename=result.output_filename,
                output_path=result.output_path,
                # Same fields a chat turn's reply carries — reused here
                # (rather than left None) so the webui's existing "model ·
                # cost" meta line under an assistant bubble also shows up
                # for a finished job, letting a professor see whether e.g.
                # a translation's page-by-page calls used their full
                # response budget. None for a plugin that doesn't report
                # usage on its UiJobResult.
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost=result.cost,
            ))
            conv.active_job_id = None
            if conv.title == "New conversation":
                # No AI-generated title for a job-only conversation — unlike
                # chat's generate_title(), that would be a whole extra billed
                # API call just to name something the job's own summary
                # already describes in plain language.
                conv.title = result.summary.strip()[:60] or conv.title
            conversation_store.save(conv)
    except Exception as e:
        # The plugin's own work already finished successfully by this point
        # (run_ui_action returned a result) — this only covers a failure
        # while *recording* that result. Without this, an error here (a
        # disk hiccup, a permissions issue, anything) would propagate out of
        # this background thread silently: active_job_id would never get
        # cleared, no job_result would ever appear, and no job_error would
        # appear either, since that path only runs when run_ui_action itself
        # raises. The conversation would stay locked forever with nothing
        # to show for it and nothing in the logs pointing at why — exactly
        # the "results never showed up" symptom this closes off.
        logger.error(
            "Job %s (%s) succeeded but recording its result failed: %s",
            job.id, job.action_id, e, exc_info=True,
        )
        job_store.set_status(job.id, "error", error=f"Result recording failed: {e}")
        try:
            conv = conversation_store.load(job.conversation_id)
            if conv is not None:
                conv.messages.append(conversation.Message(
                    role="assistant",
                    content=(
                        f"The {job.action_id} job finished, but saving its result to this "
                        f"conversation failed ({e}). The output file may still exist on the "
                        "server even though it isn't linked here."
                    ),
                    timestamp=datetime.now().isoformat(),
                    kind="job_error",
                    job_id=job.id,
                ))
                conv.active_job_id = None
                conversation_store.save(conv)
        except Exception:
            # Two independent save failures in a row — nothing more we can
            # safely do without risking further corruption. The conversation
            # stays locked, but this is now loud in the logs at least.
            logger.error(
                "Job %s (%s): also failed while trying to record that failure.",
                job.id, job.action_id, exc_info=True,
            )


def sweep_stale_jobs(professors: list[str], conversation_store_factory) -> int:
    """Clear any conversation left pointing at a job from before this process started.

    Called once, from ``create_app()`` at webui startup. Because
    ``JobStore`` is always empty at the start of a fresh process (see this
    module's docstring), *any* conversation whose ``active_job_id`` is set
    when this runs must be left over from a job that never got to finish
    — its background thread died along with the previous process. Without
    this sweep, that conversation's composer would stay locked forever,
    since nothing would ever clear the flag or supply a "done"/"error"
    message.

    Args:
        professors: Every professor's safe-name this installation knows about.
        conversation_store_factory: Called with one professor's safe name,
                                    returning a ``ConversationStore`` for
                                    them — a plain callable rather than the
                                    class directly, so tests can redirect
                                    storage without patching the class.

    Returns:
        How many conversations were found and cleared, for logging.
    """
    cleared = 0
    for professor in professors:
        store = conversation_store_factory(professor)
        for summary in store.list_conversations():
            conv = store.load(summary["id"])
            if conv is None or not conv.active_job_id:
                continue
            interrupted_job_id = conv.active_job_id
            conv.active_job_id = None
            conv.messages.append(conversation.Message(
                role="assistant",
                content=(
                    "This job was interrupted (the web server restarted while it was running) "
                    "and did not finish. If you need the rest of it, start a new job — the page-range "
                    "field lets you pick up from wherever this one left off."
                ),
                timestamp=datetime.now().isoformat(),
                kind="job_error",
                job_id=interrupted_job_id,
            ))
            store.save(conv)
            cleared += 1
    return cleared
