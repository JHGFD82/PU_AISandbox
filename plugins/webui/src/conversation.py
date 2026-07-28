"""Conversation data model and on-disk storage for the webui plugin.

Each professor's conversations live as one JSON file per conversation under
``data/conversations/{professor_netid}/{conversation_id}.json`` — see
docs/webui-plugin-plan.md section 6 for the full shape and reasoning
(one file per conversation, not one shared file, for the same
safe-to-sync-over-Dropbox reasons behind the external usage-data sources in
section 1, even though nothing about this data is expected to be shared
between installations today).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

def __getattr__(name: str):
    """Resolve the conversations directory on first use, not at import.

    Importing this module must not require knowing where the sandbox keeps
    its files: a freshly downloaded copy doesn't know yet, and the setup
    that would tell it cannot run if getting this far already failed.

    Tests that replace the name with a temporary directory still work —
    assigning it creates a real module attribute, which wins over this.
    """
    if name == "CONVERSATIONS_DIR":
        from src.paths import data_root
        return data_root() / "conversations"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _conversations_dir():
    """The conversations directory, honouring a test that replaced it."""
    replaced = globals().get("CONVERSATIONS_DIR")
    if replaced is not None:
        return replaced
    from src.paths import data_root
    return data_root() / "conversations"


# The exact shape new_conversation_id() produces below: the letters "c_"
# followed by 16 hexadecimal characters. Every id coming in from the browser
# is checked against this before being used as a filename — see
# ConversationStore._path().
_CONVERSATION_ID_RE = re.compile(r"c_[0-9a-f]{16}")


def new_conversation_id() -> str:
    """Generate a short, unique id for a new conversation, e.g. 'c_8f2a1c9de4b7a501'."""
    return "c_" + secrets.token_hex(8)


@dataclass
class Attachment:
    """A document attached to a message, for display purposes only.

    The document's extracted text itself lives in that message's
    ``api_content`` (what actually gets sent to the model), not here — this
    is just enough to show a small "📎 filename" chip in the conversation
    without the full extracted text cluttering the visible chat history.

    Args:
        filename: The original filename the professor uploaded.
        char_count: How many characters of text were extracted from it,
                    shown as a size hint next to the filename.
    """

    filename: str
    char_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Message:
    """One turn in a conversation — or, for a plugin background job, one status update about it.

    Args:
        role: Either ``'user'`` or ``'assistant'``.
        content: The message text as shown in the chat transcript. For a
                 message with attachments, this is just what the professor
                 typed — the attached document's text is not included here,
                 so the visible transcript stays readable.
        timestamp: When this message was sent, as an ISO-8601 string.
        model: Which model produced this message. ``None`` for user messages.
        prompt_tokens: Tokens the model read to produce this reply. ``None``
                       for user messages.
        completion_tokens: Tokens the model generated for this reply.
                            ``None`` for user messages.
        cost: What this turn cost, in dollars. ``None`` for user messages.
        attachments: Documents attached to this message, shown as small
                     chips in the UI. Empty for messages with no attachments.
        api_content: The text actually sent to the model for this turn, if
                     different from ``content`` — e.g. a user message with an
                     attachment sends the document's extracted text along
                     with the typed question, but only the typed question is
                     stored in ``content`` for display. ``None`` means
                     "identical to content", which is true for every message
                     without an attachment.
        kind: ``'message'`` (the default — an ordinary chat turn) or one of
              five states a plugin background job passes through:
              ``'job_progress'`` (a lightweight "page 3 of 12" status ping),
              ``'job_page'`` (one page/unit's actual translated text, shown
              as it finishes — see ``page_number``), ``'job_notice'`` (a
              one-off heads-up about how this job will behave, e.g. that
              per-page previews are off because it's running with more than
              one worker — informational, not an error), ``'job_result'``
              (the job's one finished output file, ready to download), or
              ``'job_error'`` (the job failed or was interrupted). See
              docs/webui-plugin-plan.md section 10 — job messages are
              deliberately excluded from ``api_messages()`` and
              ``display_messages()`` below, since they aren't real dialogue
              for the model to reason over. The webui's own chat transcript
              excludes ``'job_progress'`` too, rendering it in a dedicated
              progress bar under the composer instead — see
              ``progress_done``/``progress_total``. ``'job_page'`` messages
              *are* rendered as ordinary-looking bubbles (with a small page
              label), since seeing each page's actual output as it's
              produced is the point of them. ``'job_notice'`` also renders
              as a plain bubble (styled as a subdued aside, not an error),
              but without the copy/export row, since it's not content the
              professor produced or would want to reuse.
        job_id: Which background job this message reports on. ``None`` for
                an ordinary chat message.
        output_filename: For a ``'job_result'`` message, the filename to
                         show and offer for download. ``None`` otherwise.
        output_path: For a ``'job_result'`` message, where the finished file
                     was written, as an absolute server-side path.

                     **Kept for older conversations; not the way to find the
                     file.** An absolute path is a fact about one machine at
                     one moment, recorded permanently — it stops being true
                     the moment the folder is renamed, the data is moved, or
                     the same conversation is opened from a different
                     checkout. Renaming people to netIDs broke every one of
                     these at once, which is how the problem was found.

                     Use ``resolve_output_path()`` instead: it rebuilds the
                     location from the netID, the job id and
                     ``output_filename``, none of which move, and falls back
                     to this field only for records written before that
                     existed. Never sent back to the browser as something to
                     act on directly.
        progress_done: For a ``'job_progress'`` message, how many units of
                       work (pages, images) had finished as of this ping.
                       Kept as a separate number rather than only baked into
                       ``content``'s text, so the webui can render a real
                       percentage-width progress bar instead of parsing it
                       back out of a sentence. ``None`` otherwise.
        progress_total: For a ``'job_progress'`` message, the total number
                        of units of work this job expects to do. ``None``
                        otherwise.
        page_number: For a ``'job_page'`` message, which page/unit this is
                     (1-indexed, matching the page numbers already used in
                     error messages elsewhere in this project). ``None``
                     otherwise.
    """

    role: str
    content: str
    timestamp: str
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost: Optional[float] = None
    attachments: list[Attachment] = field(default_factory=list)
    api_content: Optional[str] = None
    kind: str = "message"
    job_id: Optional[str] = None
    output_filename: Optional[str] = None
    output_path: Optional[str] = None
    progress_done: Optional[int] = None
    progress_total: Optional[int] = None
    page_number: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        attachments = [Attachment(**a) for a in data.get("attachments", [])]
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=data["timestamp"],
            model=data.get("model"),
            prompt_tokens=data.get("prompt_tokens"),
            completion_tokens=data.get("completion_tokens"),
            cost=data.get("cost"),
            attachments=attachments,
            api_content=data.get("api_content"),
            kind=data.get("kind", "message"),
            job_id=data.get("job_id"),
            output_filename=data.get("output_filename"),
            output_path=data.get("output_path"),
            progress_done=data.get("progress_done"),
            progress_total=data.get("progress_total"),
            page_number=data.get("page_number"),
        )


@dataclass
class Conversation:
    """One saved conversation: its messages, current model, and (later) a compaction summary.

    Args:
        id: The conversation's unique id (see ``new_conversation_id()``).
        title: A short human-readable label, shown in the conversation list.
        created_at: When the conversation was first started, as an ISO-8601 string.
        updated_at: When the conversation was last added to, as an ISO-8601 string.
        model: The model currently selected for this conversation.
        messages: Every message exchanged so far, oldest first.
        compacted_summary: A condensed stand-in for older messages once the
                           conversation has grown past the model's context
                           window (see docs/webui-plugin-plan.md section 6).
                           ``None`` until compaction has happened at least once.
        active_job_id: The id of a plugin background job currently running
                       in this conversation (see docs/webui-plugin-plan.md
                       section 10), or ``None`` if no job is running. While
                       set, ``POST /api/chat`` on this conversation is
                       rejected and the composer is shown locked — a
                       conversation can only run one job at a time.
        temperature: This conversation's sampling-temperature override
                     (``0.0``-``2.0``), or ``None`` to use the selected
                     model's default. Persisted per-conversation the same
                     way ``model`` is, so it doesn't reset every time the
                     page is reloaded. Only meaningful for models that
                     accept it — see
                     ``src.models.catalog.model_has_fixed_parameters``.
        top_p: This conversation's nucleus-sampling override (``0.0``-``1.0``),
               or ``None`` for the model's default. Same persistence and
               model-support caveat as ``temperature``.
        max_tokens: This conversation's response-length cap override, or
                    ``None`` for the model's default. Same persistence as
                    ``temperature``, but (unlike temperature/top-p) every
                    model accepts a max-tokens cap of some kind.
    """

    id: str
    title: str
    created_at: str
    updated_at: str
    model: str
    messages: list[Message] = field(default_factory=list)
    compacted_summary: Optional[str] = None
    active_job_id: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Conversation":
        messages = [Message.from_dict(m) for m in data.get("messages", [])]
        return cls(
            id=data["id"],
            title=data["title"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            model=data["model"],
            messages=messages,
            compacted_summary=data.get("compacted_summary"),
            active_job_id=data.get("active_job_id"),
            temperature=data.get("temperature"),
            top_p=data.get("top_p"),
            max_tokens=data.get("max_tokens"),
        )

    def api_messages(self) -> list[dict[str, str]]:
        """Return this conversation's messages in the plain {role, content} shape the AI API expects.

        Uses each message's ``api_content`` in place of its displayed
        ``content`` when one is set — this is how an attached document's
        extracted text reaches the model on every turn (see ``Message``'s
        docstring) without that text ever being shown in the visible chat
        transcript.

        Skips any message whose ``kind`` isn't ``'message'`` — a job
        progress ping, result, or error isn't dialogue the model actually
        said or should treat as conversational context (see ``Message``'s
        docstring).
        """
        return [
            {"role": m.role, "content": m.api_content if m.api_content is not None else m.content}
            for m in self.messages
            if m.kind == "message"
        ]

    def display_messages(self) -> list[dict[str, str]]:
        """Return messages in the same {role, content} shape as api_messages(), but using only what's shown in the transcript.

        Unlike ``api_messages()``, this never substitutes in an attachment's
        full extracted text — it uses each message's plain ``content``, with
        a short ``[Attached: filename]`` hint appended when there were
        attachments. Meant for local, non-billed-by-the-full-document uses
        like title generation, where knowing a document was attached matters
        more than seeing every word of it. Also skips non-``'message'``
        entries, same as ``api_messages()``.
        """
        out: list[dict[str, str]] = []
        for m in self.messages:
            if m.kind != "message":
                continue
            content = m.content
            if m.attachments:
                names = ", ".join(a.filename for a in m.attachments)
                hint = f"[Attached: {names}]"
                content = f"{content}\n{hint}" if content else hint
            out.append({"role": m.role, "content": content})
        return out


class ConversationStore:
    """Reads and writes one professor's conversations under data/conversations/{professor}/."""

    def __init__(self, professor: str, base_dir: Optional[Path] = None) -> None:
        """Set up storage for one professor's conversations.

        Args:
            professor: The professor's safe-filename identifier (e.g. ``'heller'``).
            base_dir: Override for the conversations root directory. ``None``
                      (the normal case) uses ``data/conversations``;
                      redirected to a temporary directory in tests.
        """
        self.professor = professor
        self._dir = (base_dir if base_dir is not None else _conversations_dir()) / professor
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, conversation_id: str) -> Path:
        """Return the file this conversation is stored in, refusing anything malformed.

        Every id is checked against the exact shape
        ``new_conversation_id()`` produces before it is turned into a
        filename. This is a security check, not a tidiness one: an id
        arrives from the browser, and it is pasted straight into a file
        path. Without this, an id containing ``../`` would walk out of the
        professor's own folder — letting a request read, overwrite, or
        delete a ``.json`` file anywhere the server can reach.

        The check lives here, in the one place every read and write funnels
        through, rather than in each route that accepts an id. That way a
        route added later inherits the protection automatically instead of
        having to remember it.

        Raises:
            ValueError: If *conversation_id* isn't a well-formed id. Callers
                        that are simply looking something up (``load``,
                        ``delete``) catch this and report "not found"
                        instead, since a malformed id can't name a real
                        conversation anyway.
        """
        if not _CONVERSATION_ID_RE.fullmatch(conversation_id):
            raise ValueError(f"Malformed conversation id: {conversation_id!r}")
        return self._dir / f"{conversation_id}.json"

    def list_conversations(self) -> list[dict[str, Any]]:
        """Return a short summary of every saved conversation, newest first.

        Returns:
            A list of ``{'id', 'title', 'updated_at', 'model'}`` dicts, sorted
            by ``updated_at`` descending. Files that can't be read (e.g.
            corrupted JSON) are skipped rather than raising.
        """
        summaries = []
        for f in self._dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            summaries.append({
                "id": data.get("id", f.stem),
                "title": data.get("title", "Untitled conversation"),
                "updated_at": data.get("updated_at", ""),
                "model": data.get("model", ""),
            })
        summaries.sort(key=lambda s: s["updated_at"], reverse=True)
        return summaries

    def load(self, conversation_id: str) -> Optional[Conversation]:
        """Load one conversation by id, or None if it doesn't exist for this professor.

        A malformed id counts as "doesn't exist" rather than an error: it
        can't name a real conversation, and callers already handle ``None``
        by reporting that nothing was found. Answering the same way for a
        malformed id and a merely-absent one also avoids telling whoever
        sent it which of the two it was.
        """
        try:
            path = self._path(conversation_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        return Conversation.from_dict(json.loads(path.read_text()))

    def save(self, conversation: Conversation) -> None:
        """Write *conversation* to disk, updating its updated_at timestamp first.

        Written atomically (to a temp file in the same directory, then
        renamed into place) rather than with a direct ``write_text()``.
        This matters now that a background job (``jobs.py``) can be saving
        a conversation from its own thread — appending a progress message
        — at the same moment the browser polls ``GET
        /api/conversations/{id}`` and reads this same file: a plain
        ``write_text()`` truncates the file before writing the new
        content, so a read landing in that window sees a partial (often
        empty) file and fails to parse as JSON. ``os.replace()`` is atomic
        on the same filesystem — a reader always sees either the complete
        old file or the complete new one, never a half-written one.
        """
        conversation.updated_at = datetime.now().isoformat()
        path = self._path(conversation.id)
        tmp_path = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp_path.write_text(json.dumps(conversation.to_dict(), indent=2))
        os.replace(tmp_path, path)

    def create(self, model: str, title: str = "New conversation") -> Conversation:
        """Create, save, and return a brand new empty conversation."""
        now = datetime.now().isoformat()
        conv = Conversation(
            id=new_conversation_id(), title=title, created_at=now, updated_at=now,
            model=model, messages=[],
        )
        self.save(conv)
        return conv

    def delete(self, conversation_id: str) -> bool:
        """Delete one conversation by id. Returns True if it existed.

        A malformed id returns ``False`` (nothing deleted) for the same
        reason ``load()`` returns ``None`` — see its docstring.
        """
        try:
            path = self._path(conversation_id)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False
