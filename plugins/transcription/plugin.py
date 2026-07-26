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
from src.config import parse_single_language_code, register_language, LANGUAGE_MAP  # noqa: E402
from src.errors import CLIError                                    # noqa: E402
from src.runtime.ui_action import (  # noqa: E402
    PageTextCallback, ProgressCallback, UiAction, UiField, UiJobResult, UiPromptPreview,
    apply_extension_ui_hooks,
)
from src.services.constants import DEFAULT_PARALLEL_WORKERS        # noqa: E402

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
            tr.add_argument(
                "-w", "--workers",
                dest="workers",
                type=int,
                default=DEFAULT_PARALLEL_WORKERS,
                metavar="N",
                help=(
                    "Number of parallel OCR workers when transcribing a folder of images "
                    "(default: %(default)s). Ignored for a single image."
                ),
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
                workers = getattr(args, 'workers', 1)
                sandbox.process_image_folder(input_path, target_language, output_file, workers=workers)
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

    # ── Webui composer action (docs/webui-plugin-plan.md section 10) ───────────

    def run_ui_action(
        self,
        fields: dict,
        professor: str,
        model: Optional[str],
        on_progress: Optional[ProgressCallback],
        output_dir: str,
        on_page_text: Optional[PageTextCallback] = None,
    ) -> UiJobResult:
        """Run a webui-submitted "Transcribe an image" job outside the CLI's argparse path.

        The v1 (core-subset) field set this expects in ``fields`` — see the
        module-level ``ui_action`` declaration below for the matching
        ``UiField`` list:

        - ``target_language``: a short code (e.g. ``'en'``) matching a key
          in ``LANGUAGE_MAP``, the same code typed on the command line.
        - ``file_path``: absolute path to the image the webui has already
          saved to disk — or, if it points at a directory, every image in
          it is transcribed and combined, the same as passing a folder to
          ``-i`` on the command line. The composer's file field
          (``allow_folder=True``) lets a professor pick several images or a
          whole folder at once; ``plugins/webui/src/app.py``'s job-start
          route is what saves multiple uploads into one directory and sets
          this to that directory's path.
        - ``file_name``: the original filename, used only to build a
          readable output filename.
        - ``output_format``: optional, one of ``'txt'`` (default),
          ``'docx'``, ``'pdf'``, or ``'md'`` — mirrors the CLI's own
          extension-driven format choice (passing ``-o result.docx`` vs.
          ``-o result.txt`` already picks the writer by extension; this is
          the same behavior, just chosen from a dropdown instead of typed
          into a filename).
        - ``notes``: optional free text, applied to both the system and
          user prompts (the same effect as the CLI's ``-nb`` flag).
        - ``workers``: optional whole number of parallel OCR workers when
          transcribing a folder of images, same as the CLI's own
          ``-w``/``--workers``. Ignored for a single image (nothing to
          parallelize). The progress bar updates correctly at any worker
          count; only the per-image live text preview (``on_page_text``
          below) is sequential-only — see ``on_progress``/``on_page_text``
          below.
        - ``vertical`` / ``spread`` / ``passes``: optional, same meaning as
          the ``transcription-ea`` extension's own ``--vertical``,
          ``--spread``, and ``-P``/``--passes`` CLI flags. This base plugin
          doesn't declare these in its own ``ui_action`` field list (see
          below) — they're never shown, and never read as anything but
          their defaults (``False``, ``False``, ``1``), unless a language
          extension plugin registers them as its own composer fields via
          ``register_extension_ui_hooks`` (see
          ``src/runtime/ui_action.py``'s ``ExtensionUiHooks``) for whichever
          destination-language token was picked. Read directly here (rather
          than through ``apply_extension_ui_hooks`` below) because they're
          real keyword arguments this method's own
          ``sandbox.process_image``/``process_image_folder`` calls accept
          — see those methods' own signatures — not settings an extension
          can simply toggle as an attribute on the sandbox afterward.
        - ``kanbun`` / ``kanbun_main`` / ``preserve_tables``: contributed
          the same way, but applied through ``apply_extension_ui_hooks``
          instead (see below) — these DO map onto plain attributes on
          ``sandbox.image_processor_service`` (``.kanbun``, ``.kanbun_main``,
          ``.tables``), the same shape ``translation/plugin.py``'s own
          Kanbun example uses for ``variant_notes``, so an extension's
          ``apply`` callback can just set them directly.
        - ``temperature`` / ``top_p`` / ``max_tokens``: optional sampling
          overrides, same as the CLI's ``-t``/``-T``/``-M`` flags. The web
          UI only shows these controls for models that accept them (see
          ``src.models.catalog.model_has_fixed_parameters``); blank means
          "use the model's default."

        Args:
            fields: The submitted form's values, keyed by ``UiField.name``.
            professor: The professor whose API key/budget this job runs
                       under.
            model: The model explicitly requested by the webui's model
                   picker, or ``None`` for this plugin's configured default.
            on_progress: Forwarded to ``sandbox.process_image_folder`` when
                         ``file_path`` is a folder; unused for a single
                         image (nothing to report progress *between*).
                         Works at any worker count — see
                         ``process_image_folder``'s own docstring.
            output_dir: Where to write the one finished output file. Already
                        created and writable.
            on_page_text: Forwarded to ``sandbox.process_image_folder`` when
                          ``file_path`` is a folder — called with each
                          image's transcribed text as soon as it's ready, so
                          the webui can show a per-image live transcript
                          instead of only a percentage. Unused for a single
                          image, same as ``on_progress``. ``None`` (the
                          default) means no such reporting.

        Returns:
            A ``UiJobResult`` pointing at the transcription text file this
            job produced.

        Raises:
            CLIError: If a required field is missing, the language code
                isn't recognized, or the underlying OCR call fails.
        """
        import os

        from src.runtime.sandbox_processor import SandboxProcessor

        code = (fields.get("target_language") or "").strip().lower()
        if code not in LANGUAGE_MAP:
            valid = ", ".join(sorted(LANGUAGE_MAP.keys()))
            raise CLIError(f"Invalid target language '{fields.get('target_language')}'. Use one of: {valid}.")
        target_language = LANGUAGE_MAP[code]

        file_path = fields.get("file_path")
        if not file_path:
            raise CLIError("No file was attached to this transcribe job.")
        file_name = fields.get("file_name") or os.path.basename(file_path)
        notes = (fields.get("notes") or "").strip() or None

        def _to_float(value, field_label: str) -> Optional[float]:
            raw = str(value if value is not None else "").strip()
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                raise CLIError(f"Invalid {field_label} '{raw}' — must be a number.") from None

        def _to_int(value, field_label: str) -> Optional[int]:
            raw = str(value if value is not None else "").strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                raise CLIError(f"Invalid {field_label} '{raw}' — must be a whole number.") from None

        def _to_bool(value) -> bool:
            return str(value if value is not None else "").strip().lower() in ("true", "1", "on", "yes")

        workers = _to_int(fields.get("workers"), "number of parallel workers") or 1
        if workers < 1:
            raise CLIError("Number of parallel workers must be at least 1.")
        temperature = _to_float(fields.get("temperature"), "temperature")
        top_p = _to_float(fields.get("top_p"), "top-p")
        max_tokens = _to_int(fields.get("max_tokens"), "max tokens")

        # Generic reads of vertical/spread/passes — see this method's own
        # docstring above for why these are parsed unconditionally rather
        # than through apply_extension_ui_hooks below: they're keyword
        # arguments process_image/process_image_folder already accept
        # (default False/False/1), not sandbox attributes an extension can
        # toggle after the fact. Absent from every submission unless a
        # language extension registered them as its own composer fields, in
        # which case fields.get(...) just returns None/"" and these fall
        # back to the same defaults as an installation with no extension.
        vertical = _to_bool(fields.get("vertical"))
        spread = _to_bool(fields.get("spread"))
        passes = _to_int(fields.get("passes"), "number of OCR passes") or 1
        if passes < 1:
            raise CLIError("Number of OCR passes must be at least 1.")

        sandbox = SandboxProcessor(
            professor, model=model, temperature=temperature, top_p=top_p, max_tokens=max_tokens,
        )
        if notes:
            sandbox.image_processor_service.system_note = notes
            sandbox.image_processor_service.user_note = notes
        # A language-extension plugin's own composer fields (e.g.
        # transcription-ea's kanbun/kanbun_main/preserve_tables checkboxes)
        # — registered separately, not part of this plugin's own declared
        # fields, so they're applied through the shared registry rather
        # than read directly here. A no-op when nothing is registered for
        # this target-language token (the normal case without that
        # extension installed). See translation/plugin.py's matching call
        # for the sibling mechanism this mirrors.
        apply_extension_ui_hooks("transcribe", code, sandbox, fields)

        base_name = os.path.splitext(file_name)[0] or "transcription"
        # Extension-driven format choice, same idea as -o on the CLI (the
        # underlying save_translation_output() call already picks its
        # writer by the output path's extension — this just gives the
        # composer a dropdown instead of requiring a professor to know
        # "type .docx at the end of a filename" is even a thing).
        requested_format = (fields.get("output_format") or "txt").strip().lower()
        _format_ext = {"docx": ".docx", "pdf": ".pdf", "txt": ".txt", "md": ".md"}
        out_ext = _format_ext.get(requested_format, ".txt")
        output_filename = f"{base_name}_{target_language}{out_ext}"
        output_path = os.path.join(output_dir, output_filename)
        os.makedirs(output_dir, exist_ok=True)

        if os.path.isdir(file_path):
            sandbox.process_image_folder(
                file_path, target_language, output_path,
                vertical=vertical, spread=spread, passes=passes,
                workers=workers, on_progress=on_progress, on_page_text=on_page_text,
            )
            summary = f"Transcribed the images in '{file_name}' to {target_language}."
        else:
            sandbox.process_image(
                file_path, target_language, output_path,
                vertical=vertical, spread=spread, passes=passes,
            )
            summary = f"Transcribed {file_name} to {target_language}."

        if not os.path.exists(output_path):
            raise CLIError("Transcription finished but no output file was produced.")

        session_usage = sandbox.token_tracker.get_session_usage()
        return UiJobResult(
            output_path=output_path, output_filename=output_filename, summary=summary,
            prompt_tokens=session_usage["prompt_tokens"],
            completion_tokens=session_usage["completion_tokens"],
            cost=session_usage["total_cost"],
        )

    def preview_ui_action(
        self,
        fields: dict,
        professor: str,
        model: Optional[str],
    ) -> UiPromptPreview:
        """Build the live system/user prompt preview for the webui's two-pane composer panel.

        Called after every change to the composer's form, so this is
        deliberately lenient rather than raising ``CLIError`` the way
        ``run_ui_action`` does: an unresolved language falls back to a
        placeholder name. No image is read here (there is no page/document
        text to substitute a placeholder for — transcription's prompt is
        built from the target language alone; the actual image content is
        attached at real-run time, not during preview).

        Args:
            fields: Whatever the submitted form currently holds, keyed by
                    ``UiField.name`` — any of them may be missing or blank.
            professor: The professor whose model catalog/pricing to resolve
                       the model name against.
            model: The model explicitly selected in the webui, or ``None``
                   for this plugin's configured default.

        Returns:
            A ``UiPromptPreview`` with the system prompt, user prompt, and
            resolved model name that would be used.
        """
        from src.runtime.sandbox_processor import SandboxProcessor

        code = (fields.get("target_language") or "").strip().lower()
        target_language = LANGUAGE_MAP.get(code, "the selected language")
        notes = (fields.get("notes") or "").strip() or None
        vertical = str(fields.get("vertical", "")).strip().lower() in ("true", "1", "on", "yes")

        sandbox = SandboxProcessor(professor, model=model)
        if notes:
            sandbox.image_processor_service.system_note = notes
            sandbox.image_processor_service.user_note = notes
        # Same extension-hook call run_ui_action makes (see its own comment
        # there) — so a professor previewing a Japanese/Chinese/Korean job
        # sees the kanbun/preserve-tables guidance actually reflected in the
        # preview panel, not just applied silently once the job runs.
        apply_extension_ui_hooks("transcribe", code, sandbox, fields)

        sys_p, usr_p = sandbox.image_processor_service.build_prompts(target_language, vertical=vertical)
        return UiPromptPreview(
            system_prompt=sys_p,
            user_prompt=usr_p,
            model=sandbox.image_processor_service._get_model(),
            note="Image content would be base64-encoded and attached to the user message",
        )


plugin = TranscriptionPlugin()

# ── Webui composer action declaration (docs/webui-plugin-plan.md section 10) ──
# v1 core-subset fields — transcription_review is deliberately left out of
# the composer (see section 10): it consumes the *output* of a prior
# transcription as text, which is a less natural composer action than
# "process this document."
ui_action = UiAction(
    id="transcribe",
    label="Transcribe an image (OCR)",
    command="transcribe",
    fields=[
        UiField(name="target_language", label="Language in the image", kind="language", group="Document"),
        UiField(
            name="file", label="Image (or select multiple images / a whole folder of scans)",
            kind="file", group="Document", allow_folder=True,
        ),
        UiField(
            name="output_format", label="Output format", kind="select", required=False,
            choices=[
                {"value": "txt", "label": "Plain text (.txt) (default)"},
                {"value": "docx", "label": "Word (.docx)"},
                {"value": "pdf", "label": "PDF"},
                {"value": "md", "label": "Markdown (.md)"},
            ],
            group="Output",
        ),
        UiField(
            name="workers", label="Parallel workers (1 = also shows each image's text as it's transcribed)",
            kind="text", required=False, group="Performance",
        ),
        UiField(name="notes", label="Notes for the model", kind="text", required=False, group="Notes"),
    ],
    progress_verb="Transcribing",
)

# See the matching comment in plugins/translation/plugin.py: jobs.py looks
# for ui_action on the plugin *instance*, not this module, so it must be
# attached here or it's invisible to the real app.
plugin.ui_action = ui_action
