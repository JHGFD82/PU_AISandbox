"""Configuration for external/remote usage-data sources.

Lets one installation of this package include another installation's usage
data when building reports. For example: a professor runs their own copy of
this tool and configures it to write its usage history into a folder synced
with Dropbox; the person who manages several professors' accounts registers
that folder as a source on their own installation, so a single report can
show everyone's spending without anyone copying files around by hand. See
``docs/webui-plugin-plan.md`` (§1) for the full design, including why a
source can be ``read-only`` (only the other installation ever writes there)
or ``shared-write`` (both installations record usage there, using
one-file-per-call so a dumb file-sync service like Dropbox can never see two
conflicting edits to the same file).

Settings are stored in ``data_sources.json`` at the repository root — a
plain JSON file, git-ignored and specific to this installation, in the same
style as ``apis.json`` and ``src/model_catalog.json``. This feature never
reads or writes TOML: a hand-rolled JSON file keeps the read/write code
simple, and — unlike rewriting a TOML file — never risks silently discarding
comments or formatting a person added to ``settings.local.toml`` by hand.
"""

import json
import logging
import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).parent.parent.parent  # src/tracking/ -> repo root
DATA_SOURCES_FILE = _ROOT / "data_sources.json"

VALID_MODES = ("read-only", "shared-write")


@dataclass
class ExternalSource:
    """One configured external usage-data location.

    Attributes:
        label: A short, human-readable name shown in reports and by
               ``usage sources list`` (e.g. ``'Prof. Smith'``).
        path: The folder to read (and, in shared-write mode, also write)
              usage data in — typically another installation's own
              ``data/`` folder, reached over a synced or shared filesystem
              location such as Dropbox.
        mode: ``'read-only'`` (the default) when only the other
              installation ever writes there, or ``'shared-write'`` when
              this installation records usage there too.
        professor: Which professor (safe-filename identifier, e.g.
                   ``'smith'``) this source is for. Required when
                   ``mode='shared-write'``, since ``TokenTracker`` needs to
                   know which professor's own writes to redirect here.
                   Left as ``None`` for ``read-only`` sources, which are
                   scanned wholesale — whatever professors' files are found
                   there are included, without needing to know in advance
                   who they belong to.
    """
    label: str
    path: str
    mode: str = "read-only"
    professor: Optional[str] = None

    def resolved_path(self) -> Path:
        """Return ``path`` as an absolute, ``~``-expanded location on disk."""
        return Path(self.path).expanduser()


def _load_raw() -> Dict[str, Any]:
    """Read ``data_sources.json``, returning an empty-but-valid structure if it's absent or unreadable."""
    if not DATA_SOURCES_FILE.exists():
        return {"source_id": "", "external_sources": []}
    try:
        with DATA_SOURCES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.warning(f"Could not read {DATA_SOURCES_FILE.name}: {e}. Treating as empty.")
        return {"source_id": "", "external_sources": []}
    data.setdefault("source_id", "")
    data.setdefault("external_sources", [])
    return data


def _save_raw(data: Dict[str, Any]) -> None:
    """Write *data* to ``data_sources.json`` all at once (an atomic write).

    The new contents are written to a temporary file first and only swapped
    in for the real file once writing finishes completely, so a crash or
    interruption mid-write can never leave a half-written, unreadable
    config file behind. This mirrors the same safeguard already used for
    ``model_catalog.json`` (see ``src/models/catalog.py::save_model_catalog``).
    """
    DATA_SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_SOURCES_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, DATA_SOURCES_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_source_id() -> str:
    """Return the identifier this installation tags every usage record with.

    This is what lets a shared-write report distinguish "your activity on
    this account" from "theirs." Defaults to this computer's hostname when
    nothing has been configured explicitly, so there's a reasonable value
    with zero setup — see ``usage sources add`` for how to set it on
    purpose instead.

    Returns:
        The configured source id, or this machine's hostname if unset, or
        the literal string ``'unknown-machine'`` in the rare case the
        hostname itself can't be determined.
    """
    configured = _load_raw().get("source_id", "")
    return configured or platform.node() or "unknown-machine"


def set_source_id(source_id: str) -> None:
    """Set this installation's source id explicitly, overriding the hostname default."""
    data = _load_raw()
    data["source_id"] = source_id
    _save_raw(data)


def get_configured_sources() -> List[ExternalSource]:
    """Return every external usage-data source configured for this installation."""
    raw = _load_raw().get("external_sources", [])
    sources: List[ExternalSource] = []
    for entry in raw:
        path = entry.get("path")
        if not path:
            continue
        sources.append(ExternalSource(
            label=entry.get("label") or path,
            path=path,
            mode=entry.get("mode", "read-only"),
            professor=entry.get("professor"),
        ))
    return sources


def get_shared_write_source(professor: str) -> Optional[ExternalSource]:
    """Return the shared-write source configured for *professor*, if any.

    Args:
        professor: The professor identifier as used elsewhere in this
                   project (e.g. ``'smith'``). Matched case-insensitively
                   against each configured source's ``professor`` field.

    Returns:
        The matching ``ExternalSource``, or ``None`` if this professor
        isn't configured for shared-write tracking — the normal case for
        most professors, who are tracked purely locally.
    """
    target = professor.strip().lower()
    for src in get_configured_sources():
        if src.mode == "shared-write" and src.professor and src.professor.strip().lower() == target:
            return src
    return None


def add_source(label: str, path: str, mode: str = "read-only", professor: Optional[str] = None) -> None:
    """Add a new external usage-data source, or replace one already using this label.

    Args:
        label: A short, human-readable name for this source.
        path: The folder to read (and, for shared-write, also write) usage
              data in.
        mode: ``'read-only'`` or ``'shared-write'``.
        professor: Required when ``mode='shared-write'`` — which
                   professor's usage this source tracks.

    Raises:
        ValueError: If *mode* isn't a recognized value, or if
                    ``mode='shared-write'`` was requested without a
                    *professor*.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}.")
    if mode == "shared-write" and not professor:
        raise ValueError(
            "shared-write sources need a professor — which professor's usage "
            "this source holds — so TokenTracker knows whose writes to send here."
        )

    data = _load_raw()
    sources = [s for s in data["external_sources"] if s.get("label") != label]
    sources.append({"label": label, "path": path, "mode": mode, "professor": professor})
    data["external_sources"] = sources
    _save_raw(data)


def remove_source(label: str) -> bool:
    """Remove a configured source by its label.

    Returns:
        ``True`` if a source with that label was found and removed,
        ``False`` if no source had that label (nothing changed).
    """
    data = _load_raw()
    before = len(data["external_sources"])
    data["external_sources"] = [s for s in data["external_sources"] if s.get("label") != label]
    changed = len(data["external_sources"]) != before
    if changed:
        _save_raw(data)
    return changed
