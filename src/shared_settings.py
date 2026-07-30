"""Builds a starting shared settings file for whoever looks after one.

A group can agree on settings once and have every installation follow them: one
file, somewhere everyone can reach, named in each person's own ``settings.toml``
as ``shared_settings.path``. It sits between the defaults and each person's
``preferences.toml``, so it overrides what the package and its plugins ship and
is in turn overridden by anything an individual sets for themselves.

Nothing produces that file automatically, and nothing edits it afterwards. The
sandbox never writes to a shared settings file: it belongs to a group, and
several installations writing to a folder that syncs is how you end up with
conflicted copies. So the person looking after it asks for a draft, edits it,
and puts it in place — deliberately, once, knowing nobody else is writing.

This module builds that draft. It gathers every setting the package and the
installed plugins actually have, with the explanations their authors wrote, and
comments them all out so that an untouched draft changes nothing. Where a shared
file already exists, its decisions are carried across live and left alone, and
anything that has appeared since is marked, so returning for a second draft
shows what is new rather than making the whole thing be read again.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .plugin_preferences import live_line, sections_of

# Marks a setting the existing shared file says nothing about. The point of a
# second draft is to make these findable without re-reading everything.
_NEW = "# NEW:"

_HEADER = """\
# Shared settings for your group.
#
# Produced on {today} by:
#     python main.py settings export-shared
#
# What to do with it
# ------------------
# 1. Uncomment and adjust anything the whole group should share. Leave the rest
#    commented — a commented line means "whatever the sandbox or the plugin
#    ships", which is usually what you want.
# 2. Rename this file if you like. "shared-settings.toml" is only what the draft
#    comes out as; name it after your group if that reads better. Each
#    installation points at a path, not a name, so renaming now saves having to
#    tell everyone a new path later.
# 3. Put it somewhere every member can read: a synced folder, a network share,
#    anywhere they all have access to.
# 4. Tell each member to point at it, once:
#        python main.py settings set shared_settings.path <where you put it>
#
# Keeping it up to date
# --------------------
# Nothing edits this file, including the sandbox itself — several installations
# writing to a synced folder is how conflicted copies happen. When a member
# tells you a setting they need isn't in here, run the command above again: the
# decisions already in this file are carried across untouched, and anything that
# has appeared since is marked "{new}". Then replace the file in the shared
# location.
#
# What overrides what
# -------------------
# The package and its plugins ship defaults; this file overrides those for
# everyone pointing at it; each person's own preferences.toml overrides this.
"""

_SECTION_BANNER = "# ── from {label} ──"


def _collect(sources: list, existing: Optional[Path]) -> dict:
    """Gather every setting on offer, keyed by section then key.

    Organised by section rather than by the file each came from, because a
    section is what TOML counts: two plugins can both use ``[ocr]`` — an
    extension and the plugin it extends — and writing that header twice makes a
    file no reader will accept. The first source to offer a setting is the one
    whose value and explanation are used; a later one repeating it adds nothing.

    Args:
        sources: ``(label, path)`` pairs, in the order they should be considered.
        existing: A shared settings file already in use, if there is one.

    Returns:
        ``{section: {key: (line, comments, contributors)}}`` — the line as its
        author wrote it, the comment lines above it, and which sources offer this
        section, for the heading.
    """
    collected: dict = {}
    for label, source in sources:
        sections = sections_of(source)
        if not sections:
            continue
        try:
            own_lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        current: Optional[str] = None
        pending: list[str] = []
        for raw in own_lines:
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                current, pending = line[1:-1].strip(), []
                continue
            if line.startswith("#"):
                pending.append(line.lstrip("#").strip())
                continue
            if not line:
                pending = []
                continue
            if current is None or current not in sections:
                continue
            key = line.split("=")[0].strip()
            if key not in sections[current]:
                pending = []
                continue
            section = collected.setdefault(current, {"keys": {}, "from": []})
            if label not in section["from"]:
                section["from"].append(label)
            section["keys"].setdefault(key, (line, list(pending)))
            pending = []
    return collected


def _render(collected: dict, existing: Optional[Path]) -> list[str]:
    """Turn the gathered settings into the lines of a draft.

    Each section appears once, its heading live only if something under it is
    decided — an empty table is valid TOML but is noise in a file meant to be
    read by a person.
    """
    out: list[str] = []
    for section, content in collected.items():
        decided = {}
        for key in content["keys"]:
            found = live_line(existing, section, key) if existing is not None else None
            if found is not None:
                decided[key] = found

        out.append("")
        out.append(_SECTION_BANNER.format(label=", ".join(content["from"])))
        out.append(f"[{section}]" if decided else f"# [{section}]")
        for key, (line, comments) in content["keys"].items():
            out.extend(f"# {note}" for note in comments)
            if key in decided:
                out.append(decided[key])
            elif existing is not None:
                out.append(f"{_NEW} {line}")
            else:
                out.append(f"# {line}")
    return out


def build_shared_settings(
    plugins_dir: Path,
    package_defaults: Path,
    existing: Optional[Path] = None,
) -> str:
    """Build the text of a shared settings draft.

    Args:
        plugins_dir: The folder the installed plugins live in. Every plugin with
                     a settings file of its own contributes its settings, so a
                     group can standardise anything a plugin allows — not a
                     hand-maintained subset that would go stale.
        package_defaults: The package's own ``settings.default.toml``.
        existing: A shared settings file already in use, if there is one.

    Returns:
        The whole file as text, ready to write. Always valid TOML, and safe to
        place unedited: with nothing uncommented it changes nothing for anyone.
    """
    sources: list = [("the sandbox itself", package_defaults)]
    if plugins_dir.is_dir():
        for entry in sorted(plugins_dir.iterdir()):
            settings_file = entry / "settings.toml"
            if entry.is_dir() and settings_file.exists():
                sources.append((f"the {entry.name} plugin", settings_file))

    lines = [
        _HEADER.format(today=datetime.now().strftime("%-d %B %Y"), new=_NEW.rstrip(":"))
    ]
    lines.extend(_render(_collect(sources, existing), existing))
    return "\n".join(lines).rstrip() + "\n"


def count_new(text: str) -> int:
    """Return how many settings in a draft are new since the existing file."""
    return sum(1 for line in text.splitlines() if line.startswith(_NEW))
