"""ModePlugin — the contract every plugin must follow, defined in plugins/*/plugin.py.

Every plugin module must expose a module-level attribute named ``plugin``
that is an instance of a class satisfying the requirements described below.
See ``plugin.py.template`` at the repository root for a fully-annotated
example to copy from.
"""

import argparse
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ModePlugin(Protocol):
    """The set of attributes and methods every plugin must provide to be loaded.

    This is a structural contract, not a class to inherit from — a plugin
    class satisfies it simply by defining the attributes and methods listed
    below, with no need to import or extend anything from this file. The
    plugin loader (``src/runtime/plugin_loader.py``) checks each plugin
    against this contract when scanning ``plugins/*/plugin.py`` at
    application startup. A plugin that's missing something required, or that
    fails to import at all, is skipped with a warning printed to the log —
    it never crashes the rest of the application.

    Required attribute:
        ``commands`` — a list of the CLI subcommand names this plugin
        registers, e.g. ``['translate']``.

    Required methods:
        ``register_subparsers(subparsers)`` — called once when the
        command-line parser is being built. Add your subcommand(s) here using
        ``subparsers.add_parser(...)``.

        ``run(args, professor, model, temperature, top_p, max_tokens)`` —
        called whenever one of this plugin's subcommands is invoked by a
        user. Every API call made inside ``run()`` must be tracked with a
        ``TokenTracker(professor=professor)`` instance — this is mandatory
        for every plugin, so that per-professor usage reporting stays
        accurate. See ``plugin.py.template`` for the required pattern.
    """

    commands: list[str]

    def register_subparsers(
        self,
        subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    ) -> None:
        """Add this plugin's subcommand(s) to the main command-line parser."""
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
        """Carry out the requested subcommand.

        Args:
            args: The object holding all parsed command-line flags for the
                  current run (e.g. the input file path, output path, and
                  any mode-specific flags this plugin registered).
            professor: The professor running this command, used for API key
                       lookup and token tracking.
            model: The model name to use, if the user specified one with
                   ``-m``. ``None`` means the plugin should fall back to its
                   own default model.
            temperature: How varied or creative the response should be, if
                         the user specified a value with ``-t``. ``None``
                         means use the model's default.
            top_p: An alternative response-variety control, if the user
                   specified a value with ``-T``. ``None`` means use the
                   model's default.
            max_tokens: The maximum length of the AI's response, in tokens
                        (tokens are the unit AI providers use to measure text
                        length, roughly one token per word), if the user
                        specified a value with ``-M``. ``None`` means use the
                        model's default limit.

        Note:
            Token tracking is mandatory — every API call made here must be
            associated with a ``TokenTracker(professor=professor)`` instance.
        """
        ...
