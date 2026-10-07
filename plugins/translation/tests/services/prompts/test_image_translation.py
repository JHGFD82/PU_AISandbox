"""Tests for plugins/translation/src/services/prompts/image_translation.py: what translating a picture asks the model."""

import sys


ImageTranslationPromptSpec = sys.modules["src.services.prompts.image_translation"].ImageTranslationPromptSpec


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
