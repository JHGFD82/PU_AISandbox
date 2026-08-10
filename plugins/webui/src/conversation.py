"""Conversation data model and on-disk storage for the webui plugin.

Each conversation is a folder of its own, holding one JSON file plus whatever
was attached to it or produced by it — one folder per conversation rather than
one file holding all of them, so that a sync service never has two computers
editing the same file.

Where those folders sit depends on the person:

- Normally ``data/conversations/{netid}/`` in this installation's own files
  folder, which holds everybody and so keeps each person under their netID.
- Where somebody has a shared folder set to record work into (``usage_path``
  with ``usage_mode = "shared-write"``, see ``src/settings_store.py``), their
  conversations go in ``conversations/`` inside it, beside the record of what
  their work cost. That folder is already theirs alone, so nothing in it is
  filed under a netID.

A folder set to read only is never written to, so conversations stay here for
somebody whose folder is only being watched.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def merge_messages(*lists: list["Message"]) -> list["Message"]:
    """Combine several versions of one conversation's messages into one.

    Two computers sharing a folder can each hold a version of the same
    conversation with something the other has not seen. Both are real, so both
    are kept: the same message appearing in more than one version is kept once,
    and the result is put back in the order things were said.

    A message is taken to be the same message when who said it, when, and what
    it said all match. There is no id to compare — two computers generate
    messages independently, and any id invented here would differ between them
    for the same message.

    Args:
        *lists: The message lists to combine, oldest-known version first.

    Returns:
        One list, in the order the messages were written.
    """
    seen: set[tuple[str, str, str]] = set()
    merged: list["Message"] = []
    for messages in lists:
        for message in messages:
            fingerprint = (message.role, message.timestamp, message.content)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            merged.append(message)
    # Steady rather than clever: two messages written in the same instant keep
    # the order they were found in, so a single computer's transcript is left
    # exactly as it was.
    merged.sort(key=lambda m: m.timestamp)
    return merged


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


def conversations_dir_for(professor: str) -> Path:
    """Return the folder holding one person's conversations.

    Their shared folder if they have one they work into, and this
    installation's own otherwise. See this module's opening description.

    Args:
        professor: Their netID.

    Returns:
        The folder. It may not exist yet; whoever writes into it makes it.
    """
    replaced = globals().get("CONVERSATIONS_DIR")
    if replaced is not None:
        # A test said where these go, and meant it for everybody.
        return Path(replaced) / professor

    from src.settings_store import get_shared_write_source

    shared = get_shared_write_source(professor)
    if shared is not None:
        return shared.resolved_path() / "conversations"

    from src.paths import data_root
    return data_root() / "conversations" / professor


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
              ``'job_error'`` (the job failed or was interrupted). Job messages are
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


def effective_sampling(conversation: "Conversation") -> dict[str, Any]:
    """Return the settings a message in this conversation is actually sent with.

    A conversation that sets none of these is not sent without them, and the
    model does not fall back to a preference of its own: the sandbox fills in
    its own values, from the ``[prompt]`` section of ``settings.default.toml``
    (and, for the response-length cap, whatever the model catalogue says that
    model can manage). So there is always a real number, and recording the word
    "default" instead of it would hide the one thing worth knowing — what the
    answer was actually produced with.

    Args:
        conversation: The conversation to describe.

    Returns:
        ``{'temperature', 'top_p', 'max_tokens'}``, each the value that would be
        sent right now. Values chosen for the conversation win; the rest are the
        sandbox's.
    """
    # Imported here rather than at the top: this module is the plain store for
    # conversations, and it should not drag the model catalogue in on import.
    from src.models.catalog import get_model_max_completion_tokens
    from src.settings import PROMPT_MAX_TOKENS, PROMPT_TEMPERATURE, PROMPT_TOP_P

    if conversation.max_tokens is not None:
        max_tokens: Any = conversation.max_tokens
    else:
        try:
            max_tokens = get_model_max_completion_tokens(conversation.model, PROMPT_MAX_TOKENS)
        except Exception:
            # A model no longer in the catalogue still had a conversation; the
            # sandbox's own figure is what it would fall back to.
            max_tokens = PROMPT_MAX_TOKENS
    return {
        "temperature": PROMPT_TEMPERATURE if conversation.temperature is None else conversation.temperature,
        "top_p": PROMPT_TOP_P if conversation.top_p is None else conversation.top_p,
        "max_tokens": max_tokens,
    }


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
                           window.
                           ``None`` until compaction has happened at least once.
        active_job_id: The id of a plugin background job currently running
                       in this conversation, or ``None`` if no job is running. While
                       set, ``POST /api/chat`` on this conversation is
                       rejected and the composer is shown locked — a
                       conversation can only run one job at a time.
        temperature: This conversation's sampling temperature
                     (``0.0``-``2.0``), or ``None`` to use the sandbox's own
                     — *not* the model's: a value is always sent, and when
                     this is ``None`` it comes from ``[prompt]`` in
                     ``settings.default.toml``. See ``effective_sampling()``.
                     Persisted per-conversation the same
                     way ``model`` is, so it doesn't reset every time the
                     page is reloaded. Only meaningful for models that
                     accept it — see
                     ``src.models.catalog.model_accepts_sampling_params``.
        top_p: This conversation's nucleus-sampling override (``0.0``-``1.0``),
               or ``None`` for the sandbox's own. Same persistence and
               model-support caveat as ``temperature``.
        max_tokens: This conversation's response-length cap override, or
                    ``None`` for the sandbox's own, which for this one is
                    whatever the catalogue says the model can manage. Same
                    persistence as
                    ``temperature``, but (unlike temperature/top-p) every
                    model accepts a max-tokens cap of some kind.
        system_prompt: Standing instructions for the model in this conversation
                       — "answer in French", "you are a palaeographer reading
                       19th-century German hands" — or ``None`` for none. Sent
                       ahead of the messages on *every* turn, not just the
                       first, so it keeps applying however long the
                       conversation runs; a model is given the whole
                       conversation afresh each time and remembers nothing on
                       its own. Kept per conversation, like the settings above,
                       so two conversations can have different instructions and
                       neither loses them on reload.
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
    system_prompt: Optional[str] = None

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
            system_prompt=data.get("system_prompt"),
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
            base_dir: Override for the folder every person's conversations sit
                      under, with this person's own inside it. ``None`` (the
                      normal case) works out where theirs belong — see
                      ``conversations_dir_for()``. Tests point it at a
                      temporary directory.
        """
        self.professor = professor
        self._dir = (base_dir / professor if base_dir is not None
                     else conversations_dir_for(professor))
        self._dir.mkdir(parents=True, exist_ok=True)
        self._move_loose_files_into_folders()

    def _move_loose_files_into_folders(self) -> None:
        """Put each conversation saved as a loose file into a folder of its own.

        Conversations used to be single files sitting side by side. They now
        each have a folder, so that everything belonging to one — the
        documents supplied to it, the files a job produced, the settings that
        produced them — can sit together where a person can find it.

        Done once, in place: the file is moved, not copied, so nothing is
        duplicated and nothing is left behind to be read by mistake. A
        conversation whose folder already exists is left alone. Once there
        are no loose files this costs one directory listing and does nothing.
        """
        for loose in self._dir.glob("c_*.json"):
            if not _CONVERSATION_ID_RE.fullmatch(loose.stem):
                continue
            destination = self._dir / loose.stem / "conversation.json"
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(loose, destination)

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
        return self.folder(conversation_id) / "conversation.json"

    def folder(self, conversation_id: str) -> Path:
        """Return the folder holding everything belonging to one conversation.

        Each conversation has a folder of its own, named by its id, holding
        the conversation itself, a readable note of the settings that
        produced it, the documents supplied to it, and any files a plugin
        job produced from it. The point is that the whole of a piece of work
        sits in one place a person can open, keep, or cite, rather than being
        spread between a file here and a job folder there.

        The id is checked against the exact shape ``new_conversation_id()``
        produces before it becomes part of a path. This is a security check,
        not a tidiness one: an id arrives from the browser and is pasted
        straight into a path, so without this an id containing ``../`` would
        walk out of this professor's own folder — letting a request read,
        overwrite or delete files anywhere the server can reach. It lives
        here, in the one place every read and write funnels through, so that
        a route added later inherits the protection rather than having to
        remember it.

        Raises:
            ValueError: If *conversation_id* isn't a well-formed id. Callers
                        simply looking something up (``load``, ``delete``)
                        catch this and report "not found" instead, since a
                        malformed id can't name a real conversation anyway.
        """
        if not _CONVERSATION_ID_RE.fullmatch(conversation_id):
            raise ValueError(f"Malformed conversation id: {conversation_id!r}")
        return self._dir / conversation_id

    def attachments_dir(self, conversation_id: str) -> Path:
        """Return the folder for documents supplied to one conversation."""
        return self.folder(conversation_id) / "attachments"

    def outputs_dir(self, conversation_id: str) -> Path:
        """Return the folder for files a plugin job produced in one conversation."""
        return self.folder(conversation_id) / "outputs"

    def list_conversations(self) -> list[dict[str, Any]]:
        """Return a short summary of every saved conversation, newest first.

        Returns:
            A list of ``{'id', 'title', 'updated_at', 'model'}`` dicts, sorted
            by ``updated_at`` descending. Files that can't be read (e.g.
            corrupted JSON) are skipped rather than raising.
        """
        summaries = []
        for f in self._dir.glob("c_*/conversation.json"):
            try:
                data = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            summaries.append({
                "id": data.get("id", f.parent.name),
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
        self._take_back_anything_a_sync_service_set_aside(path)
        return Conversation.from_dict(json.loads(path.read_text()))

    def _read_quietly(self, path: Path) -> Optional[Conversation]:
        """Read a conversation file, or return ``None`` if it cannot be read.

        Used where the answer is wanted but its absence is not a failure —
        reading what is already on disk before writing over it, where a file
        that is missing, half-synced or damaged should not stop the write that
        was actually asked for.
        """
        try:
            return Conversation.from_dict(json.loads(path.read_text()))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as e:
            logger.warning("Could not read %s before writing it: %s", path, e)
            return None

    def _take_back_anything_a_sync_service_set_aside(self, path: Path) -> None:
        """Fold in any copy of this conversation a sync service left beside it.

        Two computers sharing one folder can both write to a conversation
        before either has seen the other's version. Where one of them was
        offline at the time, the sync service notices when it reconnects,
        keeps one version under the real name, and renames the other —
        ``conversation (Machine B's conflicted copy 2026-08-10).json`` is
        Dropbox's wording, and OneDrive, Google Drive and Syncthing each have
        their own. Whatever the wording, this software only ever reads
        ``conversation.json``, so what was set aside became invisible: on disk,
        never shown, and never mentioned.

        So it is read and put back. The two are combined rather than one
        chosen, since both are things somebody actually said, and the copy is
        removed only once the combined version is safely written.

        A read that writes is not a thing to do lightly, and it is done here
        because there is nowhere better: the moment somebody opens a
        conversation is the moment their missing messages need to be in it.
        """
        set_aside = [f for f in sorted(path.parent.glob("conversation*.json"))
                     if f != path]
        if not set_aside:
            return

        try:
            merged = Conversation.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning("Cannot take back copies beside %s: %s", path, e)
            return

        taken: list[tuple[Path, int]] = []
        for other in set_aside:
            try:
                theirs = Conversation.from_dict(json.loads(other.read_text()))
            except (json.JSONDecodeError, OSError, KeyError) as e:
                logger.warning("Could not read %s, so it was left alone: %s", other.name, e)
                continue
            before = len(merged.messages)
            merged.messages = merge_messages(merged.messages, theirs.messages)
            # What this copy actually brought back, not the size of the result.
            taken.append((other, len(merged.messages) - before))

        if not taken:
            return
        self.save(merged)
        for other, gained in taken:
            logger.warning(
                "Took %d message(s) back from %s, which a sync service had set "
                "aside because two computers wrote to this conversation at "
                "once. That copy has been removed.", gained, other.name)
            other.unlink(missing_ok=True)

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
        path = self._path(conversation.id)
        # What is on disk may have gained messages since this copy was read —
        # another computer sharing this folder, or this one's own background
        # job. Writing the whole file from a copy read before those arrived
        # would drop them without a word, so they are read back in first.
        # Nothing in this software ever removes a message, only adds, which is
        # what makes combining the two always the right answer.
        on_disk = self._read_quietly(path)
        if on_disk is not None:
            conversation.messages = merge_messages(on_disk.messages, conversation.messages)

        conversation.updated_at = datetime.now().isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp_path.write_text(json.dumps(conversation.to_dict(), indent=2))
        os.replace(tmp_path, path)
        self._write_settings_note(conversation)

    def _write_settings_note(self, conversation: "Conversation") -> None:
        """Write a plain readable note of the settings that produced this conversation.

        Beside the conversation itself, so that someone opening the folder —
        or citing it, or handing it to a colleague — can see which model
        answered and under what instructions without opening a file meant for
        the program to read. Written afresh on every save, so it always
        describes the conversation as it now stands.
        """
        sent = effective_sampling(conversation)

        def line(label: str, key: str) -> str:
            # The value and nothing else. Who settled on it — this person, their
            # group, or the sandbox — is not what anyone reading an archive is
            # asking; they want to know what the answer was produced with.
            return f"{label:<22}{sent[key]}"

        lines = [
            f"Conversation: {conversation.title}",
            f"Reference:    {conversation.id}",
            f"Started:      {conversation.created_at}",
            f"Last updated: {conversation.updated_at}",
            "",
            f"Model:                {conversation.model}",
            line("Temperature:", "temperature"),
            line("Top-p:", "top_p"),
            line("Max response tokens:", "max_tokens"),
            "",
            "Instructions given for the whole conversation:",
            conversation.system_prompt or "  (none)",
            "",
        ]
        note = self.folder(conversation.id) / "settings.txt"
        tmp = note.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        os.replace(tmp, note)

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
            folder = self.folder(conversation_id)
        except ValueError:
            return False
        # The whole folder: deleting a conversation and leaving the documents
        # supplied to it behind would be a surprise, and an invisible one.
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            return True
        return False


# ── Moving them when the folder they belong in changes ──────────────────────
#
# Registered with core rather than known to it. Where somebody's work is kept
# is core's setting, but conversations are this plugin's idea, and core moving
# them would mean core knowing that the web interface exists — see
# `plugins/CLAUDE.md`. So this says how to move them and core says when.


def _move_conversations(professor, was, now):
    """Move one person's conversations to wherever their work now goes.

    Args:
        professor: Their netID.
        was: The shared folder their work was written to, or ``None`` for this
             installation's own folder.
        now: The same, as it is now.

    Returns:
        A ``Moved`` counting the conversations that went across, and naming
        any that stayed — one already at the destination under the same name
        is left alone rather than written over.
    """
    from src.paths import data_root
    from src.tracking.relocate import Moved, move_a_folder_of_things

    def where(source):
        if source is not None:
            return source.resolved_path() / "conversations"
        return data_root() / "conversations" / professor

    moved, left = move_a_folder_of_things(where(was), where(now))
    return Moved(counts={"conversations": moved} if moved else {}, left_behind=left)


def register_with_core() -> None:
    """Tell core to move conversations when somebody's folder changes.

    Called once, by the plugin, at startup. Doing it here rather than at import
    would register it again every time this module is reloaded, and the same
    conversations would be moved twice.
    """
    from src.tracking.relocate import register_mover

    register_mover(_move_conversations, "conversations")
