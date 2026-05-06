"""ModePlugin Protocol — the public contract for plugins/*/plugin.py.

Every plugin module must expose a module-level attribute named ``plugin``
that is an instance of a class implementing this Protocol.  See
``plugin.py.template`` at the repository root for a fully-annotated example.
"""

import argparse
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ModePlugin(Protocol):
    """Contract every plugin module must satisfy.

    Plugin discovery
    ----------------
    The plugin loader scans ``plugins/*/plugin.py`` at application startup.
    Each module must expose a module-level ``plugin`` attribute that is an
    instance implementing this Protocol.  Plugins that fail to import or that
    are missing required attributes emit a warning and are silently skipped —
    they never crash the main application.

    Mandatory contract
    ------------------
    ``commands``
        A list of CLI subcommand name strings this plugin registers.

    ``register_subparsers(subparsers)``
        Called once at parser-build time.  Add your subcommand(s) here via
        ``subparsers.add_parser(...)``.

    ``run(args, professor, model, temperature, top_p, max_tokens)``
        Called when one of the plugin's subcommands is invoked.

        **Token tracking is mandatory.**  Every API call the plugin makes
        must be associated with a ``TokenTracker(professor=professor)``
        instance.  See ``plugin.py.template`` for the required pattern.
    """

    commands: list[str]

    def register_subparsers(
        self,
        subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    ) -> None:
        """Add this plugin's subcommand(s) to the main argument parser."""
        ...

    def run(
        self,
        args: argparse.Namespace,
        professor: str,
        model: Optional[str],
        temperature: Optional[float],
        top_p: Optional[float],
        max_tokens: Optional[int],
    ) -> None:
        """Execute the subcommand.  Token tracking is mandatory."""
        ...
