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
from typing import Callable, Optional

# Called with (completed_count, total_count) after each unit of work (a
# page, an image) finishes, in completion order. Every method in this
# project that accepts one defaults it to None and behaves exactly like it
# did before this was added when no callback is passed — the CLI path never
# passes one; only the webui's background job runner does.
ProgressCallback = Callable[[int, int], None]


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
              ``'file'`` (a single file upload), ``'checkbox'``, or
              ``'text'`` (a single- or multi-line text field, e.g. notes or
              a page-range string like ``'8-12'``). The web UI renders
              purely off this value; it never needs plugin-specific
              knowledge to build a form.
        required: Whether the web UI should block submitting the job until
                  this field has a value.
    """

    name: str
    label: str
    kind: str
    required: bool = True


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
    """

    output_path: str
    output_filename: str
    summary: str
