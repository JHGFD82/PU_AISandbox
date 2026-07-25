"""Tests for TranslationPlugin.run_ui_action and its module-level ui_action
declaration — the webui composer's "Translate a document" action.

See docs/webui-plugin-plan.md section 10. plugin.py isn't pre-registered
by conftest.py (only its supporting service/runtime modules are), so it's
loaded here the same way plugins/webui/plugin.py's own tests load app.py:
directly from its file path under the fabricated module name the real
plugin loader would use.
"""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import register_language
from src.errors import CLIError

_PLUGIN_FILE = Path(__file__).resolve().parents[1] / "plugin.py"


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location("pu_plugin.translation.plugin", _PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pu_plugin.translation.plugin"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def plugin_module():
    return _load_plugin_module()


@pytest.fixture(autouse=True)
def _japanese_registered():
    # Mirrors what a real Japanese extension plugin's import-time
    # register_language() call would already have done in a full
    # installation — needed here since this repo only bundles English.
    register_language("ja", "Japanese")


class TestUiActionDeclaration:

    def test_declares_expected_fields_in_order(self, plugin_module):
        names = [f.name for f in plugin_module.ui_action.fields]
        assert names == ["source_language", "target_language", "file", "scanned", "page_nums", "notes"]

    def test_id_and_command_are_translate(self, plugin_module):
        assert plugin_module.ui_action.id == "translate"
        assert plugin_module.ui_action.command == "translate"

    def test_only_languages_and_file_are_required(self, plugin_module):
        required = {f.name for f in plugin_module.ui_action.fields if f.required}
        assert required == {"source_language", "target_language", "file"}

    def test_field_kinds(self, plugin_module):
        kinds = {f.name: f.kind for f in plugin_module.ui_action.fields}
        assert kinds["source_language"] == "language"
        assert kinds["target_language"] == "language"
        assert kinds["file"] == "file"
        assert kinds["scanned"] == "checkbox"
        assert kinds["page_nums"] == "text"
        assert kinds["notes"] == "text"


class TestRunUiAction:

    def _patch_sandbox(self, monkeypatch, translate_side_effect=None):
        fake_sandbox = MagicMock()
        fake_sandbox.translation_service = MagicMock()
        fake_sandbox.image_translation_service = MagicMock()
        if translate_side_effect is not None:
            fake_sandbox.translate_document.side_effect = translate_side_effect
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        return fake_sandbox

    def _write_output(self, *args, **kwargs):
        """Stand-in for translate_document: actually creates opts.output_file,
        the way the real one does via file_output.save_translation_output."""
        opts = kwargs["opts"]
        with open(opts.output_file, "w", encoding="utf-8") as f:
            f.write("translated content")

    def test_success_writes_output_and_builds_result(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")
        output_dir = tmp_path / "job_output"

        result = plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(output_dir),
        )

        assert result.output_filename == "upload_Japanese_to_English.txt"
        assert os.path.exists(result.output_path)
        assert "Japanese" in result.summary
        assert "English" in result.summary
        fake_sandbox.translate_document.assert_called_once()

    def test_docx_source_produces_docx_output_extension(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.docx"
        src_file.write_bytes(b"fake docx bytes")
        output_dir = tmp_path / "job_output"

        result = plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.docx",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(output_dir),
        )
        assert result.output_filename.endswith(".docx")

    def test_on_progress_forwarded_to_translate_document(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        def progress(done, total):
            pass

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
            },
            professor="fake", model=None, on_progress=progress, output_dir=str(tmp_path / "out"),
        )
        _, kwargs = fake_sandbox.translate_document.call_args
        assert kwargs["on_progress"] is progress

    def test_notes_applied_to_both_prompts(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "notes": "Keep names transliterated, not translated.",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert fake_sandbox.translation_service.system_note == "Keep names transliterated, not translated."
        assert fake_sandbox.translation_service.user_note == "Keep names transliterated, not translated."
        assert fake_sandbox.image_translation_service.system_note == "Keep names transliterated, not translated."

    def test_scanned_checkbox_string_parsed_as_bool(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "scanned": "true",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        _, kwargs = fake_sandbox.translate_document.call_args
        assert kwargs["scanned"] is True

    def test_missing_file_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch)
        with pytest.raises(CLIError, match="No file was attached"):
            plugin_module.plugin.run_ui_action(
                fields={"source_language": "ja", "target_language": "en"},
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )

    def test_invalid_language_code_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")
        with pytest.raises(CLIError, match="Invalid source language"):
            plugin_module.plugin.run_ui_action(
                fields={
                    "source_language": "not-a-real-code", "target_language": "en",
                    "file_path": str(src_file), "file_name": "upload.txt",
                },
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )

    def test_translate_document_not_producing_output_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        # translate_document runs but (e.g. a bug, or an empty document)
        # never actually writes the expected file — must not silently
        # report success with a UiJobResult pointing at nothing.
        self._patch_sandbox(monkeypatch, translate_side_effect=lambda *a, **kw: None)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")
        with pytest.raises(CLIError, match="no output file"):
            plugin_module.plugin.run_ui_action(
                fields={
                    "source_language": "ja", "target_language": "en",
                    "file_path": str(src_file), "file_name": "upload.txt",
                },
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )
