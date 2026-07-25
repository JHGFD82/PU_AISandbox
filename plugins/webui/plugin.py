"""PU_AISandbox web UI plugin — a local browser-based chat interface.

Implements the ``webui`` command: ``webui serve`` starts a local FastAPI
server (conversation history, model switching, a live spend sidebar) and
``webui set-passphrase`` sets the unlock-gate passphrase, writing its hash
to ``.settings``. See ``docs/webui-plugin-plan.md`` for the full design.

Unlike every other plugin, ``webui`` sets ``requires_professor = False`` —
its command doesn't belong to one professor at CLI-invocation time (you run
``python main.py webui serve`` with no professor argument at all); instead,
whichever professor is selected in the browser's switcher determines which
API key/budget/conversations are active for each request. See
``src/runtime/plugin.py`` for what this attribute does.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent


def _register(module_name: str, rel_path: str) -> None:
    """Load one of this plugin's own files and insert it into sys.modules under *module_name*.

    Identical in spirit to the ``_register()`` helper every other plugin
    uses (see ``plugins/prompt/plugin.py`` for the fully-annotated version).
    Called once per internal file, in dependency order, before any command
    runs.

    Args:
        module_name: The name to register the module under in
                     ``sys.modules``. For files only this plugin's own code
                     ever looks up, this is a flat, dot-free name (e.g.
                     ``'_pu_webui_auth'``) rather than a dotted path — see
                     ``src/app.py``'s module docstring for why. For files
                     other parts of the core project look up by convention
                     (this plugin's chat service, its settings), this
                     follows the existing ``src.services.<name>`` /
                     ``pu_plugin.<plugin>.settings`` convention instead.
        rel_path: The file's real path, relative to this plugin's own
                  directory.
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
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])


# Registered in dependency order: settings first (nothing depends on
# anything), then auth, conversation, attachments, and export (app.py needs
# all four; none of them depend on each other), then the AI service (a
# normal src.services.* registration, needed by app.py's chat route via
# SandboxProcessor rather than imported directly), then app.py itself last
# since it's the only file that needs everything else already in place.
_register("pu_plugin.webui.settings", "src/settings.py")
_register("_pu_webui_auth", "src/auth.py")
_register("_pu_webui_conversation", "src/conversation.py")
_register("_pu_webui_attachments", "src/attachments.py")
_register("_pu_webui_export", "src/export.py")
_register("src.services.chat_service", "src/services/chat_service.py")
_register("_pu_webui_app", "src/app.py")

from src.config import register_env_field
from src.errors import CLIError

# Lets `--show-config` and `python main.py env set/list` discover these two
# optional values without needing to know anything about the webui plugin
# specifically — see register_env_field()'s docstring in src/config.py.
register_env_field(
    "webui.passphrase_hash",
    "Unlock passphrase hash (generate via 'webui set-passphrase')",
    section="Web UI plugin",
    secret=True,
)
register_env_field(
    "webui.session_secret",
    "Session signing secret (any long random string; 'env set webui.session_secret --generate' works)",
    section="Web UI plugin",
    secret=True,
)


class WebUiPlugin:
    """Runs the local browser-based chat interface."""

    commands: list[str] = ["webui"]
    requires_professor: bool = False

    def register_subparsers(self, subparsers: argparse._SubParsersAction) -> None:
        """Register the ``webui`` subcommand and its ``serve``/``set-passphrase`` subcommands."""
        p = subparsers.add_parser("webui", help="Run the local web interface")
        webui_sub = p.add_subparsers(dest="webui_subcommand", help="webui subcommand")

        serve = webui_sub.add_parser("serve", help="Start the web server")
        serve.add_argument("--host", default=None, help="Address to listen on (default: 127.0.0.1)")
        serve.add_argument("--port", type=int, default=None, help="Port to listen on (default: 8000)")

        webui_sub.add_parser(
            "set-passphrase",
            help="Set the web UI unlock passphrase (writes webui.passphrase_hash to .settings)",
        )

    def run(
        self,
        args: argparse.Namespace,
        professor: str | None,
        model: str | None,
        temperature: float | None,
        top_p: float | None,
        max_tokens: int | None,
    ) -> None:
        """Dispatch to ``serve`` (start the server, blocks) or ``set-passphrase`` (print a hash, exits).

        Args:
            args: Parsed command-line flags, including ``webui_subcommand``,
                  and for ``serve``, ``host``/``port``.
            professor: Always ``None`` here — this plugin sets
                       ``requires_professor = False``, so no professor is
                       resolved from the command line at all. Which
                       professor's data is active is chosen later, per
                       browser session, via the in-app switcher.
            model, temperature, top_p, max_tokens: Unused. There's no
                       single "the" model for this command — the model is
                       chosen per conversation, in the browser.

        Raises:
            CLIError: If an unrecognized or missing subcommand is given.
        """
        subcommand = getattr(args, "webui_subcommand", None)
        if subcommand == "set-passphrase":
            _print_passphrase_hash()
        elif subcommand == "serve":
            _serve(args)
        else:
            raise CLIError(
                "No webui subcommand specified.\n"
                "Usage: python main.py webui serve [--host HOST] [--port PORT]\n"
                "       python main.py webui set-passphrase"
            )


plugin = WebUiPlugin()


def _serve(args: argparse.Namespace) -> None:
    """Start the local web server, falling back to this plugin's configured defaults for host/port."""
    from src.settings import WEBUI_HOST, WEBUI_PORT

    host = getattr(args, "host", None) or WEBUI_HOST
    port = getattr(args, "port", None) or WEBUI_PORT
    app_module = sys.modules["_pu_webui_app"]
    print(f"Starting Princeton AI Sandbox web interface at http://{host}:{port}")
    app_module.run_server(host=host, port=port)


def _print_passphrase_hash() -> None:
    """Prompt for a new passphrase (hidden input), hash it, and write it directly to .settings.

    Writing directly here (rather than printing a line to paste in, as this
    used to work) is safe for the same reason every other ``.settings``
    write is: it's driven by a command typed locally, never over a network
    call or as part of syncing files between machines.
    """
    from src import settings_store

    passphrase = getpass.getpass("New unlock passphrase: ")
    confirm = getpass.getpass("Confirm passphrase: ")
    if passphrase != confirm:
        raise CLIError("Passphrases did not match — nothing was generated.")
    if not passphrase:
        raise CLIError("Passphrase cannot be empty.")

    auth = sys.modules["_pu_webui_auth"]
    hashed = auth.hash_passphrase(passphrase)
    settings_store.set_value("webui.passphrase_hash", hashed)
    print("\nUnlock passphrase set.")
