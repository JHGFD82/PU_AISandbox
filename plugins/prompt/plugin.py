"""PU_AISandbox Prompt plugin — reference implementation and developer template.

This plugin implements the ``prompt`` command: an interactive session that
sends a custom user prompt (and optional system prompt) to the AI model.

It also serves as the **canonical example** for writing new plugins.  Fork
this directory, rename the class, change ``commands``, and build from there.

Installation
------------
This plugin ships inside the main PU_AISandbox repository and is tracked
by it, alongside ``translation/``, ``transcription/`` and ``webui/``. The
East Asian language extensions (``translation-ea/``, ``transcription-ea/``)
are the ones that live in separate repositories. No extra setup is required.

To use it as a starting point for a new plugin::

    cp -r plugins/prompt plugins/myplugin
    # then edit plugin.py

See ``templates/plugin.py.template`` for an annotated skeleton, and
``docs/plugin-authoring-guide.md`` for the full walkthrough.

Plugin contract (three required members)
-----------------------------------------
``commands : list[str]``
    CLI subcommand names this plugin owns.

``register_subparsers(subparsers)``
    Called once at startup.  Register your subcommand(s) here.

``run(args, professor, model, temperature, top_p, max_tokens)``
    Called when one of your commands is invoked.
    Construct a ``SandboxProcessor`` — it handles API key resolution,
    token tracking, alternate-endpoint wiring, and lazy service loading.
    See the implementation below.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# ── Plugin directory ──────────────────────────────────────────────────────────

_PLUGIN_DIR = Path(__file__).parent


# ── Module registration ────────────────────────────────────────────────────────

def _register(module_name: str, rel_path: str) -> None:
    """Make one of this plugin's own files importable as if it lived in the main repo's ``src/`` tree.

    Plugins live in their own directory (``plugins/prompt/``) but their
    service files need to be reachable under a ``src.*`` import path — for
    example, ``src.services.prompt_service`` — so that ``SandboxProcessor``
    can find and load them the same way it loads the main repo's own
    services. This function loads the file directly from disk and inserts
    it into Python's registry of already-imported modules
    (``sys.modules``) under that name, so any later ``import
    src.services.prompt_service`` statement — from this plugin or from the
    main repo — resolves to this same file without needing it to actually
    exist at ``src/services/prompt_service.py``.

    Every plugin should call this once per service module it owns, at
    import time (see the call directly below this function), before any
    command runs.

    Args:
        module_name: The dotted import path to register the module under
                     (e.g. ``'src.services.prompt_service'``).
        rel_path: The module's real file path, relative to this plugin's own
                  directory (e.g. ``'src/services/prompt_service.py'``).
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
    # Expose the module as an attribute on its parent package so attribute-path
    # lookups (e.g. pytest monkeypatch) work correctly.
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])


# Registered first, so the service module below can import PROMPT_ROLE from it
# via src.settings — see src/settings.py's __getattr__ delegation.
_register("pu_plugin.prompt.settings", "src/settings.py")
_register("src.services.prompt_service", "src/services/prompt_service.py")

# ── Imports from the main repo ────────────────────────────────────────────────
# These are available because the main repo root is always on sys.path.
from src.cli import add_common_flags                        # shared flag helper  # noqa: E402
from src.errors import CLIError                             # standard user-facing error  # noqa: E402
from src.settings import PROMPT_ROLE  # noqa: E402
from src.output.file_output import FileOutputHandler        # noqa: E402

logger = logging.getLogger(__name__)


class PromptPlugin:
    """Interactive custom-prompt mode plugin.

    Sends a free-form user prompt (and an optional system prompt) to the
    configured AI model and prints the response.  Supports dry-run mode,
    output-to-file, and all standard model/sampling flags.
    """

    # Which models this plugin's work should use. Required of every plugin —
    # see src/runtime/model_role.py and the loader's _declares_model_roles().
    model_roles = {"prompt": PROMPT_ROLE}

    # ── Plugin identity ───────────────────────────────────────────────────────
    commands: list[str] = ["prompt"]

    # ── Argument registration ─────────────────────────────────────────────────
    def register_subparsers(
        self,
        subparsers: argparse._SubParsersAction,
    ) -> None:
        """Register the ``prompt`` subcommand and its command-line flags.

        Called once at startup by the plugin loader, before any command is
        run. ``subparsers`` is the object the main CLI uses to collect every
        plugin's subcommands into one ``--help`` listing; each plugin adds
        its own commands to it here rather than modifying the main CLI
        directly.

        Args:
            subparsers: The shared subcommand registry passed in by the CLI
                        startup code.
        """
        p = subparsers.add_parser(
            "prompt",
            help="Send a custom prompt to the AI model",
        )
        p.add_argument(
            "-s", "--system",
            dest="include_system_prompt",
            action="store_true",
            help="Prompt for a system (developer) prompt before the user prompt",
        )
        add_common_flags(p)   # -o, -m, -t, -T, -M, --dry-run

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
        """Run the ``prompt`` command: collect a prompt from the user and send it to the AI model.

        Called by the main CLI whenever the user runs ``prompt`` on the
        command line. This is the method every plugin must implement — study
        it as the reference example for what a plugin's ``run()`` should do:
        build a ``SandboxProcessor`` (which resolves the professor's API key,
        sets up token/cost tracking, and lazily creates whichever services
        the plugin needs), collect any command-specific input, call the
        service, and print or save the result.

        Args:
            args: The object holding all the parsed command-line flags for
                  this run (e.g. whether ``--dry-run`` was passed, the
                  requested output file path).
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
            CLIError: If no prompt text was entered, or if the API call
                fails.
        """
        # ── Mandatory setup ───────────────────────────────────────────────
        # Imported here (not at module level) because SandboxProcessor's class
        # statement discovers plugin-registered runtime mixins from
        # sys.modules at first-import time — safe only once load_plugins()
        # has run every plugin's module-level _register() calls, which
        # happens before any plugin's run() is ever dispatched.
        from src.runtime.sandbox_processor import SandboxProcessor

        # SandboxProcessor owns API key resolution, TokenTracker creation,
        # alternate-endpoint detection (colon syntax), and lazy service wiring.
        sandbox = SandboxProcessor(
            professor,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        svc = sandbox.prompt_service

        # ── Collect optional system prompt ────────────────────────────────
        system_prompt: Optional[str] = None
        if getattr(args, "include_system_prompt", False):
            system_prompt = _collect_multiline("System prompt") or None

        # ── Dry-run support ───────────────────────────────────────────────
        if getattr(args, "dry_run", False):
            effective_model = svc._get_model()
            sys_p, usr_p = svc.build_prompts(
                "[Interactive prompt — text would be entered at runtime]",
                system_prompt,
            )
            _dry_run_display(
                effective_model, sys_p, usr_p,
                temperature=temperature, top_p=top_p, max_tokens=max_tokens,
            )
            return

        # ── Collect user prompt and call the API ──────────────────────────
        user_prompt = _collect_multiline("User prompt")
        if not user_prompt.strip():
            raise CLIError("No prompt text provided.")

        try:
            response = svc.send_prompt(user_prompt, system_prompt)
        except Exception as e:
            raise CLIError(f"Error sending prompt: {e}") from e

        print("\n" + response)

        output_file = _resolve_output_path(args)
        if output_file:
            _save_response(response, output_file)


# ── Module-level instance — REQUIRED ─────────────────────────────────────────
# The plugin loader imports this module and reads the ``plugin`` attribute.
plugin = PromptPlugin()


# ── Internal helpers ──────────────────────────────────────────────────────────
# These are small utilities used only by this plugin.  If you base a new plugin
# on this file, feel free to keep, remove, or extend them as needed.

def _collect_multiline(label: str) -> str:
    """Prompt the user to type multiple lines of text in the terminal, one line at a time.

    Args:
        label: What to call the text being collected in the on-screen prompt
               (e.g. ``'User prompt'``).

    Returns:
        Everything the user typed, joined into a single string with line
        breaks preserved. Input stops when the user types ``---`` alone on
        its own line (a marker chosen because it's unlikely to appear in
        real prompt text) or reaches the end of the input stream.
    """
    print(f"{label} (type --- on its own line when done):")
    lines: list[str] = []
    while True:
        try:
            line = input()
            if line.strip() == "---":
                break
            lines.append(line)
        except EOFError:
            break
    return "\n".join(lines)


def _dry_run_display(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> None:
    """Print what would be sent to the AI model, without actually sending it.

    Used to implement ``--dry-run``, letting a user preview the exact
    prompts and settings before spending any API budget.

    Args:
        model: The model that would be called, e.g. ``'gpt-4o'``.
        system_prompt: The system prompt text that would be sent.
        user_prompt: The user prompt text that would be sent.
        temperature: The sampling temperature that would be used, if the
                     user specified one.
        top_p: The nucleus-sampling value that would be used, if specified.
        max_tokens: The maximum response length that would be requested, if
                    specified.
    """
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
    print(sep)
    print("\n--- SYSTEM PROMPT " + "-" * 52)
    print(system_prompt)
    print("\n--- USER PROMPT " + "-" * 54)
    print(user_prompt)
    print(f"\n{sep}\n")


def _resolve_output_path(args: argparse.Namespace) -> Optional[str]:
    """Turn the user-supplied output file flag into a full, absolute file path.

    Args:
        args: The object holding all the parsed command-line flags for this
              run.

    Returns:
        The absolute output file path, or ``None`` if the user didn't
        request one.
    """
    output_file = getattr(args, "output_file", None)
    if not output_file:
        return None
    return os.path.abspath(output_file)


def _save_response(response: str, output_file: str) -> None:
    """Save the AI model's response to disk, choosing the file format from the file extension.

    Args:
        response: The text to save.
        output_file: The destination path. The extension determines the
                     format — ``.json``, ``.md``, and ``.xlsx`` are handled
                     specially; anything else is saved as plain text.
    """
    ext = Path(output_file).suffix.lower()
    if ext == '.json':
        FileOutputHandler.save_to_json(response, output_file, label="Response")
    elif ext == '.md':
        FileOutputHandler.save_to_markdown(response, output_file, label="Response")
    elif ext == '.xlsx':
        FileOutputHandler.save_to_excel(response, output_file, label="Response")
    else:
        FileOutputHandler.save_to_text_file(response, output_file, label="Response")
