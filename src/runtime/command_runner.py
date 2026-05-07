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
from typing import Any, Optional, Tuple

from ..errors import CLIError

logger = logging.getLogger(__name__)


class _CommandMixin:
    """Mixin that adds helper methods to SandboxProcessor.

    All instance-method references to ``self.*`` resolve on the concrete
    ``SandboxProcessor`` subclass via normal Python MRO.
    """

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
