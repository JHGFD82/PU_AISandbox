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


@pytest.fixture
def linux(monkeypatch):
    """This computer, as far as the picker can tell, is a Linux desktop with nothing installed."""
    monkeypatch.setattr(picker.sys, "platform", "linux")
    monkeypatch.setattr(picker.shutil, "which", lambda name: None)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


class TestWhichComputersHaveAChooser:
    def test_a_mac_has_one_when_osascript_is_there(self, monkeypatch):
        monkeypatch.setattr(picker.sys, "platform", "darwin")
        monkeypatch.setattr(picker.shutil, "which", lambda name: "/usr/bin/osascript")
        assert picker.available() is True
        monkeypatch.setattr(picker.shutil, "which", lambda name: None)
        assert picker.available() is False

    def test_windows_has_one_when_powershell_is_there(self, monkeypatch):
        monkeypatch.setattr(picker.sys, "platform", "win32")
        monkeypatch.setattr(picker.os, "name", "nt")
        monkeypatch.setattr(picker.shutil, "which", lambda name: name if name == "powershell.exe" else None)
        assert picker.available() is True

    def test_a_linux_desktop_with_zenity_has_one(self, linux, monkeypatch):
        monkeypatch.setattr(picker.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
        assert picker._linux_helper() == "/usr/bin/zenity"
        assert picker.available() is True

    def test_a_linux_desktop_without_one_can_fall_back_on_pythons_own(self, linux, monkeypatch):
        monkeypatch.setattr(picker.importlib.util, "find_spec", lambda name: object())
        assert picker._linux_helper() is None
        assert picker.available() is True

    def test_python_without_tkinter_has_none_to_fall_back_on(self, linux, monkeypatch):
        monkeypatch.setattr(picker.importlib.util, "find_spec", lambda name: None)
        assert picker.available() is False

    def test_a_linux_computer_with_no_screen_has_none(self, linux, monkeypatch):
        """Reached over a plain remote connection, there is nothing to draw a window on."""
        monkeypatch.delenv("DISPLAY")
        monkeypatch.setattr(picker.importlib.util, "find_spec", lambda name: object())
        assert picker._linux_helper() is None
        assert picker._has_tkinter() is False


class TestTheOtherChoosers:
    def test_windows_folder_chooser_opens_where_asked(self):
        script = picker._windows_command("folder", "Pick one", "C:\\Users")[-1]
        assert "$dialog.SelectedPath = 'C:\\Users'" in script

    def test_windows_file_chooser_opens_where_asked(self):
        script = picker._windows_command("file", "Pick one", "C:\\Users")[-1]
        assert "$dialog.InitialDirectory = 'C:\\Users'" in script

    def test_zenity_with_nowhere_given_lets_it_choose(self):
        assert picker._linux_command("/usr/bin/zenity", "file", "Pick one", None) == [
            "/usr/bin/zenity", "--file-selection", "--title", "Pick one"]

    def test_kdialog_with_nowhere_given_opens_at_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        command = picker._linux_command("/usr/bin/kdialog", "file", "Pick one", None)
        assert command[-2:] == ["--getopenfilename", str(tmp_path)]

    @pytest.mark.parametrize("kind,chooser", [("folder", "askdirectory"), ("file", "askopenfilename")])
    def test_pythons_own_chooser_is_told_what_and_where(self, kind, chooser):
        command = picker._tkinter_command(kind, "Pick one", "/home/heller")
        assert command[:2] == [sys.executable, "-c"]
        assert f"chooser.{chooser}(title='Pick one', initialdir='/home/heller')" in command[2]


class TestChoosingOnEachComputer:
    @pytest.fixture
    def ran(self, monkeypatch):
        commands = []
        monkeypatch.setattr(picker, "available", lambda: True)
        monkeypatch.setattr(picker, "_run", lambda command: commands.append(command) or None)
        return commands

    def test_linux_uses_the_desktops_chooser(self, linux, monkeypatch, ran):
        monkeypatch.setattr(picker.shutil, "which", lambda name: "/usr/bin/kdialog" if name == "kdialog" else None)
        picker.choose("folder")
        assert ran[0][0] == "/usr/bin/kdialog"

    def test_linux_without_one_uses_pythons_own(self, linux, ran):
        picker.choose("file")
        assert ran[0][:2] == [sys.executable, "-c"]

    def test_windows_uses_powershell(self, monkeypatch, ran):
        monkeypatch.setattr(picker.sys, "platform", "win32")
        monkeypatch.setattr(picker.os, "name", "nt")
        monkeypatch.setattr(picker.shutil, "which", lambda name: "C:\\powershell.exe")
        picker.choose("folder")
        assert "-STA" in ran[0]


class TestShowingAFolder:
    """reveal() opens a folder the person already has, in the file browser they know."""

    @pytest.fixture
    def opened(self, monkeypatch):
        commands = []
        monkeypatch.setattr(picker.subprocess, "Popen", lambda command, **kw: commands.append(command))
        return commands

    @pytest.mark.parametrize("platform,program", [
        ("darwin", "open"), ("win32", "explorer"), ("linux", "xdg-open"),
    ])
    def test_each_computer_uses_its_own_file_browser(self, monkeypatch, opened, tmp_path, platform, program):
        monkeypatch.setattr(picker.sys, "platform", platform)
        assert picker.reveal(tmp_path) is True
        assert opened == [[program, str(tmp_path)]]

    def test_a_folder_that_is_not_there_is_not_opened(self, opened, tmp_path):
        assert picker.reveal(tmp_path / "gone") is False
        assert opened == []

    def test_a_computer_that_cannot_open_one_says_no_rather_than_failing(self, monkeypatch, tmp_path):
        def no_file_browser(command, **kw):
            raise OSError("xdg-open: not found")
        monkeypatch.setattr(picker.subprocess, "Popen", no_file_browser)
        assert picker.reveal(tmp_path) is False

    @pytest.mark.parametrize("platform", ["darwin", "win32"])
    def test_macs_and_windows_always_have_one(self, monkeypatch, platform):
        monkeypatch.setattr(picker.sys, "platform", platform)
        assert picker.can_reveal() is True

    def test_linux_has_one_when_xdg_open_is_there(self, linux, monkeypatch):
        assert picker.can_reveal() is False
        monkeypatch.setattr(picker.shutil, "which", lambda name: "/usr/bin/xdg-open")
        assert picker.can_reveal() is True
