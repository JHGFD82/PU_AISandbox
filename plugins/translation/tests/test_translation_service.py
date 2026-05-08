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
from unittest.mock import MagicMock, patch

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
        pipe_counts = [l.count("|") for l in lines]
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
        from src.services import image_translation_service as its_mod
        monkeypatch.setattr(its_mod, "resolve_model", lambda **_: "gpt-4o")
        monkeypatch.setattr(its_mod, "maybe_sync_model_pricing", lambda m: None)
        monkeypatch.setattr(its_mod, "get_default_model", lambda _: "gpt-4o")
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
            prompt_tokens = 5; completion_tokens = 10; total_tokens = 15
        class _Message:
            def __init__(self, c): self.content = c
        class _Choice:
            def __init__(self, c): self.message = _Message(c); self.finish_reason = "stop"
        class _Resp:
            def __init__(self, c): self.id = "r"; self.model = "gpt-4o"; self.usage = _Usage(); self.choices = [_Choice(c)]
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
