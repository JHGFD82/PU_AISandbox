"""PU_AISandbox Transcription base plugin.

Provides the ``transcribe`` command (OCR image transcription) and the
``transcription_review`` command (OCR error review) for English text.

This plugin ships with the main PU_AISandbox repo and handles English OCR.
For East Asia language support (Chinese, Japanese, Korean) with kanbun,
vertical script, multi-pass, and parallel-worker options, install the
``transcription-ea`` plugin by cloning it into ``plugins/transcription-ea/``.

ARCHITECTURE — sys.modules injection
--------------------------------------
``_register()`` (called at import time) injects each extracted service module
into ``sys.modules`` under the same ``src.services.*`` name it had in the main
repo.  This keeps everything importable after the service files are removed
from the main repo's ``src/`` directory.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Optional

# ── Module registration (must run at import time) ────────────────────────────

_PLUGIN_DIR = Path(__file__).parent


def _register(module_name: str, rel_path: str) -> None:
    """Inject a plugin module into sys.modules under the src.* namespace.

    If the module is already present (main repo's version loaded first), the
    registration is skipped.
    """
    if module_name in sys.modules:
        return
    path = _PLUGIN_DIR / rel_path
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]


# Register plugin settings first so service modules can import from src.settings
_register(
    "pu_plugin.transcription.settings",
    "src/settings.py",
)

# Register in dependency order: fragments → specs → services
_register(
    "src.services.prompts.ocr_fragments",
    "src/services/prompts/ocr_fragments.py",
)
_register(
    "src.services.prompts.ocr",
    "src/services/prompts/ocr.py",
)
_register(
    "src.services.prompts.transcription_review",
    "src/services/prompts/transcription_review.py",
)
_register(
    "src.services.image_processor_service",
    "src/services/image_processor_service.py",
)
_register(
    "src.services.transcription_review_service",
    "src/services/transcription_review_service.py",
)

# ── Main-repo imports ─────────────────────────────────────────────────────────
# These are available because the main PU_AISandbox root is on sys.path
# when running from that repo's root directory.

from src.cli import add_common_flags, add_notes_flags           # noqa: E402
from src.config import parse_single_language_code, register_language  # noqa: E402
from src.errors import CLIError                                    # noqa: E402

# Register the language supported by this base plugin.
register_language('en', 'English')

# ── Plugin class ──────────────────────────────────────────────────────────────


class TranscriptionPlugin:
    """OCR transcription and transcription-review mode plugin (base, English only).

    Registers two commands:
    - ``transcribe``            — OCR an image file or folder of images
    - ``transcription_review``  — Review prior transcription output for OCR errors

    This base plugin handles English only.  Install ``transcription-ea`` for
    East Asian language support (Japanese, Chinese, Korean) including multi-pass,
    kanbun, and vertical-script options.
    """

    commands: list[str] = ["transcribe", "transcription_review"]
    # ``handles`` lists the full language names (as returned by
    # ``parse_single_language_code``) that this plugin services.  The
    # plugin loader uses this to merge with other transcription plugins
    # via DispatchPlugin, routing each language to the correct plugin.
    handles: list[str] = ["English"]

    # ── Argument registration ─────────────────────────────────────────────────

    def register_subparsers(
        self,
        subparsers: argparse._SubParsersAction,
    ) -> None:
        # Guard against double-registration: DispatchPlugin calls this method
        # once per managed command (one instance per command), so when two
        # commands share the same primary plugin both instances will call this.
        # The idempotency check ensures the parsers are only created once.
        # ── transcribe ────────────────────────────────────────────────────────
        if "transcribe" not in subparsers.choices:
            tr = subparsers.add_parser("transcribe", help="Transcribe images using OCR")
            tr.add_argument(
                "language_code",
                type=parse_single_language_code,
                help="Target language: en (English)",
            )
            tr.add_argument(
                "-i", "--input",
                dest="input_file",
                type=str,
                required=False,
                help="Input image file path, or a folder of images to process in order",
            )
            add_common_flags(tr)
            add_notes_flags(tr)

        # ── transcription_review ──────────────────────────────────────────────
        if "transcription_review" not in subparsers.choices:
            rv = subparsers.add_parser(
                "transcription_review",
                help="Review AI transcription output for OCR errors (returns JSON report)",
            )
            rv.add_argument(
                "language_code",
                type=parse_single_language_code,
                help="Language of the transcription: en (English)",
            )
            review_input_group = rv.add_mutually_exclusive_group(required=False)
            review_input_group.add_argument(
                "-i", "--input",
                dest="input_file",
                type=str,
                help="Path to a text file containing the transcription result to review",
            )
            review_input_group.add_argument(
                "-c", "--custom",
                dest="custom_text",
                action="store_true",
                help="Paste the transcription text interactively (end with --- on its own line)",
            )
            add_common_flags(rv)
            add_notes_flags(rv)

    # ── Command execution ─────────────────────────────────────────────────────

    def run(
        self,
        args: argparse.Namespace,
        professor: str,
        model: Optional[str],
        temperature: Optional[float],
        top_p: Optional[float],
        max_tokens: Optional[int],
    ) -> None:
        """Execute the transcribe or transcription_review command."""
        import os
        from src.runtime.sandbox_processor import SandboxProcessor

        sandbox = SandboxProcessor(
            professor,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )

        if args.command == "transcribe":
            target_language: str = args.language_code

            if getattr(args, 'notes', False):
                _preview_sys, _preview_usr = sandbox.image_processor_service.build_prompts(
                    target_language
                )
                sys_note, usr_note = sandbox._collect_notes(_preview_sys, _preview_usr)
                sandbox.image_processor_service.system_note = sys_note
                sandbox.image_processor_service.user_note = usr_note

            sandbox._apply_inline_notes(sandbox.image_processor_service, args)

            if getattr(args, 'dry_run', False):
                model_dr = sandbox.image_processor_service._get_model()
                sys_p, usr_p = sandbox.image_processor_service.build_prompts(target_language)
                sandbox._dry_run_display(model_dr, sys_p, usr_p,
                                         note="Image content would be base64-encoded and attached to the user message",
                                         **sandbox._sampling_kwargs(args))
                return

            if not args.input_file:
                raise CLIError("Input file is required for transcribe command. Use -i option.")

            input_path = os.path.abspath(args.input_file)
            output_file = sandbox._resolve_output_path(args)

            if os.path.isdir(input_path):
                sandbox.process_image_folder(input_path, target_language, output_file)
            else:
                file_type = sandbox._detect_and_validate_file(input_path)
                if file_type != 'image':
                    raise CLIError(f"Transcribe command requires an image file or folder, but got {file_type}.")
                sandbox.process_image(input_path, target_language, output_file)

        else:  # transcription_review
            language: str = args.language_code

            if getattr(args, 'notes', False):
                _preview_sys, _preview_usr = sandbox.transcription_review_service.build_prompts(language)
                sys_note, usr_note = sandbox._collect_notes(_preview_sys, _preview_usr)
                sandbox.transcription_review_service.system_note = sys_note
                sandbox.transcription_review_service.user_note = usr_note

            sandbox._apply_inline_notes(sandbox.transcription_review_service, args)

            if getattr(args, 'dry_run', False):
                model_dr = sandbox.transcription_review_service._get_model()
                sys_p, usr_p = sandbox.transcription_review_service.build_prompts(language)
                sandbox._dry_run_display(
                    model_dr, sys_p, usr_p,
                    note="Transcription text would be appended to the user prompt at runtime",
                    **sandbox._sampling_kwargs(args),
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
                text = sandbox._collect_multiline("Paste the transcription result to review")
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

            output_file_r = sandbox._resolve_output_path(args)
            sandbox.process_transcription_review(text, language, output_file=output_file_r)


plugin = TranscriptionPlugin()
