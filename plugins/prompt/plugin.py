"""PU_AISandbox Prompt plugin — reference implementation and developer template.

This plugin implements the ``prompt`` command: an interactive session that
sends a custom user prompt (and optional system prompt) to the AI model.

It also serves as the **canonical example** for writing new plugins.  Fork
this directory, rename the class, change ``commands``, and build from there.

Installation
------------
This plugin ships *inside* the main PU_AISandbox repository and is tracked
by it (unlike ``translation/`` and ``transcription/``, which are separate
git repos that you clone in).  No extra setup is required.

To use it as a starting point for a new plugin::

    cp -r plugins/prompt plugins/myplugin
    cd plugins/myplugin && git init   # optional — only if you want a separate repo
    # then edit plugin.py

Plugin contract (three required members)
-----------------------------------------
``commands : list[str]``
    CLI subcommand names this plugin owns.

``register_subparsers(subparsers)``
    Called once at startup.  Register your subcommand(s) here.

``run(args, professor, model, temperature, top_p, max_tokens)``
    Called when one of your commands is invoked.
    **Token tracking is mandatory** — create a ``TokenTracker`` and pass it
    to every service you call.  See the implementation below.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Optional

# ── Imports from the main repo ────────────────────────────────────────────────
# These are available because the main repo root is always on sys.path.
from src.cli import _add_common_flags          # shared flag helper
from src.config import get_api_key             # API key resolution
from src.errors import CLIError                # standard user-facing error
from src.output.file_output import FileOutputHandler
from src.services.prompt_service import PromptService
from src.tracking.token_tracker import TokenTracker  # MANDATORY — see run()

logger = logging.getLogger(__name__)


class PromptPlugin:
    """Interactive custom-prompt mode plugin.

    Sends a free-form user prompt (and an optional system prompt) to the
    configured AI model and prints the response.  Supports dry-run mode,
    output-to-file, and all standard model/sampling flags.
    """

    # ── Plugin identity ───────────────────────────────────────────────────────
    commands: list[str] = ["prompt"]

    # ── Argument registration ─────────────────────────────────────────────────
    def register_subparsers(
        self,
        subparsers: argparse._SubParsersAction,
    ) -> None:
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
        _add_common_flags(p)   # -o, -m, -t, -T, -M, --dry-run

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
        # ── Mandatory setup ───────────────────────────────────────────────
        api_key, _ = get_api_key(professor)
        token_tracker = TokenTracker(professor=professor)   # MANDATORY

        svc = PromptService(
            api_key, professor,
            token_tracker=token_tracker,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )

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
            FileOutputHandler.save_to_text_file(response, output_file, label="Response")


# ── Module-level instance — REQUIRED ─────────────────────────────────────────
# The plugin loader imports this module and reads the ``plugin`` attribute.
plugin = PromptPlugin()


# ── Internal helpers ──────────────────────────────────────────────────────────
# These are small utilities used only by this plugin.  If you base a new plugin
# on this file, feel free to keep, remove, or extend them as needed.

def _collect_multiline(label: str) -> str:
    """Print *label* and collect lines of text until ``---`` sentinel or EOF."""
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
    """Print the prompts that *would* be sent without making any API call."""
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
    """Return an absolute output file path from args, or None."""
    output_file = getattr(args, "output_file", None)
    if not output_file:
        return None
    return os.path.abspath(output_file)
