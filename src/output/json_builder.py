"""JSON output builder: writes AI response content to .json files."""

import json
import logging

from ._output_utils import _emit_message


def save_to_json(
    content: str,
    output_path: str,
    label: str = "Output",
) -> None:
    """Save the AI's response text to a .json file.

    If the response text is already valid JSON, it's reformatted with
    consistent spacing and saved as-is. Otherwise, the plain text is wrapped
    in a simple structure — ``{"content": "<the text>"}`` — so the output is
    still valid JSON even when the AI didn't respond in that format.

    Args:
        content: The AI's response text to save.
        output_path: The file path to write to, e.g. ``'response.json'``.
        label: A short description used in the saved-confirmation message
               shown to the user (e.g. ``'Translation'`` or ``'Response'``).

    Raises:
        OSError: If the file cannot be written (e.g. the folder doesn't
                 exist or there's a permissions problem).
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        data = {"content": content}

    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        _emit_message(f"{label} saved to {output_path}")
    except OSError as exc:
        logging.error("Error saving JSON file '%s': %s", output_path, exc)
        raise
