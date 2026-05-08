"""
Pytest configuration for the translation base plugin.

Ensures the PU_AISandbox repo root is on sys.path so that src.* imports
(src.cli, src.runtime.plugin_loader, etc.) resolve when pytest is run from
this plugin's directory (plugins/translation/).
"""

import sys
from pathlib import Path

# This file lives at plugins/translation/conftest.py.
# parents[0] = plugins/translation/
# parents[1] = plugins/
# parents[2] = PU_AISandbox/  ← main repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
