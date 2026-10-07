"""Tests for plugins/translation/src/services/prompts/translation.py: what a document translation asks the model."""

import sys


TranslationPromptSpec = sys.modules["src.services.prompts.translation"].TranslationPromptSpec


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
