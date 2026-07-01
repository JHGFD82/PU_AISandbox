"""File-type detection shared by every plugin that accepts document or image input.

This stays in core (not owned by any single plugin) because both the
translate and transcribe commands need to know "what kind of file is this"
before routing to their own pipeline.
"""

import logging
import os

from ..errors import CLIError

logger = logging.getLogger(__name__)

# Maps file extension → (file_type token, human-readable label).
_EXT_TYPES: dict[str, tuple[str, str]] = {
    '.pdf':  ('pdf',      'PDF file'),
    '.docx': ('docx',     'Word document'),
    '.txt':  ('txt',      'text file'),
    '.xlsx': ('excel',    'Excel spreadsheet'),
    '.xls':  ('excel',    'Excel spreadsheet'),
    '.json': ('json',     'JSON file'),
    '.md':   ('markdown', 'Markdown file'),
}


class _FileTypeMixin:
    """File-type detection capabilities added to SandboxProcessor.

    Always present (not plugin-registered) because both document-handling
    and image-handling plugins need to identify what kind of file they've
    been handed before routing it to their own pipeline.
    """

    def _detect_and_validate_file(self, file_path: str) -> str:
        """Check that a file exists and identify its type.

        Recognises image files by content as well as by extension, so formats
        like ``.jpg`` and ``.png`` are handled alongside document formats.

        Args:
            file_path: Absolute path to the file to inspect.

        Returns:
            A short type token: one of ``'image'``, ``'pdf'``, ``'docx'``,
            ``'txt'``, ``'excel'``, ``'json'``, or ``'markdown'``.

        Raises:
            CLIError: If the file does not exist, fails image validation, or
                has an extension not supported by any installed plugin.
        """
        if not os.path.exists(file_path):
            raise CLIError(f"File '{file_path}' not found.")

        logger.debug(f"Validating file: {file_path}")
        lower_path = file_path.lower()

        if self.image_processor.is_image_file(file_path):  # type: ignore[attr-defined]
            if not self.image_processor.validate_image_file(file_path):  # type: ignore[attr-defined]
                raise CLIError(f"Image file '{file_path}' is not valid.")
            logger.debug(f"Detected image file: {file_path}")
            return 'image'

        _, ext = os.path.splitext(lower_path)
        if ext in _EXT_TYPES:
            file_type, label = _EXT_TYPES[ext]
            logger.debug(f"Detected {label}: {file_path}")
            return file_type

        raise CLIError(
            "Unsupported file format. Supported formats: PDF, DOCX, TXT, XLSX, JSON, MD, "
            "or image files (JPG, PNG, etc.)"
        )
