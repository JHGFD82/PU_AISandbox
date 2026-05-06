"""Discover and load ModePlugin instances from the plugins/ directory.

Scans ``plugins/*/plugin.py`` at application startup.  Any plugin that
fails to import or does not satisfy the ModePlugin interface emits a
warning and is skipped — it never crashes the main application.
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
    """Return a mapping of CLI command name → ModePlugin for every valid plugin.

    Scans each immediate subdirectory of *plugins_dir* for a ``plugin.py``
    entry point.  Subdirectories without a ``plugin.py`` are silently ignored.
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


def _load_one(
    plugin_name: str,
    plugin_file: Path,
    result: "dict[str, ModePlugin]",
) -> None:
    """Import one plugin module and register its commands into *result*."""
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
            "See plugin.py.template for the required structure.",
            plugin_name,
        )
        return

    if not (
        hasattr(p, "commands")
        and hasattr(p, "register_subparsers")
        and hasattr(p, "run")
    ):
        logger.warning(
            "Plugin '%s': 'plugin' object is missing required attributes "
            "(commands, register_subparsers, run) — skipped.",
            plugin_name,
        )
        return

    if not p.commands:
        logger.warning(
            "Plugin '%s': 'commands' list is empty — skipped.", plugin_name
        )
        return

    for cmd in p.commands:
        if cmd in result:
            logger.warning(
                "Plugin '%s': command '%s' already registered by another plugin "
                "— skipped.",
                plugin_name,
                cmd,
            )
            continue
        result[cmd] = p
