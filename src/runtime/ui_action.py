"""UiAction / UiField / UiJobResult — the optional contract a plugin uses to
appear as a background-job trigger in the web UI's composer.

See ``docs/webui-plugin-plan.md`` section 10 for the full design and
reasoning. In short: a plugin that wants a composer entry (translate,
transcribe, or any future plugin) declares a module-level ``ui_action``
(one ``UiAction`` instance) alongside its usual ``plugin`` object, and adds
a ``run_ui_action(fields, professor, model, on_progress)`` method to its
plugin class. Both are optional and undeclared by default — same spirit as
the existing optional ``requires_professor`` attribute documented in
``plugin.py`` — so no existing plugin needs to change, and installing a new
plugin later that declares these two things gives it a composer entry with
no changes needed in ``plugins/webui/``.

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
# page, an image) finishes, in completion order. Every method in this
# project that accepts one defaults it to None and behaves exactly like it
# did before this was added when no callback is passed — the CLI path never
# passes one; only the webui's background job runner does.
ProgressCallback = Callable[[int, int], None]

# Called with (page_number, translated_text) right after one page/unit's
# translation finishes — a sibling to ProgressCallback, carrying the actual
# text instead of just a count. Added specifically because ProgressCallback
# structurally cannot carry this: it only ever passes two integers, so a
# CLI run's per-page output (printed straight to the terminal — see
# translation_service.generate_text's inline print()) had no path to reach
# the webui's conversation at all. Same optional-everywhere convention as
# ProgressCallback: every method that accepts one defaults it to None and
# behaves exactly like it did before this existed when no callback is
# passed. page_number is 1-indexed, matching the page numbers already used
# in this project's error messages (e.g. "Translation error on page 7").
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
              whole folder at once if ``allow_folder`` is set), ``'checkbox'``,
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
                      lets the professor pick a whole folder (or several
                      individual files) instead of exactly one file, the
                      same way pointing the CLI's ``-i`` at a folder of
                      images processes every image inside it in order. Every
                      selected file is uploaded and saved into one job
                      folder, whose path then arrives in ``run_ui_action``'s
                      ``fields['file_path']`` — the same shape a plugin
                      already gets from a CLI user passing a folder path
                      directly. Ignored for every other kind, and ``False``
                      by default so a single-document upload (e.g.
                      translate's own document field) still only ever
                      accepts one file.
    """

    name: str
    label: str
    kind: str
    required: bool = True
    choices: Optional[list[dict]] = None
    group: Optional[str] = None
    allow_folder: bool = False


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
    """

    id: str
    label: str
    command: str
    fields: list[UiField] = field(default_factory=list)
    progress_verb: str = "Processing"


@dataclass
class UiPromptPreview:
    """What a plugin's optional ``preview_ui_action`` returns for the composer's live prompt preview.

    See ``docs/webui-plugin-plan.md`` section 10's two-pane preview panel —
    this is the ``--dry-run`` idea made interactive: as the professor fills
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
                     conversation it was started in — see
                     ``docs/webui-plugin-plan.md`` section 10's "your call:
                     whole-file export, not page by page."
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
    """One extension plugin's composer contribution for a single destination-language token.

    Args:
        fields: Extra ``UiField``s to render, in display order, once this
                token is selected as the destination language.
        apply: Called from the base plugin's ``run_ui_action`` right before
               the actual translation runs, as
               ``apply(sandbox, fields_dict)`` — *sandbox* is the already-
               constructed ``SandboxProcessor`` for this job (so e.g.
               ``sandbox.translation_service.variant_notes.append(...)``
               works exactly like it does inside ``_execute_translate``),
               *fields_dict* is the full submitted fields dict (so this can
               read its own field's value by name). Must not raise for a
               blank/default value — called on every job for this
               destination token, not just when something was changed.
    """

    fields: list[UiField]
    apply: Callable[[Any, dict], None]


# token (e.g. 'jp') -> that token's registered extension hooks. Populated
# only by register_extension_ui_hooks() below, read only by
# get_extension_ui_fields()/apply_extension_ui_hooks() below.
_EXTENSION_UI_HOOKS: dict[str, ExtensionUiHooks] = {}


def register_extension_ui_hooks(
    token: str, fields: list[UiField], apply: Callable[[Any, dict], None],
) -> None:
    """Register a language-extension plugin's composer fields for one destination-language token.

    Call once at plugin import time, the same way ``register_language()``
    is already called — see this module's "Extension-plugin composer
    fields" section docstring above for why this is a standalone registry
    rather than something routed through ``DispatchPlugin``.

    Args:
        token: The short language code this contribution applies to when
               selected as the *destination* language (e.g. ``'jp'``) —
               matching a key in ``LANGUAGE_MAP`` and one of this
               extension's own ``handles`` entries.
        fields: The ``UiField``s to add to the composer when this token is
                the selected destination language.
        apply: See ``ExtensionUiHooks.apply``'s docstring.
    """
    _EXTENSION_UI_HOOKS[token.lower()] = ExtensionUiHooks(fields=fields, apply=apply)


def get_extension_ui_fields(token: Optional[str]) -> list[UiField]:
    """Return the extra composer fields registered for *token* as a destination language.

    Args:
        token: The selected destination-language short code, or ``None``/
               blank if none is selected yet.

    Returns:
        The registered ``UiField`` list, or ``[]`` if *token* is blank or no
        extension has registered anything for it (the normal case for any
        installation without that extension plugin installed).
    """
    if not token:
        return []
    hooks = _EXTENSION_UI_HOOKS.get(token.strip().lower())
    return hooks.fields if hooks else []


def apply_extension_ui_hooks(token: Optional[str], sandbox: Any, fields: dict) -> None:
    """Apply a registered extension's fields to *sandbox*, if *token* has one registered.

    A no-op (not an error) when *token* is blank or nothing is registered
    for it — the normal case for any installation without that extension
    plugin installed, which must behave identically to before this existed.

    Args:
        token: The selected destination-language short code.
        sandbox: The ``SandboxProcessor`` already constructed for this job.
        fields: The full submitted fields dict, so the extension's own
                ``apply`` callback can read its field's value by name.
    """
    if not token:
        return
    hooks = _EXTENSION_UI_HOOKS.get(token.strip().lower())
    if hooks:
        hooks.apply(sandbox, fields)
