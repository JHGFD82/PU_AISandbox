"""PU_AISandbox Translation plugin — built-in base (English).

This plugin ships with the main PU_AISandbox repository. It serves two
roles at once:

  1. **Service owner** — it makes the shared translation building blocks
     (``TranslationService`` for text, ``ImageTranslationService`` for
     images, the prompt-building classes, and the translation-specific
     prompt text) reachable under a shared ``src.*`` import path, so that
     any other translation-language plugin can use the exact same
     implementation instead of bundling its own copy.

  2. **English handler** — it owns English as a source language (so
     ``translate en-jp`` routes here) and provides English destination-side
     guidance. It also contains ``_execute_translate()``, the shared
     execution logic that every translation plugin (this one and any
     installed language extensions) delegates to after handling its own
     language-specific setup.

This is a **standalone plugin** — it registers the ``translate`` command
itself. If you are adding support for an additional source language (e.g.
Japanese, Chinese), write an **extension plugin** instead — see the
"TEMPLATE GUIDE" section below, and ``docs/plugin-authoring-guide.md`` for
a longer walkthrough.

TEMPLATE GUIDE FOR EXTENSION PLUGIN AUTHORS
--------------------------------------------
Extension plugins extend the ``translate`` command to cover new source
languages. They **must not** call ``register_subparsers()`` themselves —
the ``translate`` command already exists here. Doing so would try to
register the same command twice, which causes a conflict, so the plugin
loader would silently skip your plugin.

To build a new language extension, copy this file into your own plugin
directory and adapt it as follows:

  1. Change ``handles`` to the short language codes your plugin owns as
     *source* languages (matching the keys in ``LANGUAGE_MAP``, e.g.
     ``["jp", "zh"]``). These are the same short codes a user types on the
     command line (e.g. ``jp`` for Japanese), so another developer can see
     at a glance which codes belong to your plugin.

  2. **Remove the module-registration block below** (the calls to
     ``_register()``). This base plugin already registers those shared
     modules when it loads; because plugins load in alphabetical order,
     they're already available by the time your plugin's file runs.

  3. Keep the same main-repo imports — they all come from the shared
     repository, not from this plugin, so they don't need adapting.

  4. Add your language-specific command-line flags inside
     ``register_command_flags()``. Do *not* implement
     ``register_subparsers()`` — extension plugins only implement
     ``register_command_flags()``. Do *not* re-add ``language_code`` or any
     flag already defined in the base parser here — those are added once by
     this base plugin (or by ``DispatchPlugin``, the internal component
     that merges multiple plugins' flags into one shared parser).

  5. Inside your plugin's ``run()`` method, append any language-specific
     guidance to the translation service's ``variant_notes`` list *before*
     calling the shared execution function::

         if getattr(args, 'my_flag', False):
             sandbox.translation_service.variant_notes.append(MY_NOTE)

     Each note is a plain string appended to the AI model's system prompt
     as its own additional-instructions block. You can append more than one
     note (for example, to handle a document that mixes writing
     conventions); their order in the list is preserved in the prompt.

  6. Look up and call the shared execution function::

         import sys
         _base = sys.modules.get('pu_plugin.translation.plugin')
         _base._execute_translate(sandbox, args, source_language, target_language)

  7. Optionally implement ``get_peer_guidance(token)`` to contribute
     destination-side conventions when your language is the *target* of a
     translation rather than the source (see its docstring below for
     details).

  8. Optionally call ``register_extension_ui_hooks(action_id, token, fields, apply)``
     (from ``src.runtime.ui_action``) at import time to add your own
     field(s) to the web UI composer's job modal — e.g. a checkbox like
     "Use Kanbun reading conventions" — shown as a subsection that appears
     once a professor picks your language as the *destination* in the
     composer, the same trigger point as ``get_peer_guidance`` above but for
     form fields instead of prompt text. ``action_id`` must match the owning
     action's own ``UiAction.id`` (``"translate"`` here) — required
     alongside ``token`` so a *different* action (e.g. ``"transcribe"``)
     registering the same language token doesn't silently overwrite this
     one's fields; see ``ExtensionUiHooks``'s docstring in
     ``src/runtime/ui_action.py`` for the exact shape, and for why this
     is a plain global registry rather than something routed through
     ``DispatchPlugin``. Example::

         from src.runtime.ui_action import UiField, register_extension_ui_hooks

         def _apply_kanbun(sandbox, fields):
             if str(fields.get("kanbun", "")).strip().lower() in ("true", "1", "on", "yes"):
                 sandbox.translation_service.variant_notes.append(KANBUN_NOTE)

         register_extension_ui_hooks(
             action_id="translate",
             token="jp",
             fields=[UiField(
                 name="kanbun", label="Use Kanbun reading conventions",
                 kind="checkbox", required=False, group="Japanese (kanbun)",
             )],
             apply=_apply_kanbun,
         )

How plugin-owned service files stay importable
-----------------------------------------------
``_register()`` (called once, at import time, below) loads each shared
service file directly from this plugin's own directory and inserts it into
Python's registry of already-imported modules under the ``src.services.*``
path the rest of the codebase expects — the same mechanism described in
``plugins/prompt/plugin.py``. Because plugins load in alphabetical order,
this (the "translation" plugin) always loads before any language
extension plugin, so by the time an extension plugin's file runs, these
modules are already registered and it can just write a normal ``import
src.services.translation_service`` statement.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.runtime.sandbox_processor import SandboxProcessor

# ── Plugin directory ──────────────────────────────────────────────────────────

_PLUGIN_DIR = Path(__file__).parent


# ── Module registration ────────────────────────────────────────────────────────

def _register(module_name: str, rel_path: str) -> None:
    """Make one of this plugin's own files importable under a ``src.*`` path.

    See the module docstring above ("How plugin-owned service files stay
    importable") for the full explanation of why this is needed.

    Args:
        module_name: The dotted import path to register the module under
                     (e.g. ``'src.services.translation_service'``).
        rel_path: The module's real file path, relative to this plugin's
                  own directory.
    """
    if module_name in sys.modules:
        # Already registered — for example, a development override was
        # loaded first — so there's nothing more to do.
        return
    path = _PLUGIN_DIR / rel_path
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]


# Register plugin settings first so service modules can import from src.settings.
_register(
    "pu_plugin.translation.settings",
    "src/settings.py",
)

# Register in dependency order: fragments → specs → services.
_register(
    "src.services.prompts.translation_fragments",
    "src/services/prompts/translation_fragments.py",
)
_register(
    "src.services.prompts.translation",
    "src/services/prompts/translation.py",
)
_register(
    "src.services.prompts.image_translation",
    "src/services/prompts/image_translation.py",
)
_register(
    "src.services.translation_service",
    "src/services/translation_service.py",
)
_register(
    "src.services.image_translation_service",
    "src/services/image_translation_service.py",
)
_register(
    "src.processors.docx_translation",
    "src/processors/docx_translation.py",
)
_register(
    "src.runtime.document_handler",
    "src/runtime/document_handler.py",
)


# ── Main-repo imports ──────────────────────────────────────────────────────────
# These resolve because the main PU_AISandbox root is on sys.path at runtime.

from src.cli import add_common_flags, add_notes_flags           # noqa: E402
from src.config import parse_language_code, LANGUAGE_MAP, register_language    # noqa: E402
from src.errors import CLIError                                    # noqa: E402
from src.models import OutputOptions                               # noqa: E402
from src.processors.constants import IMAGE_EXTENSIONS             # noqa: E402
from src.processors.docx_processor import DocxProcessor           # noqa: E402
from src.processors.excel_processor import ExcelProcessor         # noqa: E402
from src.processors.json_processor import JsonProcessor           # noqa: E402
from src.processors.markdown_processor import MarkdownProcessor   # noqa: E402
from src.processors.pdf_processor import generate_process_text    # noqa: E402
from src.processors.txt_processor import TxtProcessor             # noqa: E402
from src.runtime.ui_action import (  # noqa: E402
    PageTextCallback, ProgressCallback, UiAction, UiField, UiJobResult, UiPromptPreview,
    apply_extension_ui_hooks,
)
from src.services.constants import DEFAULT_PARALLEL_WORKERS       # noqa: E402
from src.settings import DEFAULT_PAGE_SIZE                        # noqa: E402

# Register this plugin's source language into the shared language registry.
register_language('en', 'English')


# ── Shared execution helper ────────────────────────────────────────────────────

def _execute_translate(
    sandbox: "SandboxProcessor",
    args: argparse.Namespace,
    source_language: str,
    target_language: str,
) -> None:
    """Validate flag combinations, then translate the requested input and produce output.

    This is the shared implementation behind every ``translate`` command,
    regardless of which source-language plugin (this base plugin, or an
    installed extension) handled the language-specific setup first. It
    checks that the combination of flags the user passed makes sense (for
    example, rejecting ``--preserve-media`` with a non-Word-document
    output), builds the output settings, and then dispatches to the
    appropriate translation method — custom pasted text, a single file, or
    a whole folder of images.

    Args:
        sandbox: The active ``SandboxProcessor`` for this run, already
                 constructed with the professor's API key, model, and
                 sampling settings. Any plugin-specific service properties
                 (like ``variant_notes``, extra guidance appended to the
                 model's prompt) must already be set on ``sandbox`` *before*
                 this function is called.
        args: The object holding all the parsed command-line flags for
              this run.
        source_language: The full name of the language being translated
                          from (e.g. ``'Japanese'``).
        target_language: The full name of the language being translated
                          into (e.g. ``'English'``).

    Raises:
        CLIError: If the flags passed are incompatible with each other or
            with the input/output file types, or if no input was given at
            all.
    """
    import os

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

    # --preserve-media compatibility checks
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
        _preview_sys: Optional[str] = None
        _preview_usr: Optional[str] = None
        if args.input_file:
            _fp = os.path.abspath(args.input_file)
            if os.path.exists(_fp) and sandbox.image_processor.is_image_file(_fp):
                _preview_sys, _preview_usr = sandbox.image_translation_service.build_prompts(
                    source_language, target_language
                )
            else:
                _placeholder = generate_process_text("", f"[{source_language} document text]", "")
                _preview_sys, _preview_usr = sandbox.translation_service.build_prompts(
                    _placeholder, source_language, target_language
                )
        else:
            _placeholder = generate_process_text("", f"[{source_language} custom text]", "")
            _preview_sys, _preview_usr = sandbox.translation_service.build_prompts(
                _placeholder, source_language, target_language
            )
        sys_note, usr_note = sandbox._collect_notes(_preview_sys, _preview_usr)
        sandbox.translation_service.system_note = sys_note
        sandbox.translation_service.user_note = usr_note
        sandbox.image_translation_service.system_note = sys_note
        sandbox.image_translation_service.user_note = usr_note

    sandbox._apply_inline_notes(sandbox.translation_service, args)
    sandbox._apply_inline_notes(sandbox.image_translation_service, args)

    if getattr(args, 'preserve_tables', False):
        sandbox.translation_service.tables = True
        sandbox.image_translation_service.tables = True

    if getattr(args, 'toc', False):
        sandbox.translation_service.toc = True

    if getattr(args, 'dry_run', False):
        model_dr = sandbox.translation_service._get_model()
        abstract_text_dr: Optional[str] = None
        if getattr(args, 'abstract', False):
            abstract_text_dr = sandbox._collect_multiline("Abstract text") or None

        if args.input_file:
            file_path_dr = os.path.abspath(args.input_file)
            file_type_dr = sandbox._detect_and_validate_file(file_path_dr)
            if file_type_dr == 'image':
                spread_dr = getattr(args, 'spread', False)
                sys_p, usr_p = sandbox.image_translation_service.build_prompts(source_language, target_language, spread=spread_dr)
                sandbox._dry_run_display(
                    sandbox.image_translation_service._get_model(), sys_p, usr_p,
                    note="Image content would be base64-encoded and attached to the user message",
                    **sandbox._sampling_kwargs(args),
                )
                return
            elif file_type_dr == 'pdf':
                if getattr(args, 'scanned', False):
                    spread_dr = getattr(args, 'spread', False)
                    sys_p, usr_p = sandbox.image_translation_service.build_prompts(
                        source_language, target_language, spread=spread_dr
                    )
                    sandbox._dry_run_display(
                        sandbox.image_translation_service._get_model(), sys_p, usr_p,
                        note="Scanned PDF: each page will be rendered as an image and attached to the user message",
                        **sandbox._sampling_kwargs(args),
                    )
                    return
                with open(file_path_dr, 'rb') as f:
                    first_page = next(iter(sandbox.pdf_processor.process_pdf(f)), None)
                    page_text_dr = sandbox.pdf_processor.process_page(first_page) if first_page else "[no text found in PDF]"
            elif file_type_dr == 'docx':
                with open(file_path_dr, 'rb') as f:
                    pages_dr = DocxProcessor.process_docx_with_pages(f, target_page_size=DEFAULT_PAGE_SIZE)
                    page_text_dr = pages_dr[0] if pages_dr else "[no text found in document]"
            elif file_type_dr == 'txt':
                with open(file_path_dr, 'r', encoding='utf-8') as f:
                    pages_dr = TxtProcessor.process_txt_with_pages(f, target_page_size=DEFAULT_PAGE_SIZE)
                    page_text_dr = pages_dr[0] if pages_dr else "[no text found in file]"
            elif file_type_dr == 'excel':
                pages_dr = ExcelProcessor.process_excel_with_pages(file_path_dr, target_page_size=DEFAULT_PAGE_SIZE)
                page_text_dr = pages_dr[0] if pages_dr else "[no data found in spreadsheet]"
            elif file_type_dr == 'json':
                pages_dr = JsonProcessor.process_json_with_pages(file_path_dr, target_page_size=DEFAULT_PAGE_SIZE)
                page_text_dr = pages_dr[0] if pages_dr else "[no content found in JSON file]"
            elif file_type_dr == 'markdown':
                pages_dr = MarkdownProcessor.process_markdown_with_pages(file_path_dr, target_page_size=DEFAULT_PAGE_SIZE)
                page_text_dr = pages_dr[0] if pages_dr else "[no content found in Markdown file]"
            else:
                page_text_dr = f"[{source_language} text to translate]"
        elif args.custom_text:
            page_text_dr = sandbox._collect_multiline(
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
            output_format_dr = {
                'pdf': 'pdf', 'docx': 'docx', 'txt': 'txt',
                'xlsx': 'xlsx', 'json': 'json', 'md': 'md',
            }.get(ext, 'file')
        elif auto_save_dr:
            output_format_dr = 'txt'
        else:
            output_format_dr = 'console'
        sys_p, usr_p = sandbox.translation_service.build_prompts(combined, source_language, target_language, output_format=output_format_dr, context_type=context_type_dr)
        sandbox._dry_run_display(model_dr, sys_p, usr_p, **sandbox._sampling_kwargs(args))
        return

    opts = OutputOptions(
        output_file=sandbox._resolve_output_path(args),
        auto_save=getattr(args, 'auto_save', False),
        progressive_save=getattr(args, 'progressive_save', False),
        custom_font=getattr(args, 'custom_font', None),
        preserve_media=getattr(args, 'preserve_media', False),
        font_size=getattr(args, 'font_size', None),
    )
    workers = getattr(args, 'workers', 1)
    spread = getattr(args, 'spread', False)
    if args.custom_text:
        sandbox.translate_custom_text(
            source_language,
            target_language,
            getattr(args, 'abstract', False),
            opts,
        )
    elif args.input_file:
        input_path = os.path.abspath(args.input_file)
        if os.path.isdir(input_path):
            sandbox.process_image_translation_folder(
                input_path,
                source_language,
                target_language,
                opts,
                workers=workers,
                spread=spread,
            )
        else:
            sandbox.translate_document(
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


# ── Plugin class ───────────────────────────────────────────────────────────────

class TranslationPlugin:
    """Translates documents and text into or out of English (the built-in base plugin).

    Owns English as a source language and also provides the shared
    translation service layer that every other translation-language plugin
    reuses. See the module docstring above for the full template guide
    describing how to write a new language extension plugin on top of this
    one.
    """

    commands: list[str] = ["translate"]

    # Declared here, assigned at the bottom of this file. The web interface
    # looks for ``ui_action`` on the plugin *instance*, so it has to be
    # attached to the object rather than left as a module-level name — but
    # the UiAction it's assigned is built further down, after the methods it
    # refers to exist. Naming it here is what makes that later assignment a
    # documented part of this class rather than an attribute appearing from
    # nowhere.
    ui_action: "UiAction"

    # Languages this plugin owns as source languages.
    # ``handles`` stores the short codes users type on the command line
    # (e.g. ``en`` for English), matching the keys in ``LANGUAGE_MAP``.
    # The plugin loader reads this list to combine multiple translation
    # plugins into one unified command, routing each requested language to
    # the plugin that declares it, rather than treating separate plugins
    # trying to own the same "translate" command as a conflict.
    handles: list[str] = ["en"]

    # ── Argument registration ──────────────────────────────────────────────────

    def register_command_flags(self, parser: argparse.ArgumentParser) -> None:
        """Add this plugin's command-line flags to the shared ``translate`` parser.

        Called both by this plugin's own ``register_subparsers()`` below,
        and by ``DispatchPlugin`` (the internal component that merges every
        installed translation-language plugin's flags into one combined
        parser) when a language extension plugin is also installed.

        Add only the flags owned by *this* plugin here. Do not re-add
        ``language_code`` or the common/notes flags shared across plugins —
        those are added elsewhere. A language extension plugin should
        follow this same pattern: put its flags in a
        ``register_command_flags()`` method of its own, called from both
        its ``register_subparsers()`` (if it's ever run standalone) and as
        the ``DispatchPlugin`` hook.

        Args:
            parser: The argument parser for the ``translate`` command to
                    add flags to.
        """
        from plugins.translation.utils import validate_page_nums  # noqa: PLC0415
        input_group = parser.add_mutually_exclusive_group(required=False)
        input_group.add_argument(
            "-i", "--input",
            dest="input_file",
            type=str,
            help="Input file path (PDF, DOCX, TXT)",
        )
        input_group.add_argument(
            "-c", "--custom",
            dest="custom_text",
            action="store_true",
            help="Input custom text",
        )

        parser.add_argument(
            "-p", "--page_nums",
            dest="page_nums",
            type=validate_page_nums,
            help='Page numbers to process (e.g., "1" or "1-5")',
        )
        parser.add_argument("-a", "--abstract", dest="abstract", action="store_true",
                            help="Text has an abstract")
        parser.add_argument("--auto-save", dest="auto_save", action="store_true",
                            help="Auto-save with timestamp")
        parser.add_argument("--progressive-save", dest="progressive_save",
                            action="store_true",
                            help="Save each page immediately (text output only)")
        parser.add_argument("-f", "--font", dest="custom_font", type=str,
                            help="Custom font name (must be in fonts/)")
        parser.add_argument("--font-size", dest="font_size", type=int, default=None,
                            metavar="PT",
                            help="Body font size in points for PDF/Word output (default: 9)")
        parser.add_argument(
            "-w", "--workers",
            dest="workers",
            type=int,
            default=DEFAULT_PARALLEL_WORKERS,
            metavar="N",
            help=(
                "Number of parallel translation workers (default: %(default)s). "
                "Each page is sent as an independent API call. "
                "Workers > 1 uses untranslated source text as context and "
                "disables progressive save."
            ),
        )
        parser.add_argument("--spread", dest="spread", action="store_true",
                            help="Image is a two-page spread (two facing pages scanned together); "
                                 "applies to image file inputs and --scanned PDFs")
        parser.add_argument(
            "--scanned", dest="scanned", action="store_true",
            help="Treat the PDF as a scanned image document: each page is rendered "
                 "as an image and processed via the OCR+translation pipeline "
                 "(vision model). PDF only.",
        )
        parser.add_argument(
            "--preserve-tables", dest="preserve_tables", action="store_true",
            help="Hint to the model that tabular data should be returned as Markdown "
                 "tables; the output layer renders them as proper tables in PDF/DOCX "
                 "or ASCII in TXT.",
        )
        parser.add_argument(
            "--preserve-media", dest="preserve_media", action="store_true",
            help="Carry embedded images from a .docx source into the translated "
                 ".docx output (requires -i *.docx and -o *.docx)",
        )
        parser.add_argument(
            "--toc", dest="toc", action="store_true",
            help="Document contains a table of contents: normalize dot leaders "
                 "(e.g. '............') to exactly five dots (.....) between "
                 "section titles and page numbers",
        )
        add_common_flags(parser)
        add_notes_flags(parser)

    def register_subparsers(
        self,
        subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    ) -> None:
        """Register the ``translate`` subcommand and delegate its flags to ``register_command_flags``.

        Called once at startup by the plugin loader. When one or more
        language extension plugins are also installed, ``DispatchPlugin``
        (the internal component that merges plugins sharing a command)
        calls this method on this base plugin to build the shared parser,
        then calls ``register_command_flags()`` on each extension plugin
        separately to add their flags to the same parser.

        Args:
            subparsers: The shared subcommand registry passed in by the CLI
                        startup code.
        """
        p = subparsers.add_parser("translate", help="Translate documents or text")
        p.add_argument(
            "language_code",
            type=parse_language_code,
            help="Translation direction as a source-target pair (e.g. jp-en, zh-en, kr-en)",
        )
        self.register_command_flags(p)

    # ── Peer guidance ──────────────────────────────────────────────────────────

    def get_peer_guidance(self, token: str) -> Optional[str]:
        """Provide extra guidance for the model when English is the translation target.

        Called by ``DispatchPlugin`` when this plugin owns the destination
        language of a translation request — for example, if a Japanese
        extension plugin is translating *into* English, it asks this
        method for any English-specific conventions to include. Returning
        ``None`` is a safe, valid answer that simply adds no extra
        guidance.

        A language extension plugin can override this method to contribute
        its own destination-side conventions — for example, honorific
        register rules or spelling norms — when its language is the
        translation target rather than the source.

        Args:
            token: The short language code being asked about (e.g.
                   ``'en'``).

        Returns:
            Extra guidance text to add to the model's prompt, or ``None``
            if no special guidance is needed. Standard English needs none.
        """
        return None  # Standard English needs no special destination guidance.

    # ── Command execution ──────────────────────────────────────────────────────

    def run(
        self,
        args: argparse.Namespace,
        professor: str,
        model: Optional[str],
        temperature: Optional[float],
        top_p: Optional[float],
        max_tokens: Optional[int],
    ) -> None:
        """Run the ``translate`` command for a source-target language pair involving English.

        Builds a ``SandboxProcessor`` (which resolves the professor's API
        key, sets up token/cost tracking, and lazily creates whichever
        services are needed), resolves the requested language pair, applies
        any destination-side guidance contributed by another plugin, and
        hands off to ``_execute_translate()`` for validation and the actual
        translation work.

        Args:
            args: The object holding all the parsed command-line flags for
                  this run (the language pair, input/output paths, and any
                  translation-specific flags like ``--preserve-media``).
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
            CLIError: If the language code isn't a valid source-target
                pair, or if ``_execute_translate`` rejects the flag
                combination or input.
        """
        from src.runtime.sandbox_processor import SandboxProcessor

        sandbox = SandboxProcessor(
            professor,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )

        language_code = args.language_code
        if not isinstance(language_code, tuple) or len(language_code) != 2:
            raise CLIError("Translation requires a language pair (e.g. jp-en).")
        source_code, target_code = language_code
        source_language = LANGUAGE_MAP[source_code]
        target_language = LANGUAGE_MAP[target_code]

        # Apply any destination-side peer guidance injected by DispatchPlugin.
        # (args._peer_guidance is set by DispatchPlugin.run() when the target
        # language is owned by a different plugin.)
        for note in getattr(args, '_peer_guidance', []):
            sandbox.translation_service.variant_notes.append(note)

        _execute_translate(sandbox, args, source_language, target_language)

    # ── Webui composer action ────────────────────────────────────────────────

    def run_ui_action(
        self,
        fields: dict,
        professor: str,
        model: Optional[str],
        on_progress: Optional[ProgressCallback],
        output_dir: str,
        on_page_text: Optional[PageTextCallback] = None,
    ) -> UiJobResult:
        """Run a webui-submitted "Translate a document" job outside the CLI's argparse path.

        The v1 (core-subset) field set this expects in ``fields`` — see the
        module-level ``ui_action`` declaration below for the matching
        ``UiField`` list the webui composer renders:

        - ``source_language`` / ``target_language``: short codes (e.g.
          ``'ja'``, ``'en'``) matching a key in ``LANGUAGE_MAP``, not full
          names — the same codes typed on the command line.
        - ``file_path``: absolute path to the document the webui has
          already saved to disk (the uploaded file itself, not a form
          value the person typed) — or, if it points at a directory, every
          image in it is translated and combined, the same as pointing the
          CLI's ``-i`` at a folder of page images (see ``run()``'s own
          ``os.path.isdir(input_path)`` branch, which this mirrors). The
          composer's file field (``allow_folder=True``) lets a professor
          pick several images or a whole folder at once; ``app.py``'s
          job-start route is what saves multiple uploads into one directory
          and sets this to that directory's path.
        - ``file_name``: the original filename, used only to build a
          readable output filename.
        - ``scanned``: optional, any of ``'true'``/``'1'``/``'on'``
          (case-insensitive) enables ``--scanned``-equivalent behavior.
        - ``page_nums``: optional page-range string (e.g. ``'8-12'``) —
          this is deliberately in the v1 field set specifically as the
          answer to an interrupted job. There is no resume, so the escape
          valve is starting a fresh job from the page the last one
          stopped at.
        - ``notes``: optional free text, applied to both the system and
          user prompts (the same effect as the CLI's ``-nb``/note-both flag).
        - ``output_format``: optional, one of ``'same'`` (default —
          ``.docx`` in, ``.docx`` out; anything else in, ``.txt`` out),
          ``'docx'``, ``'pdf'``, ``'txt'``, or ``'md'`` — mirrors ``-o``'s
          extension-driven format choice on the CLI.
        - ``preserve_tables``, ``toc``, ``preserve_media``, ``spread``:
          optional checkboxes, same meaning as the matching CLI flags
          (``--preserve-tables``, ``--toc``, ``--preserve-media``,
          ``--spread``).
        - ``workers``: optional whole number of parallel translation
          workers, same as ``-w``/``--workers`` (default 1).
        - ``font`` / ``font_size``: optional, same as ``-f``/``--font`` and
          ``--font-size`` (Word/PDF output only).
        - ``temperature`` / ``top_p`` / ``max_tokens``: optional sampling
          overrides, same as the CLI's ``-t``/``-T``/``-M`` flags. The web
          UI only shows these controls for models that accept them (see
          ``src.models.catalog.model_accepts_sampling_params``); blank means
          "use the model's default."

        Args:
            fields: The submitted form's values, keyed by ``UiField.name``.
            professor: The professor whose API key/budget this job runs
                       under.
            model: The model explicitly requested by the webui's model
                   picker, or ``None`` for this plugin's configured default.
            on_progress: Forwarded straight through to
                         ``sandbox.translate_document`` — see that method's
                         docstring for exactly when it's called.
            output_dir: Where to write the one finished output file. Already
                        created and writable; this method must not write
                        anywhere else.
            on_page_text: Forwarded straight through to
                          ``sandbox.translate_document`` — called with each
                          page's translated text as soon as it's ready, so
                          the webui can show a page-by-page live transcript
                          instead of only a percentage. ``None`` (the
                          default) means no such reporting.

        Returns:
            A ``UiJobResult`` pointing at the translated file this job
            produced.

        Raises:
            CLIError: If a required field is missing, a language code isn't
                recognized, a numeric field isn't a valid number, or the
                underlying translation call fails.
        """
        import os

        from src.runtime.sandbox_processor import SandboxProcessor

        def _resolve_language(code: str, field_label: str) -> str:
            normalized = (code or "").strip().lower()
            if normalized not in LANGUAGE_MAP:
                valid = ", ".join(sorted(LANGUAGE_MAP.keys()))
                raise CLIError(f"Invalid {field_label} '{code}'. Use one of: {valid}.")
            return LANGUAGE_MAP[normalized]

        def _to_bool(value) -> bool:
            return str(value if value is not None else "").strip().lower() in ("true", "1", "on", "yes")

        def _to_int(value, field_label: str) -> Optional[int]:
            raw = str(value if value is not None else "").strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                raise CLIError(f"Invalid {field_label} '{raw}' — must be a whole number.") from None

        def _to_float(value, field_label: str) -> Optional[float]:
            raw = str(value if value is not None else "").strip()
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                raise CLIError(f"Invalid {field_label} '{raw}' — must be a number.") from None

        target_code_raw = fields.get("target_language", "")
        source_language = _resolve_language(fields.get("source_language", ""), "source language")
        target_language = _resolve_language(target_code_raw, "target language")

        file_path = fields.get("file_path")
        if not file_path:
            raise CLIError("No file was attached to this translate job.")
        file_name = fields.get("file_name") or os.path.basename(file_path)

        scanned = _to_bool(fields.get("scanned"))
        spread = _to_bool(fields.get("spread"))
        preserve_tables = _to_bool(fields.get("preserve_tables"))
        toc = _to_bool(fields.get("toc"))
        preserve_media = _to_bool(fields.get("preserve_media"))
        page_nums = (fields.get("page_nums") or "").strip() or None
        notes = (fields.get("notes") or "").strip() or None
        font = (fields.get("font") or "").strip() or None
        font_size = _to_int(fields.get("font_size"), "font size")
        workers = _to_int(fields.get("workers"), "number of parallel workers") or 1
        if workers < 1:
            raise CLIError("Number of parallel workers must be at least 1.")
        temperature = _to_float(fields.get("temperature"), "temperature")
        top_p = _to_float(fields.get("top_p"), "top-p")
        max_tokens = _to_int(fields.get("max_tokens"), "max tokens")

        sandbox = SandboxProcessor(
            professor, model=model, temperature=temperature, top_p=top_p, max_tokens=max_tokens,
        )
        if notes:
            sandbox.translation_service.system_note = notes
            sandbox.translation_service.user_note = notes
            sandbox.image_translation_service.system_note = notes
            sandbox.image_translation_service.user_note = notes
        if preserve_tables:
            sandbox.translation_service.tables = True
            sandbox.image_translation_service.tables = True
        if toc:
            sandbox.translation_service.toc = True
        # A language-extension plugin's own composer field (e.g.
        # translation-ea's Kanbun checkbox) — registered separately, not
        # part of this plugin's own declared fields, so it's applied
        # through the shared registry rather than read directly here. A
        # no-op when nothing is registered for this destination token
        # (the normal case without that extension installed).
        apply_extension_ui_hooks("translate", target_code_raw, sandbox, fields)

        base_name = os.path.splitext(file_name)[0] or "document"
        requested_format = (fields.get("output_format") or "same").strip().lower()
        _format_ext = {"docx": ".docx", "pdf": ".pdf", "txt": ".txt", "md": ".md"}
        if requested_format in _format_ext:
            out_ext = _format_ext[requested_format]
        else:
            # "same" (or anything unrecognized) falls back to the existing
            # default: a .docx source gets a formatted .docx output,
            # everything else gets plain text — matching
            # _execute_translate's own output_is_docx gate.
            out_ext = ".docx" if file_name.lower().endswith(".docx") else ".txt"
        if preserve_media and out_ext != ".docx":
            raise CLIError(
                "Preserving embedded images requires a Word (.docx) output format."
            )
        if preserve_media and os.path.isdir(file_path):
            # Mirrors run()'s own --preserve-media validation for a single
            # image file (there's no embedded media to carry over from a
            # picture) — a folder of page images is the same situation, just
            # several pictures instead of one.
            raise CLIError(
                "Cannot use preserve embedded media with an image folder input: "
                "images have no embedded media to carry over."
            )

        output_filename = f"{base_name}_{source_language}_to_{target_language}{out_ext}"
        output_path = os.path.join(output_dir, output_filename)
        os.makedirs(output_dir, exist_ok=True)

        opts = OutputOptions(
            output_file=output_path,
            custom_font=font,
            preserve_media=preserve_media,
            font_size=font_size,
        )

        # A folder of page images, same as pointing the CLI's -i at a
        # folder (see this plugin's run()'s own os.path.isdir(input_path)
        # branch) — the composer's file field allows this via
        # UiField.allow_folder (see the module-level ui_action declaration
        # below), the same mechanism transcribe's file field already uses.
        # translate_document() below has no folder-handling logic of its
        # own (only single-file _detect_and_validate_file), so this must be
        # checked here first, exactly mirroring run()'s own CLI branch.
        if os.path.isdir(file_path):
            sandbox.process_image_translation_folder(
                file_path, source_language, target_language, opts,
                workers=workers, spread=spread, on_progress=on_progress, on_page_text=on_page_text,
            )
            summary = f"Translated the images in '{file_name}' from {source_language} to {target_language}."
        else:
            sandbox.translate_document(
                file_path,
                source_language,
                target_language,
                page_nums=page_nums,
                opts=opts,
                scanned=scanned,
                workers=workers,
                spread=spread,
                on_progress=on_progress,
                on_page_text=on_page_text,
            )
            summary = f"Translated {file_name} from {source_language} to {target_language}."

        if not os.path.exists(output_path):
            raise CLIError("Translation finished but no output file was produced.")

        session_usage = sandbox.token_tracker.get_session_usage()
        return UiJobResult(
            output_path=output_path,
            output_filename=output_filename,
            summary=summary,
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

        Called after every change to the composer's form — including
        before a file has been chosen or a language picked — so this is
        deliberately lenient rather than raising ``CLIError`` the way
        ``run_ui_action`` does: an unresolved language falls back to a
        placeholder name, and the document's actual text is always a
        placeholder (no file is read here, matching the CLI's own
        ``--dry-run`` behavior). See ``UiPromptPreview``'s docstring in
        ``src/runtime/ui_action.py`` for what the return value means.

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

        def _lang_name(code: object) -> str:
            normalized = str(code or "").strip().lower()
            return LANGUAGE_MAP.get(normalized, "the selected language")

        source_language = _lang_name(fields.get("source_language"))
        target_language = _lang_name(fields.get("target_language"))
        scanned = str(fields.get("scanned", "")).strip().lower() in ("true", "1", "on", "yes")
        notes = (fields.get("notes") or "").strip() or None
        preserve_tables = str(fields.get("preserve_tables", "")).strip().lower() in ("true", "1", "on", "yes")
        toc = str(fields.get("toc", "")).strip().lower() in ("true", "1", "on", "yes")

        sandbox = SandboxProcessor(professor, model=model)
        if notes:
            sandbox.translation_service.system_note = notes
            sandbox.translation_service.user_note = notes
            sandbox.image_translation_service.system_note = notes
            sandbox.image_translation_service.user_note = notes
        if preserve_tables:
            sandbox.translation_service.tables = True
            sandbox.image_translation_service.tables = True
        if toc:
            sandbox.translation_service.toc = True
        apply_extension_ui_hooks("translate", fields.get("target_language"), sandbox, fields)

        requested_format = (fields.get("output_format") or "").strip().lower()
        output_format = requested_format if requested_format in ("docx", "pdf", "txt", "md") else "console"

        if scanned:
            svc = sandbox.image_translation_service
            sys_p, usr_p = svc.build_prompts(source_language, target_language)
            note = "Image content would be base64-encoded and attached to the user message"
        else:
            svc = sandbox.translation_service
            placeholder = generate_process_text("", f"[{source_language} document text]", "")
            sys_p, usr_p = svc.build_prompts(
                placeholder, source_language, target_language, output_format=output_format,
            )
            note = None

        return UiPromptPreview(
            system_prompt=sys_p, user_prompt=usr_p, model=svc._get_model(), note=note,
        )


plugin = TranslationPlugin()

# ── Webui composer action declaration ────────────────────────────────────
# v1 core-subset fields — see run_ui_action's docstring above for exactly
# what each one means and how it's read out of the submitted `fields` dict.
# `page_nums` is deliberately in this v1 set (not deferred like other CLI
# flags) because it's the answer to an interrupted job, not just a nicety.
ui_action = UiAction(
    id="translate",
    label="Translate a document",
    command="translate",
    fields=[
        UiField(name="source_language", label="Source language", kind="language", group="Document"),
        UiField(name="target_language", label="Target language", kind="language", group="Document"),
        UiField(
            name="file", label="Document (or select multiple images / a whole folder of scans)",
            kind="file", group="Document", allow_folder=True,
        ),
        UiField(
            name="page_nums", label="Page range (e.g. 8-12 — leave blank for the whole document)",
            kind="text", required=False, group="Document",
        ),
        UiField(
            name="scanned", label="Scanned PDF (render pages as images)", kind="checkbox",
            required=False, group="Scanned / images",
        ),
        UiField(
            name="spread", label="Two-page spread (facing pages scanned together)", kind="checkbox",
            required=False, group="Scanned / images",
        ),
        UiField(
            name="output_format", label="Output format", kind="select", required=False,
            choices=[
                {"value": "same", "label": "Same as source document (default)"},
                {"value": "docx", "label": "Word (.docx)"},
                {"value": "pdf", "label": "PDF"},
                {"value": "txt", "label": "Plain text (.txt)"},
                {"value": "md", "label": "Markdown (.md)"},
            ],
            group="Output",
        ),
        UiField(
            name="preserve_tables", label="Preserve tables (format tabular data as tables)",
            kind="checkbox", required=False, group="Output",
        ),
        UiField(
            name="toc", label="Document has a table of contents (normalize dot leaders)",
            kind="checkbox", required=False, group="Output",
        ),
        UiField(
            name="preserve_media", label="Preserve embedded images (Word-to-Word only)",
            kind="checkbox", required=False, group="Output",
        ),
        UiField(
            name="font", label="Custom font name (must already be installed in fonts/)",
            kind="text", required=False, group="Output",
        ),
        UiField(
            name="font_size", label="Body font size in points (default 9)",
            kind="text", required=False, group="Output",
        ),
        UiField(
            name="workers", label="Parallel workers (1 = also shows each page's text as it's translated)",
            kind="text", required=False, group="Performance",
        ),
        UiField(name="notes", label="Notes for the model", kind="text", required=False, group="Notes"),
    ],
    progress_verb="Translating",
)

# jobs.py's find_plugin_for_action()/list_ui_actions() look for ui_action on
# the *plugin instance* (getattr(p, "ui_action", None), where p is one of
# load_plugins()'s returned objects — the instance, never this module) —
# matching how run_ui_action is already a bound method on that same
# instance. A bare module-level `ui_action` variable, with nothing pointing
# from the instance to it, would be invisible to that lookup in the real
# app (only this file's own tests, which import the module directly and
# read `plugin_module.ui_action`, would ever see it).
plugin.ui_action = ui_action
