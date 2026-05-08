"""Translation and image-translation prompt fragments.

Used by:
  src/services/prompts/translation.py        (TranslationPromptSpec)
  src/services/prompts/image_translation.py  (ImageTranslationPromptSpec)
  src/services/translation_service.py        (table-translation API call)

All string content, no logic. Variables use str.format() placeholders.

This file is the authoritative home for translation-mode prompts and ships
with PU_AISandbox_Translation.  To change prompt text, edit the constants
directly and open a pull request.
"""

# ---------------------------------------------------------------------------
# Shared fragments (re-exported so consumers need only one import)
# ---------------------------------------------------------------------------
from .prompt_fragments import (  # noqa: F401
    ADDITIONAL_INSTRUCTIONS,
    ADDITIONAL_NOTES,
    TABLE_HINT_SYSTEM,
    TABLE_HINT_USER,
)

# ---------------------------------------------------------------------------
# Translation — system prompt sections
# ---------------------------------------------------------------------------

# Placeholders: {source}, {target}
TRANSLATION_ROLE = (
    "Follow the instructions carefully. Please act as a professional translator from {source} "
    "to {target}. I will provide you with text from a document, and your task is "
    "to translate it from {source} to {target}. Please only output the translation and do not "
    "output any irrelevant content. If there are garbled characters or other non-standard text "
    "content, delete the garbled characters."
)

# Placeholders: {target}
# Three variants — selected by TranslationPromptSpec based on context_type.
TRANSLATION_CONTEXT_SPEC_NONE = (
    'The input is labeled "--Current Page: ". '
    "Output only the {target} translation of that text. "
    'Do not reproduce the "--Current Page: " label in your output.'
)

TRANSLATION_CONTEXT_SPEC_ABSTRACT = (
    'The input has two labeled sections. '
    '"--Context: " contains the document abstract — use it to inform the translation but do not translate or reproduce it. '
    '"--Current Page: " is the text to translate. '
    "Output only the {target} translation of \"--Current Page: \". "
    'Do not reproduce either label in your output.'
)

TRANSLATION_CONTEXT_SPEC_PREVIOUS = (
    'The input has two labeled sections. '
    '"--Context: " contains the end of the previous page — use it to maintain continuity but do not translate or reproduce it. '
    '"--Current Page: " is the text to translate. '
    "Output only the {target} translation of \"--Current Page: \". "
    'Do not reproduce either label in your output.'
)

# Output-format variants — keyed by canonical format group
TRANSLATION_FORMATTING: dict[str, str] = {
    "file": (
        "Use proper paragraph breaks and standard text formatting suitable for file output. "
        "Use actual line breaks (not \\n characters) to separate paragraphs and sections naturally."
    ),
    "console": (
        'You can format and line break the output yourself using "\\n" for line breaks in console output.'
    ),
}

# Numbered-content block — included in the system prompt only when numbered content is detected
TRANSLATION_NUMBERED_SYSTEM = (
    "IMPORTANT: Pay special attention to numbered lists, citations, and footnotes.\n"
    "Preserve ALL numbering exactly as it appears in the source text. This includes:\n"
    "\u2022 Arabic numerals: 1, 2, 3... or 1), 2), 3)...\n"
    "\u2022 Numbers in brackets: [1], [2], [3]... or (1), (2), (3)...\n"
    "\u2022 Chinese numerals: \u4e00\u3001\u4e8c\u3001\u4e09... or \uff08\u4e00\uff09\u3001\uff08\u4e8c\uff09\u3001\uff08\u4e09\uff09...\n"
    "\u2022 Japanese/Korean numbering: \u2460, \u2461, \u2462... or \uff11\u3001\uff12\u3001\uff13...\n"
    "\u2022 Japanese reference format: 14\u3000author\u300ctitle\u300d\u2192 should become \"14. Author, 'Title'\"\n"
    "\n"
    "CRITICAL DISTINCTION - DO NOT ADD NUMBERING:\n"
    "\n"
    "- If the source text has section headings WITHOUT numbers, do NOT add numbers to them\n"
    "- Only preserve numbering that already exists in the source\n"
    '- Section titles like "\u80cc\u666f" or "\u7d50\u8ad6" should remain as "Background" or "Conclusion" without numbers\n'
    "\n"
    "\n"
    "CRITICAL FOR BIBLIOGRAPHY/REFERENCES: If you encounter numbered reference lists or bibliography\n"
    '(like "1. Author Title, Publisher" format), preserve the exact numbering format. Do NOT convert\n'
    "numbered references into paragraph form. Keep each reference as a separate numbered item.\n"
    "\n"
    'CRITICAL: When you see Japanese reference format like "14\u3000\u677e\u4e0b\u5b89\u96c4\u76e3\u4fee\u6c38\u6843\u5143\u826f\u300c\u798f\u5ca1\u8529\u300d",\n'
    'translate it to proper English reference format like "14. Supervised by Matsushita Yasuo, Higaki Motoyoshi, \'Fukuoka Domain\'".\n'
    'DO NOT output just the number "14" by itself - always include the full reference text with proper formatting.'
)


# ---------------------------------------------------------------------------
# Translation — user prompt sections
# ---------------------------------------------------------------------------

# Placeholders: {source}, {target}
TRANSLATION_USER_BASE = (
    'Translate only the {source} text under "--Current Page: " to {target}, outputting only the translation with no other content.'
)

TRANSLATION_USER_BASE_WITH_CONTEXT = (
    'Translate only the {source} text under "--Current Page: " to {target}. '
    'Do not translate or reproduce the "--Context: " section. '
    'Output only the translation with no other content.'
)

# Included in the user prompt only when numbered content is detected
TRANSLATION_NUMBERED_USER = (
    "CRITICAL: Preserve all numbering systems exactly as they appear in the source "
    "(1, 2, 3... or [1], [2]... or \u2460, \u2461... etc.).\n"
    "DO NOT ADD numbering to headings or sections that are not numbered in the source text.\n"
    "\n"
    'CRITICAL FOR REFERENCES: When translating reference entries like "14\u3000\u677e\u4e0b\u5b89\u96c4\u76e3\u4fee\u6c38\u6843\u5143\u826f\u300c\u798f\u5ca1\u8529\u300d",\n'
    "translate the COMPLETE reference including author names, titles, and formatting. Output should be\n"
    '"14. Author Name, \'Title\'" NOT just the isolated number "14". Always translate the full reference text.\n'
    "\n"
    "NUMBERING CONTINUATION - VERY IMPORTANT:\n"
    '- If the context shows "Previous numbering ended with: X. Reference", you MUST continue numbering from X+1 for any new numbered items on the current page.\n'
    "- Do NOT restart numbering from 1 - always continue the sequence from the previous page.\n"
    '- Example: If context shows "Previous numbering ended with: 25. Some Reference", and current page has more numbered items, they should be numbered 26, 27, 28, etc.\n'
    "- This applies ONLY to numbered reference lists, NOT to section headings.\n"
    "\n"
    "SECTION HEADINGS: If the source has section headings without numbers, translate them as headings without adding numbers."
)

TRANSLATION_FOOTNOTE_RULE = (
    'IMPORTANT: Only add a "Footnotes:" section if there is actual explanatory footnote text at the bottom\n'
    "of the page. Do NOT add \"Footnotes:\" for simple citation numbers like (38), (39) within paragraphs."
)

TRANSLATION_NO_META_COMMENTARY = (
    'Do not provide any prompts to the user, for example: "This is the translation of the current page.":'
)

# System prompt for the dedicated per-table Markdown round-trip API call.
# Placeholders: {source}, {target}
TRANSLATION_TABLE_SYSTEM = (
    "You are a professional translator. "
    "Translate the following Markdown table from {source} to {target}. "
    "Return ONLY the translated Markdown table with exactly the same number of rows and "
    "columns and the same pipe/separator structure. "
    "Do not add any explanation, commentary, or text outside of the table."
)

# Injected into both system and user prompts when the page text contains
# [TABLE_N] placeholder tokens (i.e. tables extracted for separate translation).
TRANSLATION_TABLE_MARKER_RULE = (
    "IMPORTANT: This text contains table placeholder tokens such as [TABLE_1], [TABLE_2], etc. "
    "These tokens represent tables that are translated separately and will be reinserted into the "
    "output document automatically. "
    "Preserve every [TABLE_N] token EXACTLY as written — same case, same brackets, same number. "
    "Do NOT translate, paraphrase, remove, or reformat these tokens in any way."
)


# ---------------------------------------------------------------------------
# Image translation — system prompt sections
# ---------------------------------------------------------------------------

# Placeholders: {source}, {target}
IMAGE_TRANSLATION_ROLE = (
    "You are an expert reader and translator specialising in {source} text found in images."
)

# Placeholders: {source}, {target}
IMAGE_TRANSLATION_FORMAT_SPEC = (
    "Your task is to perform two operations on the image:\n"
    "1. TRANSCRIBE all visible {source} text exactly as it appears.\n"
    "2. TRANSLATE that transcribed text into fluent, accurate {target}.\n"
    "\n"
    "You MUST return your response in EXACTLY this format, with the section headers on their own lines:\n"
    "\n"
    "[TRANSCRIPT]\n"
    "<transcribed {source} text, preserving original layout and line breaks>\n"
    "\n"
    "[TRANSLATION]\n"
    "<{target} translation of the transcribed text>"
)

# Placeholders: {target}
IMAGE_TRANSLATION_TRANSCRIPTION_RULES = (
    "TRANSCRIPTION RULES:\n"
    "- Reproduce text exactly as it appears in the image \u2014 do not correct, modernise, or alter characters.\n"
    "- Preserve line breaks, punctuation, numbering, and overall structure.\n"
    "- Use surrounding context and translation target to resolve ambiguous or partially obscured characters; "
    "mark genuinely unreadable text with [unclear] inline rather than a trailing summary.\n"
    "- Do not skip any text, including headers, captions, inscriptions, or marginal notes."
)

# Placeholders: {target}
IMAGE_TRANSLATION_TRANSLATION_RULES = (
    "TRANSLATION RULES:\n"
    "- Produce a fluent, scholarly {target} translation.\n"
    "- Preserve the structure of the original (line breaks, stanzas, numbered items, etc.).\n"
    "- For classical or archaic language, prefer a literary translation over a literal one.\n"
    "- Do not add explanatory notes, commentary, or translator remarks."
)

IMAGE_TRANSLATION_VERTICAL_BLOCK = (
    "TEXT ORIENTATION:\n"
    "The majority of text in this image is vertical \u2014 written top-to-bottom, "
    "with columns ordered right-to-left. Read each column from top to bottom, "
    "proceeding from the rightmost column to the leftmost."
)

IMAGE_TRANSLATION_VERTICAL_NOTE = (
    " The text is predominantly vertical (top-to-bottom, right-to-left columns)."
)

IMAGE_TRANSLATION_SPREAD_NOTE = (
    "IMAGE LAYOUT: This image is a two-page spread (two facing pages scanned together). "
    "Process all text from both pages, reading the left page first and the right page second "
    "(or right-to-left for vertical text)."
)

# ---------------------------------------------------------------------------
# Language-pair-specific notes
# Injected automatically by TranslationPromptSpec when the source/target
# combination matches.  Keyed by (source_language, target_language).
# Starts empty; language plugins populate it at load time via setdefault().
# ---------------------------------------------------------------------------

TOC_NOTE = (
    "This document contains a table of contents. TOC entries use dot leaders — "
    "a long run of dots (e.g. '............') between the section title and the "
    "page number. Because the translated title will be a different length than the "
    "original, do NOT reproduce the exact number of dots. Instead, replace any "
    "sequence of three or more consecutive dots used as a leader with exactly five "
    "dots (.....), with the page number before it and the title after it."
)

LANGUAGE_PAIR_NOTES: dict[tuple[str, str], str] = {}

# Keyed by source language name; used by ImageTranslationPromptSpec.
# Starts with English; language plugins add their own entries at load time via setdefault().
IMAGE_TRANSLATION_SCRIPT_GUIDANCE: dict[str, str] = {
    "English": "The source text uses the Latin alphabet.",
}


