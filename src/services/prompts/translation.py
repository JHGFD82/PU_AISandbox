"""Prompt spec for translation operations."""

from dataclasses import dataclass
from typing import Optional

from . import fragments as F


@dataclass
class TranslationPromptSpec:
    """Parameters for a translation prompt pair.

    Call system_prompt() and user_prompt() to obtain the final strings.
    """

    source_language: str
    target_language: str
    output_format: str = "console"
    has_numbered: bool = False
    context_type: str = "none"  # "none" | "abstract" | "previous_page"
    kanbun: bool = False
    system_note: Optional[str] = None
    user_note: Optional[str] = None
    has_table_markers: bool = False
    tables: bool = False
    toc: bool = False

    def _formatting_fragment(self) -> str:
        key = "file" if self.output_format.lower() in ("pdf", "txt", "file", "docx") else "console"
        return F.TRANSLATION_FORMATTING[key]

    def _pair_note(self) -> str:
        return F.LANGUAGE_PAIR_NOTES.get((self.source_language, self.target_language), "")

    def _context_spec(self) -> str:
        if self.context_type == "abstract":
            return F.TRANSLATION_CONTEXT_SPEC_ABSTRACT.format(target=self.target_language)
        if self.context_type == "previous_page":
            return F.TRANSLATION_CONTEXT_SPEC_PREVIOUS.format(target=self.target_language)
        return F.TRANSLATION_CONTEXT_SPEC_NONE.format(target=self.target_language)

    def system_prompt(self) -> str:
        pair_note = self._pair_note()
        sections = [
            F.TRANSLATION_ROLE.format(source=self.source_language, target=self.target_language),
            self._formatting_fragment(),
            F.TRANSLATION_NUMBERED_SYSTEM if self.has_numbered else None,
            F.TRANSLATION_TABLE_MARKER_RULE if self.has_table_markers else None,
            self._context_spec(),
            F.ADDITIONAL_INSTRUCTIONS.format(note=pair_note) if pair_note else None,
            F.ADDITIONAL_INSTRUCTIONS.format(note=F.KANBUN_NOTE) if self.kanbun else None,
            F.TABLE_HINT_SYSTEM if self.tables else None,
            F.ADDITIONAL_INSTRUCTIONS.format(note=F.TOC_NOTE) if self.toc else None,
            F.ADDITIONAL_INSTRUCTIONS.format(note=self.system_note) if self.system_note else None,
        ]
        return "\n\n".join(s for s in sections if s)

    def user_prompt(self) -> str:
        base = (
            F.TRANSLATION_USER_BASE_WITH_CONTEXT if self.context_type != "none"
            else F.TRANSLATION_USER_BASE
        ).format(source=self.source_language, target=self.target_language)
        parts = [
            base,
            F.TRANSLATION_NUMBERED_USER if self.has_numbered else None,
            F.TRANSLATION_TABLE_MARKER_RULE if self.has_table_markers else None,
            F.TRANSLATION_FOOTNOTE_RULE,
            F.TRANSLATION_NO_META_COMMENTARY,
            F.TABLE_HINT_USER if self.tables else None,
            F.ADDITIONAL_NOTES.format(note=self.user_note) if self.user_note else None,
        ]
        # Trailing newline preserved for backward compatibility: the caller
        # concatenates the source text directly onto the end of this string.
        return "\n\n".join(s for s in parts if s) + "\n\n"
