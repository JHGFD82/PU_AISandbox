"""Markdown output builder: writes AI response content to .md files."""

import logging

from ._output_utils import _emit_message


def save_to_markdown(
    content: str,
    output_path: str,
    label: str = "Output",
) -> None:
    """Save *content* to a Markdown file at *output_path*.

    The AI response is written as-is because the models already produce
    Markdown-formatted text.  A trailing newline is added if absent.
    """
    text = content if content.endswith("\n") else content + "\n"
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        _emit_message(f"{label} saved to {output_path}")
    except OSError as exc:
        logging.error("Error saving Markdown file '%s': %s", output_path, exc)
        raise
