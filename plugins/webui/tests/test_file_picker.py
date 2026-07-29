"""Tests for the "Browse…" button's file chooser (plugins/webui/src/file_picker.py).

Nothing here opens a real window — a test that waited for somebody to click
something would never finish. What is checked instead is everything around
the window: that the right chooser is picked for the computer it's running
on, that the command handed to it says what was meant (including when a
folder name contains a quote), that a chooser closed without a choice is
reported as an ordinary answer rather than a failure, and that a computer
with no chooser at all says so instead of pretending.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

picker = sys.modules["_pu_webui_file_picker"]


class TestWhereTheChooserOpens:
    def test_walks_up_to_a_folder_that_exists(self, tmp_path):
        # The sandbox's suggested folder usually isn't there yet, and a
        # chooser told to open somewhere that isn't there complains.
        assert picker._existing_ancestor(tmp_path / "not" / "yet") == str(tmp_path)

    def test_a_file_opens_in_the_folder_holding_it(self, tmp_path):
        target = tmp_path / "settings.shared.toml"
        target.write_text("", encoding="utf-8")
        assert picker._existing_ancestor(target) == str(tmp_path)

    def test_nothing_typed_means_let_the_chooser_decide(self):
        assert picker._existing_ancestor(None) is None
        assert picker._existing_ancestor("") is None

    def test_expands_a_home_shortcut(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert picker._existing_ancestor("~/nowhere") == str(tmp_path)


class TestQuotingWhatIsAsked:
    def test_a_quote_in_the_prompt_cannot_end_the_applescript_string(self):
        assert picker._quote_applescript('say "hi"') == '"say \\"hi\\""'

    def test_a_backslash_is_escaped_before_anything_else(self):
        assert picker._quote_applescript("a\\b") == '"a\\\\b"'

    def test_a_quote_in_a_powershell_string_is_doubled(self):
        assert picker._quote_powershell("it's here") == "'it''s here'"


class TestBuildingTheCommand:
    def test_macos_folder_command_says_folder_and_where(self):
        script = picker._macos_command("folder", "Pick one", "/Users")[2]
        assert "choose folder" in script
        assert 'default location POSIX file "/Users"' in script
        # Brought to the front, or the window opens behind the browser and
        # reads as the button having done nothing.
        assert "tell me to activate" in script

    def test_macos_never_asks_to_control_another_program(self):
        """Controlling System Events needs a permission a professor can deny forever."""
        script = picker._macos_command("folder", "Pick one", None)[2]
        assert "System Events" not in script
        assert "tell application" not in script

    def test_macos_file_command_says_file(self):
        script = picker._macos_command("file", "Pick one", None)[2]
        assert "choose file" in script
        assert "default location" not in script

    def test_windows_folder_command_runs_on_the_thread_the_dialog_needs(self):
        command = picker._windows_command("folder", "Pick one", "C:\\Users")
        assert "-STA" in command
        assert "-NoProfile" in command
        assert "FolderBrowserDialog" in command[-1]

    def test_windows_file_command_opens_a_file_dialog(self):
        command = picker._windows_command("file", "Pick one", None)
        assert "OpenFileDialog" in command[-1]

    def test_zenity_is_told_the_folder_to_open_in(self):
        command = picker._linux_command("/usr/bin/zenity", "folder", "Pick one", "/home/x")
        assert "--directory" in command
        assert any(part.startswith("--filename=/home/x") for part in command)

    def test_kdialog_uses_its_own_flags(self):
        command = picker._linux_command("/usr/bin/kdialog", "folder", "Pick one", "/home/x")
        assert "--getexistingdirectory" in command


class TestReadingTheAnswer:
    def test_a_path_on_the_output_is_the_answer(self, monkeypatch):
        monkeypatch.setattr(
            picker.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, "/Users/x/Documents\n", ""),
        )
        assert picker._run(["anything"]) == "/Users/x/Documents"

    def test_a_closed_window_is_an_answer_not_an_error(self, monkeypatch):
        # Cancelling is what osascript reports as a non-zero exit.
        monkeypatch.setattr(
            picker.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "User canceled. (-128)"),
        )
        assert picker._run(["anything"]) is None

    def test_no_output_at_all_is_also_a_cancellation(self, monkeypatch):
        monkeypatch.setattr(
            picker.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, "  \n", ""),
        )
        assert picker._run(["anything"]) is None

    def test_a_window_nobody_ever_answers_is_abandoned(self, monkeypatch):
        def never_returns(*a, **k):
            raise subprocess.TimeoutExpired(cmd="chooser", timeout=1)

        monkeypatch.setattr(picker.subprocess, "run", never_returns)
        assert picker._run(["anything"]) is None

    def test_a_chooser_that_will_not_start_is_not_a_crash(self, monkeypatch):
        def missing(*a, **k):
            raise OSError("no such program")

        monkeypatch.setattr(picker.subprocess, "run", missing)
        assert picker._run(["anything"]) is None


class TestChoosing:
    def test_the_chosen_path_comes_back_as_a_path(self, monkeypatch):
        monkeypatch.setattr(picker, "available", lambda: True)
        monkeypatch.setattr(picker, "_run", lambda command: "/Users/x/Documents")
        assert str(picker.choose("folder")) == "/Users/x/Documents"

    def test_cancelling_gives_nothing_back(self, monkeypatch):
        monkeypatch.setattr(picker, "available", lambda: True)
        monkeypatch.setattr(picker, "_run", lambda command: None)
        assert picker.choose("folder") is None

    def test_a_computer_with_no_chooser_says_so(self, monkeypatch):
        monkeypatch.setattr(picker, "available", lambda: False)
        with pytest.raises(picker.PickerUnavailable):
            picker.choose("folder")

    def test_only_folders_and_files_can_be_asked_for(self):
        with pytest.raises(ValueError):
            picker.choose("printer")
