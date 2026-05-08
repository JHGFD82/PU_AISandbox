"""conftest for plugins/prompt/tests — ensures prompt_service is in sys.modules."""

import importlib.util
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent.parent


def _register(module_name: str, rel_path: str) -> None:
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
    # Make the module accessible as an attribute of its parent package so that
    # pytest's monkeypatch.setattr("src.services.prompt_service.*") works.
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])


_register("src.services.prompt_service", "src/services/prompt_service.py")
