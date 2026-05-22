"""Prompt spec for OCR (transcription) operations — base plugin."""

from dataclasses import dataclass
from typing import Optional

from . import ocr_fragments as F


@dataclass
class OcrPromptSpec:
    """Parameters for an OCR prompt pair (and optional refinement prompt).

    Call system_prompt(), user_prompt(), and refinement_prompt() to obtain
    the final strings.

    This is the base spec. Extension plugins may add script-specific guidance
    and additional prompt controls.
    """

    target_language: str
    system_note: Optional[str] = None
    user_note: Optional[str] = None

    def system_prompt(self) -> str:
        sections = [
            F.OCR_SYSTEM_BASE.format(target=self.target_language),
            F.OCR_RULES,
            F.ADDITIONAL_INSTRUCTIONS.format(note=self.system_note) if self.system_note else None,
        ]
        return "\n\n".join(s for s in sections if s)

    def user_prompt(self) -> str:
        parts = [
            F.OCR_USER_BASE.format(target=self.target_language),
            F.OCR_USER_RULES,
            F.ADDITIONAL_NOTES.format(note=self.user_note) if self.user_note else None,
        ]
        return "\n\n".join(s for s in parts if s)

    def refinement_prompt(self) -> str:
        return F.OCR_REFINEMENT_BASE
