"""Raw prompt text fragments referenced by the prompt spec classes.

All string content lives here.  No logic, no conditionals — just named
constants.  Variables use str.format() placeholders, documented inline.
"""

# ---------------------------------------------------------------------------
# Shared injection blocks
# ---------------------------------------------------------------------------

# Placeholders: {note}
ADDITIONAL_INSTRUCTIONS = "\n\nADDITIONAL INSTRUCTIONS:\n{note}"
ADDITIONAL_NOTES = "\n\nADDITIONAL NOTES:\n{note}"

# ---------------------------------------------------------------------------
# Table preservation hint — injected when --preserve-tables is set.
# Instructs the model to output any tabular data as GitHub-Flavored Markdown
# tables so the output layer can detect and render them as proper tables.
# ---------------------------------------------------------------------------

TABLE_HINT_SYSTEM = (
    "TABLES: If the content includes any tabular data (rows and columns of information), "
    "you MUST represent it as a GitHub-Flavored Markdown table using pipe characters and a "
    "separator row (| --- | --- |). Do not flatten tables into plain prose or lists. "
    "Preserve the original column structure exactly."
)

TABLE_HINT_USER = (
    "TABLES REMINDER: Render any tabular data as a Markdown table (pipe-separated, with a "
    "| --- | separator row). Do not convert tables to prose."
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

# Shared vertical-text block used by both OCR and image-translation services.
# OCR uses different wording for the column direction hint; they are kept separate below.
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
# Language-pair-specific notes — injected automatically by TranslationPromptSpec
# when the source/target combination matches.  Keyed by (source_language, target_language).
# Both directions of a pair should normally have entries.
# ---------------------------------------------------------------------------

# Source-style notes — injected automatically when a source-style flag is set.
# Each entry is a complete block of instructions placed after the language-pair
# note  (if any) and before the user's explicit system_note.

KANBUN_NOTE = (
    "The source text is kanbun (漢文) — Classical Chinese written for Japanese "
    "kundoku (訓読) reading. Apply the following conventions:\n"
    "- Reconstruct word order according to kundoku conventions: Japanese verb-final "
    "syntax, not the Subject-Verb-Object order of Classical Chinese.\n"
    "- Expand implicit grammatical elements (particles, verb endings, auxiliary "
    "words) that are absent in the kanbun but required by kundoku reading.\n"
    "- Preserve kanbun punctuation markers (返り点 kaeriten, 送り仮名 okurigana) "
    "as context clues — do not reproduce them literally in the translation.\n"
    "- Use the register appropriate to classical Japanese scholarly prose when "
    "producing Japanese output, or fluent academic prose for English output.\n"
    "- Proper nouns, reign names, and place names should follow established "
    "Sinological or Japanese historical conventions (e.g. Heian, not 'Hei-an')."
)

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

LANGUAGE_PAIR_NOTES: dict[tuple[str, str], str] = {
    ("Japanese", "Korean"): (
        "Japanese and Korean both have elaborate honorific systems that do not map "
        "one-to-one. Apply the following conventions:\n"
        "- Preserve the formality register of the source: formal/polite text should "
        "become formal/polite in the target (e.g. 합쇼체 or 해요체 in Korean; "
        "丁寧語 / teineigo in Japanese).\n"
        "- Translate title suffixes appropriately: Japanese 様/さん/先生 → Korean "
        "님/선생님, and vice versa.\n"
        "- Do not flatten honorific speech to plain speech (반말 / タメ口) unless "
        "the source explicitly uses an informal register."
    ),
    ("Korean", "Japanese"): (
        "Korean and Japanese both have elaborate honorific systems that do not map "
        "one-to-one. Apply the following conventions:\n"
        "- Preserve the formality register of the source: formal/polite text should "
        "become formal/polite in the target (丁寧語 / teineigo in Japanese; "
        "합쇼체 or 해요체 in Korean).\n"
        "- Translate title suffixes appropriately: Korean 님/선생님 → Japanese "
        "様/さん/先生, and vice versa.\n"
        "- Do not flatten honorific speech to plain speech (タメ口 / 반말) unless "
        "the source explicitly uses an informal register."
    ),
}


# ---------------------------------------------------------------------------
# Script guidance dictionaries
# (moved from constants.py — text lives here, services import from here)
# ---------------------------------------------------------------------------

# Keyed by target language name; used by OcrPromptSpec
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

# Keyed by source language name; used by ImageTranslationPromptSpec.
# Uses "source text" phrasing and includes translation-context notes.
IMAGE_TRANSLATION_SCRIPT_GUIDANCE: dict[str, str] = {
    "Chinese": (
        "The source text uses Chinese characters (hanzi/\u6f22\u5b57). "
        "Transcribe each character exactly as it appears."
    ),
    "Simplified Chinese": (
        "The source text uses Simplified Chinese characters (\u7b80\u4f53\u5b57). "
        "Transcribe each character exactly in its simplified form \u2014 "
        "do NOT convert to or substitute traditional variants."
    ),
    "Traditional Chinese": (
        "The source text uses Traditional Chinese characters (\u7e41\u9ad4\u5b57). "
        "Transcribe each character exactly in its traditional form \u2014 "
        "do NOT convert to or substitute simplified variants."
    ),
    "Japanese": (
        "The source text uses Japanese script, which combines kanji (Chinese-derived characters), "
        "hiragana, katakana, and possibly r\u014dmaji. "
        "Reproduce all scripts exactly as written. "
        "Some kanji may be Japanese-specific forms (kokuji) not found in standard Chinese \u2014 "
        "transcribe them faithfully and do NOT substitute simplified or traditional Chinese variants. "
        "Use kanji ambiguity resolution via translation context before committing to a transcript."
    ),
    "Korean": (
        "The source text uses Korean script (hangul/\ud55c\uae00), possibly mixed with hanja (\u6f22\u5b57) or Latin text. "
        "Transcribe all scripts exactly as they appear."
    ),
    "English": "The source text uses the Latin alphabet.",
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
