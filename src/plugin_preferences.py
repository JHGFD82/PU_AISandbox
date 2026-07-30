"""Puts every plugin's adjustable settings where a person can find them.

A plugin ships its defaults in its own ``settings.toml``, inside the package.
That is the right place for the plugin to keep them and the wrong place to ask
anyone to go looking: it sits among the code, it is tracked by the plugin's own
repository, and a newer copy of the plugin replaces it. Telling someone to edit
a file like that in order to change which models a translation uses is not a
reasonable thing to ask.

So the package copies them out. Every time it runs, it reads the settings file
of each installed plugin and makes sure every setting in it also appears in the
person's own ``preferences.toml``, along with whatever the plugin's author wrote
to explain it. Nothing has to be hunted down: the file you are meant to edit
already lists everything you could change.

Only that file. A shared settings file belongs to a group, is looked after by one
person, and usually lives somewhere that syncs — so several installations
appending to it is how you get duplicated blocks and conflicted copies, which is
the same reason usage records are written one file per call rather than into a
shared one (see ``src/tracking/token_tracker.py``). Whoever looks after the
shared settings produces it deliberately instead, and tells the group to point
at it.

Where a shared file already decides something, that is reflected here rather
than overwritten: the value offered is the one actually in effect, labelled with
where it came from, so uncommenting a line can never quietly undo the group's
choice.

They are written commented out, and that matters. A live value would pin that
setting the moment it was written: the plugin could ship a corrected list next
month — a model retired, a better default found — and the frozen copy would
quietly win. Commented, the plugin's own value keeps applying until somebody
deliberately uncomments the line, which is exactly when they mean to take it
over.

Nothing already in the file is touched, so a value someone has set, or a
comment they have written, stays as it is.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# The heading written above each plugin's block, so it is obvious where the
# settings came from and that the file did not arrive that way by hand.
_BANNER = "# ── {plugin} ─────────────────────────────────────────────────────────"

_INTRO = """
# Settings the plugins you have installed can be adjusted with. Everything
# below is commented out, which means the plugin's own value is being used.
# Uncomment a line to take it over — from then on your value is what applies,
# and the plugin's changes to that one setting stop reaching you.
"""


def _sections_of(path: Path) -> dict:
    """Return the parsed contents of a TOML file, or an empty result if unreadable."""
    try:
        with path.open("rb") as handle:
            parsed = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {name: value for name, value in parsed.items() if isinstance(value, dict)}


def _already_mentions(text: str, section: str, key: str) -> bool:
    """Return whether *text* already covers this setting, live or commented.

    Checked as text rather than by parsing, because a commented-out line is a
    comment as far as any TOML reader is concerned — and a setting that has been
    offered once must not be offered again on the next run.
    """
    in_section = False
    for raw in text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if line.startswith("[") and line.endswith("]"):
            in_section = line[1:-1].strip() == section
            continue
        if in_section and line.split("=")[0].strip() == key:
            return True
    return False


def _live_line(path: Path, section: str, key: str) -> Optional[str]:
    """Return the line where *path* sets this setting for real, if it does.

    The raw line rather than the parsed value, so whatever formatting and
    trailing comment the author of that file chose comes across as they wrote it.
    Commented lines don't count: those are offers, not decisions.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line[1:-1].strip() == section
            continue
        if in_section and line.split("=")[0].strip() == key:
            return line
    return None


def _render(
    plugin: str,
    settings_file: Path,
    missing: dict,
    beneath: Optional[list] = None,
) -> str:
    """Build the text to append for one plugin's not-yet-offered settings.

    Carries across whatever the plugin's author wrote above each setting, so the
    explanation arrives with the setting instead of being left behind in a file
    nobody is going to open.

    Args:
        plugin: The plugin's folder name, for the heading.
        settings_file: That plugin's own settings file.
        missing: Section name to the keys still to be offered.
        beneath: ``(description, path)`` pairs for the layers that already apply
                 below the file being written to, lowest first. Where one of them
                 sets a value for real, that value is what gets offered — not the
                 plugin's — with a note saying where it came from. Offering the
                 plugin's value there would misreport what is in effect, and
                 uncommenting it would quietly undo somebody else's decision.
    """
    own_lines = settings_file.read_text(encoding="utf-8").splitlines()
    out: list[str] = ["", _BANNER.format(plugin=plugin)]

    for section, keys in missing.items():
        if out[-1] != "":
            out.append("")
        out.append(f"# [{section}]")
        current: Optional[str] = None
        pending: list[str] = []
        for raw in own_lines:
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                pending = []
                continue
            if line.startswith("#"):
                pending.append(line)
                continue
            if not line:
                pending = []
                continue
            if current != section:
                continue
            name = line.split("=")[0].strip()
            if name not in keys:
                pending = []
                continue
            out.extend(f"# {note.lstrip('#').strip()}" for note in pending)
            decided = None
            for description, path in (beneath or []):
                found = _live_line(path, section, name)
                if found is not None:
                    decided = (found, description)
            if decided is None:
                out.append(f"# {line}")
            else:
                found, description = decided
                out.append(f"# {found}    # currently set by {description}")
            pending = []
    while out and not out[-1].strip("# "):
        out.pop()
    return "\n".join(out) + "\n"


def offer_plugin_settings(plugins_dir: Path) -> list[str]:
    """Make sure every installed plugin's settings appear in the files people edit.

    Called once per run, after the plugins have loaded. Reads each plugin's own
    ``settings.toml`` and appends anything not already covered to the person's
    ``preferences.toml``, commented out. Never writes to a shared settings file —
    see this module's own docstring for why.

    Args:
        plugins_dir: The folder the plugins live in.

    Returns:
        The paths written to, as strings, for logging. Empty when everything was
        already listed — the ordinary case after the first run.

    Notes:
        Never raises. This is a convenience: a sandbox that cannot write to
        these files should still run every command, so a failure is logged and
        stepped over. Files are only ever appended to, never rewritten, so a
        value someone has set cannot be disturbed.
    """
    from . import paths, settings_store

    try:
        paths.preferences_path()
    except paths.NotSetUpError:
        return []

    # Precedence runs plugin -> shared -> preferences, so what gets offered here
    # has to account for a shared file sitting in between: where the group has
    # decided something, that decision is what is shown.
    shared = settings_store.get_shared_settings_path()
    beneath = (
        [("your group's shared settings", shared)]
        if shared is not None and shared.exists()
        else []
    )
    targets: list = [(paths.preferences_path(), beneath)]

    plugin_files = sorted(
        (entry.name, entry / "settings.toml")
        for entry in plugins_dir.iterdir()
        if entry.is_dir() and (entry / "settings.toml").exists()
    ) if plugins_dir.is_dir() else []

    written: list[str] = []
    for target, beneath in targets:
        try:
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            additions: list[str] = []
            # Grows as each plugin's block is built, so that two plugins sharing
            # a section name — an extension and the plugin it extends both using
            # [ocr] — don't offer the same setting twice in one pass.
            covered = existing
            for plugin, settings_file in plugin_files:
                missing = {
                    section: [
                        key for key in keys
                        if not _already_mentions(covered, section, key)
                    ]
                    for section, keys in _sections_of(settings_file).items()
                }
                missing = {s: k for s, k in missing.items() if k}
                if missing:
                    block = _render(plugin, settings_file, missing, beneath=beneath)
                    additions.append(block)
                    covered += block
            if not additions:
                continue
            intro = _INTRO if "Settings the plugins you have installed" not in existing else ""
            with target.open("a", encoding="utf-8") as handle:
                handle.write(intro + "".join(additions))
            written.append(str(target))
        except OSError as error:
            logger.debug("Could not add plugin settings to %s: %s", target, error)
    return written
