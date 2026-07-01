"""Pytest configuration for the transcription base plugin.

Adds the main PU_AISandbox repo root to sys.path so that ``src.*`` imports
resolve correctly when running tests from within this plugin directory.

Also pre-registers this plugin's runtime mixin module into sys.modules so
tests can import SandboxProcessor with the transcribe-specific methods
already composed in.
"""

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_PLUGIN_DIR = Path(__file__).resolve().parent


def _register(module_name: str, rel_path: str) -> None:
    """Inject a plugin-owned module into sys.modules under its src.* name."""
    if module_name in sys.modules:
        return
    path = _PLUGIN_DIR / rel_path
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    # Expose the module as an attribute on its parent package so that
    # pytest's string-based monkeypatch.setattr("src.runtime.<name>.*")
    # resolution (which walks package attributes, not sys.modules) works.
    # Falls back to importlib.import_module because src.runtime may not
    # have been imported yet at this point.
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is None:
            try:
                parent = importlib.import_module(parts[0])
            except ImportError:
                parent = None
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])


_register("src.runtime.image_handler", "src/runtime/image_handler.py")
