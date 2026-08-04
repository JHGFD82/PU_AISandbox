"""UiAction / UiField / UiJobResult — the optional contract a plugin uses to
appear as a background-job trigger in the web UI's composer.

See ``docs/plugin-authoring-guide.md`` ("A button in the web interface")
for the authoring walkthrough. In short: a plugin that wants a composer
entry declares a module-level ``ui_action`` (one ``UiAction`` instance)
alongside its usual ``plugin`` object, and adds a
``run_ui_action(fields, professor, model, on_progress, output_dir)``
method to its plugin class. Both are optional and undeclared by default —
same spirit as the optional ``requires_professor`` attribute documented in
``plugin.py`` — so a plugin that declares neither stays CLI-only, and
installing a plugin that declares both gives it a composer entry with no
changes needed in ``plugins/webui/``.

These are plain dataclasses, not a formal part of the ``ModePlugin``
``Protocol`` in ``plugin.py`` — the same reasoning that keeps
``requires_professor`` out of the formal Protocol applies here: an
``@runtime_checkable`` Protocol's ``isinstance()`` check only looks for
attributes to exist, not for them to be meaningfully typed or present on
every implementer, so an *optional* capability like this is documented and
read with ``getattr(plugin, "ui_action", None)`` rather than declared as a
required Protocol member.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Called with (completed_count, total_count) after each unit of work (a
# page, an image) finishes, in completion order. Every method that accepts
# one defaults it to None and does nothing extra when none is passed — the
# CLI path never passes one; only the webui's background job runner does.
ProgressCallback = Callable[[int, int], None]

# Called with (page_number, translated_text) right after one page/unit's
# translation finishes — a sibling to ProgressCallback, carrying the actual
# text instead of just a count. Kept separate because ProgressCallback
# structurally cannot carry it: that one only ever passes two integers, so
# a CLI run's per-page output (printed straight to the terminal — see
# translation_service.generate_text's inline print()) has no other path to
# reach the webui's conversation. Same optional-everywhere convention as
# ProgressCallback: every method that accepts one defaults it to None.
# page_number is 1-indexed, matching the page numbers used in this
# project's error messages (e.g. "Translation error on page 7").
PageTextCallback = Callable[[int, str], None]


@dataclass
class UiField:
    """One form field the web UI's composer should render for a plugin action.

    Args:
        name: The key this field's value arrives under in the ``fields``
              dict passed to ``run_ui_action`` (e.g. ``'source_language'``).
        label: Human-readable label shown next to the field in the composer
               (e.g. ``'Source language'``).
        kind: What kind of control to render — ``'language'`` (a select
              populated from this project's language registry),
              ``'file'`` (a file upload — a single file by default, or a
              whole folder instead, if ``allow_folder`` is set), ``'checkbox'``,
              ``'text'`` (a single- or multi-line text field, e.g. notes or a
              page-range string like ``'8-12'``), or ``'select'`` (a
              dropdown populated from ``choices``, for a fixed set of
              options that isn't the language registry — e.g. output file
              format). The web UI renders purely off this value; it never
              needs plugin-specific knowledge to build a form.
        required: Whether the web UI should block submitting the job until
                  this field has a value.
        choices: For ``kind='select'`` only — the options to offer, each
                 ``{'value': ..., 'label': ...}``. Ignored for every other
                 kind.
        group: A short heading the web UI prints above this field when it
               differs from the previous field's group (e.g. ``'Output'``,
               ``'Performance'``). Purely cosmetic — lets a long field list
               read as organized sections instead of one dense block.
               ``None`` fields render with no heading at all.
        allow_folder: For ``kind='file'`` only — when ``True``, the web UI
                      asks which the professor wants, one file or a whole
                      folder, and offers both. A folder works the same way
                      pointing the CLI's ``-i`` at a folder of images
                      processes every image inside it in order. Note this
                      *adds* the folder option rather than replacing the
                      single-file one: a browser file input can do one or the
                      other, never both, so the choice has to be asked. The
                      selected files arrive together in one folder, whose
                      path reaches ``run_ui_action``'s
                      ``fields['file_path']`` — the same shape a plugin
                      already gets from a CLI user passing a folder path
                      directly. That folder is scratch space that goes away
                      when the job ends; a plugin should read from it, not
                      expect it to still be there afterwards. Ignored for every other kind, and ``False``
                      by default so a single-document upload (e.g.
                      translate's own document field) still only ever
                      accepts one file.
        allow_text: For ``kind='file'`` only — when ``True``, one of the modes
                    offered is typing the passage in instead of choosing a
                    file at all, which is what the CLI's ``-c/--custom`` does.
                    What gets typed reaches ``run_ui_action`` as
                    ``fields['<name>_text']`` — ``fields['file_text']`` for a
                    field named ``file`` — and no file is uploaded in that
                    mode, so ``fields['file_path']`` is absent. A plugin
                    offering this should check for the text first and fall
                    back to the file. Like ``allow_folder`` this *adds* a
                    mode rather than replacing one, and is ignored for every
                    other kind.
    """

    name: str
    label: str
    kind: str
    required: bool = True
    choices: Optional[list[dict]] = None
    group: Optional[str] = None
    allow_folder: bool = False
    allow_text: bool = False


@dataclass
class UiAction:
    """One plugin-declared action the web UI's composer can offer.

    Args:
        id: A short, unique identifier for this action (e.g.
            ``'translate'``), used to route a job-start request back to the
            plugin that owns it.
        label: Human-readable label shown in the composer's action picker
               (e.g. ``'Translate a document'``).
        command: The CLI command name this action corresponds to (e.g.
                 ``'translate'``) — informational only. The web UI always
                 calls the owning *plugin's* ``run_ui_action`` directly,
                 never the CLI's own ``run()``/argparse path, so this never
                 needs to line up with a real parsed-flags shape.
        fields: The form fields the composer should render for this
                action, in display order.
        progress_verb: The present-participle ("-ing") verb shown in a
                       running job's progress messages, e.g.
                       ``'Translating'`` -> "Translating... 3 of 12 done."
                       Plain string, not derived automatically from
                       anything else — English gerunds aren't a
                       mechanical transformation of a plugin's id or label
                       (``"translate".capitalize() + "ing"`` produces the
                       misspelled "Translateing", which is exactly the bug
                       this field replaces). Defaults to ``'Processing'``
                       for a plugin that doesn't bother to set one.
        sampling: What this action's own settings say for ``temperature``,
                  ``top_p`` and ``max_tokens`` — the values it will actually
                  use when the person leaves those boxes empty, after the
                  plugin's own settings file, the group's shared file and this
                  person's preferences have all been applied. Shown in the
                  empty boxes so that "leave it alone" names a number rather
                  than an idea. Only the plugin can answer this: the settings
                  belong to it, and which sections they live in is its own
                  business. Any key left out simply isn't shown, and a plugin
                  that sets none of this loses nothing else.
    """

    id: str
    label: str
    command: str
    fields: list[UiField] = field(default_factory=list)
    progress_verb: str = "Processing"
    sampling: dict = field(default_factory=dict)


@dataclass
class UiPromptPreview:
    """What a plugin's optional ``preview_ui_action`` returns for the composer's live prompt preview.

    This is the ``--dry-run`` idea made interactive: as the professor fills
    in the composer's form, the webui calls ``preview_ui_action`` after
    every change and shows the result in a live-updating system/user prompt
    pane, without ever making a real API call.

    Args:
        system_prompt: The system prompt that would be sent, built from
                        whatever the form currently holds (placeholder text
                        standing in for real document content, since no
                        file has necessarily even been chosen yet).
        user_prompt: The user prompt that would be sent, same caveat.
        model: The resolved model name this preview was built against
               (e.g. what a blank model field falls back to).
        note: An optional one-line caveat to show above the preview (e.g.
              ``'Image content would be base64-encoded and attached to the
              user message'`` — mirrors the same note the CLI's own
              ``--dry-run`` shows for image-based actions). ``None`` if
              there's nothing extra to say.
    """

    system_prompt: str
    user_prompt: str
    model: str
    note: Optional[str] = None


@dataclass
class UiJobResult:
    """What a plugin's ``run_ui_action`` returns once its background job finishes.

    Args:
        output_path: Absolute path to the one finished file this job
                     produced (e.g. a translated ``.docx``). The web UI's
                     job runner makes this downloadable from the
                     conversation it was started in — one finished file at
                     the end, not a stream of pages.
        output_filename: The filename to present to the browser on
                         download (may differ from ``output_path``'s own
                         basename, which typically lives under a temporary
                         or job-output directory the browser shouldn't see).
        summary: A short, human-readable description of what happened
                 (e.g. ``'Translated 12 pages, Japanese -> English'``),
                 shown as the job's completion message in the conversation.
        prompt_tokens: Total tokens read across every API call this job
                       made (e.g. one call per page for a multi-page
                       translation), or ``None`` if not tracked. Shown next
                       to the job's result the same way a chat turn's token
                       spend is shown — in particular so a professor can
                       tell a translation used its full response budget on
                       some page and may need a higher max-tokens setting,
                       even though that's invisible from the summary text
                       alone.
        completion_tokens: Total tokens generated across every API call
                           this job made, or ``None`` if not tracked.
        cost: Total cost in dollars across every API call this job made, or
              ``None`` if not tracked.
    """

    output_path: str
    output_filename: str
    summary: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost: Optional[float] = None


# ── Extension-plugin composer fields ────────────────────────────────────────
# A language-extension plugin (e.g. an East-Asian translation extension)
# never gets its own composer entry — only the base plugin's ui_action is
# ever exposed (see DispatchPlugin's proxying). But an extension can still
# contribute its own extra fields to the *base* plugin's job modal, shown as
# a subsection that appears once the professor picks a destination language
# the extension owns, exactly mirroring get_peer_guidance()'s existing
# "destination-side guidance" hook — just for form fields instead of prompt
# text.
#
# This is a plain, decoupled global registry (not routed through
# DispatchPlugin at all) for the same reason register_language() in
# src/config.py is one: run_ui_action executes as a bound method looked up
# directly off the primary plugin instance (DispatchPlugin.__getattr__
# forwards it straight through, never calling anything on DispatchPlugin
# itself — see its docstring), so it has no way to ask "which extension
# plugin owns this destination token" through DispatchPlugin. A shared
# registry that any extension plugin populates at import time sidesteps
# that entirely: the base plugin's run_ui_action/preview_ui_action just
# look up the destination token here directly, the same way they already
# look up LANGUAGE_MAP directly rather than asking DispatchPlugin for names.


@dataclass
class ExtensionUiHooks:
    """One extension plugin's composer contribution for a single action/language-token pair.

    Args:
        fields: Extra ``UiField``s to render, in display order, once this
                token is selected as the relevant language for this action
                (the destination language for ``translate``, the OCR
                language for ``transcribe``).
        apply: Called from the base plugin's ``run_ui_action`` right before
               the actual job runs, as ``apply(sandbox, fields_dict)`` —
               *sandbox* is the already-constructed ``SandboxProcessor`` for
               this job (so e.g.
               ``sandbox.translation_service.variant_notes.append(...)``
               works exactly like it does inside ``_execute_translate``),
               *fields_dict* is the full submitted fields dict (so this can
               read its own field's value by name). Must not raise for a
               blank/default value — called on every job for this action/
               token pair, not just when something was changed.
    """

    fields: list[UiField]
    apply: Callable[[Any, dict], None]


# (action_id, token) -> that pair's registered extension hooks, e.g.
# ('translate', 'jp') for translation-ea's Kanbun checkbox and
# ('transcribe', 'jp') for transcription-ea's own composer fields. Keyed by
# the action alongside the language token — not by token alone — because
# two different actions can legitimately register the *same* language token
# for entirely unrelated fields (translate's Kanbun checkbox and
# transcribe's vertical/spread/passes/kanbun-mode fields both apply to
# 'jp', but mean completely different things); a token-only key would let
# whichever plugin imports last silently overwrite the other's registration
# for that token, breaking one of the two actions with no error raised.
# Populated only by register_extension_ui_hooks() below, read only by
# get_extension_ui_fields()/apply_extension_ui_hooks() below.
_EXTENSION_UI_HOOKS: dict[tuple[str, str], ExtensionUiHooks] = {}


def register_extension_ui_hooks(
    action_id: str, token: str, fields: list[UiField], apply: Callable[[Any, dict], None],
) -> None:
    """Register a language-extension plugin's composer fields for one action's language token.

    Call once at plugin import time, the same way ``register_language()``
    is already called — see this module's "Extension-plugin composer
    fields" section docstring above for why this is a standalone registry
    rather than something routed through ``DispatchPlugin``.

    Args:
        action_id: The composer action this contribution applies to (e.g.
                   ``'translate'``, ``'transcribe'``) — matching that
                   action's own ``UiAction.id``. Required alongside *token*
                   so two different actions can each own the same language
                   token without overwriting each other's registration —
                   see the ``_EXTENSION_UI_HOOKS`` comment above for why.
        token: The short language code this contribution applies to (e.g.
               ``'jp'``) — matching a key in ``LANGUAGE_MAP`` and one of
               this extension's own ``handles`` entries. Which side of the
               job this token means depends on the action: the
               *destination* language for ``translate``, the OCR language
               for ``transcribe``.
        fields: The ``UiField``s to add to the composer when this token is
                selected for this action.
        apply: See ``ExtensionUiHooks.apply``'s docstring.
    """
    _EXTENSION_UI_HOOKS[(action_id.strip().lower(), token.strip().lower())] = ExtensionUiHooks(
        fields=fields, apply=apply,
    )


def get_extension_ui_fields(action_id: str, token: Optional[str]) -> list[UiField]:
    """Return the extra composer fields registered for *token* under *action_id*.

    Args:
        action_id: The composer action being rendered (e.g. ``'translate'``,
                   ``'transcribe'``) — see ``register_extension_ui_hooks``'s
                   docstring for why this is needed alongside *token*.
        token: The selected language short code, or ``None``/blank if none
               is selected yet.

    Returns:
        The registered ``UiField`` list, or ``[]`` if *token* is blank or no
        extension has registered anything for this action/token pair (the
        normal case for any installation without that extension plugin
        installed).
    """
    if not token:
        return []
    hooks = _EXTENSION_UI_HOOKS.get((action_id.strip().lower(), token.strip().lower()))
    return hooks.fields if hooks else []


def apply_extension_ui_hooks(action_id: str, token: Optional[str], sandbox: Any, fields: dict) -> None:
    """Apply a registered extension's fields to *sandbox*, if this action/token pair has one registered.

    Does nothing (rather than raising) when *token* is blank or nothing is
    registered for this action/token pair — the normal case for any
    installation that doesn't have that extension plugin.

    Args:
        action_id: The composer action this job is running (e.g.
                   ``'translate'``, ``'transcribe'``) — see
                   ``register_extension_ui_hooks``'s docstring for why this
                   is needed alongside *token*.
        token: The selected language short code.
        sandbox: The ``SandboxProcessor`` already constructed for this job.
        fields: The full submitted fields dict, so the extension's own
                ``apply`` callback can read its field's value by name.
    """
    if not token:
        return
    hooks = _EXTENSION_UI_HOOKS.get((action_id.strip().lower(), token.strip().lower()))
    if hooks:
        hooks.apply(sandbox, fields)
