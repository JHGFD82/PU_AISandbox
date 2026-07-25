"""Tests for TranscriptionPlugin.run_ui_action and its module-level ui_action
declaration — the webui composer's "Transcribe an image" action.

See docs/webui-plugin-plan.md section 10, and
plugins/translation/tests/test_plugin_ui_action.py for the same pattern
applied to the translate action.
"""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.errors import CLIError

_PLUGIN_FILE = Path(__file__).resolve().parents[1] / "plugin.py"


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location("pu_plugin.transcription.plugin", _PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pu_plugin.transcription.plugin"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def plugin_module():
    return _load_plugin_module()


class TestUiActionDeclaration:

    def test_declares_expected_fields_in_order(self, plugin_module):
        names = [f.name for f in plugin_module.ui_action.fields]
        assert names == ["target_language", "file", "output_format", "notes"]

    def test_file_field_allows_a_folder_of_images(self, plugin_module):
        # transcribe's CLI already accepts -i pointed at a folder of scans
        # (process_image_folder) — the composer's file field must offer the
        # same, not just a single image, per UiField.allow_folder's docstring.
        field = next(f for f in plugin_module.ui_action.fields if f.name == "file")
        assert field.allow_folder is True

    def test_output_format_offers_the_same_writers_as_the_cli_extension_switch(self, plugin_module):
        # -o result.docx / -o result.pdf / -o result.md / -o result.txt on
        # the CLI already picks the writer by extension (save_translation_output
        # in src/output/file_output.py) — this field is a dropdown for the
        # same choice, not a new capability, so it must offer exactly those.
        field = next(f for f in plugin_module.ui_action.fields if f.name == "output_format")
        assert field.kind == "select"
        assert field.required is False
        assert {c["value"] for c in field.choices} == {"txt", "docx", "pdf", "md"}

    def test_id_and_command_are_transcribe(self, plugin_module):
        assert plugin_module.ui_action.id == "transcribe"
        assert plugin_module.ui_action.command == "transcribe"

    def test_progress_verb_is_correctly_spelled_gerund(self, plugin_module):
        assert plugin_module.ui_action.progress_verb == "Transcribing"

    def test_only_language_and_file_are_required(self, plugin_module):
        required = {f.name for f in plugin_module.ui_action.fields if f.required}
        assert required == {"target_language", "file"}

    def test_ui_action_is_reachable_from_the_plugin_instance(self, plugin_module):
        # See the matching test/comment in
        # plugins/translation/tests/test_translation_plugin_ui_action.py —
        # jobs.py looks for ui_action on the plugin instance, not this module.
        assert plugin_module.plugin.ui_action is plugin_module.ui_action


class TestRunUiAction:

    def _patch_sandbox(self, monkeypatch, process_image_side_effect=None, process_folder_side_effect=None):
        fake_sandbox = MagicMock()
        fake_sandbox.image_processor_service = MagicMock()
        if process_image_side_effect is not None:
            fake_sandbox.process_image.side_effect = process_image_side_effect
        if process_folder_side_effect is not None:
            fake_sandbox.process_image_folder.side_effect = process_folder_side_effect
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        return fake_sandbox

    def _write_output_file(self, file_path, target_language, output_file=None, **kw):
        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("extracted text")

    def test_success_writes_output_and_builds_result(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, process_image_side_effect=self._write_output_file)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")
        output_dir = tmp_path / "job_output"

        result = plugin_module.plugin.run_ui_action(
            fields={"target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg"},
            professor="fake", model=None, on_progress=None, output_dir=str(output_dir),
        )

        assert result.output_filename == "scan_English.txt"
        assert os.path.exists(result.output_path)
        assert "English" in result.summary
        fake_sandbox.process_image.assert_called_once()

    def test_output_format_defaults_to_txt_when_omitted(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, process_image_side_effect=self._write_output_file)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")

        result = plugin_module.plugin.run_ui_action(
            fields={"target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg"},
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "job_output"),
        )
        assert result.output_filename == "scan_English.txt"

    @pytest.mark.parametrize("requested_format,expected_ext", [
        ("docx", ".docx"),
        ("pdf", ".pdf"),
        ("md", ".md"),
        ("txt", ".txt"),
    ])
    def test_output_format_selects_matching_extension(
        self, monkeypatch, plugin_module, tmp_path, requested_format, expected_ext,
    ):
        self._patch_sandbox(monkeypatch, process_image_side_effect=self._write_output_file)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")

        result = plugin_module.plugin.run_ui_action(
            fields={
                "target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg",
                "output_format": requested_format,
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "job_output"),
        )
        assert result.output_filename == f"scan_English{expected_ext}"
        assert result.output_path.endswith(expected_ext)

    def test_unrecognized_output_format_falls_back_to_txt(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, process_image_side_effect=self._write_output_file)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")

        result = plugin_module.plugin.run_ui_action(
            fields={
                "target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg",
                "output_format": "not-a-real-format",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "job_output"),
        )
        assert result.output_filename == "scan_English.txt"

    def test_result_includes_session_token_usage(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, process_image_side_effect=self._write_output_file)
        fake_sandbox.token_tracker.get_session_usage.return_value = {
            "prompt_tokens": 800, "completion_tokens": 150, "total_tokens": 950, "total_cost": 0.021,
        }
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")

        result = plugin_module.plugin.run_ui_action(
            fields={"target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg"},
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "job_output"),
        )
        assert result.prompt_tokens == 800
        assert result.completion_tokens == 150
        assert result.cost == 0.021

    def test_folder_input_delegates_to_process_image_folder_with_progress(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, process_folder_side_effect=self._write_output_file)
        folder = tmp_path / "scans"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"fake")
        output_dir = tmp_path / "job_output"

        def progress(done, total):
            pass

        result = plugin_module.plugin.run_ui_action(
            fields={"target_language": "en", "file_path": str(folder), "file_name": "scans"},
            professor="fake", model=None, on_progress=progress, output_dir=str(output_dir),
        )
        fake_sandbox.process_image_folder.assert_called_once()
        _, kwargs = fake_sandbox.process_image_folder.call_args
        assert kwargs["on_progress"] is progress
        assert os.path.exists(result.output_path)

    def test_notes_applied_to_both_prompts(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = self._patch_sandbox(monkeypatch, process_image_side_effect=self._write_output_file)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")

        plugin_module.plugin.run_ui_action(
            fields={
                "target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg",
                "notes": "Ignore the page-number stamp in the corner.",
            },
            professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
        )
        assert fake_sandbox.image_processor_service.system_note == "Ignore the page-number stamp in the corner."
        assert fake_sandbox.image_processor_service.user_note == "Ignore the page-number stamp in the corner."

    def test_missing_file_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch)
        with pytest.raises(CLIError, match="No file was attached"):
            plugin_module.plugin.run_ui_action(
                fields={"target_language": "en"},
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )

    def test_invalid_language_code_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")
        with pytest.raises(CLIError, match="Invalid target language"):
            plugin_module.plugin.run_ui_action(
                fields={"target_language": "not-a-real-code", "file_path": str(src_file), "file_name": "scan.jpg"},
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )

    def test_sampling_params_passed_through_to_sandbox(self, monkeypatch, plugin_module, tmp_path):
        fake_sandbox = MagicMock()
        fake_sandbox.image_processor_service = MagicMock()
        fake_sandbox.process_image.side_effect = self._write_output_file
        sandbox_cls = MagicMock(return_value=fake_sandbox)
        monkeypatch.setattr("src.runtime.sandbox_processor.SandboxProcessor", sandbox_cls)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")

        plugin_module.plugin.run_ui_action(
            fields={
                "target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg",
                "temperature": "0.2", "top_p": "0.8", "max_tokens": "1024",
            },
            professor="fake", model="gpt-4o", on_progress=None, output_dir=str(tmp_path / "out"),
        )
        sandbox_cls.assert_called_once_with(
            "fake", model="gpt-4o", temperature=0.2, top_p=0.8, max_tokens=1024,
        )

    def test_invalid_max_tokens_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, process_image_side_effect=self._write_output_file)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")
        with pytest.raises(CLIError, match="max tokens"):
            plugin_module.plugin.run_ui_action(
                fields={
                    "target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg",
                    "max_tokens": "a lot",
                },
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )

    def test_no_output_produced_raises_cli_error(self, monkeypatch, plugin_module, tmp_path):
        self._patch_sandbox(monkeypatch, process_image_side_effect=lambda *a, **kw: None)
        src_file = tmp_path / "scan.jpg"
        src_file.write_bytes(b"fake")
        with pytest.raises(CLIError, match="no output file"):
            plugin_module.plugin.run_ui_action(
                fields={"target_language": "en", "file_path": str(src_file), "file_name": "scan.jpg"},
                professor="fake", model=None, on_progress=None, output_dir=str(tmp_path / "out"),
            )


class TestPreviewUiAction:
    """The composer's live two-pane prompt preview — must never raise on
    incomplete input (called after every keystroke, before a language or
    file is necessarily chosen). See UiPromptPreview's docstring in
    src/runtime/ui_action.py."""

    def _patch_sandbox(self, monkeypatch):
        fake_sandbox = MagicMock()
        fake_sandbox.image_processor_service = MagicMock()
        fake_sandbox.image_processor_service.build_prompts.return_value = ("SYS", "USR")
        fake_sandbox.image_processor_service._get_model.return_value = "resolved-model"
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor",
            MagicMock(return_value=fake_sandbox),
        )
        return fake_sandbox

    def test_returns_prompts_and_note(self, monkeypatch, plugin_module):
        self._patch_sandbox(monkeypatch)
        preview = plugin_module.plugin.preview_ui_action(
            fields={"target_language": "en"}, professor="fake", model=None,
        )
        assert preview.system_prompt == "SYS"
        assert preview.user_prompt == "USR"
        assert preview.model == "resolved-model"
        assert preview.note is not None

    def test_missing_language_does_not_raise(self, monkeypatch, plugin_module):
        self._patch_sandbox(monkeypatch)
        preview = plugin_module.plugin.preview_ui_action(fields={}, professor="fake", model=None)
        assert preview.system_prompt == "SYS"

    def test_invalid_language_code_falls_back_to_placeholder_not_error(self, monkeypatch, plugin_module):
        self._patch_sandbox(monkeypatch)
        preview = plugin_module.plugin.preview_ui_action(
            fields={"target_language": "not-a-real-code"}, professor="fake", model=None,
        )
        assert preview.system_prompt == "SYS"

    def test_notes_applied(self, monkeypatch, plugin_module):
        fake_sandbox = self._patch_sandbox(monkeypatch)
        plugin_module.plugin.preview_ui_action(
            fields={"notes": "Ignore the stamp."}, professor="fake", model=None,
        )
        assert fake_sandbox.image_processor_service.system_note == "Ignore the stamp."
        assert fake_sandbox.image_processor_service.user_note == "Ignore the stamp."
