"""Output dispatcher: writes translated content to .txt, .pdf, .docx, .xlsx, .json, or .md files.

This module is the final step in the translation pipeline. ``FileOutputHandler``
receives the translated text and the user's chosen output format and routes the
content to the right format-specific builder (PDF, Word, Excel, etc.). It also
handles auto-generated filenames when the user has not specified one, ensures
output directories exist before writing, and supports progressive saving —
writing each translated page to disk as it arrives rather than waiting for the
full document.
"""

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
from .excel_builder import save_to_excel
from .json_builder import save_to_json
from .markdown_builder import save_to_markdown
from .pdf_builder import save_to_pdf


def generate_output_filename(input_file: str, source_lang: str, target_lang: str, extension: str = '.txt') -> str:
    """Build a timestamped output filename based on the input file and the language pair.

    The generated name is placed in the same directory as the input file and
    follows the pattern ``{original_name}_{source}to{target}_{YYYYMMDD_HHMMSS}{ext}``.
    For example, translating ``report.pdf`` from Japanese to English might
    produce ``report_Japaneseto English_20260623_143500.txt``.

    Args:
        input_file: Path to the source document (used to derive the base name
                    and the output directory).
        source_lang: Full name of the source language (e.g. ``'Japanese'``).
        target_lang: Full name of the target language (e.g. ``'English'``).
        extension: File extension for the output file, including the leading
                   dot (e.g. ``'.txt'``, ``'.pdf'``). Defaults to ``'.txt'``.

    Returns:
        Absolute path string for the new output file.
    """
    input_path = Path(input_file)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_name = f"{input_path.stem}_{source_lang}to{target_lang}_{timestamp}{extension}"
    return str(input_path.parent / output_name)


class FileOutputHandler:
    """Routes translated content to the correct file format and manages output paths.

    All format-specific writing logic lives in the builder modules
    (``pdf_builder``, ``docx_builder``, etc.). This class provides a single
    stable entry point — ``save_translation_output`` — that the rest of the
    application calls regardless of output format. It also exposes each
    builder's ``save_to_*`` method directly so callers can write a specific
    format without going through the full orchestration path.
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
    save_to_excel                    = staticmethod(save_to_excel)
    save_to_json                     = staticmethod(save_to_json)
    save_to_markdown                 = staticmethod(save_to_markdown)

    @staticmethod
    def _resolve_output_path(
        input_file: Optional[str],
        output_file: Optional[str],
        auto_save: bool,
        source_lang: str,
        target_lang: str,
        default_extension: str = '.txt',
    ) -> Optional[str]:
        """Determine the output file path from an explicit path or auto-save settings.

        Returns the explicit path if one was given, generates a timestamped
        name when auto-save is enabled, or returns ``None`` if neither applies
        (meaning the caller should not write a file).
        """
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
        """Write the complete translated content to a file in the format determined by the output path's extension.

        Chooses the correct builder automatically: ``.pdf`` → PDF builder,
        ``.docx`` → Word builder, ``.xlsx`` → Excel, ``.json`` → JSON,
        ``.md`` → Markdown, anything else → plain text. If no output path can
        be determined (no ``output_file`` and ``auto_save`` is ``False``), the
        method returns without writing anything.

        Args:
            content: The full translated text to write.
            input_file: Path to the original source document, used to generate
                        a filename when ``auto_save`` is ``True``. May be
                        ``None`` for custom-text translations.
            output_file: Explicit output file path requested by the user via
                         ``-o``. Takes precedence over auto-save. ``None``
                         means fall back to auto-save or skip.
            auto_save: When ``True`` and no ``output_file`` is given, generates
                       a timestamped filename next to the source document.
            source_lang: Full source language name, used in the auto-generated
                         filename (e.g. ``'Japanese'``).
            target_lang: Full target language name, used in the filename and
                         passed to the PDF/Word builders for font selection
                         (e.g. ``'English'``).
            custom_font: Path to a custom font file to use in PDF or Word
                         output. ``None`` uses the built-in default.
            media: A list of ``EmbeddedMedia`` objects extracted from the
                   source document to be re-embedded in the output Word file.
                   Ignored for all other formats.
            table_registry: A dictionary mapping table placeholder tokens to
                            translated cell grids, used to reconstruct Word
                            tables in the output DOCX. Ignored for other formats.
            font_size: Font size override for PDF or Word output. ``None`` uses
                       the builder default.
            label: A short label shown in the terminal message when the file is
                   saved (e.g. ``'Translation'``, ``'Transcription'``).
        """
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
        if extension == '.xlsx':
            FileOutputHandler.save_to_excel(content, output_path, label=label)
            return
        if extension == '.json':
            FileOutputHandler.save_to_json(content, output_path, label=label)
            return
        if extension == '.md':
            FileOutputHandler.save_to_markdown(content, output_path, label=label)
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
        """Append a single translated page to the output file as soon as it is ready.

        Writes immediately after each page is translated so the user can see
        partial results without waiting for the full document to finish.
        On the first page the file is created (or overwritten); subsequent
        pages are appended. PDF, Word, Excel, and JSON formats do not support
        page-by-page appending, so they are automatically redirected to a
        plain text file with a notice in the terminal.

        Args:
            content: The translated text for this page.
            input_file: Path to the source document, used for auto-generated
                        filenames when ``auto_save`` is ``True``.
            output_file: Explicit output path, or ``None`` to use auto-save.
            auto_save: Generate a filename automatically when ``True`` and no
                       ``output_file`` is given.
            source_lang: Full source language name (e.g. ``'Japanese'``).
            target_lang: Full target language name (e.g. ``'English'``).
            label: Short label for the terminal message (e.g. ``'Translation'``).
            is_first_page: ``True`` for the first page of a document — creates
                           or overwrites the file. ``False`` appends to it.
            custom_font: Reserved for future use; currently ignored.

        Returns:
            The absolute path of the file that was written, or ``None`` if no
            output path could be determined.
        """
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
        elif extension == '.xlsx':
            _emit_message(
                "Note: Progressive saving for Excel format not yet supported. Using text format.",
                level=logging.INFO,
            )
            output_path = str(Path(output_path).with_suffix('.txt'))
        elif extension == '.json':
            _emit_message(
                "Note: Progressive saving for JSON format not yet supported. Using text format.",
                level=logging.INFO,
            )
            output_path = str(Path(output_path).with_suffix('.txt'))

        current_ext = Path(output_path).suffix.lower()
        if current_ext == '.md':
            # Markdown can be progressively appended as plain text.
            if is_first_page:
                FileOutputHandler.save_to_markdown(content, output_path, label)
            else:
                FileOutputHandler.append_to_text_file(content, output_path, label)
            return output_path

        if current_ext != '.txt':
            output_path = f"{output_path}.txt"

        if is_first_page:
            FileOutputHandler.save_to_text_file(content, output_path, label)
        else:
            FileOutputHandler.append_to_text_file(content, output_path, label)

        return output_path
