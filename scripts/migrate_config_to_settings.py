#!/usr/bin/env python3
"""One-time migration: consolidate .env, apis.json, and data_sources.json into .settings.

Why this exists
----------------
As of July 2026, this project's per-installation configuration is
consolidated into fewer, more consistent files (see docs/configuration.md,
"Settings at a Glance"):

- ``.env`` (professor names/keys, optional feature secrets) and
  ``data_sources.json`` (external usage-data sources) both fold into the new
  ``.settings`` file (TOML format — see ``src/settings_store.py``).
- ``apis.json``'s endpoint *definitions* (base_url, timeout, etc.) fold into
  ``settings.local.toml`` as ``[endpoints.<name>]`` tables, merged the same
  way every other runtime setting is. Each endpoint's *credential* moves to
  ``.settings`` instead, since credentials are never meant to be shared.

This script reads whatever combination of the old files exists on this
installation and writes the new ones, without requiring you to re-enter
anything by hand. Old files are renamed to ``*.bak`` (not deleted), so
nothing is lost if something looks wrong afterwards.

This script is standalone on purpose — it is *not* a permanent CLI
subcommand. Run it once, and it's done; there's nothing to maintain
afterwards.

Usage
-----
Run from the repository root::

    python scripts/migrate_config_to_settings.py            # migrates
    python scripts/migrate_config_to_settings.py --dry-run   # show what would happen, write nothing
    python scripts/migrate_config_to_settings.py --force     # overwrite .settings even if it already exists

If ``.settings`` already exists, this script refuses to run (to avoid
silently clobbering something you've already set up by hand or via
``python main.py env ...``) unless ``--force`` is passed.
"""

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import tomlkit

ENV_PATH = _ROOT / ".env"
APIS_JSON_PATH = _ROOT / "apis.json"
DATA_SOURCES_PATH = _ROOT / "data_sources.json"
SETTINGS_PATH = _ROOT / ".settings"
SETTINGS_LOCAL_PATH = _ROOT / "settings.local.toml"

_PROF_NAME_RE = re.compile(r'^PROF_(.+?)_NAME$')
_ASSIGNMENT_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')


def _parse_env_file(path: Path) -> dict:
    """Parse a simple KEY=value .env file into a plain dict.

    Deliberately minimal (this project no longer depends on python-dotenv)
    — handles the ``KEY=value`` and ``KEY="value"`` forms this project's own
    ``.env.template`` ever produced, skipping blank lines and ``#`` comments.
    """
    values: dict = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(stripped)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _safe_name(name: str) -> str:
    """Mirror src.config.make_safe_filename without importing the package (script runs standalone)."""
    safe = re.sub(r'[^\w\-_.]', '_', name)
    safe = re.sub(r'_+', '_', safe).strip('_')
    return safe.lower()


def _migrate_professors(env_values: dict, doc: tomlkit.TOMLDocument) -> int:
    """Read PROF_<N>_* triples from *env_values* and write them into *doc* as [professors.<safe_name>]."""
    count = 0
    professors = doc.setdefault("professors", tomlkit.table())
    for key, name in env_values.items():
        match = _PROF_NAME_RE.match(key)
        if not match:
            continue
        prof_id = match.group(1)
        primary = env_values.get(f"PROF_{prof_id}_KEY")
        if not primary:
            continue
        safe_name = _safe_name(name)
        record = tomlkit.table()
        record["name"] = name
        record["key"] = primary
        backup = env_values.get(f"PROF_{prof_id}_BACKUP_KEY")
        if backup:
            record["backup_key"] = backup
        professors[safe_name] = record
        count += 1
    return count


def _migrate_optional_env_values(env_values: dict, doc: tomlkit.TOMLDocument) -> list[str]:
    """Migrate WEBUI_*, API_<NAME>_KEY, and PU_SANDBOX_LAB_SETTINGS into *doc*. Returns endpoint names seen."""
    migrated_endpoint_names = []

    if env_values.get("WEBUI_PASSPHRASE_HASH"):
        webui = doc.setdefault("webui", tomlkit.table())
        webui["passphrase_hash"] = env_values["WEBUI_PASSPHRASE_HASH"]
    if env_values.get("WEBUI_SESSION_SECRET"):
        webui = doc.setdefault("webui", tomlkit.table())
        webui["session_secret"] = env_values["WEBUI_SESSION_SECRET"]

    if env_values.get("PU_SANDBOX_LAB_SETTINGS"):
        shared = doc.setdefault("shared_settings", tomlkit.table())
        shared["path"] = env_values["PU_SANDBOX_LAB_SETTINGS"]

    for key, value in env_values.items():
        if not value:
            continue
        api_match = re.match(r'^API_(.+)_KEY$', key)
        if api_match:
            endpoint_name = api_match.group(1).lower()
            endpoints = doc.setdefault("endpoints", tomlkit.table())
            entry = endpoints.setdefault(endpoint_name, tomlkit.table())
            entry["key"] = value
            migrated_endpoint_names.append(endpoint_name)

    return migrated_endpoint_names


def _migrate_usage_sources(doc: tomlkit.TOMLDocument) -> int:
    """Read data_sources.json (if present) and write it into *doc* as [usage_sources]. Returns source count."""
    if not DATA_SOURCES_PATH.exists():
        return 0
    try:
        data = json.loads(DATA_SOURCES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  SKIP data_sources.json (invalid JSON): {e}")
        return 0

    usage_sources = doc.setdefault("usage_sources", tomlkit.table())
    if data.get("source_id"):
        usage_sources["source_id"] = data["source_id"]

    external = data.get("external_sources", [])
    if not external:
        return 0

    array = tomlkit.aot()
    for entry in external:
        if entry.get("_comment") or not entry.get("path"):
            continue
        item = tomlkit.table()
        item["label"] = entry.get("label") or entry["path"]
        item["path"] = entry["path"]
        item["mode"] = entry.get("mode", "read-only")
        if entry.get("professor"):
            item["professor"] = entry["professor"]
        array.append(item)
    usage_sources["external"] = array
    return len(array)


def _migrate_endpoint_definitions(endpoint_names: list[str]) -> int:
    """Read apis.json's endpoint definitions (minus credentials) into settings.local.toml.

    Written to settings.local.toml — not settings.default.toml — because
    that file is tracked by git; personal infrastructure details (internal
    cluster URLs, etc.) shouldn't end up committed to a shared repo.
    """
    if not APIS_JSON_PATH.exists():
        return 0
    try:
        data = json.loads(APIS_JSON_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  SKIP apis.json (invalid JSON): {e}")
        return 0

    endpoints = data.get("endpoints", {})
    if not endpoints:
        return 0

    if SETTINGS_LOCAL_PATH.exists():
        local_doc = tomlkit.parse(SETTINGS_LOCAL_PATH.read_text(encoding="utf-8"))
    else:
        local_doc = tomlkit.document()

    if data.get("default"):
        config_table = local_doc.setdefault("config", tomlkit.table())
        config_table["default_endpoint"] = data["default"]

    endpoints_table = local_doc.setdefault("endpoints", tomlkit.table())
    known_keys = {"name", "base_url", "openai_compatible", "default_model", "timeout", "verify_ssl"}
    count = 0
    for name, raw in endpoints.items():
        entry = endpoints_table.setdefault(name, tomlkit.table())
        for k, v in raw.items():
            if k in known_keys:
                entry[k] = v
        count += 1

    SETTINGS_LOCAL_PATH.write_text(tomlkit.dumps(local_doc), encoding="utf-8")
    return count


def migrate(dry_run: bool = False, force: bool = False) -> None:
    """Read the old .env/apis.json/data_sources.json files and write the new .settings/settings.local.toml."""
    if SETTINGS_PATH.exists() and not force and not dry_run:
        print(f"{SETTINGS_PATH} already exists — refusing to overwrite it.")
        print("Pass --force if you really want to regenerate it from the old files.")
        return

    env_values = _parse_env_file(ENV_PATH)
    if not env_values and not APIS_JSON_PATH.exists() and not DATA_SOURCES_PATH.exists():
        print("No .env, apis.json, or data_sources.json found — nothing to migrate.")
        return

    doc = tomlkit.document()
    prof_count = _migrate_professors(env_values, doc)
    endpoint_names = _migrate_optional_env_values(env_values, doc)
    source_count = _migrate_usage_sources(doc)

    print(f"Found {prof_count} professor(s), {len(endpoint_names)} endpoint credential(s), "
          f"{source_count} usage source(s) to migrate.")

    if dry_run:
        print("\n--- .settings would contain ---")
        print(tomlkit.dumps(doc))
        print("Dry run — nothing was written. Re-run without --dry-run to apply.")
        return

    SETTINGS_PATH.write_text(tomlkit.dumps(doc), encoding="utf-8")
    print(f"Wrote {SETTINGS_PATH}")

    definition_count = _migrate_endpoint_definitions(endpoint_names)
    if definition_count:
        print(f"Wrote {definition_count} endpoint definition(s) into {SETTINGS_LOCAL_PATH}")

    renamed = []
    for old_path in (ENV_PATH, APIS_JSON_PATH, DATA_SOURCES_PATH):
        if old_path.exists():
            backup_path = old_path.with_suffix(old_path.suffix + ".bak")
            old_path.rename(backup_path)
            renamed.append(backup_path)
    if renamed:
        print("\nOld files renamed (not deleted), in case you need to double-check anything:")
        for p in renamed:
            print(f"  {p}")

    print(f"\nDone. Run 'python main.py --show-config' to verify {SETTINGS_PATH} looks right.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing or renaming anything",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite .settings even if it already exists",
    )
    args = parser.parse_args()
    migrate(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
