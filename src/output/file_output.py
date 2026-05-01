"""File output handler: writes translations to .txt, .pdf, or .docx with CJK font support."""

import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from ._output_utils import (
    _emit_message,
    _normalize_paragraphs,
    _parse_md_table_block,
    _extract_markdown_tables,
    _render_table_as_ascii,
    _render_markdown_tables_as_ascii,
    save_to_text_file,
    append_to_text_file,
)
from .docx_builder import _apply_docx_table_borders, save_to_docx
from .pdf_builder import save_to_pdf
from ..settings import DEFAULT_FONT_SIZE


def generate_output_filename(input_file: str, source_lang: str, target_lang: str, extension: str = '.txt') -> str:
    """Generate an output filename based on input file and languages."""
    input_path = Path(input_file)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_name = f"{input_path.stem}_{source_lang}to{target_lang}_{timestamp}{extension}"
    return str(input_path.parent / output_name)


class FileOutputHandler:
    """Handles saving translations to various file formats.

    All format-specific logic lives in pdf_builder and docx_builder; this
    class is a stable public facade that delegates to those modules and
    provides the shared orchestration methods (save_translation_output,
    save_page_progressively).
    """

    # Re-expose shared helpers as class attributes so existing call sites
    # that use FileOutputHandler._normalize_paragraphs(...) etc. keep working.
    _emit_message                    = staticmethod(_emit_message)
    _normalize_paragraphs            = staticmethod(_normalize_paragraphs)
    _parse_md_table_block            = staticmethod(_parse_md_table_block)
    _extract_markdown_tables         = staticmethod(_extract_markdown_tables)
    _render_table_as_ascii           = staticmethod(_render_table_as_ascii)
    _render_markdown_tables_as_ascii = staticmethod(_render_markdown_tables_as_ascii)
    _apply_docx_table_borders        = staticmethod(_apply_docx_table_borders)
    save_to_text_file                = staticmethod(save_to_text_file)
    append_to_text_file              = staticmethod(append_to_text_file)
    save_to_pdf                      = staticmethod(save_to_pdf)
    save_to_docx                     = staticmethod(save_to_docx)

    @staticmethod
    def _resolve_output_path(
        input_file: Optional[str],
        output_file: Optional[str],
        auto_save: bool,
        source_lang: str,
        target_lang: str,
        default_extension: str = '.txt',
    ) -> Optional[str]:
        """Resolve the output path from explicit or auto-save settings."""
        if output_file:
            return output_file
        if auto_save and input_file:
            return generate_output_filename(input_file, source_lang, target_lang, default_extension)
        return None

    @staticmethod
    def _ensure_parent_directory(output_path: str) -> None:
        """Ensure the parent directory for output exists."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _fallback_to_text(content: str, output_path: str, label: str) -> None:
        """Fallback to text output when rich document generation fails."""
        text_output_path = str(Path(output_path).with_suffix('.txt'))
        save_to_text_file(content, text_output_path, label)

    @staticmethod
    def save_translation_output(
        content: str,
        input_file: Optional[str],
        output_file: Optional[str],
        auto_save: bool,
        source_lang: str,
        target_lang: str,
        custom_font: Optional[str] = None,
        media: Optional[List] = None,
        table_registry: Optional[dict] = None,
        font_size: Optional[int] = None,
        *,
        label: str,
    ) -> None:
        """Save translation output to file based on user preferences."""
        if not content.strip():
            _emit_message("No content to save.", level=logging.INFO)
            return

        output_path = FileOutputHandler._resolve_output_path(
            input_file, output_file, auto_save, source_lang, target_lang, '.txt',
        )
        if not output_path:
            return

        FileOutputHandler._ensure_parent_directory(output_path)

        extension = Path(output_path).suffix.lower()
        if extension == '.pdf':
            FileOutputHandler.save_to_pdf(
                content, output_path, custom_font, target_lang,
                table_registry=table_registry,
                font_size=font_size,
                label=label,
            )
            return
        if extension == '.docx':
            FileOutputHandler.save_to_docx(
                content, output_path, custom_font, target_lang,
                media=media, table_registry=table_registry,
                font_size=font_size,
                label=label,
            )
            return

        if extension != '.txt':
            output_path = f"{output_path}.txt"
        FileOutputHandler.save_to_text_file(
            _render_markdown_tables_as_ascii(content),
            output_path,
            label=label,
        )

    @staticmethod
    def save_page_progressively(
        content: str,
        input_file: Optional[str],
        output_file: Optional[str],
        auto_save: bool,
        source_lang: str,
        target_lang: str,
        label: str,
        is_first_page: bool = False,
        custom_font: Optional[str] = None,
    ) -> Optional[str]:
        """Save a single page progressively to output file. Returns the output path."""
        _ = custom_font
        if not content.strip():
            _emit_message("No content to save.", level=logging.INFO)
            return None

        output_path = FileOutputHandler._resolve_output_path(
            input_file, output_file, auto_save, source_lang, target_lang, '.txt',
        )
        if not output_path:
            return None

        FileOutputHandler._ensure_parent_directory(output_path)

        extension = Path(output_path).suffix.lower()
        if extension == '.pdf':
            _emit_message(
                "Note: Progressive saving for PDF format not yet supported. Using text format.",
                level=logging.INFO,
            )
            output_path = str(Path(output_path).with_suffix('.txt'))
        elif extension == '.docx':
            _emit_message(
                "Note: Progressive saving for Word document format not yet supported. Using text format.",
                level=logging.INFO,
            )
            output_path = str(Path(output_path).with_suffix('.txt'))

        if Path(output_path).suffix.lower() != '.txt':
            output_path = f"{output_path}.txt"

        if is_first_page:
            FileOutputHandler.save_to_text_file(content, output_path, label)
        else:
            FileOutputHandler.append_to_text_file(content, output_path, label)

        return output_path
