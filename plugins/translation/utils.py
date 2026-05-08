"""Lightweight CLI utility helpers for the translation plugin.

This module has no side effects on import — no sys.modules injection,
no service instantiation.  It can be imported safely by tests without
triggering the full plugin load sequence.
"""

import argparse
import re


def validate_page_nums(value: str) -> str:
    """Validate page numbers input format for CLI arguments."""
    if not re.match(r"^\d+(-\d+)?(\s*,\s*\d+(-\d+)?)*$", value):
        raise argparse.ArgumentTypeError(
            "Invalid page selection. Use a page number (5), a range (1-10), "
            "or a comma-separated mix (4,15-17,20,30-55)."
        )
    return value
