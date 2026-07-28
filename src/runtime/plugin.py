"""ModePlugin — the contract every plugin must follow, defined in plugins/*/plugin.py.

Every plugin module must expose a module-level attribute named ``plugin``
that is an instance of a class satisfying the requirements described below.
See ``templates/plugin.py.template`` for a fully-annotated
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
        accurate. See ``templates/plugin.py.template`` for the required pattern.

    Optional attribute:
        ``requires_professor`` — set this to ``False`` on a plugin whose
        command doesn't belong to any one professor (e.g. a plugin that
        starts a shared local web server rather than making a single
        professor's API calls). Every plugin is checked for this attribute
        with ``getattr(plugin, "requires_professor", True)``, so plugins
        that don't define it at all keep today's behavior — a professor
        name is still required before the command runs. Only a plugin that
        explicitly sets ``requires_professor = False`` is allowed to run
        with ``professor=None``; that plugin's ``run()`` method must handle
        ``professor`` being ``None`` itself (e.g. by prompting the user to
        pick one at runtime rather than assuming it was supplied up front).

    Optional attribute + method pair (webui composer actions):
        ``ui_action`` — a module-level ``UiAction`` instance (see
        ``src/runtime/ui_action.py``) a plugin declares to appear as a
        background-job trigger in the webui plugin's composer (e.g.
        "Translate a document"). Absent by default — a plugin that doesn't
        set this simply doesn't show up there; every existing plugin is
        unaffected. See ``docs/webui-plugin-plan.md`` section 10.

        ``run_ui_action(fields, professor, model, on_progress, output_dir)``
        — required alongside ``ui_action`` (and only then): runs that
        action outside the CLI's argparse path, given a plain ``dict`` of
        the submitted form's field values (keyed by each declared
        ``UiField.name``) instead of a parsed ``argparse.Namespace``.
        ``output_dir`` is a directory the webui has already created and
        guarantees is writable — the plugin's one output file must be
        written somewhere under it (the webui, not the plugin, owns naming
        and cleanup policy for that directory). Must return a
        ``UiJobResult`` (``src/runtime/ui_action.py``) pointing at the file
        actually written there. ``on_progress``, if not ``None``, should be
        called with ``(completed_count, total_count)`` after each page or
        image finishes, wherever the plugin's own execution methods already
        accept an ``on_progress`` parameter for this purpose (e.g.
        ``translate_document``'s — see the translation plugin for the
        reference implementation).

        ``preview_ui_action(fields, professor, model)`` — fully optional,
        independent of the pair above (a plugin can decide not to bother
        with a live preview even if it declares ``ui_action``). Returns a
        ``UiPromptPreview`` (``src/runtime/ui_action.py``) built from
        whatever the composer's form currently holds — called after every
        change to a field, so it must be cheap, must never make a real API
        call, and must tolerate incomplete/blank fields gracefully (e.g. a
        language field that hasn't been chosen yet) rather than raising.
        This is ``--dry-run`` made interactive — see
        ``docs/webui-plugin-plan.md`` section 10's two-pane preview panel,
        and the translation/transcription plugins for reference
        implementations built on their existing ``build_prompts()``
        methods.
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
