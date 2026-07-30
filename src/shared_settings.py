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

import json
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

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


def _decisions_from(existing: Optional[Path], collected: dict) -> dict:
    """Read which settings a shared file already decides, as ``{section: {key: line}}``."""
    if existing is None:
        return {}
    found: dict = {}
    for section, content in collected.items():
        for key in content["keys"]:
            line = live_line(existing, section, key)
            if line is not None:
                found.setdefault(section, {})[key] = line
    return found


def _render(collected: dict, decisions: dict, mark_new: bool) -> list[str]:
    """Turn the gathered settings into the lines of a draft.

    Each section appears once, its heading live only if something under it is
    decided — an empty table is valid TOML but is noise in a file meant to be
    read by a person.

    Args:
        collected: What ``_collect()`` gathered.
        decisions: ``{section: {key: line}}`` for the settings to write live.
                   From an existing shared file, or from someone filling in a
                   form; this doesn't care which.
        mark_new: Whether settings without a decision should be marked as new.
                  Only meaningful when working from a file that predates them —
                  on a first draft nothing is new, and on one built from a form
                  every unticked setting was seen and left alone deliberately.
    """
    out: list[str] = []
    for section, content in collected.items():
        decided = decisions.get(section, {})

        out.append("")
        out.append(_SECTION_BANNER.format(label=", ".join(content["from"])))
        out.append(f"[{section}]" if decided else f"# [{section}]")
        for key, (line, comments) in content["keys"].items():
            out.extend(f"# {note}" for note in comments)
            if key in decided:
                out.append(decided[key])
            elif mark_new:
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
    sources = _sources_for(plugins_dir, package_defaults)

    lines = [
        _HEADER.format(today=datetime.now().strftime("%-d %B %Y"), new=_NEW.rstrip(":"))
    ]
    collected = _collect(sources, existing)
    lines.extend(_render(collected, _decisions_from(existing, collected), mark_new=existing is not None))
    return "\n".join(lines).rstrip() + "\n"


def count_new(text: str) -> int:
    """Return how many settings in a draft are new since the existing file."""
    return sum(1 for line in text.splitlines() if line.startswith(_NEW))


def _sources_for(plugins_dir: Path, package_defaults: Path) -> list:
    """Return the settings files to gather from, package first then each plugin."""
    sources: list = [("the sandbox itself", package_defaults)]
    if plugins_dir.is_dir():
        for entry in sorted(plugins_dir.iterdir()):
            settings_file = entry / "settings.toml"
            if entry.is_dir() and settings_file.exists():
                sources.append((f"the {entry.name} plugin", settings_file))
    return sources


def _as_toml_text(value: Any) -> str:
    """Write a value back the way TOML spells it.

    Only the shapes a settings file actually holds — a number, a yes/no, a piece
    of text, or a list of those. Anything stranger is handed back as it came, so
    an unusual setting is passed through rather than mangled.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_as_toml_text(item) for item in value) + "]"
    return str(value)


def _split_line(line: str) -> tuple:
    """Return a line's value as TOML text and its trailing comment, if any.

    Parsed rather than split on ``#``, since a comment character inside a piece
    of text is not a comment. The value is written back out cleanly, so what an
    editor shows is a value rather than a value plus whatever trailed it.
    """
    key = line.split("=", 1)[0].strip()
    try:
        parsed = tomllib.loads(line)
    except tomllib.TOMLDecodeError:
        return line.split("=", 1)[1].strip(), ""
    value = _as_toml_text(parsed[key])
    remainder = line.split("=", 1)[1]
    # Whatever follows the value on the line is a trailing comment. Located by
    # length rather than by searching for '#', so a '#' inside the value itself
    # is not mistaken for the start of one.
    without_comment = remainder.rstrip()
    comment = ""
    if "#" in without_comment:
        candidate = without_comment[without_comment.index("#"):]
        try:
            tomllib.loads(f"{key} = {without_comment[:without_comment.index('#')]}")
            comment = candidate.lstrip("#").strip()
        except tomllib.TOMLDecodeError:
            comment = ""
    return value, comment


def inventory(
    plugins_dir: Path,
    package_defaults: Path,
    existing: Optional[Path] = None,
) -> list:
    """List every setting a group could share, and what their file already says.

    The same gathering the draft is built from, handed back as data rather than
    as a file, so it can be shown as a form. Someone looking after a group's
    settings then sees every setting at once — what it does, what it is set to
    now, and which ones have appeared since their file was written — instead of
    reading a hundred commented lines to find the three that changed.

    Args:
        plugins_dir: The folder the installed plugins live in.
        package_defaults: The package's own ``settings.default.toml``.
        existing: A shared settings file already in use, if there is one.

    Returns:
        One entry per section, in the order they are offered, each with:

        * ``section`` — the TOML section name.
        * ``sources`` — which files offer it, for showing where it came from.
        * ``settings`` — one entry per setting, with its ``key``, its ``value``
          as TOML text (the group's if they have decided it, otherwise the
          shipped default), the author's ``explanation``, ``chosen`` for whether
          the group's file already sets it, and ``new`` for whether it appeared
          after that file was written.
    """
    collected = _collect(_sources_for(plugins_dir, package_defaults), existing)
    decisions = _decisions_from(existing, collected)

    out: list = []
    for section, content in collected.items():
        settings: list = []
        for key, (line, comments) in content["keys"].items():
            decided = decisions.get(section, {}).get(key)
            value, inline = _split_line(decided if decided is not None else line)
            explanation = " ".join([*comments, inline]).strip()
            settings.append({
                "key": key,
                "value": value,
                "explanation": explanation,
                "chosen": decided is not None,
                "new": existing is not None and decided is None,
            })
        out.append({"section": section, "sources": content["from"], "settings": settings})
    return out


def render_chosen(
    plugins_dir: Path,
    package_defaults: Path,
    chosen: dict,
) -> str:
    """Build the file from what someone picked, rather than from an existing one.

    Args:
        plugins_dir: The folder the installed plugins live in.
        package_defaults: The package's own ``settings.default.toml``.
        chosen: ``{section: {key: value_as_toml_text}}`` — the settings to write
                live. Everything else is written commented, as always, so what
                was left alone keeps following whatever ships.

    Returns:
        The whole file as text. Nothing is marked new: every setting was just
        looked at, so anything left unticked was left alone on purpose.

    Raises:
        ValueError: If a chosen value isn't something TOML can express. Caught
                    here rather than written out, because a file that will not
                    parse is worse than a rejected edit — it would silently stop
                    a whole group's settings from applying.
    """
    collected = _collect(_sources_for(plugins_dir, package_defaults), None)
    decisions: dict = {}
    for section, keys in (chosen or {}).items():
        for key, value in (keys or {}).items():
            line = f"{key} = {value}"
            try:
                tomllib.loads(line)
            except tomllib.TOMLDecodeError as error:
                raise ValueError(
                    f"'{value}' is not a value this setting can take. "
                    f"Text needs quotation marks around it, and a list needs "
                    f"square brackets — for example [\"gpt-4o\", \"gpt-4o-mini\"]. "
                    f"({section}.{key}: {error})"
                ) from error
            decisions.setdefault(section, {})[key] = line

    lines = [
        _HEADER.format(today=datetime.now().strftime("%-d %B %Y"), new=_NEW.rstrip(":"))
    ]
    lines.extend(_render(collected, decisions, mark_new=False))
    return "\n".join(lines).rstrip() + "\n"
