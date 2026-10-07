"""Tests for the plain UiField/UiAction/UiJobResult dataclasses.

These are the optional, plugin-declared contract behind the webui composer's plugin-action picker.
Deliberately lightweight: there's no behavior here beyond plain dataclass
construction, so these tests just pin the shape down.
"""

import pytest

from src.runtime import ui_action as ui_action_module
from src.runtime.ui_action import (
    UiAction, UiField, UiJobResult,
    apply_extension_ui_hooks, get_extension_ui_fields, register_extension_ui_hooks,
)


class TestUiField:
    def test_defaults_required_true(self):
        f = UiField(name="notes", label="Notes", kind="text")
        assert f.required is True

    def test_can_be_optional(self):
        f = UiField(name="notes", label="Notes", kind="text", required=False)
        assert f.required is False

    def test_allow_folder_defaults_false(self):
        # A single-document field (e.g. translate's own file field) must
        # keep accepting exactly one file unless a plugin opts in.
        f = UiField(name="file", label="Document", kind="file")
        assert f.allow_folder is False

    def test_allow_folder_can_be_enabled(self):
        f = UiField(name="file", label="Image", kind="file", allow_folder=True)
        assert f.allow_folder is True


class TestUiAction:
    def test_fields_default_to_empty_list(self):
        action = UiAction(id="translate", label="Translate a document", command="translate")
        assert action.fields == []

    def test_progress_verb_defaults_to_processing(self):
        # Regression guard: progress_verb exists specifically because
        # deriving a gerund mechanically (e.g. "translate".capitalize() +
        # "ing") produces the misspelled "Translateing" — a plugin that
        # doesn't bother to set this gets a plain, correctly-spelled
        # fallback instead.
        action = UiAction(id="translate", label="Translate a document", command="translate")
        assert action.progress_verb == "Processing"

    def test_progress_verb_can_be_overridden(self):
        action = UiAction(
            id="translate", label="Translate a document", command="translate",
            progress_verb="Translating",
        )
        assert action.progress_verb == "Translating"

    def test_holds_declared_fields_in_order(self):
        fields = [
            UiField(name="source_language", label="Source language", kind="language"),
            UiField(name="target_language", label="Target language", kind="language"),
            UiField(name="file", label="Document", kind="file"),
        ]
        action = UiAction(id="translate", label="Translate a document", command="translate", fields=fields)
        assert [f.name for f in action.fields] == ["source_language", "target_language", "file"]

    def test_two_instances_do_not_share_the_default_fields_list(self):
        # Regression guard for the classic mutable-default-argument mistake —
        # dataclasses.field(default_factory=list) should already prevent
        # this, but it's cheap to pin down given how easy it is to
        # accidentally regress with `fields: list[UiField] = []`.
        a = UiAction(id="a", label="A", command="a")
        b = UiAction(id="b", label="B", command="b")
        a.fields.append(UiField(name="x", label="X", kind="text"))
        assert b.fields == []


class TestUiJobResult:
    def test_holds_output_and_summary(self):
        result = UiJobResult(
            output_path="/tmp/out.docx",
            output_filename="My Translation.docx",
            summary="Translated 12 pages, Japanese -> English",
        )
        assert result.output_path == "/tmp/out.docx"
        assert result.output_filename == "My Translation.docx"
        assert "12 pages" in result.summary

    def test_token_and_cost_default_to_none(self):
        result = UiJobResult(output_path="/tmp/out.txt", output_filename="out.txt", summary="Done.")
        assert result.prompt_tokens is None
        assert result.completion_tokens is None
        assert result.cost is None

    def test_token_and_cost_can_be_set(self):
        result = UiJobResult(
            output_path="/tmp/out.txt", output_filename="out.txt", summary="Done.",
            prompt_tokens=500, completion_tokens=120, cost=0.03,
        )
        assert result.prompt_tokens == 500
        assert result.completion_tokens == 120
        assert result.cost == 0.03


class TestExtensionUiHooksRegistry:
    """register_extension_ui_hooks()/get_extension_ui_fields()/
    apply_extension_ui_hooks() — the mechanism a language-extension plugin
    (e.g. translation-ea, a separate git-ignored repo not present in this
    checkout) uses to contribute its own composer fields, keyed by
    (action_id, language token) rather than routed through DispatchPlugin.
    See ExtensionUiHooks's docstring for the full reasoning, including why
    action_id is part of the key: two different actions (translate,
    transcribe) can legitimately register the same language token for
    unrelated fields, and a token-only key would let one silently overwrite
    the other.

    The registry is a module-level dict, so every test restores it
    afterward to avoid leaking a registration into an unrelated test."""

    @pytest.fixture(autouse=True)
    def _isolated_registry(self, monkeypatch):
        monkeypatch.setattr(ui_action_module, "_EXTENSION_UI_HOOKS", {})

    def test_nothing_registered_returns_empty_fields(self):
        assert get_extension_ui_fields("translate", "jp") == []

    def test_blank_or_none_token_returns_empty_fields(self):
        assert get_extension_ui_fields("translate", "") == []
        assert get_extension_ui_fields("translate", None) == []

    def test_registered_fields_are_returned_for_matching_action_and_token(self):
        fields = [UiField(name="kanbun", label="Use Kanbun conventions", kind="checkbox", required=False)]
        register_extension_ui_hooks(action_id="translate", token="jp", fields=fields, apply=lambda sandbox, f: None)
        assert get_extension_ui_fields("translate", "jp") == fields

    def test_token_lookup_is_case_and_whitespace_insensitive(self):
        fields = [UiField(name="kanbun", label="Use Kanbun conventions", kind="checkbox", required=False)]
        register_extension_ui_hooks(action_id="TRANSLATE", token="JP", fields=fields, apply=lambda sandbox, f: None)
        assert get_extension_ui_fields(" translate ", " jp ") == fields

    def test_unregistered_token_returns_empty_even_if_others_are_registered(self):
        register_extension_ui_hooks(
            action_id="translate", token="jp", fields=[UiField(name="a", label="A", kind="text")], apply=lambda s, f: None,
        )
        assert get_extension_ui_fields("translate", "zh") == []

    def test_same_token_under_a_different_action_does_not_collide(self):
        # The bug this key shape fixes: translate's Kanbun checkbox and
        # transcribe's own vertical/spread/passes fields both legitimately
        # apply to 'jp', but are unrelated registrations.
        translate_fields = [UiField(name="kanbun", label="Kanbun", kind="checkbox", required=False)]
        transcribe_fields = [UiField(name="vertical", label="Vertical", kind="checkbox", required=False)]
        register_extension_ui_hooks(action_id="translate", token="jp", fields=translate_fields, apply=lambda s, f: None)
        register_extension_ui_hooks(action_id="transcribe", token="jp", fields=transcribe_fields, apply=lambda s, f: None)
        assert get_extension_ui_fields("translate", "jp") == translate_fields
        assert get_extension_ui_fields("transcribe", "jp") == transcribe_fields

    def test_unregistered_action_returns_empty_even_for_a_registered_token(self):
        register_extension_ui_hooks(
            action_id="translate", token="jp", fields=[UiField(name="a", label="A", kind="text")], apply=lambda s, f: None,
        )
        assert get_extension_ui_fields("transcribe", "jp") == []

    def test_apply_is_a_noop_when_nothing_registered(self):
        # Must not raise — every installation without the extension
        # installed hits this path on every job.
        apply_extension_ui_hooks("translate", "jp", sandbox=object(), fields={})

    def test_apply_is_a_noop_for_blank_token(self):
        register_extension_ui_hooks(
            action_id="translate", token="jp", fields=[],
            apply=lambda s, f: (_ for _ in ()).throw(AssertionError("should not be called")),
        )
        apply_extension_ui_hooks("translate", "", sandbox=object(), fields={})
        apply_extension_ui_hooks("translate", None, sandbox=object(), fields={})

    def test_apply_is_a_noop_for_a_different_action_with_the_same_token(self):
        register_extension_ui_hooks(
            action_id="translate", token="jp", fields=[],
            apply=lambda s, f: (_ for _ in ()).throw(AssertionError("should not be called")),
        )
        apply_extension_ui_hooks("transcribe", "jp", sandbox=object(), fields={})

    def test_apply_calls_the_registered_callback_with_sandbox_and_fields(self):
        calls = []
        register_extension_ui_hooks(
            action_id="translate", token="jp", fields=[],
            apply=lambda sandbox, fields: calls.append((sandbox, fields)),
        )
        fake_sandbox = object()
        apply_extension_ui_hooks("translate", "jp", fake_sandbox, {"kanbun": "true"})
        assert calls == [(fake_sandbox, {"kanbun": "true"})]


class TestAimingANoteAtOneSide:
    """A note can be a standing instruction, or about the passage in hand.

    The command line has said so since the beginning: -ns for the model's
    standing instructions, -nu for the message carrying the work, -nb for both.
    The web interface only ever did the third, so the other two could not be
    asked for at all.
    """

    class Service:
        system_note = None
        user_note = None

    def _applied(self, **fields):
        from src.runtime.ui_action import apply_notes
        service = self.Service()
        apply_notes(fields, service)
        return service.system_note, service.user_note

    def test_both_is_what_happens_by_default(self):
        """What the interface always did, so nothing changes without asking."""
        assert self._applied(notes="romanise names") == ("romanise names", "romanise names")

    def test_a_standing_instruction_goes_only_to_the_system_prompt(self):
        assert self._applied(notes="romanise names", notes_target="system") == (
            "romanise names", None)

    def test_a_note_about_this_passage_goes_only_to_the_user_prompt(self):
        assert self._applied(notes="the second column is marginalia",
                             notes_target="user") == (
            None, "the second column is marginalia")

    def test_an_empty_box_sets_nothing_at_all(self):
        """Not an empty note — a service tells those apart, and so should this."""
        assert self._applied(notes="", notes_target="system") == (None, None)
        assert self._applied(notes="   ") == (None, None)

    def test_a_note_is_trimmed(self):
        assert self._applied(notes="  keep it literal  ")[0] == "keep it literal"

    def test_an_unknown_target_means_both(self):
        """A saved job from before the question existed still means what it did."""
        assert self._applied(notes="n", notes_target="somewhere else") == ("n", "n")
        assert self._applied(notes="n", notes_target="") == ("n", "n")
        assert self._applied(notes="n", notes_target=None) == ("n", "n")

    def test_the_target_is_read_however_it_is_capitalised(self):
        assert self._applied(notes="n", notes_target="SYSTEM") == ("n", None)

    def test_every_service_given_gets_it(self):
        """A job may go down either of two paths, unknown this early."""
        from src.runtime.ui_action import apply_notes
        one, two = self.Service(), self.Service()
        apply_notes({"notes": "n", "notes_target": "user"}, one, two)
        assert (one.user_note, two.user_note) == ("n", "n")
        assert (one.system_note, two.system_note) == (None, None)

    def test_the_choices_match_what_the_command_line_offers(self):
        from src.runtime.ui_action import notes_target_field
        values = [c["value"] for c in notes_target_field().choices]
        assert values == ["both", "system", "user"]

    def test_the_default_is_named_as_such_in_the_form(self):
        """So nobody has to guess which one applies when they leave it alone."""
        from src.runtime.ui_action import notes_target_field
        first = notes_target_field().choices[0]
        assert first["value"] == "both" and "default" in first["label"].lower()

    def test_it_sits_with_the_notes_box(self):
        from src.runtime.ui_action import notes_target_field
        assert notes_target_field().group == "Notes"
        assert notes_target_field(group="Elsewhere").group == "Elsewhere"

    def test_answering_is_optional(self):
        from src.runtime.ui_action import notes_target_field
        assert notes_target_field().required is False
