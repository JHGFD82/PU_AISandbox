#!/usr/bin/env python3
"""Entry point for the PU AI Sandbox CLI. See README.md for full usage."""

import sys

# Checked before anything else is imported, and deliberately written without
# any modern syntax so that it still runs on an old Python rather than
# failing to parse.
#
# The sandbox reads its settings files with `tomllib`, which only joined the
# Python standard library in version 3.11. On an older Python the first thing
# that happens is a bare "ModuleNotFoundError: No module named 'tomllib'" —
# which is accurate, and tells someone who isn't a programmer nothing at all
# about what to do next. This says it plainly instead.
_REQUIRED = (3, 11)

if sys.version_info < _REQUIRED:
    _running = ".".join(str(part) for part in sys.version_info[:3])
    sys.stderr.write(
        "\n"
        "PU AI Sandbox needs Python 3.11 or newer.\n"
        "\n"
        "    You are running: Python " + _running + "\n"
        "    Location:        " + sys.executable + "\n"
        "\n"
        "This isn't something you've done wrong — it's that some of the tools\n"
        "the sandbox relies on only exist in newer versions of Python.\n"
        "\n"
        "What to do:\n"
        "\n"
        "  1. Check whether a newer Python is already installed:\n"
        "         python3.11 --version\n"
        "     If that prints a version, use it to create the environment:\n"
        "         python3.11 -m venv .venv\n"
        "         source .venv/bin/activate\n"
        "         pip install -r requirements.txt\n"
        "\n"
        "  2. If it isn't installed, get it from https://www.python.org/downloads/\n"
        "     (or, on a Mac with Homebrew: brew install python@3.11)\n"
        "\n"
        "See the Setup section of README.md if you get stuck.\n"
        "\n"
    )
    raise SystemExit(1)

def _set_up_if_needed():
    """Offer first-time setup before anything that needs a settings file loads.

    This has to happen here, ahead of importing the rest of the sandbox.
    Several modules work out where the settings file is *while they are
    being imported*, and on a freshly downloaded copy there is no answer to
    that question yet — so importing them first would fail before there was
    any chance to ask. Only ``src.paths`` and the two setup modules are
    touched, none of which need to know where anything lives.

    Returns:
        ``True`` if setup just ran, meaning the command should not carry on
        — the files it would read have only now been put in place.
    """
    from src import paths

    if paths.is_installed():
        return False

    # Someone asking for help hasn't committed to anything yet.
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        return False

    # The commands that *are* setup have to be allowed through, or this
    # would answer them with a different setup than the one being asked
    # for — which is exactly what happened to `webui setup`: it was
    # intercepted here and replaced with the terminal version.
    words = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if words[:2] in (["settings", "setup"], ["webui", "setup"]):
        return False

    if not sys.stdin.isatty():
        sys.stderr.write(
            "\nThis copy of the sandbox hasn't been set up yet, and there's "
            "nobody at the keyboard to ask where your files should be kept.\n"
            "Run this once, from a terminal:\n"
            "    python main.py settings setup\n\n"
        )
        raise SystemExit(1)

    from src.setup_prompts import run_interactive_setup
    run_interactive_setup()
    print("\nSetup is done — run your command again and it will work.")
    return True


if __name__ == '__main__':
    if not _set_up_if_needed():
        from src.cli import main
        main()
