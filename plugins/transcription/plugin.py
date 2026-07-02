"""PU_AISandbox Transcription base plugin.

Provides the ``transcribe`` command (turning images of text into typed
text — optical character recognition, or OCR) and the
``transcription_review`` command (checking a transcription for likely OCR
mistakes) for English text.

This is a **standalone plugin** — it registers the ``transcribe`` and
``transcription_review`` commands itself. Support for other languages is
added by installing separate extension plugins that declare which
languages they handle and add their own extra command-line flags, without
re-registering these two commands from scratch.

Extension plugins for transcription must **not** call
``register_subparsers()`` themselves — the commands already exist here.
Instead, an extension plugin hooks in by declaring a ``handles`` list (the
language names it owns, e.g. ``["Japanese"]``) and implementing
``register_command_flags()`` to add its own flags to the shared parser
this plugin already built. Calling ``register_subparsers()`` from an
extension plugin would try to register the same command twice, which
causes a conflict, so any extension plugin that does this is silently
skipped by the loader.

How plugin-owned service files stay importable
-----------------------------------------------
Some of this plugin's supporting code (its prompt-building and API-calling
service classes) lives in this plugin's own directory rather than in the
main repository's ``src/`` folder. To keep those files importable under
their expected ``src.services.*`` path — the same path the main
repository's own services use — ``_register()`` (called once, at import
time, below) loads each file directly and inserts it into Python's
registry of already-imported modules under that name. This means other
code can write a normal ``import src.services.whatever`` statement without
needing to know the file actually lives inside this plugin's folder.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Module registration (must run at import time) ────────────────────────────

_PLUGIN_DIR = Path(__file__).parent


def _register(module_name: str, rel_path: str) -> None:
    """Make one of this plugin's own files importable under a ``src.*`` path.

    See the module docstring above ("How plugin-owned service files stay
    importable") for the full explanation of why this is needed.

    Args:
        module_name: The dotted import path to register the module under
                     (e.g. ``'src.services.image_processor_service'``).
        rel_path: The module's real file path, relative to this plugin's
                  own directory.
    """
    if module_name in sys.modules:
        # Already registered — most likely the main repository's own copy
        # of this module loaded first, so there's nothing more to do.
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
_register(
    "src.runtime.image_handler",
    "src/runtime/image_handler.py",
)

# ── Main-repo imports ─────────────────────────────────────────────────────────
# These are available because the main PU_AISandbox root is on sys.path
# when running from that repo's root directory.

from src.cli import add_common_flags, add_notes_flags           # noqa: E402
from src.config import parse_single_language_code, register_language  # noqa: E402
from src.errors import CLIError                                    # noqa: E402

# Register the language supported by this base plugin.
register_language('en', 'English')


# ── Shared execution helper ────────────────────────────────────────────────────

def _run_transcription_review(
    sandbox: "SandboxProcessor",  # noqa: F821 — imported lazily in run(), only for type hints
    text: str,
    language: str,
    output_file: Optional[str] = None,
) -> None:
    """Check a transcription for likely OCR mistakes and print (and optionally save) the result.

    Args:
        sandbox: The active ``SandboxProcessor`` for this run, which owns
                 the API key, model, and the transcription-review service
                 that actually calls the AI model.
        text: The transcription text to check for errors.
        language: The language the transcription is written in (e.g.
                  ``'English'``).
        output_file: Where to save the review report as a text file, or
                     ``None`` to only print it to the screen.

    Raises:
        CLIError: If the AI model call fails.
    """
    from src.errors import CLIError
    from src.output.file_output import FileOutputHandler
    try:
        result_json = sandbox.transcription_review_service.review_transcription(text, language)
        print("\n" + result_json)
        if output_file:
            FileOutputHandler.save_to_text_file(result_json, output_file, label="Review")
    except Exception as e:
        logger.error(f"Error during transcription review: {e}", exc_info=True)
        raise CLIError(f"Error during transcription review: {e}") from e


# ── Plugin class ──────────────────────────────────────────────────────────────


class TranscriptionPlugin:
    """Turns images of English text into typed text, and checks transcriptions for errors.

    Registers two commands:

    - ``transcribe`` — reads an image file (or a whole folder of images) and
      produces typed-out text using optical character recognition (OCR),
      the process of an AI model reading text out of a picture.
    - ``transcription_review`` — takes the *output* of a prior transcription
      run and checks it against common OCR mistake patterns, producing a
      structured report of likely errors.

    This base plugin handles English only. Other languages (e.g. Japanese,
    Chinese) are added by installing separate transcription extension
    plugins alongside this one — see the module docstring above for how
    extensions plug into these same two commands.
    """

    commands: list[str] = ["transcribe", "transcription_review"]
    # ``handles`` lists the full language names (as returned by
    # ``parse_single_language_code``) that this plugin services. The
    # plugin loader uses this list to combine this plugin with any
    # installed language extension plugins (via DispatchPlugin), so a
    # ``transcribe ja`` request is routed to the extension that declares
    # ``handles = ["Japanese"]`` instead of to this base plugin.
    handles: list[str] = ["English"]

    # ── Argument registration ─────────────────────────────────────────────────

    def register_subparsers(
        self,
        subparsers: argparse._SubParsersAction,
    ) -> None:
        """Register the ``transcribe`` and ``transcription_review`` subcommands and their flags.

        Called once at startup by the plugin loader. Because both this base
        plugin and any installed language extension are wired up as
        separate instances that route through the same two command names,
        this method may be called more than once for the same command set
        — the ``if ... not in subparsers.choices`` checks below make sure
        each command's parser is only actually built the first time,
        avoiding a duplicate-registration error.

        Args:
            subparsers: The shared subcommand registry passed in by the CLI
                        startup code.
        """
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
        """Run the ``transcribe`` or ``transcription_review`` command, whichever was invoked.

        Builds a ``SandboxProcessor`` (which resolves the professor's API
        key, sets up token/cost tracking, and lazily creates whichever
        services are needed), then branches on ``args.command`` to run the
        requested command: transcribing an image or folder of images, or
        reviewing a previously-produced transcription for likely OCR
        errors.

        Args:
            args: The object holding all the parsed command-line flags for
                  this run (which command was invoked, the input file path,
                  whether ``--dry-run`` was passed, etc.).
            professor: The Princeton NetID whose configuration and API key
                       should be used for this run (e.g. ``'heller'``).
            model: The AI model explicitly requested on the command line, or
                   ``None`` to use this plugin's configured default.
            temperature: The requested sampling temperature (controls how
                         predictable vs. varied the model's wording is), or
                         ``None`` to use the default.
            top_p: The requested nucleus-sampling value (an alternative way
                   of controlling response variety), or ``None`` to use the
                   default.
            max_tokens: The requested maximum response length, in tokens
                        (the small chunks of text models process and bill
                        by), or ``None`` to use the default.

        Raises:
            CLIError: If a required input is missing, the wrong file type
                is supplied, or the AI model call fails.
        """
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
            _run_transcription_review(sandbox, text, language, output_file=output_file_r)


plugin = TranscriptionPlugin()
