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

if __name__ == '__main__':
    from src.cli import main
    main()
