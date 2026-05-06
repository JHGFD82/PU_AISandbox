"""Shared prompt fragments used by multiple modes and the built-in prompt command.

These constants are re-exported from both translation_fragments.py and
ocr_fragments.py so that mode-specific prompt spec files can use a single
``import … as F`` without a separate import.

This file stays in the main PU_AISandbox repo and is NOT extracted into
any plugin repo.
"""

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
    "Preserve the original column structure exactly. "
    "Each row MUST be on its own separate line. "
    "Never use <br>, <br/>, or any HTML tags inside table cells or anywhere in the output."
)

TABLE_HINT_USER = (
    "TABLES REMINDER: Render any tabular data as a Markdown table (pipe-separated, with a "
    "| --- | separator row). Each row must be on its own line. "
    "Do not use <br> or any HTML tags. Do not convert tables to prose."
)
