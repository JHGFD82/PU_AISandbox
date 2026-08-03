"""Tests for TranslationPlugin.run_ui_action and its module-level ui_action
declaration — the webui composer's "Translate a document" action.

plugin.py isn't pre-registered
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
from src.runtime import ui_action as ui_action_module
from src.runtime.ui_action import UiField, register_extension_ui_hooks

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
        assert names == [
            "source_language", "target_language", "file", "abstract", "page_nums",
            "scanned", "spread",
            "output_format", "preserve_tables", "toc", "preserve_media", "font", "font_size",
            "workers",
            "notes",
        ]

    def test_id_and_command_are_translate(self, plugin_module):
        assert plugin_module.ui_action.id == "translate"
        assert plugin_module.ui_action.command == "translate"

    def test_progress_verb_is_correctly_spelled_gerund(self, plugin_module):
        # Regression guard for the "Translateing..." bug: jobs.py used to
        # derive this by string manipulation instead of reading a plugin-
        # declared value.
        assert plugin_module.ui_action.progress_verb == "Translating"

    def test_only_languages_and_file_are_required(self, plugin_module):
        required = {f.name for f in plugin_module.ui_action.fields if f.required}
        assert required == {"source_language", "target_language", "file"}

    def test_file_field_allows_a_folder_of_images(self, plugin_module):
        # translate's CLI already accepts -i pointed at a folder of page
        # images (run()'s own os.path.isdir(input_path) branch, calling
        # process_image_translation_folder) — the composer's file field
        # must offer the same, matching transcribe's own file field.
        field = next(f for f in plugin_module.ui_action.fields if f.name == "file")
        assert field.allow_folder is True

    def test_field_kinds(self, plugin_module):
        kinds = {f.name: f.kind for f in plugin_module.ui_action.fields}
        assert kinds["source_language"] == "language"
        assert kinds["target_language"] == "language"
        assert kinds["file"] == "file"
        assert kinds["scanned"] == "checkbox"
        assert kinds["spread"] == "checkbox"
        assert kinds["page_nums"] == "text"
        assert kinds["notes"] == "text"
        assert kinds["output_format"] == "select"
        assert kinds["preserve_tables"] == "checkbox"
        assert kinds["toc"] == "checkbox"
        assert kinds["preserve_media"] == "checkbox"
        assert kinds["font"] == "text"
        assert kinds["font_size"] == "text"
        assert kinds["workers"] == "text"

    def test_output_format_choices(self, plugin_module):
        field = next(f for f in plugin_module.ui_action.fields if f.name == "output_format")
        values = [c["value"] for c in field.choices]
        assert values == ["same", "docx", "pdf", "txt", "md"]

    def test_fields_are_grouped_for_display(self, plugin_module):
        # Purely cosmetic (see UiField.group's docstring) but every field
        # beyond the always-visible core three should belong to some group,
        # so the composer never renders a dense, header-less wall of extras.
        groups = {f.name: f.group for f in plugin_module.ui_action.fields}
        assert groups["source_language"] == "Document"
        assert groups["output_format"] == "Output"
        assert groups["workers"] == "Performance"
        assert groups["notes"] == "Notes"
        assert all(g is not None for g in groups.values())

    def test_ui_action_is_reachable_from_the_plugin_instance(self, plugin_module):
        # jobs.py's find_plugin_for_action()/list_ui_actions() call
        # getattr(p, "ui_action", None) on the *instance* load_plugins()
        # returns (plugins.values()), never on this module directly — a
        # bare module-level `ui_action` with nothing pointing to it from
        # `plugin` would be invisible to the real app even though this
        # module's own tests (like the ones above) can still see it.
        assert plugin_module.plugin.ui_action is plugin_module.ui_action


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

    def test_result_includes_session_token_usage(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        fake_sandbox.token_tracker.get_session_usage.return_value = {
            "prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500, "total_cost": 0.0456,
        }
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        result = plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert result.prompt_tokens == 1200
        assert result.completion_tokens == 300
        assert result.cost == 0.0456

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

    def test_on_page_text_forwarded_to_translate_document(self, monkeypatch, plugin_module, tmp_path):
        # Regression coverage for the "no messages from each page" report —
        # confirms the webui's per-page callback actually reaches
        # sandbox.translate_document, not just on_progress.
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        def page_text(page_number, text):
            pass

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            on_page_text=page_text,
        )
        _, kwargs = fake_sandbox.translate_document.call_args
        assert kwargs["on_page_text"] is page_text

    def test_on_page_text_defaults_to_none(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        _, kwargs = fake_sandbox.translate_document.call_args
        assert kwargs["on_page_text"] is None

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

    def test_output_format_overrides_source_derived_extension(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        result = plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "output_format": "docx",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert result.output_filename.endswith(".docx")

    def test_blank_output_format_falls_back_to_source_derived_extension(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.docx"
        src_file.write_bytes(b"fake docx bytes")

        result = plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.docx",
                "output_format": "same",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert result.output_filename.endswith(".docx")

    def test_preserve_media_without_docx_output_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")
        with pytest.raises(CLIError, match="Word .*docx"):
            plugin_module.plugin.run_ui_action(
                fields={
                    "source_language": "ja", "target_language": "en",
                    "file_path": str(src_file), "file_name": "upload.txt",
                    "preserve_media": "true", "output_format": "txt",
                },
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )

    def test_preserve_tables_and_toc_set_on_both_services(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "preserve_tables": "true", "toc": "true",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert fake_sandbox.translation_service.tables is True
        assert fake_sandbox.image_translation_service.tables is True
        assert fake_sandbox.translation_service.toc is True

    def test_workers_and_spread_forwarded_to_translate_document(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "workers": "4", "spread": "true",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        _, kwargs = fake_sandbox.translate_document.call_args
        assert kwargs["workers"] == 4
        assert kwargs["spread"] is True

    def test_font_and_font_size_forwarded_via_output_options(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "font": "Times New Roman", "font_size": "11",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        _, kwargs = fake_sandbox.translate_document.call_args
        assert kwargs["opts"].custom_font == "Times New Roman"
        assert kwargs["opts"].font_size == 11

    def test_invalid_workers_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")
        with pytest.raises(CLIError, match="parallel workers"):
            plugin_module.plugin.run_ui_action(
                fields={
                    "source_language": "ja", "target_language": "en",
                    "file_path": str(src_file), "file_name": "upload.txt",
                    "workers": "not-a-number",
                },
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )

    def test_sampling_params_passed_through_to_sandbox(self, monkeypatch, plugin_module, tmp_path):
        sandbox_cls = MagicMock(return_value=self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output))
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", sandbox_cls)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "temperature": "0.4", "top_p": "0.9", "max_tokens": "2048",
            },
            professor="fake", model="gpt-4o", on_progress=None, output_dir=str(tmp_path / "out"),
        )
        sandbox_cls.assert_called_once_with(
            "fake", model="gpt-4o", temperature=0.4, top_p=0.9, max_tokens=2048,
        )

    def test_invalid_temperature_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")
        with pytest.raises(CLIError, match="temperature"):
            plugin_module.plugin.run_ui_action(
                fields={
                    "source_language": "ja", "target_language": "en",
                    "file_path": str(src_file), "file_name": "upload.txt",
                    "temperature": "hot",
                },
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )


class TestRunUiActionFolderInput:
    """A folder of page images uploaded via the composer's file field
    (UiField.allow_folder=True) — mirrors transcribe's own folder-input
    tests in plugins/transcription/tests/test_transcription_plugin_ui_action.py.
    Routes through sandbox.process_image_translation_folder instead of
    sandbox.translate_document, matching run()'s own os.path.isdir(input_path)
    branch on the CLI side."""

    def _patch_sandbox(self, monkeypatch, folder_side_effect=None):
        fake_sandbox = MagicMock()
        fake_sandbox.translation_service = MagicMock()
        fake_sandbox.image_translation_service = MagicMock()
        if folder_side_effect is not None:
            fake_sandbox.process_image_translation_folder.side_effect = folder_side_effect
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        return fake_sandbox

    def _write_output(self, *args, **kwargs):
        opts = kwargs["opts"] if "opts" in kwargs else args[3]
        with open(opts.output_file, "w", encoding="utf-8") as f:
            f.write("translated content")

    def test_folder_input_delegates_to_process_image_translation_folder(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, folder_side_effect=self._write_output)
        folder = tmp_path / "scans"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")
        output_dir = tmp_path / "job_output"

        result = plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(folder), "file_name": "scans",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(output_dir),
        )
        fake_sandbox.process_image_translation_folder.assert_called_once()
        fake_sandbox.translate_document.assert_not_called()
        assert os.path.exists(result.output_path)
        assert "scans" in result.summary

    def test_on_progress_and_on_page_text_forwarded(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, folder_side_effect=self._write_output)
        folder = tmp_path / "scans"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")

        def progress(done, total):
            pass

        def page_text(idx, text):
            pass

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(folder), "file_name": "scans",
            },
            professor="fake", model=None, on_progress=progress, output_dir=str(tmp_path / "out"),
            on_page_text=page_text,
        )
        _, kwargs = fake_sandbox.process_image_translation_folder.call_args
        assert kwargs["on_progress"] is progress
        assert kwargs["on_page_text"] is page_text

    def test_workers_and_spread_forwarded(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, folder_side_effect=self._write_output)
        folder = tmp_path / "scans"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(folder), "file_name": "scans",
                "workers": "4", "spread": "true",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        _, kwargs = fake_sandbox.process_image_translation_folder.call_args
        assert kwargs["workers"] == 4
        assert kwargs["spread"] is True

    def test_output_format_defaults_to_txt_for_a_folder(self, monkeypatch, plugin_module, tmp_path):
        # file_name for a folder upload has no extension (e.g. "scans"),
        # so the "same" default's docx-vs-txt gate falls to txt — matching
        # transcribe's own folder-upload default.
        self._patch_sandbox(monkeypatch, folder_side_effect=self._write_output)
        folder = tmp_path / "scans"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")

        result = plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(folder), "file_name": "scans",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert result.output_filename.endswith(".txt")

    def test_preserve_media_with_a_folder_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, folder_side_effect=self._write_output)
        folder = tmp_path / "scans"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")

        with pytest.raises(CLIError, match="no embedded media"):
            plugin_module.plugin.run_ui_action(
                fields={
                    "source_language": "ja", "target_language": "en",
                    "file_path": str(folder), "file_name": "scans",
                    "output_format": "docx", "preserve_media": "true",
                },
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )


class TestPreviewUiAction:
    """The composer's live two-pane prompt preview — see UiPromptPreview's
    docstring in src/runtime/ui_action.py. Unlike run_ui_action, this must
    never raise on incomplete input, since it's called after every
    keystroke, potentially before a language or file has been chosen."""

    def _patch_sandbox(self, monkeypatch):
        fake_sandbox = MagicMock()
        fake_sandbox.translation_service = MagicMock()
        fake_sandbox.translation_service.build_prompts.return_value = ("SYS", "USR")
        fake_sandbox.translation_service._get_model.return_value = "resolved-model"
        fake_sandbox.image_translation_service = MagicMock()
        fake_sandbox.image_translation_service.build_prompts.return_value = ("IMG-SYS", "IMG-USR")
        fake_sandbox.image_translation_service._get_model.return_value = "resolved-vision-model"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        return fake_sandbox

    def test_returns_text_prompts_by_default(self, monkeypatch, plugin_module):
        self._patch_sandbox(monkeypatch)
        preview = plugin_module.plugin.preview_ui_action(
            fields={"source_language": "ja", "target_language": "en"},
            professor="fake", model=None,
        )
        assert preview.system_prompt == "SYS"
        assert preview.user_prompt == "USR"
        assert preview.model == "resolved-model"
        assert preview.note is None

    def test_scanned_checkbox_uses_image_service(self, monkeypatch, plugin_module):
        self._patch_sandbox(monkeypatch)
        preview = plugin_module.plugin.preview_ui_action(
            fields={"source_language": "ja", "target_language": "en", "scanned": "true"},
            professor="fake", model=None,
        )
        assert preview.system_prompt == "IMG-SYS"
        assert preview.model == "resolved-vision-model"
        assert preview.note is not None

    def test_missing_languages_do_not_raise(self, monkeypatch, plugin_module):
        # No source_language/target_language at all yet — the very first
        # call, right after the composer panel opens.
        self._patch_sandbox(monkeypatch)
        preview = plugin_module.plugin.preview_ui_action(fields={}, professor="fake", model=None)
        assert preview.system_prompt == "SYS"

    def test_invalid_language_code_falls_back_to_placeholder_not_error(self, monkeypatch, plugin_module):
        self._patch_sandbox(monkeypatch)
        preview = plugin_module.plugin.preview_ui_action(
            fields={"source_language": "not-a-real-code"}, professor="fake", model=None,
        )
        assert preview.system_prompt == "SYS"

    def test_notes_applied_to_both_services(self, monkeypatch, plugin_module):
        fake_sandbox = self._patch_sandbox(monkeypatch)
        plugin_module.plugin.preview_ui_action(
            fields={"notes": "Keep names transliterated."}, professor="fake", model=None,
        )
        assert fake_sandbox.translation_service.system_note == "Keep names transliterated."
        assert fake_sandbox.image_translation_service.user_note == "Keep names transliterated."

    def test_model_passed_through_to_sandbox(self, monkeypatch, plugin_module):
        sandbox_cls = MagicMock(return_value=self._patch_sandbox(monkeypatch))
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", sandbox_cls)
        plugin_module.plugin.preview_ui_action(fields={}, professor="fake", model="gpt-4o")
        sandbox_cls.assert_called_once_with("fake", model="gpt-4o")

    def test_output_format_forwarded_to_build_prompts(self, monkeypatch, plugin_module):
        fake_sandbox = self._patch_sandbox(monkeypatch)
        plugin_module.plugin.preview_ui_action(
            fields={"source_language": "ja", "target_language": "en", "output_format": "docx"},
            professor="fake", model=None,
        )
        _, kwargs = fake_sandbox.translation_service.build_prompts.call_args
        assert kwargs["output_format"] == "docx"

    def test_missing_output_format_defaults_to_console(self, monkeypatch, plugin_module):
        fake_sandbox = self._patch_sandbox(monkeypatch)
        plugin_module.plugin.preview_ui_action(
            fields={"source_language": "ja", "target_language": "en"}, professor="fake", model=None,
        )
        _, kwargs = fake_sandbox.translation_service.build_prompts.call_args
        assert kwargs["output_format"] == "console"

    def test_preserve_tables_and_toc_reflected_in_preview(self, monkeypatch, plugin_module):
        fake_sandbox = self._patch_sandbox(monkeypatch)
        plugin_module.plugin.preview_ui_action(
            fields={"preserve_tables": "true", "toc": "true"}, professor="fake", model=None,
        )
        assert fake_sandbox.translation_service.tables is True
        assert fake_sandbox.translation_service.toc is True


class TestExtensionUiHooksIntegration:
    """A language-extension plugin (e.g. translation-ea, a separate
    git-ignored repo not present in this checkout) contributes its own
    composer fields via register_extension_ui_hooks() rather than a
    per-plugin UiField declaration — see ExtensionUiHooks's docstring in
    src/runtime/ui_action.py. These tests stand in for that real extension
    with a fake registration, since the real one can't be exercised here."""

    @pytest.fixture(autouse=True)
    def _isolated_registry(self, monkeypatch):
        monkeypatch.setattr(ui_action_module, "_EXTENSION_UI_HOOKS", {})

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
        opts = kwargs["opts"]
        with open(opts.output_file, "w", encoding="utf-8") as f:
            f.write("translated content")

    def test_run_ui_action_applies_registered_hook_for_destination_token(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        calls = []
        register_extension_ui_hooks(
            action_id="translate",
            token="ja",  # matches the "ja" -> Japanese registration this test file's own fixture adds
            fields=[UiField(name="kanbun", label="Use Kanbun conventions", kind="checkbox", required=False)],
            apply=lambda sandbox, fields: calls.append((sandbox, fields)),
        )
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "en", "target_language": "ja",
                "file_path": str(src_file), "file_name": "upload.txt",
                "kanbun": "true",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert len(calls) == 1
        sandbox_arg, fields_arg = calls[0]
        assert sandbox_arg is fake_sandbox
        assert fields_arg["kanbun"] == "true"

    def test_run_ui_action_does_not_apply_hook_for_a_different_destination(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        calls = []
        register_extension_ui_hooks(
            action_id="translate", token="ja", fields=[], apply=lambda sandbox, fields: calls.append(1),
        )
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert calls == []

    def test_run_ui_action_tolerates_no_hook_registered(self, monkeypatch, plugin_module, tmp_path):
        # The normal case for any installation without a matching language
        # extension plugin — must not raise.
        self._patch_sandbox(monkeypatch, translate_side_effect=self._write_output)
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")

        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "en", "target_language": "ja",
                "file_path": str(src_file), "file_name": "upload.txt",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )  # no exception

    def test_preview_ui_action_applies_registered_hook(self, monkeypatch, plugin_module):
        fake_sandbox = MagicMock()
        fake_sandbox.translation_service = MagicMock()
        fake_sandbox.translation_service.build_prompts.return_value = ("SYS", "USR")
        fake_sandbox.translation_service._get_model.return_value = "resolved-model"
        fake_sandbox.image_translation_service = MagicMock()
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        calls = []
        register_extension_ui_hooks(
            action_id="translate", token="ja", fields=[], apply=lambda sandbox, fields: calls.append(fields),
        )
        plugin_module.plugin.preview_ui_action(
            fields={"source_language": "en", "target_language": "ja", "kanbun": "true"},
            professor="fake", model=None,
        )
        assert calls == [{"source_language": "en", "target_language": "ja", "kanbun": "true"}]


class TestTheAbstractReachesBothWays:
    """-a on the command line and the Abstract box in the browser are one setting.

    The command line asks for the text at a terminal; the browser is given a
    box to type it in. What travels inwards from either is the abstract itself,
    which is what lets a background job have one at all — before this, the
    prompt lived deep inside the handler, where a job would have been left
    waiting at a question nobody could see.
    """

    def test_what_is_typed_in_the_box_reaches_the_translation(
        self, monkeypatch, plugin_module, tmp_path
    ):
        """The gap that made the box worth adding: it has to arrive."""
        fake_sandbox = MagicMock()
        fake_sandbox.translation_service = MagicMock()
        fake_sandbox.image_translation_service = MagicMock()
        # The real one writes the output file; run_ui_action refuses to report
        # success without it.
        fake_sandbox.translate_document.side_effect = (
            lambda *a, **kw: Path(kw["opts"].output_file).write_text("done")
        )
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")
        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "abstract": "  What the paper argues  ",
            },
            professor="fake", model=None, on_progress=None,
            output_dir=str(tmp_path / "out"),
        )
        assert (fake_sandbox.translate_document.call_args.kwargs["abstract_text"]
                == "What the paper argues")

    def test_an_empty_box_is_no_abstract_rather_than_an_empty_one(
        self, monkeypatch, plugin_module, tmp_path
    ):
        fake_sandbox = MagicMock()
        fake_sandbox.translation_service = MagicMock()
        fake_sandbox.image_translation_service = MagicMock()
        # The real one writes the output file; run_ui_action refuses to report
        # success without it.
        fake_sandbox.translate_document.side_effect = (
            lambda *a, **kw: Path(kw["opts"].output_file).write_text("done")
        )
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        src_file = tmp_path / "upload.txt"
        src_file.write_text("hello", encoding="utf-8")
        plugin_module.plugin.run_ui_action(
            fields={
                "source_language": "ja", "target_language": "en",
                "file_path": str(src_file), "file_name": "upload.txt",
                "abstract": "   ",
            },
            professor="fake", model=None, on_progress=None,
            output_dir=str(tmp_path / "out"),
        )
        assert fake_sandbox.translate_document.call_args.kwargs["abstract_text"] is None

    def test_the_preview_shows_the_abstract_that_would_be_sent(
        self, monkeypatch, plugin_module
    ):
        """A preview without the context is a preview of a different request."""
        fake_sandbox = MagicMock()
        fake_sandbox.translation_service = MagicMock()
        fake_sandbox.image_translation_service = MagicMock()
        fake_sandbox.translation_service.build_prompts.return_value = ("sys", "usr")
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        plugin_module.plugin.preview_ui_action(
            {"source_language": "ja", "target_language": "en",
             "abstract": "What the paper argues"},
            professor="fake", model=None,
        )
        sent = fake_sandbox.translation_service.build_prompts.call_args.args[0]
        assert "What the paper argues" in sent

    def test_the_web_form_offers_a_box_for_the_text(self, plugin_module):
        field = next(f for f in plugin_module.ui_action.fields if f.name == "abstract")
        assert field.kind == "text", "a checkbox would have nowhere to type the abstract"
        assert field.required is False

    def _run_cli(self, plugin_module, has_abstract):
        import argparse
        sandbox = MagicMock()
        sandbox._collect_multiline.return_value = "The paper argues X"
        args = argparse.Namespace(
            abstract=has_abstract, custom_text=True, input_file=None, page_nums=None,
            workers=1, spread=False, scanned=False, output_file=None,
            auto_save=False, progressive_save=False, custom_font=None,
            preserve_media=False, font_size=None, dry_run=False,
        )
        plugin_module._execute_translate(sandbox, args, "Japanese", "English")
        return sandbox

    def test_the_command_line_still_asks_at_the_terminal(self, plugin_module):
        """Moving the prompt outwards must not have quietly removed it."""
        sandbox = self._run_cli(plugin_module, has_abstract=True)
        labels = [c.args[0] for c in sandbox._collect_multiline.call_args_list]
        assert any("Abstract" in label for label in labels)
        # And the text it collected is what went in, not a flag.
        assert "The paper argues X" in sandbox.translate_custom_text.call_args.args

    def test_without_the_flag_nothing_is_asked(self, plugin_module):
        sandbox = self._run_cli(plugin_module, has_abstract=False)
        labels = [c.args[0] for c in sandbox._collect_multiline.call_args_list]
        assert not any("Abstract" in label for label in labels)
        assert None in sandbox.translate_custom_text.call_args.args
