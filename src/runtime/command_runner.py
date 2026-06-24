"""Shared interactive helpers used by SandboxProcessor and plugins.

This module provides the tools that commands use when they need input from the
user at runtime — collecting multi-line text, prompting for notes to append to
a prompt, showing a preview of what would be sent without making an API call,
and resolving where to save output files. Plugin developers can call these
methods via ``SandboxProcessor`` or call the standalone helpers directly.
"""

import argparse
import logging
import os
from typing import Any, Optional, Tuple


logger = logging.getLogger(__name__)


class _CommandMixin:
    """Interactive helpers and output utilities shared by all SandboxProcessor commands.

    Provides methods for collecting multi-line input from the user, appending
    notes to prompts, previewing what would be sent to the AI without making
    a real call, and resolving where output files should be saved. These
    methods are available on ``SandboxProcessor`` because it inherits from
    this class — plugin developers can call them as ``sandbox.<method>()``.
    """

    @staticmethod
    def _collect_multiline(label: str) -> str:
        """Show a labelled prompt and collect lines of text until the user signals they are done.

        The user types as many lines as they like, then types ``---`` on its
        own line (the end-of-input signal) to finish. End-of-file (e.g. piped
        input) also stops collection.

        Args:
            label: The heading shown above the input area to tell the user
                   what kind of text to enter (e.g. ``'User prompt'``,
                   ``'Abstract text'``).

        Returns:
            All typed lines joined into a single string with newlines between
            them.
        """
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
        """Interactively ask the user where they want to add a note, then collect the note text.

        If either prompt is provided, it is displayed first so the user can
        see what they are annotating. The user then chooses whether to add
        the note to the system prompt, the user prompt, both at once, or a
        different note to each. The collected note text is returned ready to
        be appended to the relevant prompt before the API call is made.

        Args:
            system_prompt: The current system prompt text to display for
                           context. ``None`` if there is no system prompt to
                           show.
            user_prompt: The current user prompt text to display for context.
                         ``None`` if there is no user prompt to show.

        Returns:
            A two-item tuple of ``(system_note, user_note)``. Either item may
            be ``None`` if the user did not add a note to that prompt.
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
        """Copy any inline note flags from the parsed command-line arguments onto the service.

        Inline notes are notes passed directly on the command line via
        ``-ns``, ``-nu``, or ``-nb`` rather than typed interactively. This
        method reads those values from the parsed flags and assigns them to
        the service so they are appended to the relevant prompt before the
        API call is made. ``-nb`` (note-both) sets both prompts; ``-ns`` and
        ``-nu`` set each individually and take precedence over ``-nb`` for
        their respective prompt.

        Args:
            service: The service instance whose ``system_note`` and
                     ``user_note`` attributes will be set
                     (e.g. ``sandbox.translation_service``).
            args: The object holding all parsed command-line flags for the
                  current run.
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
        """Extract the temperature, top-p, and max-tokens values from the parsed command-line flags.

        Args:
            args: The object holding all parsed command-line flags for the
                  current run.

        Returns:
            A dictionary with keys ``'temperature'``, ``'top_p'``, and
            ``'max_tokens'``, each set to the value from the flags or
            ``None`` if that flag was not used.
        """
        return {
            'temperature': getattr(args, 'temperature', None),
            'top_p': getattr(args, 'top_p', None),
            'max_tokens': getattr(args, 'max_tokens', None),
        }

    @staticmethod
    def _dry_run_display(model: str, system_prompt: str, user_prompt: str, note: Optional[str] = None,
                         temperature: Optional[float] = None, top_p: Optional[float] = None,
                         max_tokens: Optional[int] = None) -> None:
        """Print a formatted preview of what would be sent to the AI, without making any API call.

        Shows the model name, any sampling overrides, and the full system and
        user prompts. Used when ``--dry-run`` is passed on the command line so
        the user can verify the prompt content before committing to an API call.

        Args:
            model: The model that would be used (e.g. ``'gpt-4o'``).
            system_prompt: The system prompt that would be sent.
            user_prompt: The user prompt that would be sent.
            note: An optional additional note that would be appended,
                  shown separately in the preview.
            temperature: The temperature override in effect, or ``None`` if
                         the default would be used.
            top_p: The top-p override in effect, or ``None`` if the default
                   would be used.
            max_tokens: The max-tokens override in effect, or ``None`` if the
                        default would be used.
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
        if note:
            print(f"  Note:  {note}")
        print(sep)
        print("\n--- SYSTEM PROMPT " + "-" * 52)
        print(system_prompt)
        print("\n--- USER PROMPT " + "-" * 54)
        print(user_prompt)
        print(f"\n{sep}\n")

    def _resolve_output_path(self, args: argparse.Namespace) -> Optional[str]:
        """Determine the absolute path where the output file should be saved.

        If a relative output path was specified, it is resolved relative to the
        input file's directory rather than the current working directory. This
        keeps the output file next to the source document, which is usually
        what the user expects. An absolute output path is returned unchanged.

        Args:
            args: The object holding all parsed command-line flags for the
                  current run. Reads ``output_file`` and ``input_file``
                  from this object.

        Returns:
            The absolute output file path as a string, or ``None`` if no
            output file was requested.
        """
        output_file_arg: Optional[str] = getattr(args, 'output_file', None)
        input_file_arg: Optional[str] = getattr(args, 'input_file', None)

        if output_file_arg:
            if not os.path.isabs(output_file_arg) and input_file_arg:
                abs_input = os.path.abspath(input_file_arg)
                input_dir = abs_input if os.path.isdir(abs_input) else os.path.dirname(abs_input)
                return os.path.join(input_dir, output_file_arg)
            return os.path.abspath(output_file_arg)

        return None
