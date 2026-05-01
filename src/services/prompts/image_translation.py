"""Prompt spec for combined image OCR + translation operations."""

from dataclasses import dataclass
from typing import Optional

from . import fragments as F


@dataclass
class ImageTranslationPromptSpec:
    """Parameters for an image transcribe-and-translate prompt pair.

    Call system_prompt() and user_prompt() to obtain the final strings.
    """

    source_language: str
    target_language: str
    vertical: bool = False
    spread: bool = False
    tables: bool = False
    system_note: Optional[str] = None
    user_note: Optional[str] = None

    def _script_note(self) -> str:
        return F.IMAGE_TRANSLATION_SCRIPT_GUIDANCE.get(self.source_language, "")

    def system_prompt(self) -> str:
        script_note = self._script_note()
        sections = [
            F.IMAGE_TRANSLATION_ROLE.format(source=self.source_language),
            ("SCRIPT NOTES:\n" + script_note) if script_note else None,
            F.IMAGE_TRANSLATION_VERTICAL_BLOCK if self.vertical else None,
            F.IMAGE_TRANSLATION_SPREAD_NOTE if self.spread else None,
            F.IMAGE_TRANSLATION_FORMAT_SPEC.format(
                source=self.source_language, target=self.target_language
            ),
            F.IMAGE_TRANSLATION_TRANSCRIPTION_RULES,
            F.IMAGE_TRANSLATION_TRANSLATION_RULES.format(target=self.target_language),
            F.TABLE_HINT_SYSTEM if self.tables else None,
            F.ADDITIONAL_INSTRUCTIONS.format(note=self.system_note) if self.system_note else None,
        ]
        return "\n\n".join(s for s in sections if s)

    def user_prompt(self) -> str:
        vertical_note = F.IMAGE_TRANSLATION_VERTICAL_NOTE if self.vertical else ""
        prompt = (
            "Transcribe all visible {source} text from this image exactly as it appears,"
            "{vertical_note} then translate it to {target}."
        ).format(
            source=self.source_language,
            target=self.target_language,
            vertical_note=vertical_note,
        )
        if self.tables:
            prompt += F.TABLE_HINT_USER
        if self.user_note:
            prompt += F.ADDITIONAL_NOTES.format(note=self.user_note)
        return prompt
