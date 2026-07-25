"""Conversation data model and on-disk storage for the webui plugin.

Each professor's conversations live as one JSON file per conversation under
``data/conversations/{professor_safe_name}/{conversation_id}.json`` — see
docs/webui-plugin-plan.md section 6 for the full shape and reasoning
(one file per conversation, not one shared file, for the same
safe-to-sync-over-Dropbox reasons behind the external usage-data sources in
section 1, even though nothing about this data is expected to be shared
between installations today).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# plugins/webui/src/conversation.py -> repo root is four parents up.
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONVERSATIONS_DIR = _ROOT / "data" / "conversations"


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
    """One turn in a conversation.

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
    """

    id: str
    title: str
    created_at: str
    updated_at: str
    model: str
    messages: list[Message] = field(default_factory=list)
    compacted_summary: Optional[str] = None

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
        )

    def api_messages(self) -> list[dict[str, str]]:
        """Return this conversation's messages in the plain {role, content} shape the AI API expects.

        Uses each message's ``api_content`` in place of its displayed
        ``content`` when one is set — this is how an attached document's
        extracted text reaches the model on every turn (see ``Message``'s
        docstring) without that text ever being shown in the visible chat
        transcript.
        """
        return [
            {"role": m.role, "content": m.api_content if m.api_content is not None else m.content}
            for m in self.messages
        ]

    def display_messages(self) -> list[dict[str, str]]:
        """Return messages in the same {role, content} shape as api_messages(), but using only what's shown in the transcript.

        Unlike ``api_messages()``, this never substitutes in an attachment's
        full extracted text — it uses each message's plain ``content``, with
        a short ``[Attached: filename]`` hint appended when there were
        attachments. Meant for local, non-billed-by-the-full-document uses
        like title generation, where knowing a document was attached matters
        more than seeing every word of it.
        """
        out: list[dict[str, str]] = []
        for m in self.messages:
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
        self._dir = (base_dir if base_dir is not None else CONVERSATIONS_DIR) / professor
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, conversation_id: str) -> Path:
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
        """Load one conversation by id, or None if it doesn't exist for this professor."""
        path = self._path(conversation_id)
        if not path.exists():
            return None
        return Conversation.from_dict(json.loads(path.read_text()))

    def save(self, conversation: Conversation) -> None:
        """Write *conversation* to disk, updating its updated_at timestamp first."""
        conversation.updated_at = datetime.now().isoformat()
        self._path(conversation.id).write_text(json.dumps(conversation.to_dict(), indent=2))

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
        """Delete one conversation by id. Returns True if it existed."""
        path = self._path(conversation_id)
        if path.exists():
            path.unlink()
            return True
        return False
