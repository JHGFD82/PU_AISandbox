"""Chooses the right font for PDF and Word output, including support for non-Latin scripts."""

import logging
from pathlib import Path
from typing import Optional


def _fonts_dir() -> Path:
    """Return the path to this project's ``fonts/`` folder."""
    return Path(__file__).resolve().parents[2] / 'fonts'


def _emit_warning(message: str, log_message: Optional[str] = None) -> None:
    """Show a warning to the user on-screen and record the same event in the log file.

    Args:
        message: The warning text to print to the terminal.
        log_message: A more detailed version of the message to write to the
                     log file, if different from what's shown on-screen.
                     ``None`` reuses ``message`` for both.
    """
    logging.warning(log_message or message)
    print(message)


def get_pdf_font(custom_font: Optional[str] = None) -> str:
    """Choose which font to use when writing a PDF, favoring ones that can display CJK text.

    CJK refers to Chinese, Japanese, and Korean writing systems, which need a
    font specifically designed to include those characters — most standard
    fonts cannot display them at all. This function looks in the project's
    ``fonts/`` folder for a usable font, in this order:

    1. The font named in ``custom_font``, if provided and found.
    2. One of a small list of preferred CJK-capable fonts bundled with common
       operating systems (e.g. Arial Unicode, AppleGothic).
    3. Any other ``.ttf`` font file found in the ``fonts/`` folder.
    4. As a last resort, a plain built-in font that won't display CJK
       characters correctly, printing guidance on how to fix this.

    Args:
        custom_font: The name of a specific font file (without the ``.ttf``
                     extension) to look for first, e.g. ``'NotoSansCJK'``.
                     ``None`` skips straight to the preferred font list.

    Returns:
        The name of the font to use with the PDF-generation library
        (reportlab). Falls back to ``'Times-Roman'`` if no CJK font could be
        found or if the PDF-generation library isn't installed.
    """
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        fonts_dir = _fonts_dir()
        if fonts_dir.exists():
            if custom_font:
                custom_font_path = fonts_dir / f"{custom_font}.ttf"
                if custom_font_path.exists():
                    try:
                        custom_font_name = f"CustomFont_{custom_font}"
                        if custom_font_name not in pdfmetrics.getRegisteredFontNames():
                            pdfmetrics.registerFont(TTFont(custom_font_name, str(custom_font_path)))  # type: ignore
                        logging.info(f"Using custom CJK font: {custom_font_name}")
                        return custom_font_name
                    except (OSError, ValueError, TypeError) as error:
                        _emit_warning(
                            f"Warning: Custom font '{custom_font}' failed to load. Using default font selection.",
                            log_message=f"Failed to register custom font {custom_font}: {error}",
                        )
                else:
                    _emit_warning(
                        f"Warning: Custom font '{custom_font}.ttf' not found in fonts/ directory. Using default font selection.",
                        log_message=f"Custom font file not found: {custom_font_path}",
                    )

            preferred_fonts = [
                ('Arial Unicode.ttf', 'ArialUnicode'),
                ('AppleGothic.ttf', 'AppleGothic'),
                ('AppleMyungjo.ttf', 'AppleMyungjo'),
            ]

            for font_filename, font_name in preferred_fonts:
                font_path = fonts_dir / font_filename
                if font_path.exists():
                    try:
                        if font_name not in pdfmetrics.getRegisteredFontNames():
                            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))  # type: ignore
                        logging.info(f"Using preferred CJK font: {font_name} ({font_filename})")
                        return font_name
                    except (OSError, ValueError, TypeError) as error:
                        logging.warning(f"Failed to register preferred font {font_name}: {error}")
                        continue

            for font_path in fonts_dir.glob('*.ttf'):
                safe_font_name = font_path.stem.replace('-', '_').replace(',', '_').replace(' ', '_')
                try:
                    if safe_font_name not in pdfmetrics.getRegisteredFontNames():
                        pdfmetrics.registerFont(TTFont(safe_font_name, str(font_path)))  # type: ignore
                    logging.info(f"Using available CJK font: {safe_font_name} ({font_path.name})")
                    return safe_font_name
                except (OSError, ValueError, TypeError) as error:
                    logging.warning(f"Failed to register font {safe_font_name}: {error}")
                    continue

        _emit_warning("Warning: No CJK fonts available for PDF generation.", log_message="No CJK fonts found in fonts/ directory.")
        print("To fix: Add CJK .ttf fonts to the 'fonts/' directory in this project.")
        print("Note: Only .ttf fonts are supported. OTF fonts will not work with reportlab.")
        print("Recommended CJK fonts:")
        print("  - Arial Unicode MS (Microsoft)")
        print("  - Source Han Sans (Adobe): https://github.com/adobe-fonts/source-han-sans")
        print("  - Apple system fonts (AppleGothic, AppleMyungjo)")
        print("Alternative: Save as .txt file for proper CJK character display.")
        return 'Times-Roman'

    except ImportError as error:
        logging.warning(f"reportlab is unavailable for PDF font resolution: {error}")
        return 'Times-Roman'
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        logging.warning(f"Error checking CJK fonts: {error}")
        return 'Times-Roman'


def get_docx_font(custom_font: Optional[str] = None) -> str:
    """Choose which font to use when writing a Word document, favoring ones that can display CJK text.

    CJK refers to Chinese, Japanese, and Korean writing systems, which need a
    font specifically designed to include those characters. Unlike PDF
    generation, Word documents only need the font's name (Word itself locates
    and renders the actual font file), so this function mainly returns a
    sensible font name rather than registering font files.

    Args:
        custom_font: The name of a specific font to use, e.g.
                     ``'NotoSansCJK'``. Only used if a matching ``.ttf`` file
                     exists in the ``fonts/`` folder; otherwise ignored.
                     ``None`` skips straight to the preferred font list.

    Returns:
        The font name to apply to the Word document. Falls back to
        ``'Times New Roman'`` if a filesystem error prevents checking for
        fonts.
    """
    try:
        fonts_dir = _fonts_dir()
        if fonts_dir.exists() and custom_font:
            custom_font_path = fonts_dir / f"{custom_font}.ttf"
            if custom_font_path.exists():
                logging.info(f"Using custom CJK font for Word: {custom_font}")
                return custom_font
            _emit_warning(
                f"Warning: Custom font '{custom_font}.ttf' not found in fonts/ directory. Using default font selection.",
                log_message=f"Custom font file not found: {custom_font_path}",
            )

        preferred_word_fonts = [
            'Arial Unicode MS',
            'AppleGothic',
            'AppleMyungjo',
            'Arial',
            'Calibri',
        ]

        selected_font = preferred_word_fonts[0]
        logging.info(f"Using CJK font for Word: {selected_font}")
        return selected_font

    except OSError as error:
        logging.warning(f"Error checking fonts for Word document: {error}")
        return 'Times New Roman'
