"""DispatchPlugin — merges two plugins that both claim the same command.

Occasionally two installed plugins both register the same CLI command — for
example, the base ``translation`` plugin and an East-Asian-language
extension plugin might both want to own the ``translate`` command, one
handling English and the other handling Japanese or Korean. When this
happens, and both plugins declare which source-language tokens they own (via
a ``handles`` list), the plugin loader builds a ``DispatchPlugin`` to combine
them into a single command instead of raising a conflict error.

How routing works: when the merged command runs, ``DispatchPlugin`` looks at
the source-language token the user requested (e.g. ``'jp'`` for Japanese) and
hands the request off to whichever original plugin declared ownership of
that token. If the destination language belongs to a different plugin, and
that plugin can offer extra guidance for translating into its language, that
guidance is collected and made available to the plugin that ends up running
the command.

How the command-line flags are built: the first-loaded ("primary") plugin
builds the shared subcommand parser as normal. Each additional plugin then
gets a chance to add its own extra flags onto that same parser, so the
command ends up supporting every merged plugin's options.

This file contains no translation-specific logic itself — the routing key
is always the first part of ``args.language_code`` — so the same approach
could support other kinds of merged commands in the future.
"""

from __future__ import annotations

import argparse
import logging
from typing import Optional

from .plugin import ModePlugin
from src.errors import CLIError

logger = logging.getLogger(__name__)


class DispatchPlugin:
    """A stand-in plugin that routes a shared command to whichever merged plugin owns it.

    Built automatically by the plugin loader (never constructed directly by a
    plugin developer) whenever two or more plugins claim the same command and
    each declares a ``handles`` list of the source-language tokens it's
    responsible for.

    Attributes:
        commands: A single-item list containing the shared command name
                  (e.g. ``['translate']``).
        source_registry: A dictionary mapping each source-language token
                         (e.g. ``'jp'`` for Japanese) to the plugin that
                         handles it.
        handles: Every source-language token owned by any plugin merged so
                 far. See the property below for why a dispatcher needs to
                 report this the same way a real plugin does.
    """

    def __init__(self, command: str, primary: "ModePlugin") -> None:
        """Start a new dispatcher with the first-loaded plugin as its initial owner.

        Args:
            command: The CLI subcommand name shared by all merged plugins,
                     e.g. ``'translate'``.
            primary: The first plugin encountered for this command. It must
                     declare a ``handles`` list of the source-language
                     tokens it owns.
        """
        self.commands: list[str] = [command]
        self._command = command
        self._primary = primary
        self._secondary: list["ModePlugin"] = []
        self.source_registry: dict[str, "ModePlugin"] = {
            token: primary for token in getattr(primary, "handles", [])
        }

    @property
    def handles(self) -> list[str]:
        """Return every source-language token any merged plugin owns.

        A dispatcher has to answer this question the same way an ordinary
        plugin does, because the plugin loader uses "do both sides declare
        ``handles``?" to decide whether two plugins claiming one command can
        be merged or are a genuine conflict.

        Without this, only the *first* two plugins for a command could ever
        merge: once a dispatcher had replaced them, a third plugin claiming
        the same command would find a dispatcher with no ``handles`` of its
        own, fail that check, and be skipped with a "command already
        registered by another plugin" warning — which points at the wrong
        cause and is misleading to whoever wrote that third plugin. With this
        property, a third (and fourth, and so on) language extension merges
        in exactly like the second.

        Returns:
            The source-language tokens owned so far (e.g.
            ``['en', 'jp', 'ko']``), in the order they were registered.
        """
        return list(self.source_registry)

    def _absorb(self, plugin: "ModePlugin", plugin_name: str) -> None:
        """Add another plugin's owned tokens into this dispatcher.

        Args:
            plugin: The additional plugin being merged into this command.
            plugin_name: The plugin's folder name, used only to identify it
                         in warning messages if a token conflict is found.
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

    # ── Webui composer action passthrough ───────────────────────────────────
    # Only the primary (base) plugin's ui_action/run_ui_action/
    # preview_ui_action are ever exposed here — a language-extension plugin
    # (e.g. an EA translation plugin) doesn't get a separate composer entry
    # of its own. The base plugin's action already covers every language
    # registered globally via register_language() (including ones an
    # extension plugin added), since the CLI-level language-specific flags
    # a real translate/transcribe invocation might apply simply aren't part
    # of the composer's field set.

    # Optional attributes worth forwarding to the primary plugin. The UI three
    # so a composer action isn't hidden in exactly the installations that have a
    # language extension; model_roles so that asking a merged command which
    # models it uses gets the answer the primary declared, rather than nothing.
    _PROXIED_UI_ATTRS = (
        "ui_action", "run_ui_action", "preview_ui_action", "model_roles",
    )

    def __getattr__(self, name: str):
        """Forward a small allowlist of optional attributes to the primary plugin.

        Only reached when normal attribute lookup on this object fails —
        Python calls ``__getattr__`` as the last resort, so this never
        interferes with ``commands``, ``run``, ``source_registry``, etc.,
        which are real attributes set in ``__init__``/defined above and
        found normally. Restricted to a small allowlist (rather than
        forwarding anything unrecognized) so a genuine typo'd attribute
        access still raises ``AttributeError`` instead of silently reaching
        into the wrong plugin's namespace.

        Without this, ``jobs.py``'s ``getattr(p, "ui_action", None)`` and
        ``hasattr(p, "run_ui_action")`` checks (``p`` being whatever
        ``load_plugins()`` returns — a ``DispatchPlugin`` whenever an
        extension plugin like ``translation-ea`` is installed) would always
        see ``None``/``False``, even though the primary plugin declares
        both — silently hiding the composer action in exactly the
        installations that have a language extension plugin added.

        Raises:
            AttributeError: If *name* isn't in the allowlist, or if the
                primary plugin doesn't have it either (e.g. it declares
                ``ui_action`` but has no ``preview_ui_action``) — propagated
                from the primary's own lookup, which is what lets
                ``hasattr()``/``getattr(..., default)`` correctly report
                "not available" rather than raising for the wrong reason.
        """
        if name in DispatchPlugin._PROXIED_UI_ATTRS:
            return getattr(self._primary, name)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    # ── Parser building ────────────────────────────────────────────────────────

    def register_subparsers(
        self,
        subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    ) -> None:
        """Build the shared command's flags by combining every merged plugin's options.

        The first-loaded plugin builds the subcommand parser as it normally
        would. Each additional merged plugin is then given a chance to add
        its own extra flags onto that same parser (via
        ``register_command_flags()``), so the final command supports every
        merged plugin's options together.

        Args:
            subparsers: The shared subcommand-registration object passed
                        down from ``create_argument_parser()``.
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
        """Hand this command off to whichever merged plugin owns the requested source language.

        Reads the source-language token the user requested from
        ``args.language_code``, which can be either a two-item pair
        ``(source, target)`` — as used by translation commands — or a single
        string — as used by transcription commands, where the string itself
        is the source token.

        Args:
            args: The object holding all parsed command-line flags for the
                  current run, including ``language_code``.
            professor: The professor running this command, used for API key
                       lookup and token tracking.
            model: The model name requested with ``-m``, if any. ``None``
                   uses the owning plugin's default.
            temperature: The response-variety setting requested with ``-t``,
                         if any. ``None`` uses the default.
            top_p: The alternative response-variety setting requested with
                   ``-T``, if any. ``None`` uses the default.
            max_tokens: The maximum response length requested with ``-M``,
                        if any. ``None`` uses the default.

        Raises:
            CLIError: If ``args.language_code`` isn't in a recognized format,
                      or if no merged plugin owns the requested source
                      language.
        """
        language_code = getattr(args, "language_code", None)
        if isinstance(language_code, tuple) and len(language_code) == 2:
            source_token, dest_token = language_code
        elif isinstance(language_code, str):
            source_token = language_code
            dest_token = None
        else:
            raise CLIError(
                f"Command '{self._command}' requires a language-code argument "
                "(either a single code such as 'en', or a language-code pair "
                "such as 'jp-en')."
            )

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
        # A single-code command (e.g. 'transcribe en') has no destination
        # token, so there is no destination owner to ask. Looking the missing
        # token up in the registry happened to return nothing anyway, but
        # saying so directly is what the next few lines already assume.
        dest_owner = self.source_registry.get(dest_token) if dest_token else None
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
