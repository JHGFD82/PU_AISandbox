"""Pytest configuration for the transcription base plugin.

Adds the main PU_AISandbox repo root to sys.path so that ``src.*`` imports
resolve correctly when running tests from within this plugin directory.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
