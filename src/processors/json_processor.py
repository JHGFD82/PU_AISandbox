"""JSON file processor: reads .json files and converts to readable text pages."""

import json
import logging
from typing import List

from ..errors import CLIError
from .base_text_processor import BaseTextProcessor
from .constants import DEFAULT_PAGE_SIZE


def _flatten_value(value: object, indent: int = 0) -> List[str]:
    """Recursively convert a JSON value into human-readable lines."""
    prefix = "  " * indent
    lines: List[str] = []

    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}{k}:")
                lines.extend(_flatten_value(v, indent + 1))
            else:
                lines.append(f"{prefix}{k}: {v}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}[{i}]:")
                lines.extend(_flatten_value(item, indent + 1))
            else:
                lines.append(f"{prefix}[{i}]: {item}")
    else:
        lines.append(f"{prefix}{value}")

    return lines


class JsonProcessor(BaseTextProcessor):
    """Handles extraction of text from JSON files."""

    @staticmethod
    def process_json_with_pages(file_path: str, target_page_size: int = DEFAULT_PAGE_SIZE) -> List[str]:
        """Read a JSON file and return its content as logical text pages.

        The JSON is rendered as an indented human-readable representation that
        is suitable for translation or other text processing.  The result is then
        split into logical pages the same way as :meth:`TxtProcessor.process_txt_with_pages`.

        Args:
            file_path: Absolute path to the .json file.
            target_page_size: Target number of characters per logical page.

        Returns:
            List of strings, each representing a logical page of content.
        """
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise CLIError(
                f"'{file_path}' doesn't appear to be a valid JSON file. JSON is a "
                "structured text format, and this file has something in it that "
                f"breaks that structure ({exc}). If you exported it from another "
                "program, try exporting it again."
            ) from exc
        except OSError as exc:
            raise CLIError(
                f"Could not open '{file_path}' ({exc}). Check that the file is "
                "where you think it is and that you have permission to read it."
            ) from exc

        lines = _flatten_value(data)
        if not lines:
            logging.warning("No content found in JSON file '%s'", file_path)
            return [""]

        full_text = "\n".join(lines)
        processor = JsonProcessor()
        paragraphs = processor.parse_text_into_paragraphs(full_text)
        pages = processor.split_text_into_pages(paragraphs, target_page_size)

        logging.info("Split JSON file into %d logical page(s)", len(pages))
        return pages
