"""DispatchPlugin — multi-plugin command merger.

When two or more plugins both register the same CLI command *and* each
declares a ``handles`` attribute (a list of source-language / token strings
they own), the plugin loader creates a DispatchPlugin to merge them instead
of raising a conflict error.

ROUTING
-------
At runtime ``run()`` inspects ``args.language_code`` (expected to be a 2-tuple
``(source, target)``), looks up which plugin owns the source token, and
delegates to that plugin's ``run()``.

If the *destination* token belongs to a different plugin and that plugin
exposes a ``get_peer_guidance(token)`` method, DispatchPlugin calls it and
injects the returned string into ``args._peer_guidance`` (a list) before
delegating.  The owning plugin's ``run()`` method is then responsible for
consuming ``args._peer_guidance`` (e.g. appending each entry to variant_notes
on the service object).

PARSER BUILDING
---------------
``register_subparsers()`` delegates to the primary plugin, which creates and
fully populates the subparser.  DispatchPlugin then retrieves the newly-created
parser via ``subparsers.choices`` and calls ``register_command_flags(parser)``
on each secondary plugin to append their flags to the shared parser.

This file contains no translation-specific logic.  The routing key is always
``args.language_code[0]`` (the first element of the language-code tuple), but
this can be extended for other dispatch schemes by subclassing.
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING, Optional

from .plugin import ModePlugin
from src.errors import CLIError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DispatchPlugin:
    """Merged plugin that owns multiple sub-plugins and routes by source token.

    Created by the plugin loader when two plugins both claim the same command
    and both declare ``handles``.

    Attributes
    ----------
    commands:
        Single-element list containing the shared command name.
    source_registry:
        Maps each handled source token to the plugin that owns it.
    """

    def __init__(self, command: str, primary: "ModePlugin") -> None:
        """Initialise with the *primary* plugin as the first owner.

        Parameters
        ----------
        command:
            The CLI subcommand name shared by all merged plugins.
        primary:
            The first plugin loaded; it must declare ``handles``.
        """
        self.commands: list[str] = [command]
        self._command = command
        self._primary = primary
        self._secondary: list["ModePlugin"] = []
        self.source_registry: dict[str, "ModePlugin"] = {
            token: primary for token in getattr(primary, "handles", [])
        }

    def _absorb(self, plugin: "ModePlugin", plugin_name: str) -> None:
        """Merge *plugin* into this dispatcher.

        Adds each token in ``plugin.handles`` to ``source_registry``.
        Tokens already claimed by an earlier plugin emit a warning and are
        skipped — the first-loaded plugin keeps ownership.

        Parameters
        ----------
        plugin:
            The additional plugin to merge.
        plugin_name:
            Human-readable name for warning messages (typically the plugin
            folder name).
        """
        for token in getattr(plugin, "handles", []):
            if token in self.source_registry:
                logger.warning(
                    "DispatchPlugin('%s'): token '%s' already owned by another "
                    "plugin — '%s' will not take ownership of this token.",
                    self._command,
                    token,
                    plugin_name,
                )
            else:
                self.source_registry[token] = plugin
        self._secondary.append(plugin)

    # ── Parser building ────────────────────────────────────────────────────────

    def register_subparsers(
        self,
        subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    ) -> None:
        """Build the shared subparser by merging all plugin flags.

        Calls the primary plugin's ``register_subparsers()`` to create the
        parser, then retrieves it from ``subparsers.choices`` and calls
        ``register_command_flags()`` on each secondary plugin to append their
        flags.
        """
        self._primary.register_subparsers(subparsers)
        parser = subparsers.choices.get(self._command)
        if parser is None:
            logger.warning(
                "DispatchPlugin('%s'): primary plugin did not create a "
                "subparser for '%s'; secondary flags will not be registered.",
                self._command,
                self._command,
            )
            return
        for secondary in self._secondary:
            if hasattr(secondary, "register_command_flags"):
                secondary.register_command_flags(parser)  # type: ignore[attr-defined]

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
        """Route execution to the plugin that owns the source token.

        Reads ``args.language_code`` (must be a 2-tuple ``(source, target)``),
        looks up the owning plugin for the source token, optionally collects
        destination guidance from the plugin owning the target token, and
        delegates to the owning plugin's ``run()``.

        Raises
        ------
        CLIError
            If ``args.language_code`` is not a 2-tuple, or if no plugin owns
            the source token.
        """
        language_code = getattr(args, "language_code", None)
        if not isinstance(language_code, tuple) or len(language_code) != 2:
            raise CLIError(
                f"Command '{self._command}' requires a language-code pair "
                "(e.g. J-E).  Provide source and target separated by a hyphen."
            )

        source_token, dest_token = language_code

        owner = self.source_registry.get(source_token)
        if owner is None:
            available = ", ".join(sorted(self.source_registry))
            raise CLIError(
                f"No plugin handles '{source_token}' as a source language. "
                f"Available source languages: {available}."
            )

        # Collect destination-side guidance from whichever plugin owns the
        # destination token, if it differs from the source owner.
        peer_guidance: list[str] = []
        dest_owner = self.source_registry.get(dest_token)
        if dest_owner is not None and dest_owner is not owner:
            if hasattr(dest_owner, "get_peer_guidance"):
                guidance = dest_owner.get_peer_guidance(dest_token)  # type: ignore[attr-defined]
                if guidance:
                    peer_guidance.append(guidance)
        elif dest_owner is None and dest_token:
            logger.debug(
                "DispatchPlugin('%s'): no plugin owns destination token '%s'; "
                "proceeding without destination guidance.",
                self._command,
                dest_token,
            )

        # Inject peer guidance so the owning plugin's run() can consume it.
        args._peer_guidance = peer_guidance  # type: ignore[attr-defined]

        owner.run(args, professor, model, temperature, top_p, max_tokens)
