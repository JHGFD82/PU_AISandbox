"""Runtime execution modules used by the CLI controller.

Deliberately does NOT import SandboxProcessor here: SandboxProcessor's class
statement discovers plugin-registered runtime mixins from sys.modules at
first-import time (see sandbox_processor._discover_plugin_mixins), which is
only safe once load_plugins() has run every plugin's _register() calls.
Importing the src.runtime package (e.g. via `from .runtime import
load_plugins` in src/cli.py) must not force-load sandbox_processor.py before
that happens. Import it directly — `from src.runtime.sandbox_processor
import SandboxProcessor` — which is what every plugin's run() method already
does, after load_plugins() has completed.
"""

from .dispatch_plugin import DispatchPlugin
from .info_commands import handle_info_commands
from .plugin import ModePlugin
from .plugin_loader import load_plugins

__all__ = ["DispatchPlugin", "handle_info_commands", "load_plugins", "ModePlugin"]
