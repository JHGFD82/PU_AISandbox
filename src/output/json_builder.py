"""JSON output builder: writes AI response content to .json files."""

import json
import logging

from ._output_utils import _emit_message


def save_to_json(
    content: str,
    output_path: str,
    label: str = "Output",
) -> None:
    """Save *content* to a JSON file at *output_path*.

    If *content* is already valid JSON it is round-tripped (pretty-printed).
    Otherwise the text is wrapped as ``{"content": "<text>"}``.
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
