"""Tests for updating the sandbox from the browser.

There is one outcome all of this exists to prevent: somebody pressing a button
and being left with a sandbox that will not start, no terminal open, and no
idea what happened. So most of what is tested here is what an update
*refuses* to do, and what it leaves behind when it cannot finish.

The tests that matter most build real git repositories in a temp folder and
run the real thing against them. A recorded call proves an argument was
passed; only a real repository proves it did what the argument is for.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

upgrade = sys.modules["_pu_webui_upgrade"]
git_tool = sys.modules["_pu_webui_git_tool"]

_REPO_ROOT = Path(__file__).resolve().parents[3]

needs_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed on this computer")


@pytest.fixture(autouse=True)
def _forget_the_launcher():
    """Make each test load start.py again from wherever it has put one.

    ``_launcher()`` keeps what it loaded, which is right in a running sandbox
    and wrong here: every test points PACKAGE_ROOT somewhere else.
    """
    upgrade._launcher_module = None
    yield
    upgrade._launcher_module = None


def _git(where: Path, *arguments: str) -> str:
    """Run git in *where* and return what it said, failing loudly if it did not work."""
    done = subprocess.run(["git", "-C", str(where), *arguments],
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _commit(where: Path, message: str) -> None:
    _git(where, "add", "-A")
    _git(where, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)


def _a_published_sandbox(tmp_path: Path) -> Path:
    """Make something that looks enough like this repository to update from.

    The real start.py is copied in rather than a stand-in written: it is the
    file that owns the fingerprint, the stamp and the sandbox's own Python,
    and a stand-in would only prove that the stand-in agrees with itself.
    """
    published = tmp_path / "published"
    published.mkdir()
    (published / "requirements.txt").write_text("tomlkit\n", encoding="utf-8")
    shutil.copy(_REPO_ROOT / "start.py", published / "start.py")
    (published / "README.md").write_text("first\n", encoding="utf-8")
    _git(published, "init", "-q", "-b", "main")
    _commit(published, "first")
    return published


def _a_copy_of_it(tmp_path: Path, published: Path) -> Path:
    """Clone *published* the way somebody's own installation is a clone."""
    copy = tmp_path / "copy"
    subprocess.run(["git", "clone", "-q", str(published), str(copy)],
                   check=True, capture_output=True)
    # The private environment start.py builds. Only the folder is needed here;
    # what is tested is the note written into it.
    (copy / ".venv").mkdir()
    return copy


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A published sandbox and a copy of it, with the copy pointed at."""
    published = _a_published_sandbox(tmp_path)
    copy = _a_copy_of_it(tmp_path, published)
    monkeypatch.setattr(upgrade, "PACKAGE_ROOT", copy)
    return published, copy


def _publish(published: Path, message: str, **files: str) -> None:
    """Add another change to the published sandbox."""
    for name, text in files.items():
        (published / name.replace("__", ".")).write_text(text, encoding="utf-8")
    if not files:
        (published / "README.md").write_text(message + "\n", encoding="utf-8")
    _commit(published, message)


class TestWhatItRefusesToDo:
    """Every one of these has to leave somebody knowing what to do next, so
    each test reads the message rather than only checking that it refused."""

    @needs_git
    def test_a_copy_that_was_not_fetched_with_git(self, tmp_path, monkeypatch):
        """Downloading the ZIP file is the ordinary way to get this wrong."""
        plain = tmp_path / "downloaded"
        plain.mkdir()
        monkeypatch.setattr(upgrade, "PACKAGE_ROOT", plain)
        said = upgrade.why_this_copy_cannot_be_updated()
        assert said is not None
        assert "ZIP" in said
        assert "start.py" in said, "it does not say what to do instead"

    @needs_git
    def test_a_copy_sitting_inside_somebody_elses_repository(self, tmp_path, monkeypatch):
        """git looks upwards. Without this, the button would quietly move
        whatever repository the sandbox happened to be unpacked inside."""
        outer = tmp_path / "someones-work"
        outer.mkdir()
        _git(outer, "init", "-q", "-b", "main")
        (outer / "theirs.txt").write_text("mine\n", encoding="utf-8")
        _commit(outer, "theirs")
        inside = outer / "PU_AISandbox"
        inside.mkdir()
        monkeypatch.setattr(upgrade, "PACKAGE_ROOT", inside)

        said = upgrade.why_this_copy_cannot_be_updated()
        assert said is not None
        assert str(outer) in said, "it does not say which repository it found"

    @needs_git
    def test_files_changed_here(self, sandbox):
        """The likeliest one by far: a settings file edited where it sits."""
        _published, copy = sandbox
        (copy / "requirements.txt").write_text("tomlkit\nsomething-else\n",
                                               encoding="utf-8")
        said = upgrade.why_this_copy_cannot_be_updated()
        assert said is not None
        assert "requirements.txt" in said, "it does not say which file"
        assert "preferences.toml" in said, "it does not say where they belong instead"

    @needs_git
    def test_it_names_the_files_rather_than_counting_them(self, sandbox):
        _published, copy = sandbox
        (copy / "README.md").write_text("changed\n", encoding="utf-8")
        (copy / "requirements.txt").write_text("changed\n", encoding="utf-8")
        said = upgrade.why_this_copy_cannot_be_updated()
        assert "README.md" in said and "requirements.txt" in said

    @needs_git
    def test_a_branch_following_nothing(self, sandbox):
        _published, copy = sandbox
        _git(copy, "switch", "-q", "-c", "something-of-my-own")
        said = upgrade.why_this_copy_cannot_be_updated()
        assert said is not None
        assert "something-of-my-own" in said

    @needs_git
    def test_a_copy_parked_on_one_version(self, sandbox):
        _published, copy = sandbox
        _git(copy, "checkout", "-q", "--detach", "HEAD")
        said = upgrade.why_this_copy_cannot_be_updated()
        assert said is not None
        assert "git switch main" in said, "it does not say how to get back"

    @needs_git
    def test_changes_of_its_own(self, sandbox):
        """Somebody has been working on the code. Moving it forward would
        have to merge, which is not a thing to do from a browser."""
        _published, copy = sandbox
        (copy / "mine.txt").write_text("mine\n", encoding="utf-8")
        _commit(copy, "something of my own")

        found = upgrade.check_for_updates()
        assert found.blocked is not None
        assert "terminal" in found.blocked

    def test_a_git_that_cannot_be_used(self, monkeypatch):
        """A Mac without the developer tools has a git that is found and then
        fails. The message has to be about git, not about updating."""
        monkeypatch.setattr(
            git_tool, "usable_git",
            lambda purpose: (_ for _ in ()).throw(git_tool.GitUnusable("no git here")))
        assert upgrade.why_this_copy_cannot_be_updated() == "no git here"
        assert upgrade.package_version() is None, "it should not raise either"


class TestTheCommandsItRuns:
    """Recorded rather than run, so the flags can be read."""

    def _recorded(self, monkeypatch, returns=""):
        calls = []

        def fake(git, folder, *arguments, timeout):
            calls.append({"arguments": list(arguments), "timeout": timeout,
                          "folder": folder})
            return subprocess.CompletedProcess([], 0, returns, "")

        monkeypatch.setattr(git_tool, "run_git", fake)
        monkeypatch.setattr(git_tool, "usable_git", lambda purpose: "/usr/bin/git")
        return calls

    def test_it_never_uses_git_pull(self):
        """A pull that cannot be done cleanly stops halfway and leaves the
        folder full of conflict markers. Nobody gets out of that from a
        browser, so the word is not in the file at all."""
        source = Path(upgrade.__file__).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        assert '"pull"' not in code

    def test_it_moves_forward_only(self, monkeypatch, tmp_path):
        calls = self._recorded(monkeypatch)
        monkeypatch.setattr(upgrade, "why_this_copy_cannot_be_updated", lambda: None)
        monkeypatch.setattr(upgrade, "dependencies_are_current", lambda: True)
        monkeypatch.setattr(upgrade, "_record_what_is_installed", lambda: None)
        monkeypatch.setattr(upgrade, "package_version",
                            lambda: upgrade.Version("new1234", "2026-09-18", "x"))
        upgrade.apply_update(lambda step, label: None)

        merges = [c for c in calls if c["arguments"][:1] == ["merge"]]
        assert merges, "it never got as far as moving the files"
        assert "--ff-only" in merges[0]["arguments"]

    def test_every_command_is_given_a_time_to_give_up(self, monkeypatch):
        """A git waiting on something nobody can see would otherwise hold a
        request open for ever."""
        calls = self._recorded(monkeypatch)
        upgrade.check_for_updates()
        assert calls
        for call in calls:
            assert isinstance(call["timeout"], int) and call["timeout"] > 0, call

    def test_git_is_never_given_a_chance_to_ask_for_a_password(self):
        """Asked with nobody at the keyboard, git waits until it is stopped."""
        surroundings = git_tool.quiet_environment()
        assert surroundings["GIT_TERMINAL_PROMPT"] == "0"
        assert surroundings["GIT_ASKPASS"] == ""
        assert "BatchMode=yes" in surroundings["GIT_SSH_COMMAND"]


@needs_git
class TestMovingForward:
    """The ordinary case, against real repositories."""

    def test_it_finds_what_has_been_published(self, sandbox):
        published, _copy = sandbox
        _publish(published, "fix: the first thing")
        _publish(published, "feat: the second thing")

        found = upgrade.check_for_updates()
        assert found.blocked is None and found.offline is None
        assert found.behind == 2
        assert found.changes == ["feat: the second thing", "fix: the first thing"]
        assert found.current is not None and found.latest is not None
        assert found.current.sha != found.latest.sha

    def test_a_copy_with_nothing_waiting_says_so(self, sandbox):
        found = upgrade.check_for_updates()
        assert found.behind == 0
        assert found.blocked is None

    def test_it_says_when_new_software_will_be_needed(self, sandbox):
        published, _copy = sandbox
        _publish(published, "deps: another one",
                 requirements__txt="tomlkit\nsomething-new\n")
        found = upgrade.check_for_updates()
        assert found.requirements_changing is True

    def test_and_when_it_will_not_be(self, sandbox):
        published, _copy = sandbox
        _publish(published, "docs: nothing but words")
        found = upgrade.check_for_updates()
        assert found.requirements_changing is False

    def test_the_files_actually_move(self, sandbox, monkeypatch):
        published, copy = sandbox
        _publish(published, "feat: a new thing")
        monkeypatch.setattr(upgrade, "dependencies_are_current", lambda: True)

        said = []
        done = upgrade.apply_update(lambda step, label: said.append((step, label)))

        assert (copy / "README.md").read_text() == "feat: a new thing\n"
        assert done.was != done.now
        assert done.installed_dependencies is False
        assert [step for step, _ in said] == [1, 2, 3, 4], said

    def test_nothing_is_installed_when_nothing_new_is_needed(self, sandbox, monkeypatch):
        """A change to the documentation must not cost a minute of installing."""
        published, copy = sandbox
        _publish(published, "docs: nothing but words")
        # The note start.py keeps, saying the list as it stands is installed.
        launcher = upgrade._launcher()
        Path(launcher.STAMP).write_text(launcher.requirements_fingerprint(),
                                        encoding="utf-8")
        tried = []
        monkeypatch.setattr(upgrade, "_install_dependencies",
                            lambda on_line: tried.append(True))

        done = upgrade.apply_update(lambda step, label: None)
        assert tried == [], "it installed software nothing had asked for"
        assert done.installed_dependencies is False

    def test_new_software_is_installed_when_it_is(self, sandbox, monkeypatch):
        published, copy = sandbox
        _publish(published, "deps: another one",
                 requirements__txt="tomlkit\nsomething-new\n")
        installed = []
        monkeypatch.setattr(upgrade, "_install_dependencies",
                            lambda on_line: installed.append(True))

        done = upgrade.apply_update(lambda step, label: None)
        assert installed == [True]
        assert done.installed_dependencies is True


@needs_git
class TestWhenInstallingFails:
    """The one failure that leaves anything behind, and the whole reason the
    record of what is installed is written last."""

    def _a_failing_install(self, monkeypatch):
        def fail(on_line):
            on_line("Collecting something-new")
            raise upgrade.UpgradeError("No matching distribution found")
        monkeypatch.setattr(upgrade, "_install_dependencies", fail)

    def test_it_says_the_files_are_in_place_and_offers_a_way_back(self, sandbox,
                                                                  monkeypatch):
        published, copy = sandbox
        before = _git(copy, "rev-parse", "HEAD")
        _publish(published, "deps: another one",
                 requirements__txt="tomlkit\nsomething-new\n")
        self._a_failing_install(monkeypatch)

        with pytest.raises(upgrade.StoppedPartway) as raised:
            upgrade.apply_update(lambda step, label: None)
        assert raised.value.was == before[:7]
        assert "No matching distribution" in str(raised.value)

    def test_the_files_are_left_alone_rather_than_undone(self, sandbox, monkeypatch):
        """Installing fails for passing reasons far more often than lasting
        ones, so trying again is offered before putting it back."""
        published, copy = sandbox
        _publish(published, "deps: another one",
                 requirements__txt="tomlkit\nsomething-new\n")
        self._a_failing_install(monkeypatch)

        with pytest.raises(upgrade.StoppedPartway):
            upgrade.apply_update(lambda step, label: None)
        assert "something-new" in (copy / "requirements.txt").read_text()

    def test_what_is_installed_is_not_written_down(self, sandbox, monkeypatch):
        """This is the safety net. An unwritten note is what makes the next
        `python3 start.py` notice and finish the installing by itself."""
        published, copy = sandbox
        _publish(published, "deps: another one",
                 requirements__txt="tomlkit\nsomething-new\n")
        self._a_failing_install(monkeypatch)

        with pytest.raises(upgrade.StoppedPartway):
            upgrade.apply_update(lambda step, label: None)
        assert not (copy / ".venv" / ".requirements-stamp").exists()

    def test_trying_again_installs_rather_than_declaring_success(self, sandbox,
                                                                 monkeypatch):
        """By the second attempt the files have already moved, so nothing
        changed in *that* pass. Asking what the update contained would skip
        the installing and then write the note saying it had been done."""
        published, copy = sandbox
        _publish(published, "deps: another one",
                 requirements__txt="tomlkit\nsomething-new\n")
        self._a_failing_install(monkeypatch)
        with pytest.raises(upgrade.StoppedPartway):
            upgrade.apply_update(lambda step, label: None)

        installed = []
        monkeypatch.setattr(upgrade, "_install_dependencies",
                            lambda on_line: installed.append(True))
        done = upgrade.apply_update(lambda step, label: None)
        assert installed == [True], "the second attempt skipped the installing"
        assert done.installed_dependencies is True

    def test_putting_it_back_puts_it_back(self, sandbox, monkeypatch):
        published, copy = sandbox
        before = _git(copy, "rev-parse", "HEAD")
        _publish(published, "deps: another one",
                 requirements__txt="tomlkit\nsomething-new\n")
        self._a_failing_install(monkeypatch)
        with pytest.raises(upgrade.StoppedPartway) as raised:
            upgrade.apply_update(lambda step, label: None)

        upgrade.put_the_files_back(raised.value.was)
        assert (copy / "requirements.txt").read_text() == "tomlkit\n"
        assert _git(copy, "rev-parse", "HEAD") == before

    def test_putting_it_back_leaves_somebodys_own_files_alone(self, sandbox,
                                                              monkeypatch):
        """git clean would take these away, and on this computer they are
        somebody's work rather than clutter."""
        published, copy = sandbox
        before = _git(copy, "rev-parse", "HEAD")
        _publish(published, "feat: a new thing")
        monkeypatch.setattr(upgrade, "dependencies_are_current", lambda: True)
        upgrade.apply_update(lambda step, label: None)
        (copy / "my-notes.txt").write_text("do not delete me\n", encoding="utf-8")

        upgrade.put_the_files_back(before)
        assert (copy / "my-notes.txt").exists()


@needs_git
class TestOneAnswerAboutWhatIsInstalled:
    """start.py owns the fingerprint, the note and the sandbox's own Python.
    Two copies of any of those is two answers that have to agree for ever."""

    def test_this_module_does_not_work_it_out_for_itself(self):
        source = Path(upgrade.__file__).read_text(encoding="utf-8")
        assert "sha256" not in source, (
            "the fingerprint has grown a second implementation; start.py is "
            "the one place it belongs")

    def test_the_note_it_writes_is_the_one_start_py_reads(self, sandbox):
        published, copy = sandbox
        launcher = upgrade._launcher()
        upgrade._record_what_is_installed()

        note = (copy / ".venv" / ".requirements-stamp").read_text(encoding="utf-8")
        assert note == launcher.requirements_fingerprint()
        assert launcher.environment_is_ready() is False, (
            "there is no Python in this .venv, so start.py should still say no")

    def test_and_it_is_written_after_an_update(self, sandbox, monkeypatch):
        published, copy = sandbox
        _publish(published, "deps: another one",
                 requirements__txt="tomlkit\nsomething-new\n")
        monkeypatch.setattr(upgrade, "_install_dependencies", lambda on_line: None)
        upgrade.apply_update(lambda step, label: None)

        launcher = upgrade._launcher()
        note = (copy / ".venv" / ".requirements-stamp").read_text(encoding="utf-8")
        assert note == launcher.requirements_fingerprint()


class TestOnlyOneAtATime:
    """The settings page can be open on its own and inside the chat window's
    settings panel at the same time. Two updates in one folder is the worst
    thing that could happen here."""

    def test_a_second_one_is_turned_away(self, monkeypatch):
        upgrade._only_one_at_a_time.acquire()
        try:
            assert upgrade.an_update_is_running() is True
            with pytest.raises(upgrade.UpgradeError, match="already running"):
                upgrade.apply_update(lambda step, label: None)
        finally:
            upgrade._only_one_at_a_time.release()

    def test_and_the_lock_is_let_go_of_afterwards(self, monkeypatch):
        monkeypatch.setattr(upgrade, "why_this_copy_cannot_be_updated",
                            lambda: "no, for a reason")
        with pytest.raises(upgrade.UpgradeError):
            upgrade.apply_update(lambda step, label: None)
        assert upgrade.an_update_is_running() is False
