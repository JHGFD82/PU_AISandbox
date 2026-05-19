"""Core ``api-call`` command — send a prompt to any configured API endpoint.

This is a built-in CLI command (not a plugin).  It sends a free-form prompt
to any OpenAI-compatible API endpoint configured in ``settings.toml`` under
``[apis.<name>]``.

Usage::

    python main.py heller api-call --api pu_sandbox
    python main.py heller api-call -m della:qwen-preview
    python main.py heller api-call --list-apis
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Optional

from ..errors import CLIError
from ..output.file_output import FileOutputHandler
from ..services import (
    APICallService,
    load_api_config,
    list_apis,
    parse_model_source,
    get_default_api_name,
)
from ..tracking.token_tracker import TokenTracker

logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``api-call`` subcommand."""
    p = subparsers.add_parser(
        "api-call",
        help="Send a prompt to a configured API endpoint",
    )
    p.add_argument(
        "--api",
        dest="api_name",
        type=str,
        default=None,
        metavar="NAME",
        help=(
            "Name of the API to use, as declared in settings.toml "
            "(e.g. --api pu_sandbox).  Run with --list-apis to see all options."
        ),
    )
    p.add_argument(
        "--list-apis",
        dest="list_apis",
        action="store_true",
        help="List all configured APIs and exit",
    )
    p.add_argument(
        "-s", "--system",
        dest="include_system_prompt",
        action="store_true",
        help="Prompt for a system (developer) prompt before the user prompt",
    )
    # Common flags: -o, -m, -t, -T, -M, --dry-run
    from ..cli import add_common_flags
    add_common_flags(p)


def run(
    args: argparse.Namespace,
    professor: str,
    model: Optional[str],
    temperature: Optional[float],
    top_p: Optional[float],
    max_tokens: Optional[int],
) -> None:
    """Execute the ``api-call`` command."""
    if getattr(args, "list_apis", False):
        _print_api_list()
        return

    # Priority: colon in -m > --api flag > apis.default
    api_name = getattr(args, "api_name", None)
    bare_model = model

    if model:
        colon_api, colon_model = parse_model_source(model)
        if colon_api:
            api_name = colon_api
            bare_model = colon_model

    if not api_name:
        api_name = get_default_api_name()

    if not api_name:
        apis = list_apis()
        hint = (
            f"Available: {', '.join(apis)}"
            if apis
            else "No APIs are configured in settings.toml."
        )
        raise CLIError(
            f"--api <name> is required.  {hint}\n"
            "Run with --list-apis to see all configured options.\n"
            'Or set [apis] default = "<name>" in settings.toml to use a default.'
        )

    try:
        config = load_api_config(api_name)
    except ValueError as e:
        raise CLIError(str(e)) from e

    token_tracker = TokenTracker(professor=professor)
    svc = APICallService(
        config,
        professor=professor,
        token_tracker=token_tracker,
        model=bare_model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )

    system_prompt: Optional[str] = None
    if getattr(args, "include_system_prompt", False):
        system_prompt = _collect_multiline("System prompt") or None

    if getattr(args, "dry_run", False):
        messages = svc.build_messages(
            "[Interactive prompt — text would be entered at runtime]",
            system_prompt,
        )
        _dry_run_display(
            config=config,
            messages=messages,
            model=bare_model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        return

    user_prompt = _collect_multiline("User prompt")
    if not user_prompt.strip():
        raise CLIError("No prompt text provided.")

    try:
        response = svc.send_prompt(user_prompt, system_prompt)
    except Exception as e:
        raise CLIError(f"Error calling {config.display_name}: {e}") from e

    print("\n" + response)

    output_file = getattr(args, "output_file", None)
    if output_file:
        FileOutputHandler.save_to_text_file(
            response, os.path.abspath(output_file), label="Response"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_multiline(label: str) -> str:
    """Print *label* and collect lines until ``---`` sentinel or EOF."""
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


def _print_api_list() -> None:
    """Print all configured APIs."""
    apis = list_apis()
    default = get_default_api_name()
    if not apis:
        print(
            "No APIs are configured.\n"
            "Add [apis.<name>] sections to settings.toml."
        )
        return
    print("Configured APIs:")
    for name in apis:
        try:
            cfg = load_api_config(name)
            kind = "openai-compatible" if cfg.openai_compatible else "REST"
            model_note = f", model: {cfg.default_model}" if cfg.default_model else ""
            default_note = "  [default]" if name == default else ""
            print(
                f"  {name:20s}  {cfg.display_name}  [{kind}{model_note}]"
                f"  {cfg.base_url}{default_note}"
            )
        except ValueError:
            print(
                f"  {name:20s}  "
                f"(API key not set — set EXTERNAL_API_{name.upper()}_KEY in .env)"
            )


def _dry_run_display(
    config: object,
    messages: list[dict],
    model: Optional[str],
    temperature: Optional[float],
    top_p: Optional[float],
    max_tokens: Optional[int],
) -> None:
    """Print what would be sent without making an API call."""
    sep = "=" * 70
    print(f"\n{sep}")
    print("  DRY RUN — No API call will be made")
    print(f"  API:     {getattr(config, 'display_name', '?')} ({getattr(config, 'api_name', '?')})")
    print(f"  URL:     {getattr(config, 'base_url', '?')}")
    if model:
        print(f"  Model:   {model}")
    if temperature is not None:
        print(f"  Temperature: {temperature}")
    if top_p is not None:
        print(f"  Top-p: {top_p}")
    if max_tokens is not None:
        print(f"  Max tokens: {max_tokens}")
    print(sep)
    for msg in messages:
        role = msg.get("role", "?").upper()
        content = msg.get("content", "")
        print(f"\n--- {role} " + "-" * (65 - len(role)))
        print(content)
    print(f"\n{sep}\n")
