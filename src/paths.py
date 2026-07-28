"""Where this installation keeps the things that belong to the person using it.

The sandbox separates two kinds of thing:

* **The package** — the code, which is what you replace when you upgrade.
* **The extras folder** — settings, API keys, the model catalog, and all
  usage history and conversations. This is yours, and it must survive the
  package being deleted and replaced, because that is what "get the new
  version" means to most people.

Keeping the second inside the first is what made upgrading dangerous: the
obvious way to upgrade — delete the folder, download a fresh one — took the
API keys and months of history with it.

How the sandbox knows where your extras folder is
-------------------------------------------------
A small marker file sits *inside the package* recording the location. It has
to live there rather than in the settings file, because the sandbox needs to
know where the settings file is before it can read it.

That placement does something useful as a side effect: a freshly downloaded
package has no marker, so its absence is exactly the signal "this package
has not been set up yet" — either a first-time install or an upgrade. No
version numbers to compare, nothing to keep in sync.

Choosing where it goes
----------------------
``~/PU_AISandbox_data`` is the default, not a rule. Somewhere visible and
easy to explain beats somewhere conventional and hidden, for people who may
need to reach in and fetch a translated document. Anyone who would rather
keep it beside their other work can put it wherever they like.

One caveat the sandbox warns about rather than forbids: a folder that syncs
to the cloud also syncs the API keys in it. See ``describe_cloud_sync()``.
"""

from __future__ import annotations

import os
from pathlib import Path

# The package directory — where the code lives. src/paths.py -> repo root.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# The marker file, inside the package, naming the extras folder. Git-ignored:
# it describes one installation and must never travel to another.
INSTALL_MARKER = PACKAGE_ROOT / ".installation"

# Overrides everything, for tests and for anyone running more than one
# installation side by side.
EXTRAS_ENV_VAR = "PU_SANDBOX_EXTRAS"

# Where setup offers to put the extras folder if the person doesn't choose.
DEFAULT_EXTRAS_ROOT = Path.home() / "PU_AISandbox_data"

SETTINGS_FILENAME = ".settings"
MODEL_CATALOG_FILENAME = "model_catalog.json"
DATA_DIRNAME = "data"


def read_install_marker() -> Path | None:
    """Return the extras folder this package was set up against, if it has been.

    Returns:
        The recorded path, or ``None`` if this package has never been set up
        — which is the case for a fresh download, whether that's a
        first-time install or an upgrade.
    """
    try:
        recorded = INSTALL_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(recorded).expanduser() if recorded else None


def write_install_marker(extras_root: Path) -> None:
    """Record which extras folder this package is set up against.

    Args:
        extras_root: The folder holding settings, the model catalog and data.
    """
    INSTALL_MARKER.write_text(f"{extras_root}\n", encoding="utf-8")


def is_installed() -> bool:
    """Return whether this package has been set up yet.

    ``False`` means a fresh package — first-time install or upgrade — and is
    what triggers the setup flow. An override in the environment counts as
    set up, since it says where everything is.
    """
    return bool(os.environ.get(EXTRAS_ENV_VAR)) or read_install_marker() is not None


def extras_root() -> Path:
    """Return the folder holding this person's settings, catalog and data.

    Checked in order: an override in the environment, then the marker file,
    then — for an installation that predates all of this — the package
    directory itself, which is where everything used to live. That last
    fallback keeps an un-migrated installation working instead of behaving
    as though its data had vanished.
    """
    override = os.environ.get(EXTRAS_ENV_VAR)
    if override:
        return Path(override).expanduser()
    recorded = read_install_marker()
    if recorded is not None:
        return recorded
    return PACKAGE_ROOT


def settings_path() -> Path:
    """Return the file holding API keys and this installation's own configuration."""
    return extras_root() / SETTINGS_FILENAME


def model_catalog_path() -> Path:
    """Return the model catalog file (pricing and per-model capabilities).

    An installation that hasn't been set up yet still keeps this under
    ``src/``, where it used to live, so it is found rather than reported
    missing.
    """
    if not is_installed():
        return PACKAGE_ROOT / "src" / MODEL_CATALOG_FILENAME
    return extras_root() / MODEL_CATALOG_FILENAME


def data_root() -> Path:
    """Return the folder holding usage history, archives and conversations."""
    return extras_root() / DATA_DIRNAME


# ---------------------------------------------------------------------------
# Cloud-sync detection
# ---------------------------------------------------------------------------

# Folders whose contents are copied to a cloud service. Matched by position
# rather than by asking the service, because there is no reliable way to ask
# and being approximately right in a warning is worth more than being
# exactly right in silence.
_SYNC_ROOTS: tuple[tuple[str, str], ...] = (
    ("Library/CloudStorage", "a cloud service (OneDrive, Google Drive, Dropbox or Box)"),
    ("Library/Mobile Documents", "iCloud Drive"),
    ("Dropbox", "Dropbox"),
    ("OneDrive", "OneDrive"),
    ("Google Drive", "Google Drive"),
    ("Box", "Box"),
    ("Sync", "a sync service"),
)


def describe_cloud_sync(path: Path) -> str | None:
    """Return a plain description of the sync service *path* sits in, if any.

    Args:
        path: A folder being considered as, or already used as, the extras
              folder.

    Returns:
        A phrase naming the service, for use in a sentence — or ``None`` if
        the folder doesn't appear to be synced.

    Notes:
        Only positional guesswork, so it will miss an unusual setup and can
        in principle be wrong. It is used to *warn*, never to refuse: where
        someone keeps their own files is their decision. The reason to warn
        at all is that the folder holds API keys, and a synced folder means
        those keys exist on every device signed into that account and in
        that vendor's storage — spending against a real budget.

        Worth re-checking at startup and not only when the folder is first
        chosen: on macOS, turning on Desktop & Documents syncing is a single
        checkbox that retroactively uploads what is already there.
    """
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    parts = resolved.parts

    for marker, description in _SYNC_ROOTS:
        marker_parts = tuple(marker.split("/"))
        n = len(marker_parts)
        for i in range(len(parts) - n + 1):
            if parts[i:i + n] != marker_parts:
                continue
            # macOS mounts every third-party provider under CloudStorage and
            # names the folder after it — "OneDrive-PrincetonUniversity" and
            # so on. Naming the actual service beats listing the candidates.
            if marker == "Library/CloudStorage" and len(parts) > i + n:
                provider = parts[i + n].split("-")[0]
                return provider if provider else description
            return description

    # The one that isn't visible in the path: turning on iCloud's "Desktop &
    # Documents Folders" leaves ~/Documents looking exactly as it did while
    # syncing everything already inside it. It is a single checkbox, so a
    # folder chosen today can start syncing tomorrow with no sign of it here.
    # Detected by the container iCloud creates when that setting is on.
    icloud_docs = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Documents"
    if icloud_docs.exists():
        for folder in ("Documents", "Desktop"):
            if _is_within(resolved, Path.home() / folder):
                return "iCloud Drive (Desktop & Documents syncing is turned on)"
    return None


def _is_within(candidate: Path, parent: Path) -> bool:
    """Return whether *candidate* sits inside *parent*."""
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def cloud_sync_warning(path: Path) -> str | None:
    """Return the full warning to show for a synced extras folder, or ``None``.

    Says what is being synced and why that matters, rather than only that
    something was detected — a warning nobody can act on is one everybody
    dismisses.
    """
    service = describe_cloud_sync(path)
    if service is None:
        return None
    return (
        f"'{path}' is inside {service}.\n"
        "Anything kept there is copied to that service, including the file "
        "holding your API keys — which means a copy of those keys exists on "
        "every device signed into that account, and in that company's "
        "storage. Anyone with them can spend against your budget.\n"
        "Usage history and conversations are fine to sync; the keys are the "
        "part worth thinking about. Somewhere outside the synced folder — "
        f"{DEFAULT_EXTRAS_ROOT}, for instance — avoids this."
    )
