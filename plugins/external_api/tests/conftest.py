"""conftest for plugins/external_api/tests — registers plugin modules."""

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
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])


# Register prompts subpackage first so that relative imports in the service work
_register(
    "src.services.external_api_call_service_prompts",
    "src/services/prompts/fragments.py",
)
# Make it accessible under the path the service's relative import expects
if "src.services.external_api_call_service_prompts" in sys.modules:
    import types
    _pkg = types.ModuleType("src.services.prompts")
    sys.modules.setdefault("src.services.prompts", _pkg)
    _frags = sys.modules["src.services.external_api_call_service_prompts"]
    sys.modules["src.services.prompts.fragments"] = _frags
    _pkg.fragments = _frags  # type: ignore[attr-defined]

_register(
    "src.services.external_api_call_service",
    "src/services/external_api_call_service.py",
)
