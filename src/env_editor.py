"""Safe, comment-preserving read/write access to the ``.env`` file.

Every other config surface in this project that needs to be edited
programmatically (``data_sources.json``, ``apis.json``, ``model_catalog.json``)
is plain JSON, which is trivial to rewrite wholesale without losing anything.
``.env`` is different: professors hand-edit it directly and expect their own
comments and formatting to survive a script touching it. This module never
rewrites the whole file — it uses ``python-dotenv``'s ``set_key``/``unset_key``
(already a project dependency), which update or insert exactly one
``KEY=value`` line and leave every other line untouched.

This is the one place in the project that writes to ``.env`` on its own.
Earlier, nothing did — the working assumption was that a file holding live
API keys shouldn't be touched by automation at all (see ``webui
set-passphrase``, which still only prints the line for a person to paste in
themselves). That blanket rule has been narrowed on purpose: editing ``.env``
this way only ever happens locally, at the keyboard of the person who owns
the keys, driven by a command they typed themselves — never over a network
call, and never as part of syncing files between machines.

That said, the one habit this doesn't change: never place this project's
real ``.env`` file itself in a synced folder (Dropbox, iCloud, a shared
drive, etc.). If a value needs to move between two machines, copy that one
line by hand over a channel you trust, rather than syncing the whole file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from dotenv import set_key, unset_key

from .config import load_professor_config, make_safe_filename

_ROOT = Path(__file__).parent.parent
ENV_PATH = _ROOT / ".env"

_PROF_NAME_PATTERN = re.compile(r'^PROF_(.+?)_NAME$')


def _ensure_env_file_exists() -> None:
    """Create an empty ``.env`` file if one doesn't exist yet, so there's somewhere to write."""
    if not ENV_PATH.exists():
        ENV_PATH.touch()


def _set(key: str, value: str) -> None:
    """Write one ``KEY=value`` line to ``.env`` (updating it in place if already present).

    Also updates the current process's environment immediately, so a change
    is visible right away (e.g. to a following ``--show-config`` call)
    without needing to restart.
    """
    _ensure_env_file_exists()
    set_key(str(ENV_PATH), key, value, quote_mode="always")
    os.environ[key] = value


def _unset(key: str) -> None:
    """Remove one ``KEY=...`` line from ``.env``, if present, and drop it from this process too."""
    if ENV_PATH.exists():
        unset_key(str(ENV_PATH), key)
    os.environ.pop(key, None)


def next_professor_id() -> str:
    """Return the next unused ``PROF_<N>`` id, one higher than the highest already configured.

    Returns:
        A numeric id as a string (e.g. ``'4'`` if ``PROF_1``, ``PROF_2``, and
        ``PROF_3`` are already taken). Returns ``'1'`` if none are configured yet.
    """
    used_ids = set()
    for key in os.environ:
        match = _PROF_NAME_PATTERN.match(key)
        if match:
            try:
                used_ids.add(int(match.group(1)))
            except ValueError:
                continue
    return str(max(used_ids, default=0) + 1)


def add_professor(name: str, primary_key: str, backup_key: Optional[str] = None) -> str:
    """Add a new professor's configuration directly to ``.env``.

    Args:
        name: The professor's display name (e.g. ``'Jeff Heller'``).
        primary_key: Their primary API key.
        backup_key: Their backup API key, used automatically if the primary
                    one ever stops working. Optional — pass ``None`` or an
                    empty string to skip it.

    Returns:
        The safe-filename identifier assigned to this professor (e.g.
        ``'jeff_heller'``), which is what gets typed on the command line
        (e.g. ``python main.py jeff_heller prompt``).

    Raises:
        ValueError: If *name* or *primary_key* is blank, or if a professor
                    with this name is already configured.
    """
    name = name.strip()
    primary_key = primary_key.strip()
    if not name:
        raise ValueError("Professor name cannot be blank.")
    if not primary_key:
        raise ValueError("Primary API key cannot be blank.")

    safe_name = make_safe_filename(name)
    existing = load_professor_config()
    if safe_name in existing:
        raise ValueError(
            f"A professor named '{existing[safe_name]['name']}' is already configured "
            f"(safe name '{safe_name}'). Remove them first if you want to replace them: "
            f"python main.py env remove-professor {safe_name}"
        )

    prof_id = next_professor_id()
    _set(f"PROF_{prof_id}_NAME", name)
    _set(f"PROF_{prof_id}_KEY", primary_key)
    if backup_key and backup_key.strip():
        _set(f"PROF_{prof_id}_BACKUP_KEY", backup_key.strip())
    return safe_name


def remove_professor(identifier: str) -> str:
    """Remove a professor's configuration from ``.env`` by safe name or display name.

    Args:
        identifier: Either the safe-filename identifier (e.g. ``'heller'``)
                    or the full display name (e.g. ``'Jeff Heller'``),
                    matched case-insensitively.

    Returns:
        The removed professor's display name.

    Raises:
        ValueError: If no configured professor matches *identifier*.
    """
    professors = load_professor_config()
    match = professors.get(identifier)
    if match is None:
        for prof in professors.values():
            if prof["name"].lower() == identifier.lower():
                match = prof
                break
    if match is None:
        raise ValueError(f"No configured professor matches '{identifier}'.")

    prof_id = match["id"]
    _unset(f"PROF_{prof_id}_NAME")
    _unset(f"PROF_{prof_id}_KEY")
    _unset(f"PROF_{prof_id}_BACKUP_KEY")
    return match["name"]


def set_optional_value(key: str, value: str) -> None:
    """Set an arbitrary optional ``.env`` variable (a webui secret, an alternate-endpoint key, etc.).

    Raises:
        ValueError: If *value* is blank.
    """
    value = value.strip()
    if not value:
        raise ValueError(f"{key} cannot be blank.")
    _set(key, value)


def unset_optional_value(key: str) -> None:
    """Remove an arbitrary optional ``.env`` variable."""
    _unset(key)
