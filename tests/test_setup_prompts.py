"""Tests for the terminal side of first-time setup.

The thing being checked is mostly *how little* is asked. Someone upgrading
should be able to press Enter once; only a genuine first install should have
to answer a question about where anything goes.
"""

from pathlib import Path

import pytest

from src import paths
from src.errors import CLIError
from src import setup_prompts


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    package = tmp_path / "package"
    templates = package / "templates"
    templates.mkdir(parents=True)
    (templates / "settings.template").write_text("# starting point\n", encoding="utf-8")
    (templates / "model_catalog.template.json").write_text('{"models": {}}\n', encoding="utf-8")

    monkeypatch.setattr(paths, "PACKAGE_ROOT", package)
    monkeypatch.setattr(paths, "TEMPLATES_DIR", templates)
    monkeypatch.setattr(paths, "INSTALL_MARKER", package / ".installation")
    monkeypatch.setattr(paths, "DEFAULT_EXTRAS_ROOT", tmp_path / "PU_AISandbox_data")
    return package


class _Conversation:
    """Records what was shown and replays scripted answers."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.shown: list[str] = []
        self.asked: list[str] = []

    def input_fn(self, prompt: str) -> str:
        self.asked.append(prompt)
        if not self.answers:
            raise AssertionError(f"asked more than expected: {prompt!r}")
        return self.answers.pop(0)

    def print_fn(self, *parts) -> None:
        self.shown.append(" ".join(str(p) for p in parts))

    @property
    def transcript(self) -> str:
        """Everything shown, with line wrapping flattened.

        Assertions here are about what the person is told, not about where
        the text happens to wrap — a sentence broken across two lines still
        reads as one sentence.
        """
        return " ".join(" ".join(self.shown).split())


def _make_setup(root: Path, *, people: int = 1, months: int = 0):
    root.mkdir(parents=True, exist_ok=True)
    tables = "\n".join(
        f'[professors.p{i}]\nname = "Person {i}"\nkey = "sk-{i}"\n' for i in range(people)
    )
    (root / paths.SETTINGS_FILENAME).write_text(tables, encoding="utf-8")
    (root / paths.MODEL_CATALOG_FILENAME).write_text('{"models": {}}', encoding="utf-8")
    data = root / paths.DATA_DIRNAME
    data.mkdir(exist_ok=True)
    for i in range(months):
        archive = data / "archives" / "p0"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / f"2026-{i + 1:02d}.json").write_text("{}", encoding="utf-8")
    return root


class TestFilesAlreadyOutsideThePackage:
    """The ordinary upgrade: the package was replaced, the files are fine."""

    def test_one_keypress_carries_them_forward(self):
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=2, months=4)
        talk = _Conversation("")
        result = setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert result == paths.DEFAULT_EXTRAS_ROOT
        assert paths.is_installed() is True

    def test_nothing_is_moved_or_rewritten(self):
        root = _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=2)
        before = (root / paths.SETTINGS_FILENAME).read_bytes()
        talk = _Conversation("")
        setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert (root / paths.SETTINGS_FILENAME).read_bytes() == before


class TestGenuineFirstInstall:
    def test_asks_where_and_creates_it(self, tmp_path):
        talk = _Conversation("")          # accept the default
        result = setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert result == paths.DEFAULT_EXTRAS_ROOT
        assert (result / paths.SETTINGS_FILENAME).is_file()
        assert (result / paths.DATA_DIRNAME).is_dir()
        assert "add-professor" in talk.transcript    # says what to do next

    def test_a_typed_folder_is_used_once_confirmed(self, tmp_path):
        chosen = tmp_path / "my files" / "sandbox"
        talk = _Conversation(str(chosen), "y")
        assert setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn) == chosen
        assert (chosen / paths.SETTINGS_FILENAME).is_file()

    def test_a_typed_path_is_shown_in_full_before_anything_is_made(self, tmp_path):
        chosen = tmp_path / "somewhere"
        talk = _Conversation(str(chosen), "y")
        setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert f"That folder is: {chosen}" in talk.transcript

    def test_a_word_that_is_not_a_path_does_not_silently_become_a_folder(self, tmp_path, monkeypatch):
        """Typing 'y' at this prompt used to create a folder called 'y'.

        Whatever is typed here is a path, and a bare word is a perfectly
        valid *relative* one — so it landed wherever the command happened
        to be run from, with the person's API keys inside, and they would
        never find it again. Declining now re-asks instead.
        """
        monkeypatch.chdir(tmp_path)
        good = tmp_path / "proper place"
        talk = _Conversation("y", "n", str(good), "y")
        result = setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert result == good
        assert not (tmp_path / "y").exists()

    def test_a_folder_inside_the_package_is_called_out(self, _isolate, tmp_path):
        """The one place these files must not go."""
        inside = _isolate / "my_data"
        elsewhere = tmp_path / "outside"
        talk = _Conversation(str(inside), "n", str(elsewhere), "y")
        setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert "inside the sandbox folder itself" in talk.transcript
        assert "would delete them" in talk.transcript

    def test_a_typed_path_that_is_a_file_is_rejected_and_re_asked(self, tmp_path):
        a_file = tmp_path / "not-a-folder"
        a_file.write_text("x", encoding="utf-8")
        good = tmp_path / "good"
        talk = _Conversation(str(a_file), str(good), "y")
        assert setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn) == good
        assert "is a file, not a folder" in talk.transcript

    def test_typing_the_path_of_an_unlisted_setup_carries_it_forward(self, tmp_path):
        """Never initialise over real settings, however we arrived at them."""
        existing = _make_setup(tmp_path / "hidden away", people=5)
        before = (existing / paths.SETTINGS_FILENAME).read_bytes()
        talk = _Conversation(str(existing), "y")
        result = setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert result == existing
        assert (existing / paths.SETTINGS_FILENAME).read_bytes() == before
        assert "already holds a setup" in talk.transcript


class TestCloudSyncWarning:
    def test_warns_when_the_chosen_folder_is_synced(self, tmp_path, monkeypatch):
        synced = tmp_path / "home" / "Dropbox" / "sandbox"
        monkeypatch.setattr(paths, "DEFAULT_EXTRAS_ROOT", synced)
        talk = _Conversation("")
        setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        transcript = talk.transcript
        assert "Dropbox" in transcript
        assert "API keys" in transcript

    def test_warning_does_not_prevent_the_choice(self, tmp_path, monkeypatch):
        """Where someone keeps their own files is their decision."""
        synced = tmp_path / "home" / "Dropbox" / "sandbox"
        monkeypatch.setattr(paths, "DEFAULT_EXTRAS_ROOT", synced)
        talk = _Conversation("")
        assert setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn) == synced
        assert (synced / paths.SETTINGS_FILENAME).is_file()


class TestAnswerHandling:
    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", ""])
    def test_yes_answers(self, answer):
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=1)
        talk = _Conversation(answer)
        setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert paths.is_installed() is True

    def test_unrecognised_answer_is_re_asked(self):
        _make_setup(paths.DEFAULT_EXTRAS_ROOT, people=1)
        talk = _Conversation("maybe", "")
        setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
        assert "Please answer y or n" in talk.transcript


class TestFailures:
    def test_a_folder_that_cannot_be_created_reports_clearly(self, tmp_path, monkeypatch):
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file", encoding="utf-8")
        monkeypatch.setattr(paths, "DEFAULT_EXTRAS_ROOT", tmp_path / "unused")
        talk = _Conversation(str(blocker / "inside"), "y")
        with pytest.raises(CLIError, match="Could not prepare"):
            setup_prompts.run_interactive_setup(talk.input_fn, talk.print_fn)
