"""Every command this project tells someone to run has to be a command it has.

A message that ends "run this to fix it" is only worth printing if running that
does something. These are read by people who are stuck — no API key, a netID
already taken — and who have no way of telling a real instruction from one that
was renamed years ago. Following a wrong one costs them the only lead they had
and answers with an argparse error about an invalid choice.

That is not hypothetical. Four such messages named `python main.py env ...`,
under a command called `env` that no longer exists; two of them went further and
named `remove-professor` and `set`, which have never existed under any command
at all. The one instruction offered to somebody whose key was missing could not
be followed.

So the messages are checked against the real parser — the same one built at
startup, with the real plugins loaded — rather than against a list kept by hand,
which would be one more thing able to drift.
"""

import argparse
import re
from pathlib import Path

import pytest

from src.cli import create_argument_parser
from src.runtime.plugin_loader import load_plugins

_ROOT = Path(__file__).resolve().parent.parent
_PLUGINS_DIR = _ROOT / "plugins"

# Where a person-facing message could be written. Tests are left out: they quote
# commands to check them, including deliberately wrong ones.
_SEARCHED = ("src", "plugins", "scripts")
_SKIP_PARTS = {"tests", ".venv", "__pycache__", "node_modules", ".git"}

# "python main.py ..." up to the end of the line or the end of the string it
# sits in. Quotes end it because a message is usually built from several.
_INVOCATION = re.compile(r"python\s+main\.py\s+([^\n\"'`]*)")

# Stands for something the reader fills in — their netID, a month, a total.
# Never a command, so it is stepped over rather than checked.
_PLACEHOLDER = re.compile(r"[<{\[]")

# The leading part of a word that could be a command name. Stops at the closing
# bracket, comma or escaped newline that so often follows one in source. It has
# to start with a letter, which is what tells a command from the bare "..." that
# stands for the rest of a command line in a docstring.
_COMMAND_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")


def _real_commands() -> dict[str, set[str]]:
    """Return every command the parser accepts, each with its own subcommands.

    Built from the real parser rather than written down here, so that a command
    added, renamed or removed is reflected without anyone remembering to.

    Returns:
        ``{command: {subcommand, ...}}``. The set is empty for a command that
        takes no subcommand of its own.
    """
    parser = create_argument_parser(load_plugins(_PLUGINS_DIR))
    commands: dict[str, set[str]] = {}

    def subparser_actions(p: argparse.ArgumentParser):
        return [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]

    for action in subparser_actions(parser):
        for name, sub in action.choices.items():
            names: set[str] = set()
            for inner in subparser_actions(sub):
                names.update(inner.choices)
            commands[name] = names
    return commands


def _source_files() -> list[Path]:
    files = []
    for folder in _SEARCHED:
        for path in (_ROOT / folder).rglob("*.py"):
            if _SKIP_PARTS & set(path.parts):
                continue
            files.append(path)
    return files


def _invocations() -> list[tuple[Path, int, str, list[str]]]:
    """Find every "python main.py ..." written anywhere in the source.

    Returns:
        One entry per occurrence: its file, line number, the text as written,
        and the words after ``main.py`` with flags and fill-in-the-blanks
        removed.
    """
    found = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for match in _INVOCATION.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            words = []
            for raw in match.group(1).split():
                if raw.startswith("-") or _PLACEHOLDER.search(raw):
                    continue
                # A command word as it appears in source is rarely clean: it may
                # carry a closing bracket, a comma, or the "\n" that ends the
                # line of the message it sits in. Keep the leading run that
                # could be a command and drop the rest.
                cleaned = _COMMAND_WORD.match(raw)
                if cleaned:
                    words.append(cleaned.group(0))
            found.append((path, line, match.group(0).strip(), words))
    return found


@pytest.fixture(scope="module")
def commands():
    return _real_commands()


def _describe(path: Path, line: int, text: str) -> str:
    return f"{path.relative_to(_ROOT)}:{line} says: {text!r}"


class TestTheParserHasSomethingToCheckAgainst:
    """If these ever fail, everything below is passing for the wrong reason."""

    def test_the_real_commands_were_found(self, commands):
        assert {"usage", "settings"} <= set(commands)

    def test_subcommands_were_found_too(self, commands):
        assert "add-professor" in commands["settings"]

    def test_messages_were_found_to_check(self):
        assert _invocations(), "found no 'python main.py ...' anywhere, so nothing was checked"


class TestEveryCommandNamedInAMessageExists:

    def test_the_command_word_is_real(self, commands):
        wrong = []
        for path, line, text, words in _invocations():
            # A professor's netID comes before the command, so the command is
            # the first word that is one. Nothing to check when the whole line
            # is flags or blanks, as in "python main.py --help".
            if not words or any(w in commands for w in words):
                continue
            wrong.append(_describe(path, line, text))
        assert not wrong, (
            "these name a command that does not exist:\n  " + "\n  ".join(wrong)
        )

    def test_the_subcommand_is_real_too(self, commands):
        wrong = []
        for path, line, text, words in _invocations():
            position = next((i for i, w in enumerate(words) if w in commands), None)
            if position is None:
                continue
            command = words[position]
            rest = words[position + 1:]
            if not rest or not commands[command]:
                continue
            if rest[0] not in commands[command]:
                wrong.append(
                    f"{_describe(path, line, text)} — '{command}' has no "
                    f"'{rest[0]}' (it has: {', '.join(sorted(commands[command]))})"
                )
        assert not wrong, (
            "these name a subcommand that does not exist:\n  " + "\n  ".join(wrong)
        )
