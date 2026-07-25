"""Tests for the plain UiField/UiAction/UiJobResult dataclasses.

See docs/webui-plugin-plan.md section 10 — these are the optional,
plugin-declared contract behind the webui composer's plugin-action picker.
Deliberately lightweight: there's no behavior here beyond plain dataclass
construction, so these tests just pin the shape down.
"""

from src.runtime.ui_action import UiAction, UiField, UiJobResult


class TestUiField:
    def test_defaults_required_true(self):
        f = UiField(name="notes", label="Notes", kind="text")
        assert f.required is True

    def test_can_be_optional(self):
        f = UiField(name="notes", label="Notes", kind="text", required=False)
        assert f.required is False


class TestUiAction:
    def test_fields_default_to_empty_list(self):
        action = UiAction(id="translate", label="Translate a document", command="translate")
        assert action.fields == []

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
