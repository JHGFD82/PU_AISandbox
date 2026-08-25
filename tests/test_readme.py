"""Tests for README.md — the vocabulary it defines, and the output it quotes.

Two things here are worth holding still. The README names an installation's
three locations — `package`, `settings` and `data` — and uses those names
throughout; a reader who meets `data` on page four has to have been told what
it means on page one. And it quotes what the terminal prints during setup,
which is a copy of something the code produces and so can drift away from it
silently, as it already did once.

Nothing here pins a sentence. The wording is the maintainer's and moves.
"""

import re
from pathlib import Path

import pytest

_README = Path(__file__).resolve().parent.parent / "README.md"
LOCATIONS = ("package", "settings", "data")


@pytest.fixture
def readme():
    return _README.read_text(encoding="utf-8")


class TestTheThreeLocationsAreNamedConsistently:

    def test_each_one_is_defined(self, readme):
        """Before the reader is expected to recognise it later on."""
        heading = readme.index("## Where everything lives")
        block = readme[heading:readme.index("## Architecture")]
        for name in LOCATIONS:
            assert f"**`{name}`**" in block, name

    def test_they_are_defined_before_they_are_used(self, readme):
        defined = readme.index("## Where everything lives")
        for name in LOCATIONS:
            first_use = readme.index(f"`{name}` location")
            assert defined < first_use, f"{name} is used before it is explained"

    def test_the_name_is_always_written_as_code(self, readme):
        """`settings` the location, against settings the ordinary word. Only
        the location sense is meant here, which is what "folder"/"location"
        after it marks.

        Fenced blocks are skipped. What is inside one is either a command or a
        copy of what the program prints, and neither is prose to be marked up
        — the program does not print backticks.
        """
        loose = []
        fenced = False
        for number, line in enumerate(readme.splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            for found in re.finditer(r"(.?)\b(package|settings|data) (folder|location)",
                                     line):
                if found.group(1) != "`":
                    loose.append(f"{number}: {line.strip()[:72]}")
        assert not loose, "written as prose rather than as a name:\n" + "\n".join(loose)

    def test_one_word_follows_each_of_them(self, readme):
        """"the `settings` location" and "the `settings` folder" are the same
        thing said two ways, and reading them as two is the whole problem."""
        qualifiers = set(re.findall(r"`(?:package|settings|data)` (folder|location)",
                                    readme))
        assert qualifiers <= {"location"}, f"also says: {sorted(qualifiers)}"


class TestWhatTheReadmeQuotesIsWhatIsPrinted:
    """The README shows what setup prints on finding an existing installation.
    That is a copy, and it drifted: it still showed "Your files are already
    here" long after the code had stopped saying it — and showed it as
    terminal output when the layout quoted was the browser's."""

    def _quoted(self, readme):
        block = readme[readme.index("Found an existing installation"):]
        return block[:block.index("```")]

    def test_the_block_is_still_there_to_check(self, readme):
        assert "Found an existing installation" in readme

    def test_it_opens_the_way_setup_opens(self, readme, tmp_path, monkeypatch):
        from src import first_run, paths, setup_prompts

        root = tmp_path / "extras"
        (root / paths.DATA_DIRNAME).mkdir(parents=True)
        (root / paths.SETTINGS_FILENAME).write_text(
            '[professors.p0]\nname = "P0"\nkey = "sk"\n', encoding="utf-8")
        printed = "\n".join(setup_prompts._describe(first_run.inspect_extras(root)))

        quoted = self._quoted(readme)
        # The headings are what a reader matches the screen against.
        for heading in ("Settings location", "Data location"):
            assert heading in printed, f"the code no longer prints {heading!r}"
            assert heading in quoted, f"the README no longer shows {heading!r}"

    def test_it_is_not_shown_as_something_it_is_not(self, readme):
        """The browser's own layout was once quoted as terminal output."""
        quoted = self._quoted(readme)
        assert "[ Use these files ]" not in quoted
        assert "›" not in quoted

    def test_the_question_underneath_is_the_one_that_is_asked(self, readme):
        from src import setup_prompts

        source = Path(setup_prompts.__file__).read_text(encoding="utf-8")
        asked = re.search(r'_ask_yes_no\(\s*"\\n([^"]+)"', source)
        assert asked, "setup no longer asks in a way this can find"
        assert asked.group(1) in self._quoted(readme)


class TestCommandsAreWrittenTheWayTheyAreRun:

    def test_python3_is_only_for_the_one_that_needs_it(self, readme):
        """start.py runs before there is an environment, so it is whatever
        python the computer has. main.py finds its own."""
        for number, line in enumerate(readme.splitlines(), 1):
            if "python3" in line:
                assert "start.py" in line, f"{number}: {line.strip()[:72]}"
