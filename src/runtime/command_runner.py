"""CLI command dispatch mixin for SandboxProcessor.

This module holds the ``_CommandMixin`` class, which translates parsed CLI
arguments into calls on the concrete ``SandboxProcessor`` processing methods.
It is split out from ``sandbox_processor.py`` solely for readability; all
``self.*`` references resolve on the ``SandboxProcessor`` subclass via normal
Python MRO.
"""

import argparse
import logging
import os
import sys
from typing import TYPE_CHECKING, Any, Optional, Tuple, cast

from ..errors import CLIError
from ..models import OutputOptions
from ..processors.constants import IMAGE_EXTENSIONS
from ..processors.docx_processor import DocxProcessor
from ..processors.pdf_processor import generate_process_text
from ..processors.txt_processor import TxtProcessor
from ..settings import DEFAULT_PAGE_SIZE

if TYPE_CHECKING:
    from ..processors.image_processor import ImageProcessor
    from ..processors.pdf_processor import PDFProcessor
    from ..services.image_processor_service import ImageProcessorService
    from ..services.image_translation_service import ImageTranslationService
    from ..services.prompt_service import PromptService
    from ..services.transcription_review_service import TranscriptionReviewService
    from ..services.translation_service import TranslationService

logger = logging.getLogger(__name__)


class _CommandMixin:
    """Mixin that adds CLI command dispatch to SandboxProcessor.

    All instance-method references to ``self.*`` resolve on the concrete
    ``SandboxProcessor`` subclass via normal Python MRO.
    """

    if TYPE_CHECKING:
        # Attributes and processing methods provided by the SandboxProcessor subclass.
        image_processor: "ImageProcessor"
        image_translation_service: "ImageTranslationService"
        translation_service: "TranslationService"
        image_processor_service: "ImageProcessorService"
        pdf_processor: "PDFProcessor"
        prompt_service: "PromptService"
        transcription_review_service: "TranscriptionReviewService"

        def _detect_and_validate_file(self, file_path: str) -> str: ...
        def translate_custom_text(self, source_language: str, target_language: str, abstract: bool, opts: OutputOptions) -> None: ...
        def process_image_translation_folder(self, folder_path: str, source_language: str, target_language: str, opts: OutputOptions, workers: int = 1, spread: bool = False) -> None: ...
        def translate_document(self, file_path: str, source_language: str, target_language: str, page_nums: Optional[str], abstract: bool, opts: OutputOptions, workers: int = 1, spread: bool = False, scanned: bool = False) -> None: ...
        def process_image_folder(self, folder_path: str, target_language: str, output_file: Optional[str] = None, vertical: bool = False, spread: bool = False, passes: int = 1, workers: int = 1) -> None: ...
        def process_image(self, file_path: str, target_language: str, output_file: Optional[str] = None, vertical: bool = False, spread: bool = False, passes: int = 1) -> None: ...
        def process_prompt(self, user_prompt: str, system_prompt: Optional[str] = None, output_file: Optional[str] = None) -> None: ...
        def process_transcription_review(self, text: str, language: str, kanbun: bool = False, kanbun_main: bool = False, output_file: Optional[str] = None) -> None: ...

    @staticmethod
    def _collect_multiline(label: str) -> str:
        """Print a prompt label and collect lines until '---' or EOF."""
        print(f"{label} (type --- on its own line when done):")
        lines: list[str] = []
        while True:
            try:
                line = input()
                if line.strip() == '---':
                    break
                lines.append(line)
            except EOFError:
                break
        return '\n'.join(lines)

    @staticmethod
    def _collect_notes(
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Optionally display the current prompts, then collect note text to append.

        If *system_prompt* or *user_prompt* are provided they are shown before
        the question so the user has context for what they are annotating.

        Options:
          system   — one note appended to the system prompt only
          user     — one note appended to the user prompt only
          both     — the same note appended to both prompts
          separate — different notes collected individually for system then user
        """
        if system_prompt is not None or user_prompt is not None:
            sep = "-" * 70
            print(f"\n{sep}")
            print("  CURRENT PROMPTS  (your notes will be appended to these)")
            print(sep)
            if system_prompt is not None:
                print("\n--- SYSTEM PROMPT ---")
                print(system_prompt)
            if user_prompt is not None:
                print("\n--- USER PROMPT ---")
                print(user_prompt)
            print(f"\n{sep}\n")

        while True:
            try:
                target = input("Add notes to (system / user / both / separate): ").strip().lower()
            except EOFError:
                return None, None
            if target in ('system', 'user', 'both', 'separate'):
                break
            print("Please enter 'system', 'user', 'both', or 'separate'.")

        if target == 'separate':
            system_note = _CommandMixin._collect_multiline("System note") or None
            user_note   = _CommandMixin._collect_multiline("User note")   or None
            return system_note, user_note

        note_text = _CommandMixin._collect_multiline("Notes")
        if not note_text.strip():
            return None, None

        system_note = note_text if target in ('system', 'both') else None
        user_note   = note_text if target in ('user',   'both') else None
        return system_note, user_note

    @staticmethod
    def _apply_inline_notes(service: Any, args: argparse.Namespace) -> None:
        """Apply inline note flags (-ns/-nu/-nb) from *args* to *service*.

        -nb (note_both) sets both slots; -ns/-nu (note_system/note_user) set
        individually and take precedence over -nb for their own slot.
        """
        _inline_both = getattr(args, 'note_both', None)
        _inline_sys  = getattr(args, 'note_system', None) or _inline_both
        _inline_usr  = getattr(args, 'note_user', None)   or _inline_both
        if _inline_sys is not None:
            service.system_note = _inline_sys
        if _inline_usr is not None:
            service.user_note = _inline_usr

    @staticmethod
    def _sampling_kwargs(args: argparse.Namespace) -> dict:
        """Return temperature/top_p/max_tokens from args for _dry_run_display."""
        return {
            'temperature': getattr(args, 'temperature', None),
            'top_p': getattr(args, 'top_p', None),
            'max_tokens': getattr(args, 'max_tokens', None),
        }

    @staticmethod
    def _dry_run_display(model: str, system_prompt: str, user_prompt: str, note: Optional[str] = None,
                         temperature: Optional[float] = None, top_p: Optional[float] = None,
                         max_tokens: Optional[int] = None) -> None:
        """Print prompts in a structured format without making any API calls."""
        sep = "=" * 70
        print(f"\n{sep}")
        print("  DRY RUN — No API call will be made")
        print(f"  Model: {model}")
        if temperature is not None:
            print(f"  Temperature: {temperature}")
        if top_p is not None:
            print(f"  Top-p: {top_p}")
        if max_tokens is not None:
            print(f"  Max tokens: {max_tokens}")
        if note:
            print(f"  Note:  {note}")
        print(sep)
        print("\n--- SYSTEM PROMPT " + "-" * 52)
        print(system_prompt)
        print("\n--- USER PROMPT " + "-" * 54)
        print(user_prompt)
        print(f"\n{sep}\n")

    def _resolve_output_path(self, args: argparse.Namespace) -> Optional[str]:
        """Resolve output file path based on arguments."""
        output_file_arg: Optional[str] = getattr(args, 'output_file', None)
        input_file_arg: Optional[str] = getattr(args, 'input_file', None)

        if output_file_arg:
            if not os.path.isabs(output_file_arg) and input_file_arg:
                abs_input = os.path.abspath(input_file_arg)
                input_dir = abs_input if os.path.isdir(abs_input) else os.path.dirname(abs_input)
                return os.path.join(input_dir, output_file_arg)
            return os.path.abspath(output_file_arg)

        return None

    def _run_translate(self, args: argparse.Namespace) -> None:
        """Handle the 'translate' command."""
        language_code = args.language_code

        if not isinstance(language_code, tuple) or len(language_code) != 2:
            raise CLIError("Translation requires a 2-character language code (e.g., CE, JE, KE)")

        lang_tuple = cast(Tuple[str, str], language_code)

        source_language: str = lang_tuple[0]
        target_language: str = lang_tuple[1]

        # --scanned compatibility checks
        if getattr(args, 'scanned', False):
            _scanned_input: Optional[str] = getattr(args, 'input_file', None)
            if not _scanned_input:
                raise CLIError("--scanned requires a file input (-i).")
            if getattr(args, 'custom_text', False):
                raise CLIError("Cannot use --scanned with custom text input (-c).")
            _scanned_ext = os.path.splitext(_scanned_input)[1].lower()
            if _scanned_ext != '.pdf':
                raise CLIError(
                    f"--scanned is only valid for PDF files (got '{_scanned_ext}'). "
                    "For image files, the translate command routes through OCR automatically."
                )
            if getattr(args, 'preserve_media', False):
                raise CLIError("Cannot combine --scanned with --preserve-media.")

        # --preserve-media compatibility checks — raise an error immediately so
        # the user can correct their command before any tokens are spent.
        if getattr(args, 'preserve_media', False):
            if getattr(args, 'progressive_save', False):
                raise CLIError("Cannot combine --preserve-media with --progressive-save.")
            if getattr(args, 'custom_text', False):
                raise CLIError(
                    "Cannot use --preserve-media with custom text input (-c): "
                    "pasted text contains no embedded media."
                )
            input_file_arg: Optional[str] = getattr(args, 'input_file', None)
            if not input_file_arg:
                raise CLIError(
                    "Cannot use --preserve-media without a file input (-i)."
                )
            input_ext = os.path.splitext(input_file_arg)[1].lower()
            if input_ext in IMAGE_EXTENSIONS:
                raise CLIError(
                    "Cannot use --preserve-media with an image file input: "
                    "images have no embedded media to carry over."
                )
            if input_ext not in ('.docx', '.pdf'):
                raise CLIError(
                    f"Cannot use --preserve-media with '{input_ext}' files: "
                    "media preservation supports Word documents (.docx) and PDF files (.pdf)."
                )
            output_file_arg: Optional[str] = getattr(args, 'output_file', None)
            if getattr(args, 'auto_save', False) and not output_file_arg:
                raise CLIError(
                    "Cannot use --preserve-media with --auto-save: auto-save produces a .txt file. "
                    "Specify a .docx output with -o."
                )
            if not output_file_arg:
                raise CLIError(
                    "Cannot use --preserve-media without a .docx output file. "
                    "Specify an output with -o, e.g. -o translated.docx."
                )
            out_ext = os.path.splitext(output_file_arg)[1].lower()
            if out_ext == '.txt':
                raise CLIError(
                    "--preserve-media requires a .docx output file; "
                    ".txt files cannot embed images."
                )
            if out_ext == '.pdf':
                raise CLIError(
                    "--preserve-media does not yet support PDF output. "
                    "Specify a .docx output file with -o."
                )
            if out_ext != '.docx':
                raise CLIError(
                    "--preserve-media requires a .docx output file "
                    f"(got '{out_ext}')."
                )

        if getattr(args, 'notes', False):
            # Build a prompt preview so the user can see what they are annotating.
            # Use the image-translation prompts for image inputs, text-translation
            # prompts (with a placeholder) for everything else.
            _preview_sys: Optional[str] = None
            _preview_usr: Optional[str] = None
            if args.input_file:
                _fp = os.path.abspath(args.input_file)
                if os.path.exists(_fp) and self.image_processor.is_image_file(_fp):
                    _preview_sys, _preview_usr = self.image_translation_service.build_prompts(
                        source_language, target_language
                    )
                else:
                    _placeholder = generate_process_text("", f"[{source_language} document text]", "")
                    _preview_sys, _preview_usr = self.translation_service.build_prompts(
                        _placeholder, source_language, target_language
                    )
            else:
                _placeholder = generate_process_text("", f"[{source_language} custom text]", "")
                _preview_sys, _preview_usr = self.translation_service.build_prompts(
                    _placeholder, source_language, target_language
                )
            sys_note, usr_note = self._collect_notes(_preview_sys, _preview_usr)
            self.translation_service.system_note = sys_note
            self.translation_service.user_note = usr_note
            self.image_translation_service.system_note = sys_note
            self.image_translation_service.user_note = usr_note

        # Inline note flags (-ns / -nu / -nb) apply directly without interactive flow.
        # -nb sets both; -ns/-nu set individually (and override -nb for their slot).
        self._apply_inline_notes(self.translation_service, args)
        self._apply_inline_notes(self.image_translation_service, args)

        if getattr(args, 'kanbun', False):
            self.translation_service.kanbun = True

        if getattr(args, 'preserve_tables', False):
            self.translation_service.tables = True
            self.image_translation_service.tables = True

        if getattr(args, 'toc', False):
            self.translation_service.toc = True

        if getattr(args, 'dry_run', False):
            model_dr = self.translation_service._get_model()
            abstract_text_dr: Optional[str] = None
            if getattr(args, 'abstract', False):
                abstract_text_dr = self._collect_multiline("Abstract text") or None

            if args.input_file:
                file_path_dr = os.path.abspath(args.input_file)
                file_type_dr = self._detect_and_validate_file(file_path_dr)
                if file_type_dr == 'image':
                    spread_dr = getattr(args, 'spread', False)
                    sys_p, usr_p = self.image_translation_service.build_prompts(source_language, target_language, spread=spread_dr)
                    self._dry_run_display(
                        self.image_translation_service._get_model(), sys_p, usr_p,
                        note="Image content would be base64-encoded and attached to the user message",
                        **self._sampling_kwargs(args),
                    )
                    return
                elif file_type_dr == 'pdf':
                    if getattr(args, 'scanned', False):
                        spread_dr = getattr(args, 'spread', False)
                        sys_p, usr_p = self.image_translation_service.build_prompts(
                            source_language, target_language, spread=spread_dr
                        )
                        self._dry_run_display(
                            self.image_translation_service._get_model(), sys_p, usr_p,
                            note="Scanned PDF: each page will be rendered as an image and attached to the user message",
                            **self._sampling_kwargs(args),
                        )
                        return
                    with open(file_path_dr, 'rb') as f:
                        first_page = next(iter(self.pdf_processor.process_pdf(f)), None)
                        page_text_dr = self.pdf_processor.process_page(first_page) if first_page else "[no text found in PDF]"
                elif file_type_dr == 'docx':
                    with open(file_path_dr, 'rb') as f:
                        pages_dr = DocxProcessor.process_docx_with_pages(f, target_page_size=DEFAULT_PAGE_SIZE)
                        page_text_dr = pages_dr[0] if pages_dr else "[no text found in document]"
                elif file_type_dr == 'txt':
                    with open(file_path_dr, 'r', encoding='utf-8') as f:
                        pages_dr = TxtProcessor.process_txt_with_pages(f, target_page_size=DEFAULT_PAGE_SIZE)
                        page_text_dr = pages_dr[0] if pages_dr else "[no text found in file]"
                else:
                    page_text_dr = f"[{source_language} text to translate]"
            elif args.custom_text:
                page_text_dr = self._collect_multiline(
                    f"Enter the {source_language} text you want to translate to {target_language}"
                )
                if not page_text_dr.strip():
                    page_text_dr = f"[{source_language} text to translate]"
            else:
                page_text_dr = f"[{source_language} text to translate]"

            combined = generate_process_text(abstract_text_dr or "", page_text_dr, "")
            context_type_dr = "abstract" if abstract_text_dr else "none"
            output_file_dr = getattr(args, 'output_file', None)
            auto_save_dr = getattr(args, 'auto_save', False)
            if output_file_dr:
                ext = output_file_dr.lower().rsplit('.', 1)[-1] if '.' in output_file_dr else ''
                output_format_dr = {'pdf': 'pdf', 'docx': 'docx', 'txt': 'txt'}.get(ext, 'file')
            elif auto_save_dr:
                output_format_dr = 'txt'
            else:
                output_format_dr = 'console'
            sys_p, usr_p = self.translation_service.build_prompts(combined, source_language, target_language, output_format=output_format_dr, context_type=context_type_dr)
            self._dry_run_display(model_dr, sys_p, usr_p, **self._sampling_kwargs(args))
            return

        opts = OutputOptions(
            output_file=self._resolve_output_path(args),
            auto_save=getattr(args, 'auto_save', False),
            progressive_save=getattr(args, 'progressive_save', False),
            custom_font=getattr(args, 'custom_font', None),
            preserve_media=getattr(args, 'preserve_media', False),
            font_size=getattr(args, 'font_size', None),
        )
        workers = getattr(args, 'workers', 1)
        spread = getattr(args, 'spread', False)
        if args.custom_text:
            self.translate_custom_text(
                source_language,
                target_language,
                getattr(args, 'abstract', False),
                opts,
            )
        elif args.input_file:
            input_path = os.path.abspath(args.input_file)
            if os.path.isdir(input_path):
                self.process_image_translation_folder(
                    input_path,
                    source_language,
                    target_language,
                    opts,
                    workers=workers,
                    spread=spread,
                )
            else:
                self.translate_document(
                    args.input_file,
                    source_language,
                    target_language,
                    getattr(args, 'page_nums', None),
                    getattr(args, 'abstract', False),
                    opts,
                    workers=workers,
                    spread=spread,
                    scanned=getattr(args, 'scanned', False),
                )
        else:
            raise CLIError("No input specified. Use -i for file input or -c for custom text.")

    def _run_transcribe(self, args: argparse.Namespace) -> None:
        """Handle the 'transcribe' command."""
        # language_code is already resolved to a full name by parse_single_language_code
        target_language: str = args.language_code

        if getattr(args, 'notes', False):
            _vertical_flag = getattr(args, 'vertical', False)
            _preview_sys, _preview_usr = self.image_processor_service.build_prompts(
                target_language, vertical=_vertical_flag
            )
            sys_note, usr_note = self._collect_notes(_preview_sys, _preview_usr)
            self.image_processor_service.system_note = sys_note
            self.image_processor_service.user_note = usr_note

        # Inline note flags (-ns / -nu / -nb) apply directly without interactive flow.
        self._apply_inline_notes(self.image_processor_service, args)

        if getattr(args, 'kanbun', False):
            self.image_processor_service.kanbun = True

        if getattr(args, 'kanbun_main', False):
            self.image_processor_service.kanbun_main = True

        if getattr(args, 'preserve_tables', False):
            self.image_processor_service.tables = True

        if getattr(args, 'dry_run', False):
            vertical_dr = getattr(args, 'vertical', False)
            spread_dr = getattr(args, 'spread', False)
            passes_dr = getattr(args, 'passes', 1)
            model_dr = self.image_processor_service._get_model()
            sys_p, usr_p = self.image_processor_service.build_prompts(target_language, vertical=vertical_dr, spread=spread_dr)
            note = "Image content would be base64-encoded and attached to the user message"
            if passes_dr > 1:
                note += f"; {passes_dr} OCR passes would run sequentially"
            self._dry_run_display(model_dr, sys_p, usr_p, note=note, **self._sampling_kwargs(args))
            return

        if not args.input_file:
            raise CLIError("Input file is required for transcribe command. Use -i option.")

        input_path = os.path.abspath(args.input_file)
        output_file = self._resolve_output_path(args)
        vertical = getattr(args, 'vertical', False)
        spread = getattr(args, 'spread', False)
        passes = getattr(args, 'passes', 1)
        workers = getattr(args, 'workers', 1)
        if passes < 1:
            raise CLIError("--passes must be at least 1.")

        if os.path.isdir(input_path):
            self.process_image_folder(input_path, target_language, output_file, vertical=vertical, spread=spread, passes=passes, workers=workers)
        else:
            file_type = self._detect_and_validate_file(input_path)
            if file_type != 'image':
                raise CLIError(f"Transcribe command requires an image file or folder, but got {file_type}.")
            self.process_image(input_path, target_language, output_file, vertical=vertical, spread=spread, passes=passes)

    def _run_prompt(self, args: argparse.Namespace) -> None:
        system_prompt_text: Optional[str] = None
        if getattr(args, 'include_system_prompt', False):
            system_prompt_text = self._collect_multiline("System prompt") or None

        if getattr(args, 'dry_run', False):
            model_dr = self.prompt_service._get_model()
            sys_p, usr_p = self.prompt_service.build_prompts(
                "[Interactive prompt — text would be entered at runtime]",
                system_prompt_text,
            )
            self._dry_run_display(model_dr, sys_p, usr_p)
            return

        user_prompt_text = self._collect_multiline("User prompt")
        if not user_prompt_text.strip():
            raise CLIError("No prompt text provided.")

        output_file_p = self._resolve_output_path(args)
        self.process_prompt(user_prompt_text, system_prompt_text, output_file_p)

    def _run_transcription_review(self, args: argparse.Namespace) -> None:
        """Handle the 'transcription_review' command."""
        language: str = args.language_code  # already resolved by parse_single_language_code
        kanbun = getattr(args, 'kanbun', False)
        kanbun_main = getattr(args, 'kanbun_main', False)

        if getattr(args, 'notes', False):
            _preview_sys, _preview_usr = self.transcription_review_service.build_prompts(language, kanbun=kanbun, kanbun_main=kanbun_main)
            sys_note, usr_note = self._collect_notes(_preview_sys, _preview_usr)
            self.transcription_review_service.system_note = sys_note
            self.transcription_review_service.user_note = usr_note

        self._apply_inline_notes(self.transcription_review_service, args)

        if getattr(args, 'dry_run', False):
            model_dr = self.transcription_review_service._get_model()
            sys_p, usr_p = self.transcription_review_service.build_prompts(language, kanbun=kanbun, kanbun_main=kanbun_main)
            self._dry_run_display(
                model_dr, sys_p, usr_p,
                note="Transcription text would be appended to the user prompt at runtime",
                **self._sampling_kwargs(args),
            )
            return

        if args.input_file:
            input_path = os.path.abspath(args.input_file)
            if not os.path.exists(input_path):
                raise CLIError(f"Input file '{input_path}' not found.")
            with open(input_path, 'r', encoding='utf-8') as f:
                text = f.read()
            if not text.strip():
                raise CLIError(f"Input file '{input_path}' is empty.")
        elif args.custom_text:
            text = self._collect_multiline("Paste the transcription result to review")
            if not text.strip():
                raise CLIError("No transcription text provided.")
        else:
            raise CLIError(
                "No input supplied.\n"
                "  transcription_review expects the text output of a prior transcription, "
                "not the original document or image.\n"
                "  Use -i <file.txt> to supply a saved transcription file, "
                "or -c to paste the text interactively."
            )

        output_file_r = self._resolve_output_path(args)
        self.process_transcription_review(text, language, kanbun=kanbun, kanbun_main=kanbun_main, output_file=output_file_r)

    def run(self, args: argparse.Namespace) -> None:
        """Run the translation application with the given arguments."""
        _dispatch = {
            'translate': self._run_translate,
            'transcribe': self._run_transcribe,
            'prompt': self._run_prompt,
            'transcription_review': self._run_transcription_review,
        }
        try:
            handler = _dispatch.get(args.command)
            if handler is None:
                raise CLIError(f"Unknown command: {args.command}")
            handler(args)
        except CLIError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            print("\nOperation cancelled.", file=sys.stderr)
            sys.exit(130)
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            sys.exit(1)
