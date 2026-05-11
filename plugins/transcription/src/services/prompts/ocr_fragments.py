"""OCR (transcription) and transcription-review prompt fragments — base plugin.

Used by:
  src/services/prompts/ocr.py                    (OcrPromptSpec)
  src/services/prompts/transcription_review.py   (TranscriptionReviewPromptSpec)

All string content, no logic. Variables use str.format() placeholders.

This file contains language-agnostic fragments only.  East Asia-specific
content (kanbun, vertical script guidance, CJK language notes) lives in
``plugins/transcription-ea``.

This file is the authoritative home for base transcription prompts and ships
with the main PU_AISandbox repo.  To change prompt text, edit the constants
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

TRANSCRIPTION_REVIEW_APPROACH = (
    "REVIEW APPROACH:\n"
    "1. Assess whether the text makes sense as a whole.\n"
    "2. Identify the source type (genre, period, register) to establish interpretive context.\n"
    "3. Use that context to spot characters or words that are likely OCR misreadings.\n"
    "4. For each error, record the most probable correction(s) in descending confidence order."
)

TRANSCRIPTION_REVIEW_SCHEMA = (
    'OUTPUT FORMAT:\n'
    'Respond with ONLY a valid JSON object \u2014 no markdown, no code fences, no prose outside the JSON.\n'
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
    "  'corrections' \u2014 they are covered by the global rule. Use 'corrections' only for errors\n"
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
