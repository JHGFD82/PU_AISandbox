"""Markdown output builder: writes AI response content to .md files."""

import logging

from ._output_utils import _emit_message


def save_to_markdown(
    content: str,
    output_path: str,
    label: str = "Output",
) -> None:
    """Save the AI's response text to a .md (Markdown) file.

    The text is written exactly as the AI produced it, since AI models
    already format their responses using Markdown (a simple text formatting
    style using symbols like ``#`` for headings and ``**bold**`` for bold
    text) when asked to. A trailing blank line is added if one isn't already
    present.

    Args:
        content: The AI's response text to save.
        output_path: The file path to write to, e.g. ``'response.md'``.
        label: A short description used in the saved-confirmation message
               shown to the user (e.g. ``'Translation'`` or ``'Response'``).

    Raises:
        OSError: If the file cannot be written (e.g. the folder doesn't
                 exist or there's a permissions problem).
    """
    text = content if content.endswith("\n") else content + "\n"
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        _emit_message(f"{label} saved to {output_path}")
    except OSError as exc:
        logging.error("Error saving Markdown file '%s': %s", output_path, exc)
        raise
