"""Prompt spec for transcription review operations."""

from dataclasses import dataclass
from typing import Optional

from . import ocr_fragments as F


@dataclass
class TranscriptionReviewPromptSpec:
    """Parameters for a transcription-review prompt pair.

    Call system_prompt() and user_prompt(text) to obtain the final strings.
    The model name is intentionally absent from the schema shown to the model;
    it is injected by TranscriptionReviewService after parsing the response.
    """

    language: str
    kanbun: bool = False
    kanbun_main: bool = False
    system_note: Optional[str] = None
    user_note: Optional[str] = None

    def system_prompt(self) -> str:
        kanbun_note = (
            F.TRANSCRIPTION_REVIEW_KANBUN_MAIN_NOTE if self.kanbun_main else
            F.TRANSCRIPTION_REVIEW_KANBUN_NOTE if self.kanbun else
            None
        )
        sections = [
            F.TRANSCRIPTION_REVIEW_ROLE.format(language=self.language),
            kanbun_note,
            F.TRANSCRIPTION_REVIEW_APPROACH,
            F.TRANSCRIPTION_REVIEW_SCHEMA,
            F.TRANSCRIPTION_REVIEW_RULES,
            F.ADDITIONAL_INSTRUCTIONS.format(note=self.system_note) if self.system_note else None,
        ]
        return "\n\n".join(s for s in sections if s)

    def user_prompt(self, text: str = "[transcription text would appear here]") -> str:
        parts = [
            F.TRANSCRIPTION_REVIEW_USER_BASE.format(language=self.language),
            F.TRANSCRIPTION_REVIEW_TEXT_BLOCK.format(text=text),
            F.ADDITIONAL_NOTES.format(note=self.user_note) if self.user_note else None,
        ]
        return "\n\n".join(s for s in parts if s)
