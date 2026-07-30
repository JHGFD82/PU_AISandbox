"""Finds and loads every installed plugin from the plugins/ directory.

Runs once at application startup, before the command-line parser is built.
Any plugin that fails to import, or that doesn't provide everything a
plugin needs to provide (see ``ModePlugin`` in ``plugin.py``), is skipped
with a warning printed to the log — a broken or incomplete plugin never
crashes the rest of the application.
"""

import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .plugin import ModePlugin

logger = logging.getLogger(__name__)


def load_plugins(plugins_dir: Path) -> "dict[str, ModePlugin]":
    """Find every valid plugin and map each of its commands to that plugin.

    Looks inside every immediate subfolder of ``plugins_dir`` for a
    ``plugin.py`` file. Subfolders without one are simply skipped — they
    aren't treated as plugins at all.

    Args:
        plugins_dir: The folder to search for plugins, e.g. the project's
                     top-level ``plugins/`` directory.

    Returns:
        A dictionary mapping each CLI command name (e.g. ``'translate'``) to
        the plugin object that handles it.
    """
    result: dict[str, "ModePlugin"] = {}
    if not plugins_dir.is_dir():
        return result
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir():
            continue
        plugin_file = entry / "plugin.py"
        if not plugin_file.exists():
            continue
        _load_one(entry.name, plugin_file, result)
    return result



def _declares_model_roles(p: object, plugin_name: str) -> bool:
    """Check that a plugin has said which models its work should use.

    Required, and deliberately so. Without a declaration the sandbox has
    nothing to go on and falls through to the cheapest model in the catalog —
    which keeps working, so nobody notices, and the answers quietly come from
    whichever model happens to be least expensive. That is how a translation
    command ended up defaulting to a four-billion-parameter model with only a
    line in the terminal to say so.

    A plugin that genuinely calls no AI model says so with an empty
    ``model_roles = {}``. That is accepted: the point is that the decision was
    made rather than forgotten.

    Args:
        p: The plugin object being loaded.
        plugin_name: Its directory name, for the message.

    Returns:
        ``True`` if the plugin may load. ``False`` if it may not, with the
        reason and the fix already logged.
    """
    roles = getattr(p, "model_roles", None)
    if roles is None:
        logger.error(
            "Plugin '%s': no 'model_roles' declared — skipped. Every plugin must say "
            "which models its work should use, because without it the sandbox falls "
            "back to whichever model is cheapest. Add a dict of role name to "
            "ModelRole (see src/runtime/model_role.py), or 'model_roles = {}' if this "
            "plugin calls no AI model at all.",
            plugin_name,
        )
        return False

    if not isinstance(roles, dict):
        logger.error(
            "Plugin '%s': 'model_roles' must be a dict of role name to ModelRole, "
            "not %s — skipped.",
            plugin_name, type(roles).__name__,
        )
        return False

    for role_name, role in roles.items():
        if not hasattr(role, "models") or not getattr(role, "models", None):
            logger.error(
                "Plugin '%s': model role '%s' names no models — skipped. Give it a "
                "ModelRole with at least one model name, best first.",
                plugin_name, role_name,
            )
            return False
    return True

def _load_one(
    plugin_name: str,
    plugin_file: Path,
    result: "dict[str, ModePlugin]",
) -> None:
    """Load a single plugin's code and add its commands to the shared results.

    Args:
        plugin_name: The plugin's folder name (e.g. ``'translation'``), used
                     only in log messages to identify which plugin a warning
                     refers to.
        plugin_file: The full path to that plugin's ``plugin.py`` file.
        result: The command-name-to-plugin dictionary being built up by
                ``load_plugins()``; this plugin's commands are added to it
                directly rather than returned.
    """
    module_name = f"pu_plugin.{plugin_name}.plugin"
    try:
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec is None or spec.loader is None:
            logger.warning(
                "Plugin '%s': could not create import spec — skipped.", plugin_name
            )
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning(
            "Plugin '%s': import failed (%s) — skipped.", plugin_name, exc
        )
        return

    p = getattr(module, "plugin", None)
    if p is None:
        logger.warning(
            "Plugin '%s': no module-level 'plugin' attribute — skipped. "
            "See templates/plugin.py.template for the required structure.",
            plugin_name,
        )
        return

    _has_standalone_interface = hasattr(p, "register_subparsers")
    _has_extension_interface = hasattr(p, "handles") and hasattr(p, "register_command_flags")
    if not (
        hasattr(p, "commands")
        and (_has_standalone_interface or _has_extension_interface)
        and hasattr(p, "run")
    ):
        logger.warning(
            "Plugin '%s': 'plugin' object is missing required attributes — skipped. "
            "Standalone plugins need (commands, register_subparsers, run). "
            "Extension plugins need (commands, handles, register_command_flags, run).",
            plugin_name,
        )
        return

    if not p.commands:
        logger.warning(
            "Plugin '%s': 'commands' list is empty — skipped.", plugin_name
        )
        return

    if not _declares_model_roles(p, plugin_name):
        return

    for cmd in p.commands:
        if cmd in result:
            existing = result[cmd]
            if hasattr(existing, "handles") and hasattr(p, "handles"):
                # Both plugins declare ownership lists — merge into a DispatchPlugin.
                from .dispatch_plugin import DispatchPlugin
                if not isinstance(existing, DispatchPlugin):
                    dispatcher = DispatchPlugin(cmd, existing)
                    result[cmd] = dispatcher
                else:
                    dispatcher = existing
                dispatcher._absorb(p, plugin_name)
            else:
                logger.warning(
                    "Plugin '%s': command '%s' already registered by another plugin "
                    "— skipped.",
                    plugin_name,
                    cmd,
                )
            continue
        result[cmd] = p
