"""conftest for plugins/webui/tests — ensures every module this plugin registers is in sys.modules.

Mirrors plugins/prompt/tests/conftest.py's ``_register()`` pattern, extended
to also cover the flat, dot-free names used for this plugin's internal-only
files (``_pu_webui_auth``, ``_pu_webui_conversation``, ``_pu_webui_app``) —
see ``plugins/webui/src/app.py``'s module docstring for why those aren't
registered under a dotted ``pu_plugin.webui.*`` path like
``pu_plugin.webui.settings`` is.
"""

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
        spec.loader.exec_module(mod)
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])


# Same dependency order plugin.py itself uses.
_register("pu_plugin.webui.settings", "src/settings.py")
_register("_pu_webui_file_picker", "src/file_picker.py")
_register("_pu_webui_auth", "src/auth.py")
_register("_pu_webui_conversation", "src/conversation.py")
_register("_pu_webui_attachments", "src/attachments.py")
_register("_pu_webui_export", "src/export.py")
_register("_pu_webui_jobs", "src/jobs.py")
_register("src.services.chat_service", "src/services/chat_service.py")
_register("_pu_webui_app", "src/app.py")

# Also import the real plugin.py module (not just the src/*.py files above,
# which conftest registers directly to avoid needing the full plugin loader).
# plugin.py's own _register() calls are no-ops for anything already
# registered above, but its module-level register_env_field() calls for
# webui.passphrase_hash/webui.session_secret only run once this actually
# imports — without this, tests that depend on those two paths being known
# (e.g. the /api/settings routes' "which paths are directly editable" check)
# would only pass if some other test file happened to import plugin.py
# first, which depends on alphabetical test-file collection order rather
# than being reliably true.
import plugins.webui.plugin  # noqa: F401
