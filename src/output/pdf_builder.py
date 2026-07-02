"""PDF document builder for translated content."""

import logging
from pathlib import Path
from typing import Optional

from .font_resolver import get_pdf_font
from ._output_utils import _emit_message, _normalize_paragraphs, _extract_markdown_tables, save_to_text_file
from ..settings import DEFAULT_FONT_SIZE

# PDF page margins (in points, 72 pts = 1 inch)
PDF_MARGINS = {
    'left': 72,
    'right': 72,
    'top': 72,
    'bottom': 18,
}


def save_to_pdf(
    content: str,
    output_path: str,
    custom_font: Optional[str] = None,
    target_lang: Optional[str] = None,
    table_registry: Optional[dict] = None,
    font_size: Optional[int] = None,
    *,
    label: str,
) -> None:
    """Save the AI's response text to a PDF file.

    Uses the reportlab library to build the document. If any tables were set
    aside for separate translation, their placeholder markers (like
    ``'[TABLE_1]'``) are swapped out for real PDF tables with visible grid
    lines, instead of being printed as plain text.

    Args:
        content: The AI's response text to write into the PDF.
        output_path: The file path to save to, e.g. ``'translated.pdf'``.
        custom_font: The name of a specific font to use. ``None`` chooses a
                     sensible default automatically based on the target
                     language.
        target_lang: The language the content was translated into (e.g.
                     ``'English'``, ``'Korean'``), used to decide whether a
                     font capable of displaying non-Latin scripts is needed.
                     ``None`` if this isn't a translation.
        table_registry: A dictionary mapping each table placeholder marker
                        (e.g. ``'[TABLE_1]'``) to that table's translated
                        cell text (a list of rows, each row a list of cell
                        strings). ``None`` if no tables were extracted for
                        this document.
        font_size: The body text size, in points, e.g. ``11``. ``None`` uses
                   the default size.
        label: A short description used in the saved-confirmation message
               shown to the user (e.g. ``'Translation'``).
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Flowable, Table, TableStyle
        from reportlab.lib import colors

        doc = SimpleDocTemplate(
            output_path,
            pagesize=letter,
            rightMargin=PDF_MARGINS['right'],
            leftMargin=PDF_MARGINS['left'],
            topMargin=PDF_MARGINS['top'],
            bottomMargin=PDF_MARGINS['bottom'],
        )

        fs = font_size if font_size is not None else DEFAULT_FONT_SIZE
        story: list[Flowable] = []
        styles = getSampleStyleSheet()

        if not custom_font and target_lang == 'English':
            font_name = 'Times-Roman'
            logging.debug(f"Using Times-Roman for English translation (target_lang={target_lang})")
        else:
            font_name = get_pdf_font(custom_font)
            logging.debug(f"Using CJK font: {font_name} (custom_font={custom_font}, target_lang={target_lang})")

        try:
            normal_style = ParagraphStyle(
                'CJKNormal',
                parent=styles['Normal'],
                fontName=font_name,
                fontSize=fs,
                leading=round(fs * 1.5),
                spaceAfter=fs,
                encoding='utf-8',
            )
            logging.debug(f"Created paragraph style with font: {font_name}")
        except (TypeError, ValueError, KeyError) as e:
            logging.warning(f"Failed to create custom style with font {font_name}: {e}")
            normal_style = styles['Normal']
            font_name = 'Times-Roman'

        fallback_style = ParagraphStyle(
            'FallbackCJK',
            parent=styles['Normal'],
            fontName='Times-Roman',
            fontSize=fs,
            leading=round(fs * 1.5),
            spaceAfter=fs,
        )

        # Extract any inline Markdown tables and merge into the table_registry.
        content, _md_reg = _extract_markdown_tables(content)
        if _md_reg:
            table_registry = dict(table_registry) if table_registry else {}
            table_registry.update(_md_reg)

        for i, clean_text in enumerate(_normalize_paragraphs(content), start=1):

            # --- Table placeholder: render as a reportlab Table flowable ---
            if table_registry and clean_text.strip() in table_registry:
                rows = table_registry[clean_text.strip()]
                if rows:
                    try:
                        cell_data = [
                            [Paragraph(cell or '', normal_style) for cell in row]
                            for row in rows
                        ]
                        tbl = Table(cell_data, hAlign='LEFT')
                        tbl.setStyle(TableStyle([
                            ('GRID',       (0, 0), (-1, -1), 0.5, colors.black),
                            ('BACKGROUND', (0, 0), (-1, 0),  colors.lightgrey),
                            ('FONTNAME',   (0, 0), (-1, -1), font_name),
                            ('FONTSIZE',   (0, 0), (-1, -1), max(fs - 2, 6)),
                            ('TOPPADDING',    (0, 0), (-1, -1), 4),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ]))
                        story.append(tbl)
                        story.append(Spacer(1, 12))
                        logging.debug(
                            f"Inserted PDF table for '{clean_text.strip()}' ({len(rows)} row(s))"
                        )
                        continue
                    except Exception as tbl_err:
                        logging.warning(
                            f"Could not render PDF table for '{clean_text.strip()}': {tbl_err}; "
                            "falling back to plain text"
                        )

            try:
                paragraph = Paragraph(clean_text, normal_style)
                story.append(paragraph)
                story.append(Spacer(1, 12))
                logging.debug(f"Successfully added paragraph {i} with font {font_name}")
            except Exception as paragraph_error:
                logging.warning(f"Error processing paragraph {i} with font {font_name}: {paragraph_error}")
                try:
                    paragraph = Paragraph(clean_text, fallback_style)
                    story.append(paragraph)
                    story.append(Spacer(1, 12))
                    logging.debug(f"Used fallback font Times-Roman for paragraph {i}")
                except Exception as fallback_error:
                    logging.warning(f"Fallback font also failed for paragraph {i}: {fallback_error}")
                    ascii_safe_text = clean_text.encode('ascii', 'ignore').decode('ascii')
                    if ascii_safe_text.strip():
                        paragraph = Paragraph(ascii_safe_text, styles['Normal'])
                        story.append(paragraph)
                        story.append(Spacer(1, 12))
                        logging.warning(f"Used ASCII-safe fallback for paragraph {i}")
                    else:
                        logging.warning(f"Paragraph {i} contained no ASCII-safe characters, skipping")

        if story:
            doc.build(story)
            _emit_message(
                f"{label} saved to PDF: {Path(output_path).name}",
                level=logging.INFO,
                log_message=f'{label} saved to PDF file: {output_path}',
                leading_newline=True,
            )
            if font_name != 'Times-Roman':
                _emit_message(f"Used font: {font_name}", level=logging.DEBUG)
        else:
            _emit_message(
                "Error: No content could be processed for PDF generation",
                level=logging.ERROR,
                log_message="No content could be processed for PDF generation",
            )
            _fallback_to_text(content, output_path, label)

    except ImportError:
        _emit_message(
            "Warning: reportlab not installed. Saving as text file instead.",
            level=logging.WARNING,
            log_message='reportlab not installed. Falling back to text file.',
        )
        _fallback_to_text(content, output_path, label)
    except Exception as e:
        _emit_message(
            f"Error generating PDF: {e}",
            level=logging.ERROR,
            log_message=f'Error saving to PDF: {e}',
        )
        _emit_message(
            "Falling back to text file for reliable CJK character support...",
            level=logging.WARNING,
        )
        _fallback_to_text(content, output_path, label)


def _fallback_to_text(content: str, output_path: str, label: str) -> None:
    """Save as a plain .txt file instead, used when PDF generation fails.

    Args:
        content: The text to save.
        output_path: The originally-requested .pdf path; the extension is
                     swapped to .txt automatically.
        label: A short description used in the saved-confirmation message
               shown to the user.
    """
    text_output_path = str(Path(output_path).with_suffix('.txt'))
    save_to_text_file(content, text_output_path, label)
