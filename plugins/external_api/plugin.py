"""PU_AISandbox External API plugin — reference implementation.

This plugin implements the ``api-call`` command: send a prompt to any
external API (OpenAI-compatible AI endpoint or generic REST API) that
is configured in ``settings.toml`` under ``[external_apis.<name>]``.

It also serves as the **canonical example** for writing plugins that
call external (non-Portkey) APIs.  Fork this directory, rename the
class, and change ``commands``.

Installation
------------
This plugin ships inside the main PU_AISandbox repository.
No extra setup is required beyond adding your API config to
``settings.toml`` and setting the corresponding key in ``.env``.

Plugin contract (three required members)
-----------------------------------------
``commands : list[str]``
    CLI subcommand names this plugin owns.

``register_subparsers(subparsers)``
    Called once at startup.  Register your subcommand(s) here.

``run(args, professor, model, temperature, top_p, max_tokens)``
    Called when one of your commands is invoked.
    **Token tracking is mandatory** for AI endpoints.

Configuration
-------------
Add to ``settings.toml``::

    [external_apis.pu_sandbox]
    name = "PU AI Sandbox"
    base_url = "https://api.aisandbox.princeton.edu/v1"
    openai_compatible = true
    default_model = "gpt-4o"

Add to ``.env``::

    EXTERNAL_API_PU_SANDBOX_KEY=your_key_here

Then invoke::

    python main.py heller api-call --api pu_sandbox
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
    """Inject a plugin-owned module into sys.modules under its src.* namespace."""
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
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])


_register(
    "src.services.external_api_call_service",
    "src/services/external_api_call_service.py",
)

# ── Imports from the main repo ────────────────────────────────────────────────
from src.cli import add_common_flags                        # noqa: E402
from src.errors import CLIError                             # noqa: E402
from src.output.file_output import FileOutputHandler        # noqa: E402
from src.services import (                                  # noqa: E402
    load_api_config,
    list_apis,
    parse_model_source,
    get_default_api_name,
)
from src.services.external_api_call_service import APICallService  # noqa: E402
from src.tracking.token_tracker import TokenTracker         # noqa: E402

logger = logging.getLogger(__name__)


class ExternalAPIPlugin:
    """External API call plugin.

    Sends a free-form prompt to any configured external API endpoint
    (OpenAI-compatible AI endpoints or generic REST APIs) and prints
    the response.  Supports dry-run mode, output-to-file, and all
    standard model/sampling flags.
    """

    # ── Plugin identity ───────────────────────────────────────────────────────
    commands: list[str] = ["api-call"]

    # ── Argument registration ─────────────────────────────────────────────────
    def register_subparsers(
        self,
        subparsers: argparse._SubParsersAction,
    ) -> None:
        p = subparsers.add_parser(
            "api-call",
            help="Send a prompt to a configured external API endpoint",
        )
        p.add_argument(
            "--api",
            dest="api_name",
            type=str,
            default=None,
            metavar="NAME",
            help=(
                "Name of the external API to use, as declared in settings.toml "
                "(e.g. --api pu_sandbox).  Run with --list-apis to see all options."
            ),
        )
        p.add_argument(
            "--list-apis",
            dest="list_apis",
            action="store_true",
            help="List all configured external APIs and exit",
        )
        p.add_argument(
            "-s", "--system",
            dest="include_system_prompt",
            action="store_true",
            help="Prompt for a system (developer) prompt before the user prompt",
        )
        add_common_flags(p)  # -o, -m, -t, -T, -M, --dry-run

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
        # ── --list-apis ───────────────────────────────────────────────────
        if getattr(args, "list_apis", False):
            _print_api_list()
            return

        # ── Resolve API name and model ────────────────────────────────────
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
                "Or set [apis] default = \"<name>\" in settings.toml to use a default."
            )

        try:
            config = load_api_config(api_name)
        except ValueError as e:
            raise CLIError(str(e)) from e

        # ── Mandatory setup ───────────────────────────────────────────────
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

        # ── Collect optional system prompt ────────────────────────────────
        system_prompt: Optional[str] = None
        if getattr(args, "include_system_prompt", False):
            system_prompt = _collect_multiline("System prompt") or None

        # ── Dry-run support ───────────────────────────────────────────────
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

        # ── Collect user prompt and call the API ──────────────────────────
        user_prompt = _collect_multiline("User prompt")
        if not user_prompt.strip():
            raise CLIError("No prompt text provided.")

        try:
            response = svc.send_prompt(user_prompt, system_prompt)
        except Exception as e:
            raise CLIError(f"Error calling {config.display_name}: {e}") from e

        print("\n" + response)

        output_file = _resolve_output_path(args)
        if output_file:
            FileOutputHandler.save_to_text_file(response, output_file, label="Response")


# ── Module-level instance — REQUIRED ─────────────────────────────────────────
plugin = ExternalAPIPlugin()


# ── Internal helpers ──────────────────────────────────────────────────────────

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
            config = load_api_config(name)
            kind = "openai-compatible" if config.openai_compatible else "REST"
            model_note = f", model: {config.default_model}" if config.default_model else ""
            default_note = "  [default]" if name == default else ""
            print(f"  {name:20s}  {config.display_name}  [{kind}{model_note}]  {config.base_url}{default_note}")
        except ValueError:
            print(f"  {name:20s}  (API key not set — set EXTERNAL_API_{name.upper()}_KEY in .env)")


def _dry_run_display(
    config: object,
    messages: list[dict],
    model: Optional[str],
    temperature: Optional[float],
    top_p: Optional[float],
    max_tokens: Optional[int],
) -> None:
    """Print the request that *would* be sent without making any API call."""
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


def _resolve_output_path(args: argparse.Namespace) -> Optional[str]:
    """Return an absolute output file path from args, or None."""
    output_file = getattr(args, "output_file", None)
    if not output_file:
        return None
    return os.path.abspath(output_file)
