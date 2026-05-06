"""OCR (transcription) and transcription-review prompt fragments.

Used by:
  src/services/prompts/ocr.py                    (OcrPromptSpec)
  src/services/prompts/transcription_review.py   (TranscriptionReviewPromptSpec)

All string content, no logic. Variables use str.format() placeholders.

This file is the authoritative home for transcription-mode prompts and ships
with PU_AISandbox_Transcription.  The override contract (prompts.toml sections
[ocr], [transcription_review], [ocr_script_guidance]) is also defined here.
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
# OCR — system prompt sections
# ---------------------------------------------------------------------------

# Placeholders: {target}
OCR_SYSTEM_BASE = (
    "You are an expert OCR assistant specializing in {target} text extraction from images.\n"
    "\n"
    "Your task is to transcribe all legibly visible text from the image exactly as it appears, "
    "preserving layout, orientation (horizontal or vertical), and structure as closely as possible."
)

OCR_RULES = (
    "RULES:\n"
    "- Extract ONLY text that is actually visible in the image \u2014 do NOT add, invent, or hallucinate any content\n"
    "- Do NOT repeat text unless it genuinely appears multiple times in the image\n"
    "- Do NOT translate \u2014 output text in its original language and script exactly as shown\n"
    "- Do NOT add commentary, analysis, disclaimers, or assumptions\n"
    "- Preserve original formatting, line breaks, numbering, symbols, and special characters\n"
    "- If text is partially obscured or unclear, extract what you can; note any unreadable sections with a "
    'single brief line at the end (e.g., "[Some text unclear due to image quality]")'
)

# Placeholders: {target}
OCR_USER_BASE = (
    "Transcribe all legibly visible text from this image exactly as it appears in {target}."
)

OCR_USER_BASE_KANBUN = (
    "Transcribe all legibly visible text from this image exactly as it appears,"
    " preserving the kanbun (漢文) characters and all kundoku annotations."
)

OCR_USER_RULES = (
    "CRITICAL RULES FOR THIS IMAGE:\n"
    "- Output ONLY text that is genuinely visible \u2014 do NOT invent, fill in, or hallucinate any characters or words\n"
    "- Do NOT translate \u2014 preserve the original script and language exactly as shown, even in mixed-language content\n"
    "- Include ALL text elements: body text, headings, captions, page numbers, table contents, labels, and marginalia\n"
    "- Preserve line breaks, paragraph spacing, and structural layout as faithfully as plain text allows\n"
    "- Reproduce punctuation, symbols, and special characters exactly as they appear\n"
    "- If a section of text is partially obscured or too degraded to read, extract what you can and note the gap "
    'with a single brief marker (e.g., "[text unclear]") \u2014 do not skip the surrounding legible text\n'
    "- Do not add commentary, disclaimers, or explanatory notes outside of the above illegibility marker"
)

OCR_REFINEMENT_BASE = (
    "Review the transcription above carefully against this image."
    "\n\n"
    "Correct any errors you find: wrong or missing characters, extra or hallucinated text, "
    "misread characters, or formatting issues. "
    "If the transcription is already accurate, return it unchanged.\n\n"
    "Return ONLY the corrected transcription \u2014 no commentary, no explanation, no preamble."
)

OCR_VERTICAL_BLOCK = (
    "TEXT ORIENTATION:\n"
    "The majority of text in this image is vertical \u2014 written top-to-bottom, "
    "with columns ordered right-to-left. Read and transcribe each column from top to bottom, "
    "proceeding from the rightmost column to the leftmost."
)

OCR_VERTICAL_REINFORCEMENT = (
    "ORIENTATION REMINDER: Text is vertical \u2014 transcribe each column top-to-bottom, "
    "proceeding right-to-left across columns."
)

OCR_SPREAD_NOTE = (
    "IMAGE LAYOUT: This image is a two-page spread (two facing pages scanned together). "
    "Transcribe all text from both pages as a single continuous document, "
    "reading the left page first and the right page second (or right-to-left for vertical text)."
)

# ---------------------------------------------------------------------------
# Kanbun OCR — script and annotation notes
# ---------------------------------------------------------------------------

KANBUN_SCRIPT_NOTE = (
    "The text is kanbun (漢文) — Classical Chinese written in kanji only. "
    "Hiragana and katakana appear only as annotations (送り仮名 okurigana, "
    "振り仮名 furigana) beside the main characters, not as independent text. "
    "Transcribe all kanji and all kana annotations exactly as they appear — "
    "do NOT omit small or lightly printed kana, as they are critical annotations."
)

KANBUN_MAIN_SCRIPT_NOTE = (
    "The text is kanbun (漢文) — Classical Chinese written primarily in kanji. "
    "Small kana characters beside the main characters are kundoku annotations "
    "(送り仮名 okurigana, 振り仮名 furigana). Transcribe ONLY the main-line kanji; "
    "do NOT transcribe any small annotation kana."
)

KANBUN_MAIN_OCR_NOTE = (
    "The image contains kanbun (漢文) — Classical Chinese text with kundoku annotations. "
    "Transcribe ONLY the large main-line kanji characters. Omit all of the following:\n"
    "- 送り仮名 (okurigana) — small kana written beside the main characters\n"
    "- 振り仮名 / ルビ (furigana/ruby) — small kana giving readings above or beside characters\n"
    "- 返り点 (kaeriten: レ点, 一二三点, 上中下点, etc.) — reordering marks beside characters\n"
    "- Any other small subscript or superscript annotation\n"
    "- Handwritten characters, stamps, seals, or ink marks of any kind\n"
    "- Any text written in the margins, between columns, or outside the main text block\n"
    "EXCEPTIONS — include these even if they appear outside the main body columns:\n"
    "- Chapter titles, section headings, and fascicle labels\n"
    "- Printed page numbers\n"
    "Repetition marks that occupy a main-character position (々, 〻, 〱, 〲, etc.) ARE "
    "main-line characters and must be transcribed.\n"
    "Do NOT reorder characters or apply kundoku reading order; transcribe the main characters "
    "in the visual order as they appear on the page (top-to-bottom, right-to-left columns)."
)

OCR_USER_BASE_KANBUN_MAIN = (
    "Transcribe ONLY the large main-line kanji from this kanbun (漢文) image. "
    "Do not include okurigana, furigana, kaeriten, any other small annotation characters, "
    "handwritten marks, or text in the margins. "
    "Chapter titles and printed page numbers may be included."
)

KANBUN_OCR_NOTE = (
    "The image contains kanbun (漢文) — Classical Chinese text annotated for "
    "Japanese kundoku (訓読) reading. Transcribe the image faithfully, preserving "
    "all annotations exactly as they appear:\n"
    "- Transcribe all 返り点 (kaeriten: レ点, 一二三点, 上中下点, etc.) as they appear "
    "beside or below the main characters.\n"
    "- Transcribe all 送り仮名 (okurigana) — small kana written beside the main "
    "characters — exactly as they appear.\n"
    "- Preserve 句読点 (punctuation marks) and any 訓点 (kunten) annotations.\n"
    "- Preserve all repetition marks exactly as the character(s) that appear on the page — "
    "do NOT convert them to the character(s) they represent or normalise sequences:\n"
    "    々  (noma, kanji repetition mark) → always 々\n"
    "    〻  (variant noma, resembles ノ＋一) → always 〻\n"
    "    〱 〲 (ku-no-ji-ten, angled z-shape) → always 〱 or 〲\n"
    "    〳 〴 〵 (vertical ku-no-ji-ten variants) → always the exact character shown\n"
    "    ゝ ゞ (hiragana iteration marks) → always ゝ or ゞ\n"
    "  When repetition marks appear consecutively, preserve every mark in order.\n"
    "- Do NOT reorder characters, expand grammar, or interpret kundoku conventions; "
    "transcribe the text exactly as written on the page."
)

# ---------------------------------------------------------------------------
# OCR script guidance dictionary
# Keyed by target language name; used by OcrPromptSpec._script_note()
# ---------------------------------------------------------------------------

OCR_SCRIPT_GUIDANCE: dict[str, str] = {
    "Chinese": (
        "The text uses Chinese characters (hanzi/\u6f22\u5b57). "
        "Transcribe each character exactly as it appears."
    ),
    "Simplified Chinese": (
        "The text uses Simplified Chinese characters (\u7b80\u4f53\u5b57). "
        "Transcribe each character exactly in its simplified form \u2014 "
        "do NOT convert to or substitute traditional variants."
    ),
    "Traditional Chinese": (
        "The text uses Traditional Chinese characters (\u7e41\u9ad4\u5b57). "
        "Transcribe each character exactly in its traditional form \u2014 "
        "do NOT convert to or substitute simplified variants."
    ),
    "Japanese": (
        "The text uses Japanese script, which combines kanji (Chinese-derived characters), "
        "hiragana, katakana, and possibly r\u014dmaji. "
        "Reproduce all scripts exactly as written. "
        "Some kanji may be Japanese-specific forms (kokuji) not found in standard Chinese \u2014 "
        "transcribe them faithfully and do NOT substitute simplified or traditional Chinese variants. "
        "Hiragana printed at very small sizes may be omitted only if completely illegible."
    ),
    "Korean": (
        "The text uses Korean script (hangul/\ud55c\uae00), possibly mixed with hanja (\u6f22\u5b57) or Latin text. "
        "Transcribe all scripts exactly as they appear."
    ),
    "English": "The text uses the Latin alphabet.",
}

# ---------------------------------------------------------------------------
# Transcription review — system prompt sections
# ---------------------------------------------------------------------------

# Placeholders: {language}
TRANSCRIPTION_REVIEW_ROLE = (
    "You are an expert proofreader and language scholar specialising in {language} texts. "
    "You will be given text that was produced by an AI transcription (OCR) system from a "
    "historical or archival document. Your task is to review it for OCR errors, identify "
    "the probable source, and report each error with one or more corrected candidates."
)

TRANSCRIPTION_REVIEW_KANBUN_NOTE = (
    "The text contains kanbun (漢文) with kundoku annotations (返り点, 送り仮名). "
    "Evaluate annotations as part of the transcription — they are intentional and should "
    "not be flagged as errors unless clearly wrong.\n"
    "Repetition marks are valid transcription characters and must NOT be flagged as errors "
    "simply because they differ from the character they represent. The following are all "
    "legitimate, distinct Unicode characters — treat each one as correct if it plausibly "
    "matches what would appear in a historical kanbun manuscript:\n"
    "  々  (noma, kanji repetition mark)\n"
    "  〻  (variant noma, resembles ノ＋一)\n"
    "  〱 〲 (ku-no-ji-ten, angled z-shape)\n"
    "  〳 〴 〵 (vertical ku-no-ji-ten variants)\n"
    "  ゝ ゞ (hiragana iteration marks)\n"
    "Only flag a repetition mark as an error if it is clearly the wrong mark for its "
    "position (e.g. a kanji repetition mark where a kana iteration mark is expected), "
    "or if it has been substituted for a regular character that cannot be a repetition."
)

TRANSCRIPTION_REVIEW_KANBUN_MAIN_NOTE = (
    "This transcription was produced in main-character-only mode from a kanbun (漢文) image: "
    "okurigana (送り仮名), furigana (振り仮名/ルビ), kaeriten (返り点), all other small "
    "kundoku annotations, handwritten marks, and any text in the margins were intentionally "
    "omitted during OCR. Do NOT flag the absence of any of these as errors — they were never "
    "part of the transcription.\n"
    "Focus your review solely on the accuracy of the main-line kanji characters that are "
    "present. Chapter titles and printed page numbers may appear and are legitimate. "
    "Repetition marks that occupy a main-character position (々, 〻, 〱, 〲, etc.) "
    "are valid and should not be flagged unless clearly wrong for their context."
)

TRANSCRIPTION_REVIEW_APPROACH = (
    "REVIEW APPROACH:\n"
    "1. Assess whether the text makes sense as a whole.\n"
    "2. Identify the source type (genre, period, register) to establish interpretive context.\n"
    "3. Use that context to spot characters or words that are likely OCR misreadings.\n"
    "4. For each error, record the most probable correction(s) in descending confidence order."
)

TRANSCRIPTION_REVIEW_SCHEMA = (
    'OUTPUT FORMAT:\n'
    'Respond with ONLY a valid JSON object — no markdown, no code fences, no prose outside the JSON.\n'
    '\n'
    '{\n'
    '  "meta": {\n'
    '    "language": "<language of the text>",\n'
    '    "identified_source": "<source type / genre / period, or \\"unknown\\">",\n'
    '    "source_confidence": "<high | medium | low | unknown>",\n'
    '    "overall_quality": "<good | fair | poor>",\n'
    '    "assessment": "<1\u20133 sentences on transcription quality and any systematic error patterns>",\n'
    '    "error_count": <integer, count of entries in \'corrections\' only>\n'
    '  },\n'
    '  "global_replacements": [\n'
    '    {\n'
    '      "from": "<character(s) as mistakenly transcribed>",\n'
    '      "to": "<correct character(s)>",\n'
    '      "confidence": "<high | medium | low>",\n'
    '      "note": "<optional: brief explanation, e.g. visually similar in low-resolution scans>"\n'
    '    }\n'
    '  ],\n'
    '  "corrections": [\n'
    '    {\n'
    '      "page": <integer | null>,\n'
    '      "line": <integer>,\n'
    '      "position": <integer, 1-based character index within the line>,\n'
    '      "context": "<\u223c20 characters surrounding the error>",\n'
    '      "original": "<erroneous character(s) as transcribed>",\n'
    '      "candidates": [\n'
    '        {"char": "<most likely>", "confidence": "high"},\n'
    '        {"char": "<alternative>", "confidence": "low"}\n'
    '      ],\n'
    '      "error_type": "<substitution | insertion | deletion>"\n'
    '    }\n'
    '  ]\n'
    '}'
)

TRANSCRIPTION_REVIEW_RULES = (
    "RULES:\n"
    '- Set "page" only when the text contains clear page-break markers; otherwise use null.\n'
    "- List candidates in descending confidence order; one entry is sufficient when certain.\n"
    '- "position" is the 1-based index of the first erroneous character within the line.\n'
    '- "context" should show approximately 10 characters before and after the error.\n'
    "- If no errors are found, return an empty corrections array.\n"
    "- Do not flag punctuation normalization or stylistic preferences \u2014 only genuine OCR errors.\n"
    "- GLOBAL REPLACEMENTS vs CORRECTIONS: If the same character substitution error (one specific\n"
    "  character or sequence mistakenly rendered as another) occurs in three or more places,\n"
    "  record it ONCE in 'global_replacements' as a find-and-replace rule rather than listing\n"
    "  every occurrence in 'corrections'. A global replacement means: every instance of 'from'\n"
    "  in the transcription should be replaced with 'to'. Do NOT also add those instances to\n"
    "  'corrections' — they are covered by the global rule. Use 'corrections' only for errors\n"
    "  that are unique to a specific context or whose correction depends on surrounding text.\n"
    '- "error_count" reflects the number of entries in "corrections" only; global replacements\n'
    "  are not counted individually."
)

# Placeholders: {language}
TRANSCRIPTION_REVIEW_USER_BASE = (
    "Review the following {language} transcription for OCR errors. "
    "Output only the JSON review object, with no additional text."
)

# Placeholders: {text}
TRANSCRIPTION_REVIEW_TEXT_BLOCK = "\n\nTRANSCRIPTION:\n{text}"


# ---------------------------------------------------------------------------
# User override loader
# ---------------------------------------------------------------------------

def _load_user_overrides() -> None:
    """Apply overrides from prompts.toml, if present.

    Looks for prompts.toml at the repository root (four .parent calls from
    this file's location: src/services/prompts/ocr_fragments.py).
    """
    import tomllib
    from pathlib import Path

    _toml_path = Path(__file__).parent.parent.parent.parent / "prompts.toml"
    if not _toml_path.exists():
        return

    with _toml_path.open("rb") as _f:
        _overrides = tomllib.load(_f)

    _g = globals()

    _MAP: dict[tuple[str, str], "str | tuple[str, str]"] = {
        # ── [ocr] ─────────────────────────────────────────────────────────
        ("ocr", "system_base"): "OCR_SYSTEM_BASE",
        ("ocr", "rules"):       "OCR_RULES",
        ("ocr", "user_base"):   "OCR_USER_BASE",
        ("ocr", "user_rules"):  "OCR_USER_RULES",
        # ── [transcription_review] ────────────────────────────────────────
        ("transcription_review", "role"):     "TRANSCRIPTION_REVIEW_ROLE",
        ("transcription_review", "approach"): "TRANSCRIPTION_REVIEW_APPROACH",
    }

    for (section, key), target in _MAP.items():
        if section not in _overrides or key not in _overrides[section]:
            continue
        value = _overrides[section][key]
        if isinstance(target, tuple):
            dict_name, dict_key = target
            _g[dict_name][dict_key] = value
        else:
            _g[target] = value

    for lang, value in _overrides.get("ocr_script_guidance", {}).items():
        _g["OCR_SCRIPT_GUIDANCE"][lang] = value


_load_user_overrides()
