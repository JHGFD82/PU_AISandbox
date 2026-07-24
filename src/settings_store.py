"""Comment-preserving read/write access to ``.settings`` — this installation's
own credentials and local identity (professors, secrets, endpoint keys, and
which other installations' usage data to include in reports).

This is the one file in the project that is never meant to be shared or
synced — everything else configurable (``settings.default.toml``,
``settings.shared.toml``, ``settings.local.toml``) is designed to be shared
or layered; ``.settings`` deliberately is not. See the project's July 2026
configuration-consolidation discussion for the full reasoning: editing this
file programmatically is safe specifically because every edit happens
locally, driven by a command the person types at their own keyboard — never
over a network call, never as part of syncing files between machines. That
reasoning does not extend to placing ``.settings`` itself in a synced folder
(Dropbox, iCloud, etc.) — never do that.

``.settings`` replaces four things that used to be separate files:

- ``.env`` — professor names/keys and optional feature secrets
  (``WEBUI_PASSPHRASE_HASH``, ``WEBUI_SESSION_SECRET``).
- The API-key half of ``apis.json`` (the endpoint *definitions* — base URL,
  timeout, etc. — moved into the ``settings.*.toml`` layering; see
  ``src/settings.py`` and ``src/services/api_config.py``).
- ``data_sources.json`` — this installation's own list of external/remote
  usage-data sources, which is exactly the kind of private, per-installation
  knowledge that belongs here rather than in a shared file.

Uses ``tomlkit`` rather than the standard library's ``tomllib`` because
``tomllib`` is read-only — ``tomlkit`` reads and writes TOML while
preserving comments and formatting, the same safety property ``.env``
editing relied on via ``python-dotenv``'s ``set_key``/``unset_key``.

Schema::

    [professors.jeff_heller]
    name = "Jeff Heller"
    key = "sk-..."
    backup_key = "sk-..."          # optional

    [webui]
    passphrase_hash = "..."
    session_secret = "..."

    [endpoints.hpc_cluster]
    key = "..."                     # the credential only; base_url etc.
                                     # live in settings.*.toml instead

    [shared_settings]
    path = "/path/to/settings.shared.toml"

    [usage_sources]
    source_id = "toms-mac"

    [[usage_sources.external]]
    label = "Prof. Smith"
    path = "/path/to/shared/data"
    mode = "read-only"
"""

from __future__ import annotations

import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, overload

import tomlkit

_ROOT = Path(__file__).parent.parent
SETTINGS_PATH = _ROOT / ".settings"

VALID_SOURCE_MODES = ("read-only", "shared-write")


def _load() -> tomlkit.TOMLDocument:
    """Parse ``.settings``, returning an empty (but valid) document if it doesn't exist yet."""
    if not SETTINGS_PATH.exists():
        return tomlkit.document()
    with SETTINGS_PATH.open("r", encoding="utf-8") as f:
        return tomlkit.parse(f.read())


def _save(doc: tomlkit.TOMLDocument) -> None:
    """Write *doc* back to ``.settings`` atomically (temp file + replace), preserving formatting."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(SETTINGS_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
        os.replace(tmp_path, SETTINGS_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@overload
def _get_table(doc: Any, dotted_section: str, create: Literal[True]) -> Any: ...
@overload
def _get_table(doc: Any, dotted_section: str, create: bool = False) -> Any | None: ...
def _get_table(doc: Any, dotted_section: str, create: bool = False) -> Any | None:
    """Navigate to the table at *dotted_section* (e.g. ``'webui'`` or ``'endpoints.hpc_cluster'``).

    Returns ``None`` if any part of the path is missing and *create* is
    ``False``; otherwise creates missing tables along the way (used by
    writers, which is why passing ``create=True`` is typed as never
    returning ``None``).
    """
    node = doc
    for part in dotted_section.split("."):
        if part not in node:
            if not create:
                return None
            node[part] = tomlkit.table()
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# Generic dotted-path values (webui secrets, endpoint credentials, the
# shared-settings pointer) — a single get/set/unset covers all of them.
# ---------------------------------------------------------------------------

def get_value(dotted_path: str) -> str | None:
    """Return the string stored at *dotted_path* (e.g. ``'webui.session_secret'``), or ``None``."""
    section, _, key = dotted_path.rpartition(".")
    doc = _load()
    table = _get_table(doc, section) if section else doc
    if table is None or key not in table:
        return None
    value = table[key]
    return str(value) if value is not None else None


def set_value(dotted_path: str, value: str) -> None:
    """Set the string at *dotted_path*, creating any missing tables along the way."""
    section, _, key = dotted_path.rpartition(".")
    doc = _load()
    table = _get_table(doc, section, create=True) if section else doc
    table[key] = value
    _save(doc)


def unset_value(dotted_path: str) -> None:
    """Remove the value at *dotted_path*, if present. Does nothing if it isn't set."""
    section, _, key = dotted_path.rpartition(".")
    doc = _load()
    table = _get_table(doc, section) if section else doc
    if table is not None and key in table:
        del table[key]
        _save(doc)


def get_shared_settings_path() -> Path | None:
    """Return the configured shared settings file path, expanded, or ``None`` if unset."""
    raw = get_value("shared_settings.path")
    return Path(raw).expanduser() if raw else None


# ---------------------------------------------------------------------------
# Professors
# ---------------------------------------------------------------------------

def get_professors() -> dict[str, dict[str, Any]]:
    """Return every configured professor, keyed by safe name.

    Each value has ``name`` (display name), ``key`` (primary API key),
    ``backup_key`` (backup API key, or ``None``), and ``safe_name`` (same as
    the outer key).
    """
    doc = _load()
    table = _get_table(doc, "professors")
    if table is None:
        return {}
    result = {}
    for safe_name, record in table.items():
        result[safe_name] = {
            "name": str(record.get("name", safe_name)),
            "key": str(record["key"]) if record.get("key") else "",
            "backup_key": str(record["backup_key"]) if record.get("backup_key") else None,
            "safe_name": safe_name,
        }
    return result


def add_professor(name: str, key: str, backup_key: str | None = None) -> str:
    """Add a new professor's configuration directly to ``.settings``.

    Args:
        name: The professor's display name (e.g. ``'Jeff Heller'``).
        key: Their primary API key.
        backup_key: Their backup API key, used automatically if the primary
                    one ever stops working. Optional.

    Returns:
        The safe-filename identifier assigned to this professor (e.g.
        ``'jeff_heller'``), for use on the command line.

    Raises:
        ValueError: If *name* or *key* is blank, or if a professor with this
                    name is already configured.
    """
    from .config import (
        make_safe_filename,  # deferred: config.py imports this module too
    )

    name = name.strip()
    key = key.strip()
    if not name:
        raise ValueError("Professor name cannot be blank.")
    if not key:
        raise ValueError("Primary API key cannot be blank.")

    safe_name = make_safe_filename(name)
    existing = get_professors()
    if safe_name in existing:
        raise ValueError(
            f"A professor named '{existing[safe_name]['name']}' is already configured "
            f"(safe name '{safe_name}'). Remove them first if you want to replace them: "
            f"python main.py env remove-professor {safe_name}"
        )

    doc = _load()
    professors = _get_table(doc, "professors", create=True)
    record = tomlkit.table()
    record["name"] = name
    record["key"] = key
    if backup_key and backup_key.strip():
        record["backup_key"] = backup_key.strip()
    professors[safe_name] = record
    _save(doc)
    return safe_name


def set_professor_key(safe_name: str, key: str) -> None:
    """Replace an existing professor's primary API key.

    Unlike ``add_professor``, this updates a professor that's already
    configured — used when someone's key needs rotating (e.g. from the web
    UI's settings page) without having to remove and re-add them first.

    Args:
        safe_name: The professor's safe-filename identifier (e.g. ``'heller'``).
        key: The new primary API key.

    Raises:
        ValueError: If *key* is blank, or no professor matches *safe_name*.
    """
    key = key.strip()
    if not key:
        raise ValueError("Primary API key cannot be blank.")

    doc = _load()
    professors = _get_table(doc, "professors")
    if professors is None or safe_name not in professors:
        raise ValueError(f"No configured professor with safe name '{safe_name}'.")

    professors[safe_name]["key"] = key
    _save(doc)


def set_professor_backup_key(safe_name: str, backup_key: str | None) -> None:
    """Replace or clear an existing professor's backup API key.

    Args:
        safe_name: The professor's safe-filename identifier (e.g. ``'heller'``).
        backup_key: The new backup API key, or a blank/``None`` value to
                    remove the backup key entirely (the professor then has
                    only a primary key, same as never having set one).

    Raises:
        ValueError: If no professor matches *safe_name*.
    """
    doc = _load()
    professors = _get_table(doc, "professors")
    if professors is None or safe_name not in professors:
        raise ValueError(f"No configured professor with safe name '{safe_name}'.")

    record = professors[safe_name]
    cleaned = backup_key.strip() if backup_key else ""
    if cleaned:
        record["backup_key"] = cleaned
    elif "backup_key" in record:
        del record["backup_key"]
    _save(doc)


def remove_professor(identifier: str) -> str:
    """Remove a professor's configuration from ``.settings`` by safe name or display name.

    Args:
        identifier: Either the safe-filename identifier (e.g. ``'heller'``)
                    or the full display name (e.g. ``'Jeff Heller'``),
                    matched case-insensitively.

    Returns:
        The removed professor's display name.

    Raises:
        ValueError: If no configured professor matches *identifier*.
    """
    professors = get_professors()
    match = professors.get(identifier)
    if match is None:
        for prof in professors.values():
            if prof["name"].lower() == identifier.lower():
                match = prof
                break
    if match is None:
        raise ValueError(f"No configured professor matches '{identifier}'.")

    doc = _load()
    table = _get_table(doc, "professors")
    if table is not None and match["safe_name"] in table:
        del table[match["safe_name"]]
        _save(doc)
    return match["name"]


# ---------------------------------------------------------------------------
# External/remote usage-data sources (formerly data_sources.json)
# ---------------------------------------------------------------------------

@dataclass
class ExternalSource:
    """One configured external usage-data location.

    Attributes:
        label: A short, human-readable name (e.g. ``'Prof. Smith'``).
        path: The folder to read (and, in shared-write mode, also write)
              usage data in.
        mode: ``'read-only'`` (the default) or ``'shared-write'``.
        professor: Which professor (safe-filename identifier) this source is
                   for. Required for ``mode='shared-write'``; ``None`` for
                   ``read-only`` sources.
    """
    label: str
    path: str
    mode: str = "read-only"
    professor: str | None = None

    def resolved_path(self) -> Path:
        """Return ``path`` as an absolute, ``~``-expanded location on disk."""
        return Path(self.path).expanduser()


def get_source_id() -> str:
    """Return the identifier this installation tags every usage record with.

    Defaults to this computer's hostname when nothing has been configured
    explicitly.

    Returns:
        The configured source id, or this machine's hostname if unset, or
        ``'unknown-machine'`` if the hostname itself can't be determined.
    """
    configured = get_value("usage_sources.source_id")
    return configured or platform.node() or "unknown-machine"


def set_source_id(source_id: str) -> None:
    """Set this installation's source id explicitly, overriding the hostname default."""
    set_value("usage_sources.source_id", source_id)


def get_configured_sources() -> list[ExternalSource]:
    """Return every external usage-data source configured for this installation."""
    doc = _load()
    table = _get_table(doc, "usage_sources")
    if table is None or "external" not in table:
        return []
    sources = []
    for entry in table["external"]:
        path = entry.get("path")
        if not path:
            continue
        sources.append(ExternalSource(
            label=entry.get("label") or path,
            path=path,
            mode=entry.get("mode", "read-only"),
            professor=entry.get("professor") or None,
        ))
    return sources


def get_shared_write_source(professor: str) -> ExternalSource | None:
    """Return the shared-write source configured for *professor*, if any.

    Args:
        professor: The professor identifier as used elsewhere in this
                   project (e.g. ``'smith'``). Matched case-insensitively.

    Returns:
        The matching ``ExternalSource``, or ``None`` if this professor isn't
        configured for shared-write tracking.
    """
    target = professor.strip().lower()
    for src in get_configured_sources():
        if src.mode == "shared-write" and src.professor and src.professor.strip().lower() == target:
            return src
    return None


def add_source(label: str, path: str, mode: str = "read-only", professor: str | None = None) -> None:
    """Add a new external usage-data source, or replace one already using this label.

    Args:
        label: A short, human-readable name for this source.
        path: The folder to read (and, for shared-write, also write) usage
              data in.
        mode: ``'read-only'`` or ``'shared-write'``.
        professor: Required when ``mode='shared-write'``.

    Raises:
        ValueError: If *mode* isn't recognized, or ``mode='shared-write'``
                    was requested without a *professor*.
    """
    if mode not in VALID_SOURCE_MODES:
        raise ValueError(f"mode must be one of {VALID_SOURCE_MODES}, got {mode!r}.")
    if mode == "shared-write" and not professor:
        raise ValueError(
            "shared-write sources need a professor — which professor's usage "
            "this source holds — so TokenTracker knows whose writes to send here."
        )

    doc = _load()
    usage_sources = _get_table(doc, "usage_sources", create=True)
    existing = list(usage_sources.get("external", []))
    existing = [e for e in existing if e.get("label") != label]

    new_entry: dict[str, Any] = {"label": label, "path": path, "mode": mode}
    if professor:
        new_entry["professor"] = professor
    existing.append(new_entry)

    array = tomlkit.aot()
    for entry in existing:
        item = tomlkit.table()
        for k, v in entry.items():
            item[k] = v
        array.append(item)
    usage_sources["external"] = array
    _save(doc)


def remove_source(label: str) -> bool:
    """Remove a configured source by its label.

    Returns:
        ``True`` if a source with that label was found and removed,
        ``False`` otherwise.
    """
    doc = _load()
    table = _get_table(doc, "usage_sources")
    if table is None or "external" not in table:
        return False
    before = list(table["external"])
    after = [e for e in before if e.get("label") != label]
    if len(after) == len(before):
        return False

    array = tomlkit.aot()
    for entry in after:
        item = tomlkit.table()
        for k, v in dict(entry).items():
            item[k] = v
        array.append(item)
    table["external"] = array
    _save(doc)
    return True
