"""Prompt spec for OCR (transcription) operations."""

from dataclasses import dataclass, field
from typing import Optional

from . import ocr_fragments as F


@dataclass
class OcrPromptSpec:
    """Parameters for an OCR prompt pair (and optional refinement prompt).

    Call system_prompt(), user_prompt(), and refinement_prompt() to obtain
    the final strings.
    """

    target_language: str
    vertical: bool = False
    kanbun: bool = False
    kanbun_main: bool = False
    spread: bool = False
    tables: bool = False
    system_note: Optional[str] = None
    user_note: Optional[str] = None

    def _script_note(self) -> str:
        if self.kanbun_main:
            return F.KANBUN_MAIN_SCRIPT_NOTE
        if self.kanbun:
            return F.KANBUN_SCRIPT_NOTE
        return F.OCR_SCRIPT_GUIDANCE.get(self.target_language, "")

    def system_prompt(self) -> str:
        script_note = self._script_note()
        kanbun_note = (
            F.KANBUN_MAIN_OCR_NOTE if self.kanbun_main else
            F.KANBUN_OCR_NOTE if self.kanbun else
            None
        )
        sections = [
            F.OCR_SYSTEM_BASE.format(target=self.target_language),
            ("SCRIPT NOTES:\n" + script_note) if script_note else None,
            F.OCR_VERTICAL_BLOCK if self.vertical else None,
            F.OCR_SPREAD_NOTE if self.spread else None,
            F.ADDITIONAL_INSTRUCTIONS.format(note=kanbun_note) if kanbun_note else None,
            F.OCR_RULES,
            F.TABLE_HINT_SYSTEM if self.tables else None,
            F.ADDITIONAL_INSTRUCTIONS.format(note=self.system_note) if self.system_note else None,
        ]
        return "\n\n".join(s for s in sections if s)

    def user_prompt(self) -> str:
        script_note = self._script_note()
        if self.kanbun_main:
            base = F.OCR_USER_BASE_KANBUN_MAIN
        elif self.kanbun:
            base = F.OCR_USER_BASE_KANBUN
        else:
            base = F.OCR_USER_BASE.format(target=self.target_language)
        parts = [
            base,
            ("SCRIPT REMINDER: " + script_note) if script_note else None,
            F.OCR_VERTICAL_REINFORCEMENT if self.vertical else None,
            F.OCR_USER_RULES,
            F.TABLE_HINT_USER if self.tables else None,
            F.ADDITIONAL_NOTES.format(note=self.user_note) if self.user_note else None,
        ]
        return "\n\n".join(s for s in parts if s)

    def refinement_prompt(self) -> str:
        script_note = self._script_note()
        parts = [
            F.OCR_REFINEMENT_BASE,
            ("SCRIPT REMINDER: " + script_note) if script_note else None,
            F.OCR_VERTICAL_REINFORCEMENT if self.vertical else None,
        ]
        return "\n\n".join(s for s in parts if s)
