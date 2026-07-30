"""Tests for translation plugin services and prompt specs.

Covers:
- TranslationService: __init__, pure/static methods, build_prompts,
  _create_translation_prompt, translate_text (mocked API)
- TranslationPromptSpec: system_prompt, user_prompt variants
- ImageTranslationPromptSpec: system_prompt, user_prompt variants
- ImageTranslationService: __init__, _build_system_prompt, _build_user_prompt,
  build_prompts
"""

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

# Modules are injected by conftest.py via _register().
TranslationService = sys.modules["src.services.translation_service"].TranslationService
ImageTranslationService = sys.modules["src.services.image_translation_service"].ImageTranslationService
TranslationPromptSpec = sys.modules["src.services.prompts.translation"].TranslationPromptSpec
ImageTranslationPromptSpec = sys.modules["src.services.prompts.image_translation"].ImageTranslationPromptSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_svc(monkeypatch) -> TranslationService:
    """Return a TranslationService with API + model catalog patched out."""
    monkeypatch.setattr(
        "src.services.base_service.get_model_max_completion_tokens",
        lambda m, d: d,
    )
    svc = TranslationService("fake-api-key", professor="test")
    return svc


def _make_img_svc(monkeypatch) -> ImageTranslationService:
    """Return an ImageTranslationService with API + catalog patched."""
    monkeypatch.setattr(
        "src.services.base_service.get_model_max_completion_tokens",
        lambda m, d: d,
    )
    svc = ImageTranslationService("fake-api-key", professor="test")
    return svc


# ---------------------------------------------------------------------------
# TranslationService — __init__
# ---------------------------------------------------------------------------

class TestTranslationServiceInit:

    def test_instantiates_without_error(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        assert svc is not None

    def test_default_flags_set(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        assert svc.tables is False
        assert svc.toc is False
        assert svc.variant_notes == []
        assert svc._blank_page_count == 0
        assert svc._api_error_count == 0


# ---------------------------------------------------------------------------
# TranslationService — _get_model
# ---------------------------------------------------------------------------

class TestTranslationServiceModel:

    def test_get_model_returns_string(self, monkeypatch):
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        svc = TranslationService("fake-key")
        model = svc._get_model()
        assert isinstance(model, str)
        assert len(model) > 0

    def test_get_model_passes_this_services_declared_role(self, monkeypatch):
        """The plugin owns the preference; the service hands it to the resolver.

        Nothing in src/ holds a list of what this plugin wants, which is the
        whole point — so what reaches resolve_model must be the role declared
        in this plugin's own settings.
        """
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        captured = {}

        def _fake_resolve_model(*, requested_model=None, role=None, **_):
            captured["role"] = role
            return role.models[0] if role else "fallback"

        monkeypatch.setattr("src.services.base_service.resolve_model", _fake_resolve_model)
        model = TranslationService("fake-key")._get_model()
        assert captured["role"] is TranslationService.model_role
        assert captured["role"].models, "the declared role must name at least one model"
        assert model == captured["role"].models[0]


# ---------------------------------------------------------------------------
# TranslationService — _find_split_point (static, pure)
# ---------------------------------------------------------------------------

class TestFindSplitPoint:

    def test_prefers_paragraph_break(self):
        text = "abc" + "\n\n" + "def"
        result = TranslationService._find_split_point(text, 3)
        assert result == 5  # just after the \n\n

    def test_falls_back_to_sentence_boundary(self):
        text = "Hello world. This is a test."
        result = TranslationService._find_split_point(text, 11)
        # Should return position just after a sentence-ending punctuation near the middle
        assert text[result - 1] in '.!?。'

    def test_falls_back_to_middle_when_no_boundary(self):
        text = "abcdefghij"
        result = TranslationService._find_split_point(text, 5)
        assert result == 5


# ---------------------------------------------------------------------------
# TranslationService — _rows_to_markdown (static, pure)
# ---------------------------------------------------------------------------

class TestRowsToMarkdown:

    def test_empty_returns_empty_string(self):
        assert TranslationService._rows_to_markdown([]) == ""

    def test_single_row_produces_header_and_separator(self):
        result = TranslationService._rows_to_markdown([["A", "B"]])
        lines = result.splitlines()
        assert "| A | B |" in lines[0]
        assert "---" in lines[1]

    def test_two_rows_header_and_data(self):
        result = TranslationService._rows_to_markdown([["H1", "H2"], ["R1", "R2"]])
        lines = result.splitlines()
        assert len(lines) == 3  # header, sep, data
        assert "R1" in lines[2]

    def test_uneven_rows_padded(self):
        result = TranslationService._rows_to_markdown([["A"], ["B", "C"]])
        lines = result.splitlines()
        # All lines should have the same number of | separators
        pipe_counts = [line.count("|") for line in lines]
        assert len(set(pipe_counts)) == 1


# ---------------------------------------------------------------------------
# TranslationService — _parse_markdown_table (static, pure)
# ---------------------------------------------------------------------------

class TestParseMarkdownTable:

    def test_parses_simple_table(self):
        md = "| A | B |\n| --- | --- |\n| C | D |"
        result = TranslationService._parse_markdown_table(md)
        assert result is not None
        assert result[0] == ["A", "B"]
        assert result[1] == ["C", "D"]

    def test_separator_row_skipped(self):
        md = "| H |\n| --- |\n| V |"
        result = TranslationService._parse_markdown_table(md)
        assert result is not None
        assert len(result) == 2  # header + value, no separator

    def test_empty_string_returns_none(self):
        result = TranslationService._parse_markdown_table("")
        assert result is None

    def test_non_table_text_returns_none(self):
        result = TranslationService._parse_markdown_table("Just plain text\nno pipes")
        assert result is None


# ---------------------------------------------------------------------------
# TranslationService — _resolve_output_format (static, pure)
# ---------------------------------------------------------------------------

class TestResolveOutputFormat:
    """Covers the _resolve_output_format static method."""

    def _opts(self, output_file=None, auto_save=False):
        from src.models import OutputOptions
        return OutputOptions(output_file=output_file, auto_save=auto_save)

    def test_pdf_extension(self):
        assert TranslationService._resolve_output_format(self._opts("out.pdf")) == "pdf"

    def test_docx_extension(self):
        assert TranslationService._resolve_output_format(self._opts("out.docx")) == "docx"

    def test_txt_extension(self):
        assert TranslationService._resolve_output_format(self._opts("out.txt")) == "txt"

    def test_unknown_extension_returns_file(self):
        assert TranslationService._resolve_output_format(self._opts("out.xyz")) == "file"

    def test_auto_save_no_file_returns_txt(self):
        assert TranslationService._resolve_output_format(self._opts(auto_save=True)) == "txt"

    def test_no_file_no_auto_save_returns_console(self):
        assert TranslationService._resolve_output_format(self._opts()) == "console"


# ---------------------------------------------------------------------------
# TranslationService — _make_text_triples (static, pure)
# ---------------------------------------------------------------------------

class TestMakeTextTriples:

    def test_yields_indexed_triples(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        result = list(svc._make_text_triples(["page one", "page two", "page three"]))
        assert result[0] == (0, "page one", "")
        assert result[1] == (1, "page two", "page one")
        assert result[2] == (2, "page three", "page two")

    def test_empty_list_yields_nothing(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        assert list(svc._make_text_triples([])) == []


# ---------------------------------------------------------------------------
# TranslationService — _create_translation_prompt / build_prompts
# ---------------------------------------------------------------------------

class TestBuildPrompts:

    def test_returns_two_non_empty_strings(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        system, user = svc.build_prompts("Hello world", "English", "Japanese")
        assert isinstance(system, str) and len(system) > 0
        assert isinstance(user, str) and len(user) > 0

    def test_user_prompt_ends_with_source_text(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        text = "Source text here"
        _, user = svc.build_prompts(text, "English", "Japanese")
        assert user.endswith(text)

    def test_table_markers_flag_toggled(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        sys_no_table, _ = svc.build_prompts("no table", "English", "Japanese")
        sys_with_table, _ = svc.build_prompts("[TABLE_1]", "English", "Japanese")
        # When table markers are present the system prompt should differ
        assert sys_with_table != sys_no_table


# ---------------------------------------------------------------------------
# TranslationService — translate_text (mocked API)
# ---------------------------------------------------------------------------

class TestTranslateText:

    def _mock_response(self, content: str):
        """Return a non-iterable fake response (MagicMock is iterable, which fails
        the ABCIterator assertion in _record_response_usage)."""
        class _Usage:
            prompt_tokens = 10
            completion_tokens = 20
            total_tokens = 30
        class _Message:
            def __init__(self, c): self.content = c
        class _Choice:
            def __init__(self, c):
                self.message = _Message(c)
                self.finish_reason = "stop"
        class _Resp:
            def __init__(self, c):
                self.id = "resp-1"
                self.model = "gpt-4o"
                self.usage = _Usage()
                self.choices = [_Choice(c)]
        return _Resp(content)

    def test_returns_translated_string(self, monkeypatch, capsys):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "_create_completion", lambda *a, **kw: self._mock_response("日本語訳"))
        result = svc.translate_text("Hello", "English", "Japanese")
        assert result == "日本語訳"

    def test_prints_content_inline(self, monkeypatch, capsys):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "_create_completion", lambda *a, **kw: self._mock_response("translation output"))
        svc.translate_text("Hello", "English", "Japanese")
        out = capsys.readouterr().out
        assert "translation output" in out

    def test_suppress_inline_print_suppresses_output(self, monkeypatch, capsys):
        svc = _make_svc(monkeypatch)
        svc._suppress_inline_print = True
        monkeypatch.setattr(svc, "_create_completion", lambda *a, **kw: self._mock_response("silent"))
        svc.translate_text("Hello", "English", "Japanese")
        out = capsys.readouterr().out
        assert "silent" not in out

    def test_no_choices_returns_empty_string(self, monkeypatch, capsys):
        svc = _make_svc(monkeypatch)

        class _EmptyChoicesResp:
            id = "r"
            model = "gpt-4o"

            class _Usage:
                prompt_tokens = 1
                completion_tokens = 1
                total_tokens = 2

            usage = _Usage()
            choices: list = []

        monkeypatch.setattr(svc, "_call_translation_api",
                            lambda *a, **kw: _EmptyChoicesResp())
        result = svc.translate_text("Hello", "English", "Japanese")
        assert result == ""
        out = capsys.readouterr().out
        assert "No content returned" in out


# ---------------------------------------------------------------------------
# TranslationPromptSpec — system_prompt / user_prompt branches
# ---------------------------------------------------------------------------

class TestTranslationPromptSpec:

    def test_basic_system_prompt(self):
        spec = TranslationPromptSpec(source_language="English", target_language="Japanese")
        result = spec.system_prompt()
        assert "English" in result
        assert "Japanese" in result

    def test_numbered_content_fragment_added(self):
        spec_no = TranslationPromptSpec(source_language="English", target_language="Japanese", has_numbered=False)
        spec_yes = TranslationPromptSpec(source_language="English", target_language="Japanese", has_numbered=True)
        assert len(spec_yes.system_prompt()) > len(spec_no.system_prompt())

    def test_table_marker_rule_added(self):
        spec_no = TranslationPromptSpec(source_language="English", target_language="Japanese", has_table_markers=False)
        spec_yes = TranslationPromptSpec(source_language="English", target_language="Japanese", has_table_markers=True)
        assert len(spec_yes.system_prompt()) > len(spec_no.system_prompt())

    def test_variant_notes_included(self):
        spec = TranslationPromptSpec(
            source_language="English",
            target_language="Japanese",
            variant_notes=["Use formal register."],
        )
        result = spec.system_prompt()
        assert "formal register" in result

    def test_user_prompt_with_context_type_abstract(self):
        spec = TranslationPromptSpec(
            source_language="English",
            target_language="Japanese",
            context_type="abstract",
        )
        result = spec.user_prompt()
        assert len(result) > 0

    def test_user_prompt_with_context_type_previous_page(self):
        spec = TranslationPromptSpec(
            source_language="English",
            target_language="Japanese",
            context_type="previous_page",
        )
        result = spec.user_prompt()
        assert len(result) > 0

    def test_file_output_format_changes_formatting_fragment(self):
        spec_console = TranslationPromptSpec(
            source_language="English", target_language="Japanese", output_format="console"
        )
        spec_file = TranslationPromptSpec(
            source_language="English", target_language="Japanese", output_format="pdf"
        )
        assert spec_console.system_prompt() != spec_file.system_prompt()

    def test_toc_note_added(self):
        spec_no = TranslationPromptSpec(source_language="English", target_language="Japanese", toc=False)
        spec_yes = TranslationPromptSpec(source_language="English", target_language="Japanese", toc=True)
        assert len(spec_yes.system_prompt()) > len(spec_no.system_prompt())

    def test_tables_flag(self):
        spec_no = TranslationPromptSpec(source_language="English", target_language="Japanese", tables=False)
        spec_yes = TranslationPromptSpec(source_language="English", target_language="Japanese", tables=True)
        assert len(spec_yes.system_prompt()) > len(spec_no.system_prompt())


# ---------------------------------------------------------------------------
# ImageTranslationPromptSpec — system_prompt / user_prompt branches
# ---------------------------------------------------------------------------

class TestImageTranslationPromptSpec:

    def test_basic_system_prompt(self):
        spec = ImageTranslationPromptSpec(source_language="Japanese", target_language="English")
        result = spec.system_prompt()
        assert "Japanese" in result

    def test_vertical_flag_adds_block(self):
        spec_no = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", vertical=False)
        spec_yes = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", vertical=True)
        assert len(spec_yes.system_prompt()) > len(spec_no.system_prompt())

    def test_spread_flag_adds_note(self):
        spec_no = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", spread=False)
        spec_yes = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", spread=True)
        assert len(spec_yes.system_prompt()) > len(spec_no.system_prompt())

    def test_tables_flag(self):
        spec_no = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", tables=False)
        spec_yes = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", tables=True)
        assert len(spec_yes.system_prompt()) > len(spec_no.system_prompt())

    def test_user_prompt_basic(self):
        spec = ImageTranslationPromptSpec(source_language="Japanese", target_language="English")
        result = spec.user_prompt()
        assert "Japanese" in result
        assert "English" in result

    def test_user_prompt_vertical_note(self):
        spec_no = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", vertical=False)
        spec_yes = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", vertical=True)
        assert len(spec_yes.user_prompt()) > len(spec_no.user_prompt())

    def test_user_prompt_tables_note(self):
        spec_no = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", tables=False)
        spec_yes = ImageTranslationPromptSpec(source_language="Japanese", target_language="English", tables=True)
        assert len(spec_yes.user_prompt()) > len(spec_no.user_prompt())

    def test_system_note_included(self):
        spec = ImageTranslationPromptSpec(
            source_language="Japanese", target_language="English",
            system_note="Extra instruction."
        )
        result = spec.system_prompt()
        assert "Extra instruction" in result

    def test_user_note_included(self):
        spec = ImageTranslationPromptSpec(
            source_language="Japanese", target_language="English",
            user_note="Important note."
        )
        result = spec.user_prompt()
        assert "Important note" in result


# ---------------------------------------------------------------------------
# ImageTranslationService — __init__, build_prompts, _build_system/user_prompt
# ---------------------------------------------------------------------------

class TestImageTranslationService:

    def test_instantiates_without_error(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        assert svc is not None
        assert svc.tables is False

    def test_build_system_prompt(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        result = svc._build_system_prompt("Japanese", "English")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_user_prompt(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        result = svc._build_user_prompt("Japanese", "English")
        assert "Japanese" in result

    def test_build_prompts_returns_tuple(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        system, user = svc.build_prompts("Japanese", "English")
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_build_prompts_vertical_flag(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        _, user_normal = svc.build_prompts("Japanese", "English", vertical=False)
        _, user_vertical = svc.build_prompts("Japanese", "English", vertical=True)
        assert len(user_vertical) > len(user_normal)


# ---------------------------------------------------------------------------
# ImageTranslationService — _get_model / _get_max_tokens
# ---------------------------------------------------------------------------

class TestImageTranslationServiceModel:

    def test_get_max_tokens_uses_custom(self, monkeypatch):
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        svc = ImageTranslationService("fake-key", max_tokens=999)
        assert svc._get_max_tokens("gpt-4o") == 999

    def test_get_max_tokens_uses_catalog_default(self, monkeypatch):
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        from plugins.translation.src.settings import IMAGE_TRANSLATION_MAX_TOKENS
        svc = ImageTranslationService("fake-key")
        result = svc._get_max_tokens("gpt-4o")
        assert result == IMAGE_TRANSLATION_MAX_TOKENS

    def test_get_model_returns_string(self, monkeypatch):
        monkeypatch.setattr("src.services.base_service.get_model_max_completion_tokens", lambda m, d: d)
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        svc = ImageTranslationService("fake-key")
        model = svc._get_model()
        assert isinstance(model, str)
        assert len(model) > 0


# ---------------------------------------------------------------------------
# TranslationService — generate_text blank page short-circuit
# ---------------------------------------------------------------------------

class TestGenerateTextBlankPage:

    def test_blank_page_increments_counter_and_returns_marker(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        result = svc.generate_text("", "", "", page_num=0,
                                   source_language="Japanese", target_language="English")
        assert "Page 1" in result
        assert svc._blank_page_count == 1


# ---------------------------------------------------------------------------
# TranslationService — translate_table_grid
# ---------------------------------------------------------------------------

class TestTranslateTableGrid:

    def _make_response(self, content: str):
        class _Usage:
            prompt_tokens = 5
            completion_tokens = 10
            total_tokens = 15
        class _Message:
            def __init__(self, c): self.content = c
        class _Choice:
            def __init__(self, c):
                self.message = _Message(c)
                self.finish_reason = "stop"
        class _Resp:
            def __init__(self, c):
                self.id = "r"
                self.model = "gpt-4o"
                self.usage = _Usage()
                self.choices = [_Choice(c)]
        return _Resp(content)

    def test_empty_rows_returns_empty_immediately(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        result = svc.translate_table_grid([], "English", "Japanese")
        assert result == []

    def test_successful_translation_returns_parsed_grid(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        # Response with same number of rows as input
        response_md = "| 日本語 | 英語 |\n| --- | --- |\n| 翻訳 | Translation |"
        monkeypatch.setattr(svc, "_create_completion", lambda *a, **kw: self._make_response(response_md))
        rows = [["Japanese", "English"], ["Translation", "翻訳"]]
        result = svc.translate_table_grid(rows, "English", "Japanese")
        assert isinstance(result, list)

    def test_api_failure_returns_original(self, monkeypatch):
        from src.services import APISignal
        svc = _make_svc(monkeypatch)
        # Return an APISignal to simulate failure
        monkeypatch.setattr(svc, "_run_with_retry", lambda *a, **kw: APISignal.CONTEXT_LENGTH_EXCEEDED)
        rows = [["A", "B"], ["C", "D"]]
        result = svc.translate_table_grid(rows, "English", "Japanese")
        assert result == rows

    def test_unparseable_response_returns_original(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "_run_with_retry", lambda *a, **kw: "no table here at all")
        rows = [["A", "B"]]
        result = svc.translate_table_grid(rows, "English", "Japanese")
        assert result == rows

    def test_row_count_mismatch_returns_original(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        # Response has only 1 row, input has 2
        monkeypatch.setattr(svc, "_run_with_retry", lambda *a, **kw: "| X |\n| --- |\n| Y |")
        rows = [["A"], ["B"], ["C"]]
        result = svc.translate_table_grid(rows, "English", "Japanese")
        assert result == rows


# ---------------------------------------------------------------------------
# TranslationPromptSpec — system_prompt context type branches
# (lines 37, 39 in translation.py: _context_spec abstract / previous_page)
# ---------------------------------------------------------------------------

class TestTranslationPromptSpecSystemContext:

    def test_system_prompt_with_context_type_abstract(self):
        spec = TranslationPromptSpec(
            source_language="English", target_language="Japanese",
            context_type="abstract",
        )
        result = spec.system_prompt()
        assert isinstance(result, str) and len(result) > 0

    def test_system_prompt_with_context_type_previous_page(self):
        spec = TranslationPromptSpec(
            source_language="English", target_language="Japanese",
            context_type="previous_page",
        )
        result = spec.system_prompt()
        assert isinstance(result, str) and len(result) > 0


# ---------------------------------------------------------------------------
# TranslationService — translate_page_text
# ---------------------------------------------------------------------------

class TestTranslatePageText:

    def test_uses_abstract_context_type(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        captured: dict = {}

        def fake_translate(text, src, tgt, fmt="console", context_type="none"):
            captured["context_type"] = context_type
            return "translated"

        monkeypatch.setattr(svc, "translate_text", fake_translate)
        svc.translate_page_text("abstract text", "page text", "", "English", "Japanese")
        assert captured["context_type"] == "abstract"

    def test_uses_previous_page_context_type(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        captured: dict = {}

        def fake_translate(text, src, tgt, fmt="console", context_type="none"):
            captured["context_type"] = context_type
            return "translated"

        monkeypatch.setattr(svc, "translate_text", fake_translate)
        svc.translate_page_text("", "page text", "previous page content", "English", "Japanese")
        assert captured["context_type"] == "previous_page"

    def test_uses_none_context_type(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        captured: dict = {}

        def fake_translate(text, src, tgt, fmt="console", context_type="none"):
            captured["context_type"] = context_type
            return "translated"

        monkeypatch.setattr(svc, "translate_text", fake_translate)
        svc.translate_page_text("", "page text", "", "English", "Japanese")
        assert captured["context_type"] == "none"


# ---------------------------------------------------------------------------
# TranslationService — generate_text API signal handling
# ---------------------------------------------------------------------------

class TestGenerateTextSignals:

    def test_content_filter_appends_marker(self, monkeypatch):
        from src.services.api_errors import APISignal
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "translate_page_text",
                            lambda *a, **kw: APISignal.CONTENT_FILTER)
        result = svc.generate_text("", "some page text", "", 2, "English", "Japanese")
        assert "Content filter triggered" in result
        assert "Page 3" in result

    def test_empty_result_appends_error_marker(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "translate_page_text", lambda *a, **kw: "")
        result = svc.generate_text("", "some page text", "", 0, "English", "Japanese")
        assert "Translation error" in result

    def test_context_length_exceeded_splits_and_completes(self, monkeypatch):
        from src.services.api_errors import APISignal
        svc = _make_svc(monkeypatch)
        call_count = [0]

        def fake_translate(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return APISignal.CONTEXT_LENGTH_EXCEEDED
            return "translated"

        monkeypatch.setattr(svc, "translate_page_text", fake_translate)
        result = svc.generate_text("", "word " * 50, "", 0, "English", "Japanese")
        assert "Page 1" in result
        assert call_count[0] > 1


# ---------------------------------------------------------------------------
# TranslationService — translate_text_pages (sequential path)
# ---------------------------------------------------------------------------

class TestTranslateTextPages:

    def test_single_page_returns_one_result(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "generate_text",
                            lambda *a, **kw: "\n\n-- Page 1 -- \n\ntranslated text")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        result = svc.translate_text_pages(["Hello world"], None, "English", "Japanese")
        assert len(result) == 1
        assert "translated text" in result[0]

    def test_multiple_pages_returns_all_results(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)
        call_num = [0]

        def fake_generate(*a, **kw):
            call_num[0] += 1
            return f"result {call_num[0]}"

        monkeypatch.setattr(svc, "generate_text", fake_generate)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        result = svc.translate_text_pages(["p1", "p2", "p3"], None, "English", "Japanese")
        assert len(result) == 3
        assert "result 1" in result[0]
        assert "result 3" in result[2]

    def test_exception_in_page_appends_error_message(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)

        def fake_generate(*a, **kw):
            raise RuntimeError("API failure")

        monkeypatch.setattr(svc, "generate_text", fake_generate)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        result = svc.translate_text_pages(["Hello"], None, "English", "Japanese")
        assert len(result) == 1
        assert "Translation error" in result[0]

    def test_blank_page_summary_printed(self, monkeypatch, capsys):
        import time as _time
        svc = _make_svc(monkeypatch)
        svc._blank_page_count = 2
        monkeypatch.setattr(svc, "generate_text",
                            lambda *a, **kw: "\n\n-- Page 1 -- \n\ntranslated")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        svc.translate_text_pages(["Hello"], None, "English", "Japanese")
        out = capsys.readouterr().out
        assert "image-only" in out


# ---------------------------------------------------------------------------
# ImageTranslationService — _parse_response
# ---------------------------------------------------------------------------

class TestImageParseResponse:

    def test_parses_both_sections(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        transcript, translation = svc._parse_response(
            "[TRANSCRIPT]\nhello world\n[TRANSLATION]\nhow are you"
        )
        assert transcript == "hello world"
        assert translation == "how are you"

    def test_translation_only(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        transcript, translation = svc._parse_response("[TRANSLATION]\nsome translation")
        assert transcript == ""
        assert translation == "some translation"

    def test_fallback_when_no_sections(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        transcript, translation = svc._parse_response("plain response text without sections")
        assert transcript == ""
        assert translation == "plain response text without sections"


# ---------------------------------------------------------------------------
# ImageTranslationService — _call_api
# ---------------------------------------------------------------------------

class TestImageCallApi:

    def test_call_api_builds_multipart_message(self, monkeypatch):
        svc = _make_img_svc(monkeypatch)
        captured: dict = {}

        def fake_completion(model, messages, max_tokens, **kw):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            return MagicMock()

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        svc._call_api("gpt-4o", "system", "sys prompt", "user prompt",
                      "data:image/jpeg;base64,abc", 1000)
        assert captured["max_tokens"] == 1000
        user_msg = captured["messages"][1]
        assert user_msg["role"] == "user"
        assert any(c.get("type") == "image_url" for c in user_msg["content"])

    def test_call_api_uses_custom_temperature(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        svc = ImageTranslationService("fake-key", temperature=0.3)
        captured: dict = {}

        def fake_completion(model, messages, max_tokens, **kw):
            captured["temperature"] = kw.get("temperature")
            return MagicMock()

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        svc._call_api("gpt-4o", "system", "sys prompt", "user prompt",
                      "data:image/jpeg;base64,abc", 500)
        assert captured["temperature"] == 0.3


# ---------------------------------------------------------------------------
# ImageTranslationService — _get_model warning branch (line 59)
# ---------------------------------------------------------------------------

class TestImageGetModelWarning:

    def test_logs_warning_when_preferred_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "fallback-model")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        svc = ImageTranslationService("fake-key")
        model = svc._get_model()
        assert model == "fallback-model"


# ---------------------------------------------------------------------------
# ImageTranslationService — process_image_translation
# ---------------------------------------------------------------------------

class TestProcessImageTranslation:

    def _make_api_response(self, content: str) -> Any:
        class _Usage:
            prompt_tokens = 5
            completion_tokens = 20
            total_tokens = 25

        class _Message:
            def __init__(self, c: str) -> None:
                self.content = c

        class _Choice:
            def __init__(self, c: str) -> None:
                self.message = _Message(c)
                self.finish_reason = "stop"

        class _Resp:
            def __init__(self, c: str) -> None:
                self.id = "resp-img-1"
                self.model = "gpt-4o"
                self.usage = _Usage()
                self.choices = [_Choice(c)]

        return _Resp(content)

    def _patch_svc(self, monkeypatch) -> ImageTranslationService:
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(its_mod, "get_model_system_role", lambda m: "system")
        svc = ImageTranslationService("fake-key")
        monkeypatch.setattr(svc.image_processor, "local_image_to_data_url",
                            lambda path: "data:image/jpeg;base64,abc")
        return svc

    def test_returns_transcript_and_translation(self, monkeypatch):
        svc = self._patch_svc(monkeypatch)
        content = "[TRANSCRIPT]\nオリジナル\n[TRANSLATION]\nOriginal"
        monkeypatch.setattr(svc, "_create_completion",
                            lambda *a, **kw: self._make_api_response(content))
        transcript, translation = svc.process_image_translation(
            "dummy.jpg", "Japanese", "English"
        )
        assert transcript == "オリジナル"
        assert translation == "Original"

    def test_raises_value_error_when_model_lacks_vision(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "text-only-model")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "model_supports_vision", lambda m: False)
        monkeypatch.setattr(its_mod, "get_vision_capable_models", lambda: ["gpt-4o"])
        svc = ImageTranslationService("fake-key")
        with pytest.raises(ValueError, match="does not support image processing"):
            svc.process_image_translation("dummy.jpg", "Japanese", "English")

    def test_raises_when_image_file_unreadable(self, monkeypatch):
        svc = self._patch_svc(monkeypatch)

        def fail_read(path: str) -> str:
            raise IOError("File not found")

        monkeypatch.setattr(svc.image_processor, "local_image_to_data_url", fail_read)
        with pytest.raises(IOError):
            svc.process_image_translation("missing.jpg", "Japanese", "English")

    def test_no_choices_response_exhausts_retries(self, monkeypatch):
        import src.services.base_service as bsm
        import time as _time
        svc = self._patch_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 1)
        monkeypatch.setattr(_time, "sleep", lambda x: None)

        class _NoChoicesResp:
            id = "r"
            model = "gpt-4o"

            class _Usage:
                prompt_tokens = 5
                completion_tokens = 10
                total_tokens = 15

            usage = _Usage()
            choices: list = []

        monkeypatch.setattr(svc, "_create_completion", lambda *a, **kw: _NoChoicesResp())
        with pytest.raises(RuntimeError):
            svc.process_image_translation("dummy.jpg", "Japanese", "English")


# ---------------------------------------------------------------------------
# TranslationService — generate_text with citation-number text (lines 182, 222)
# ---------------------------------------------------------------------------

class TestGenerateTextCitationNumbers:

    def test_citation_numbers_in_text_and_translation(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        # Text with citation pattern (1) and translated result also containing (1)
        monkeypatch.setattr(svc, "translate_page_text",
                            lambda *a, **kw: "Translated text with reference (1) inline.")
        result = svc.generate_text("", "Source text citing (1) author.", "", 0, "English", "Japanese")
        assert "Page 1" in result
        assert "Translated text" in result


# ---------------------------------------------------------------------------
# TranslationService — translate_text_pages: api_error summary + progressive save
# ---------------------------------------------------------------------------

class TestTranslateTextPagesExtended:

    def test_api_error_count_summary_printed(self, monkeypatch, capsys):
        import time as _time
        svc = _make_svc(monkeypatch)
        svc._api_error_count = 1
        monkeypatch.setattr(svc, "generate_text",
                            lambda *a, **kw: "\n\n-- Page 1 -- \n\nok")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        svc.translate_text_pages(["Hello"], None, "English", "Japanese")
        out = capsys.readouterr().out
        assert "failed" in out

    def test_progressive_save_called_on_success(self, monkeypatch):
        import time as _time
        import sys
        from src.models import OutputOptions
        svc = _make_svc(monkeypatch)
        ts_mod = sys.modules["src.services.translation_service"]
        saved_calls: list = []

        class _MockFileOutputHandler:
            @staticmethod
            def save_page_progressively(*args, **kwargs):
                saved_calls.append(args)
                return "output.txt"

        monkeypatch.setattr(ts_mod, "FileOutputHandler", _MockFileOutputHandler)
        monkeypatch.setattr(svc, "generate_text",
                            lambda *a, **kw: "\n\n-- Page 1 -- \n\ntranslated")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        opts = OutputOptions(progressive_save=True, auto_save=True)
        svc.translate_text_pages(["Hello"], None, "English", "Japanese", opts=opts)
        assert len(saved_calls) == 1

    def test_progressive_save_called_on_exception(self, monkeypatch):
        import time as _time
        import sys
        from src.models import OutputOptions
        svc = _make_svc(monkeypatch)
        ts_mod = sys.modules["src.services.translation_service"]
        saved_calls: list = []

        class _MockFileOutputHandler:
            @staticmethod
            def save_page_progressively(*args, **kwargs):
                saved_calls.append(args)
                return "output.txt"

        monkeypatch.setattr(ts_mod, "FileOutputHandler", _MockFileOutputHandler)

        def fake_generate(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(svc, "generate_text", fake_generate)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        opts = OutputOptions(progressive_save=True, auto_save=True)
        result = svc.translate_text_pages(["Hello"], None, "English", "Japanese", opts=opts)
        assert "Translation error" in result[0]
        assert len(saved_calls) == 1

    def test_workers_gt1_delegates_to_parallel(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        parallel_calls: list = []

        def fake_parallel(all_triples, **kw):
            parallel_calls.append(all_triples)
            return ["translated"] * len(all_triples)

        monkeypatch.setattr(svc, "_translate_pages_parallel", fake_parallel)
        result = svc.translate_text_pages(["p1", "p2"], None, "English", "Japanese", workers=2)
        assert len(result) == 2
        assert len(parallel_calls) == 1


# ---------------------------------------------------------------------------
# TranslationService — on_progress callback (the webui's background job
# runner is the only real caller of this)
# ---------------------------------------------------------------------------

class TestTranslateTextPagesProgress:

    def test_on_progress_called_once_per_page_in_order(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "generate_text", lambda *a, **kw: "translated")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        calls: list = []
        svc.translate_text_pages(
            ["p1", "p2", "p3"], None, "English", "Japanese",
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert calls == [(1, 3), (2, 3), (3, 3)]

    def test_on_progress_still_called_when_a_page_errors(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)

        def flaky(*a, **kw):
            # The 2nd page (i == 1, zero-based) fails; the loop must still
            # report progress for it rather than silently skipping it.
            if a[3] == 1:
                raise RuntimeError("boom")
            return "translated"

        monkeypatch.setattr(svc, "generate_text", flaky)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        calls: list = []
        result = svc.translate_text_pages(
            ["p1", "p2", "p3"], None, "English", "Japanese",
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert calls == [(1, 3), (2, 3), (3, 3)]
        assert "Translation error" in result[1]

    def test_default_none_means_no_progress_reporting(self, monkeypatch):
        # Regression guard: every CLI call path passes nothing for
        # on_progress, so this must be a true no-op, not a crash from
        # calling None().
        import time as _time
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "generate_text", lambda *a, **kw: "translated")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        result = svc.translate_text_pages(["p1"], None, "English", "Japanese")
        assert result == ["translated"]

    def test_workers_gt1_dispatch_does_not_call_on_progress_itself(self, monkeypatch):
        # _translate_page_sequence's own workers>1 branch just hands off to
        # _translate_pages_parallel (stubbed out here) — it must not call
        # on_progress directly itself; that's _translate_pages_parallel's
        # job (see the real-call test below, and its own on_progress tests
        # in TestTranslatePagesParallelOnProgress).
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "_translate_pages_parallel", lambda all_triples, **kw: ["t"] * len(all_triples))
        calls: list = []
        svc.translate_text_pages(
            ["p1", "p2"], None, "English", "Japanese", workers=2,
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert calls == []

    def test_workers_gt1_forwards_on_progress_to_translate_pages_parallel(self, monkeypatch):
        # Regression guard for the "progress bar frozen with workers > 1"
        # fix: _translate_page_sequence must pass its own on_progress
        # through to _translate_pages_parallel rather than silently
        # dropping it — see TestTranslatePagesParallelOnProgress below for
        # coverage that _translate_pages_parallel itself actually calls it.
        svc = _make_svc(monkeypatch)
        received = {}

        def fake_parallel(all_triples, **kw):
            received.update(kw)
            return ["t"] * len(all_triples)

        monkeypatch.setattr(svc, "_translate_pages_parallel", fake_parallel)
        def on_progress(done, total):
            return None
        svc.translate_text_pages(
            ["p1", "p2"], None, "English", "Japanese", workers=2, on_progress=on_progress,
        )
        assert received["on_progress"] is on_progress


# ---------------------------------------------------------------------------
# TranslationService — on_progress genuinely fires on the REAL parallel path
# (regression coverage for the "progress bar frozen with workers > 1" bug:
# _translate_pages_parallel previously had no on_progress parameter at all,
# so a webui job run with more than one worker never produced a single
# job_progress message, even though jobs.py's own job_notice message
# claimed the progress bar would "still update normally").
# ---------------------------------------------------------------------------

class TestTranslatePagesParallelOnProgress:

    def _make_parallel_svc(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "_get_model", lambda: "gpt-4o")
        monkeypatch.setattr(svc, "generate_text", lambda *a, **kw: "translated")
        return svc

    def test_on_progress_called_once_per_page_reaching_the_full_total(self, monkeypatch):
        svc = self._make_parallel_svc(monkeypatch)
        calls: list = []
        result = svc.translate_text_pages(
            ["p1", "p2", "p3", "p4"], None, "English", "Japanese", workers=2,
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert len(result) == 4
        # Parallel workers can finish in any order, so only the *count* and
        # final call are guaranteed — not which page happened to finish
        # when. Every call must report the same total, and cumulative
        # "done" counts must be exactly 1..4 with no gaps or repeats.
        assert len(calls) == 4
        assert all(total == 4 for _done, total in calls)
        assert sorted(done for done, _total in calls) == [1, 2, 3, 4]
        assert calls[-1] == (4, 4)

    def test_on_progress_still_called_when_a_page_errors(self, monkeypatch):
        svc = self._make_parallel_svc(monkeypatch)

        def flaky(*a, **kw):
            if a[3] == 1:
                raise RuntimeError("boom")
            return "translated"

        monkeypatch.setattr(svc, "generate_text", flaky)
        calls: list = []
        svc.translate_text_pages(
            ["p1", "p2", "p3"], None, "English", "Japanese", workers=2,
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) == 3
        assert calls[-1] == (3, 3)

    def test_default_none_means_no_progress_reporting(self, monkeypatch):
        svc = self._make_parallel_svc(monkeypatch)
        # Must not raise (i.e. must not try calling None()).
        result = svc.translate_text_pages(["p1", "p2"], None, "English", "Japanese", workers=2)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TranslationService — on_page_text callback (a sibling to on_progress that
# carries the actual translated text — see PageTextCallback's docstring in
# src/runtime/ui_action.py, added after a report that a webui translation
# job showed a progress percentage but never the pages' actual content)
# ---------------------------------------------------------------------------

class TestTranslateTextPagesPageText:

    def test_on_page_text_called_once_per_page_with_translated_text_in_order(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)
        pages_text = {0: "translated one", 1: "translated two", 2: "translated three"}
        monkeypatch.setattr(svc, "generate_text", lambda *a, **kw: pages_text[a[3]])
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        calls: list = []
        svc.translate_text_pages(
            ["p1", "p2", "p3"], None, "English", "Japanese",
            on_page_text=lambda page_number, text: calls.append((page_number, text)),
        )
        assert calls == [(1, "translated one"), (2, "translated two"), (3, "translated three")]

    def test_on_page_text_not_called_for_an_errored_page(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)

        def flaky(*a, **kw):
            if a[3] == 1:
                raise RuntimeError("boom")
            return "translated"

        monkeypatch.setattr(svc, "generate_text", flaky)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        calls: list = []
        svc.translate_text_pages(
            ["p1", "p2", "p3"], None, "English", "Japanese",
            on_page_text=lambda page_number, text: calls.append((page_number, text)),
        )
        # Page 2 (index 1) errored — only pages 1 and 3 ever produced real
        # translated text to report.
        assert calls == [(1, "translated"), (3, "translated")]

    def test_default_none_means_no_page_text_reporting(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "generate_text", lambda *a, **kw: "translated")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        result = svc.translate_text_pages(["p1"], None, "English", "Japanese")
        assert result == ["translated"]

    def test_workers_gt1_never_invokes_on_page_text(self, monkeypatch):
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "_translate_pages_parallel", lambda all_triples, **kw: ["t"] * len(all_triples))
        calls: list = []
        svc.translate_text_pages(
            ["p1", "p2"], None, "English", "Japanese", workers=2,
            on_page_text=lambda page_number, text: calls.append((page_number, text)),
        )
        assert calls == []

    def test_on_progress_and_on_page_text_can_both_be_used_together(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(svc, "generate_text", lambda *a, **kw: "translated")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        progress_calls: list = []
        text_calls: list = []
        svc.translate_text_pages(
            ["p1", "p2"], None, "English", "Japanese",
            on_progress=lambda done, total: progress_calls.append((done, total)),
            on_page_text=lambda page_number, text: text_calls.append((page_number, text)),
        )
        assert progress_calls == [(1, 2), (2, 2)]
        assert text_calls == [(1, "translated"), (2, "translated")]


# ---------------------------------------------------------------------------
# TranslationService — translate_document (covers _make_pdf_triples + entry)
# ---------------------------------------------------------------------------

class TestTranslateDocument:

    def test_translate_document_delegates_to_sequence(self, monkeypatch):
        import time as _time
        svc = _make_svc(monkeypatch)

        class _MockPage:
            pass

        monkeypatch.setattr(svc.pdf_processor, "process_page",
                            lambda page: "extracted text from page")
        monkeypatch.setattr(svc, "generate_text",
                            lambda *a, **kw: "\n\n-- Page 1 -- \n\ntranslated")
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        result = svc.translate_document([_MockPage()], None, 0, None, "English", "Japanese")
        assert len(result) == 1
        assert "translated" in result[0]


# ---------------------------------------------------------------------------
# ImageTranslationService — process_image_translation retry body branches
# ---------------------------------------------------------------------------

class TestProcessImageTranslationRetryPaths:

    def _make_patch_svc(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(its_mod, "get_model_system_role", lambda m: "system")
        svc = ImageTranslationService("fake-key")
        monkeypatch.setattr(svc.image_processor, "local_image_to_data_url",
                            lambda path: "data:image/jpeg;base64,abc")
        return svc

    def _resp_with_content(self, content):
        class _Usage:
            prompt_tokens = 5
            completion_tokens = 10
            total_tokens = 15

        class _Msg:
            def __init__(self, c): self.content = c

        class _Choice:
            def __init__(self, c):
                self.message = _Msg(c)
                self.finish_reason = "stop"

        class _Resp:
            def __init__(self, c):
                self.id = "r"
                self.model = "gpt-4o"
                self.usage = _Usage()
                self.choices = [_Choice(c)]

        return _Resp(content)

    def test_none_content_then_valid_returns_translation(self, monkeypatch):
        import src.services.base_service as bsm
        import time as _time
        svc = self._make_patch_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 3)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        call_count = [0]

        def fake_completion(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._resp_with_content(None)  # content is None → retry
            return self._resp_with_content("[TRANSCRIPT]\nhello\n[TRANSLATION]\nworld")

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        transcript, translation = svc.process_image_translation("dummy.jpg", "Japanese", "English")
        assert translation == "world"
        assert call_count[0] == 2

    def test_whitespace_content_then_valid_returns_translation(self, monkeypatch):
        import src.services.base_service as bsm
        import time as _time
        svc = self._make_patch_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 3)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        call_count = [0]

        def fake_completion(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._resp_with_content("   ")  # whitespace → retry
            return self._resp_with_content("[TRANSCRIPT]\nhello\n[TRANSLATION]\nworld")

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        transcript, translation = svc.process_image_translation("dummy.jpg", "Japanese", "English")
        assert translation == "world"
        assert call_count[0] == 2

    def test_non_string_content_then_valid_returns_translation(self, monkeypatch):
        import src.services.base_service as bsm
        import time as _time
        svc = self._make_patch_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 3)
        monkeypatch.setattr(_time, "sleep", lambda x: None)
        call_count = [0]

        def fake_completion(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._resp_with_content(42)  # int, not str → retry
            return self._resp_with_content("[TRANSCRIPT]\nhello\n[TRANSLATION]\nworld")

        monkeypatch.setattr(svc, "_create_completion", fake_completion)
        transcript, translation = svc.process_image_translation("dummy.jpg", "Japanese", "English")
        assert translation == "world"
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# ImageTranslationService — blank image short-circuit
# ---------------------------------------------------------------------------

class TestProcessImageTranslationBlankShortCircuit:

    def _patch_svc(self, monkeypatch) -> "ImageTranslationService":
        monkeypatch.setattr(
            "src.services.base_service.get_model_max_completion_tokens", lambda m, d: d
        )
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr("src.services.base_service.resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr("src.services.base_service.maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "model_supports_vision", lambda m: True)
        monkeypatch.setattr(its_mod, "get_model_system_role", lambda m: "system")
        svc = ImageTranslationService("fake-key")
        monkeypatch.setattr(svc.image_processor, "local_image_to_data_url",
                            lambda path: "data:image/jpeg;base64,abc")
        return svc

    def test_blank_image_returns_empty_tuple_without_api_call(self, monkeypatch):
        """process_image_translation must return ('', '') immediately for blank images."""
        svc = self._patch_svc(monkeypatch)
        monkeypatch.setattr(svc.image_processor, "is_blank_image", lambda *a, **kw: True)
        api_called = [False]

        def _no_api(*a, **kw):
            api_called[0] = True
            raise AssertionError("API should not be called for blank images")

        monkeypatch.setattr(svc, "_create_completion", _no_api)
        transcript, translation = svc.process_image_translation("blank.png", "Japanese", "English")
        assert transcript == ""
        assert translation == ""
        assert not api_called[0]

    def test_non_blank_image_still_calls_api(self, monkeypatch):
        """process_image_translation must proceed normally when image is not blank."""
        svc = self._patch_svc(monkeypatch)
        monkeypatch.setattr(svc.image_processor, "is_blank_image", lambda *a, **kw: False)

        class _Usage:
            prompt_tokens = 5
            completion_tokens = 20
            total_tokens = 25

        class _Msg:
            content = "[TRANSCRIPT]\n文字\n[TRANSLATION]\nText"

        class _Choice:
            message = _Msg()
            finish_reason = "stop"

        class _Resp:
            id = "r"
            model = "gpt-4o"
            usage = _Usage()
            choices = [_Choice()]

        monkeypatch.setattr(svc, "_create_completion", lambda *a, **kw: _Resp())
        transcript, translation = svc.process_image_translation("content.png", "Japanese", "English")
        assert transcript == "文字"
        assert translation == "Text"


# ---------------------------------------------------------------------------
# TranslationService — translate_text retry when content is None (line 118)
# ---------------------------------------------------------------------------

class TestTranslateTextRetryPath:

    def test_choices_present_content_none_exhausts_to_signal(self, monkeypatch):
        import types
        import src.services.base_service as bsm
        import time as _time
        svc = _make_svc(monkeypatch)
        monkeypatch.setattr(bsm, "MAX_RETRIES", 1)
        monkeypatch.setattr(_time, "sleep", lambda x: None)

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1
            total_tokens = 2

        msg = types.SimpleNamespace(content=None)
        choice = types.SimpleNamespace(message=msg, finish_reason="stop")
        none_content_resp = types.SimpleNamespace(
            id="r", model="gpt-4o", usage=_Usage(), choices=[choice]
        )
        monkeypatch.setattr(svc, "_call_translation_api",
                            lambda *a, **kw: none_content_resp)
        from src.services.api_errors import APISignal
        result = svc.translate_text("Hello", "English", "Japanese")
        assert isinstance(result, APISignal)
