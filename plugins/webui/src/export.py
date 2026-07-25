"""Builds a downloadable transcript of a conversation.

Reuses this project's own document *writers* (``src/output/``) — the
inverse of ``attachments.py``, which reuses the document *readers* — so a
saved chat session can be handed to someone else, or kept as a record,
without reimplementing any formatting logic here. Those writers already
turn a Markdown table embedded in a reply into a real table in PDF/DOCX
output, so a table an assistant produced comes through intact rather than
as raw ``|---|---|`` text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.output.docx_builder import save_to_docx
from src.output.markdown_builder import save_to_markdown
from src.output.pdf_builder import save_to_pdf

# Maps each supported export format to (content-type, human file extension).
FORMATS: dict[str, tuple[str, str]] = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "pdf": ("application/pdf", "pdf"),
    "md": ("text/markdown", "md"),
}


class ExportError(Exception):
    """Raised when a conversation can't be exported — currently only for an unrecognized format."""


def build_transcript(conversation: Any) -> str:
    """Render a conversation's messages as a single, readable plain-text transcript.

    Args:
        conversation: The ``Conversation`` (see ``conversation.py``) to render.

    Returns:
        The full transcript as one string, with each turn as its own
        paragraph (blank-line separated) — ready to hand to any of this
        project's ``save_to_*`` output writers, which already know how to
        turn plain paragraphs (and any embedded Markdown tables) into a
        properly formatted document.
    """
    blocks: list[str] = [
        "Princeton University AI Sandbox — Conversation Transcript",
        (
            f"Title: {conversation.title}\n"
            f"Model: {conversation.model}\n"
            f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        ),
    ]
    for message in conversation.messages:
        speaker = "You" if message.role == "user" else "Assistant"
        header = f"{speaker} — {message.timestamp}"
        if message.model:
            header += f" · {message.model}"
        if message.cost is not None:
            header += f" · ${message.cost:.4f}"

        body = (message.content or "").strip() or "(empty message)"
        block = f"{header}\n{body}"
        for attachment in message.attachments:
            block += f"\n[Attached: {attachment.filename}, {attachment.char_count:,} characters]"
        blocks.append(block)

    return "\n\n".join(blocks).strip() + "\n"


def export_conversation(conversation: Any, fmt: str, output_path: str) -> None:
    """Write *conversation* to *output_path* as a formatted transcript.

    Args:
        conversation: The ``Conversation`` to export.
        fmt: One of ``'docx'``, ``'pdf'``, or ``'md'`` (see ``FORMATS``).
        output_path: Where to write the file. The caller is responsible for
                     choosing a path with a matching extension and cleaning
                     the file up afterward (e.g. after streaming it back as
                     a download).

    Raises:
        ExportError: If *fmt* isn't one of the supported formats.
    """
    if fmt not in FORMATS:
        supported = ", ".join(sorted(FORMATS))
        raise ExportError(f"Unsupported export format '{fmt}'. Supported: {supported}.")

    content = build_transcript(conversation)
    if fmt == "docx":
        save_to_docx(content, output_path, label="Conversation")
    elif fmt == "pdf":
        save_to_pdf(content, output_path, label="Conversation")
    else:
        save_to_markdown(content, output_path, label="Conversation")
