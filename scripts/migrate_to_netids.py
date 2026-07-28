#!/usr/bin/env python3
"""One-off move from name-based identifiers to netIDs.

The sandbox used to identify a person by a "safe name" made out of their
display name — ``Jeff Heller`` became ``jeff_heller``, or ``heller`` if that
was what was typed. That name got used as a filename, and because two parts
of the code made it safe in two different ways, one person's spending could
end up recorded under two names at once.

netIDs remove the problem rather than patching it: a netID is letters and
digits only, so there is nothing to make safe and nothing for two parts of
the code to disagree about.

This script moves an existing installation over. It renames, for each
person:

  data/token_usage_<old>.json      ->  data/token_usage_<netid>.json
  data/archives/<old>/             ->  data/archives/<netid>/
  data/conversations/<old>/        ->  data/conversations/<netid>/

and rewrites the ``[professors.<old>]`` section names in ``.settings`` to
``[professors.<netid>]``, leaving every value and comment in place.

Run it once. Nothing here is needed again afterwards.

Usage
-----
Preview, changing nothing (do this first)::

    python scripts/migrate_to_netids.py --map heller=jh43 --map conlan=abc123

Apply::

    python scripts/migrate_to_netids.py --map heller=jh43 --apply

Sweep away the empty archive folders left behind by an old bug where merely
listing the configuration created a folder for every name it saw::

    python scripts/migrate_to_netids.py --map heller=jh43 --apply --drop testprof --drop warntest
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data"
_SETTINGS = _ROOT / ".settings"

_NETID_RE = re.compile(r"^[a-z0-9]+$")


def _parse_mapping(pairs: list[str]) -> dict[str, str]:
    """Turn ``['heller=jh43', ...]`` into ``{'heller': 'jh43', ...}``, checking each netID."""
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--map needs the form old=netid, got {pair!r}")
        old, _, netid = pair.partition("=")
        old, netid = old.strip(), netid.strip().lower()
        if not old:
            raise SystemExit(f"--map is missing the current name in {pair!r}")
        if not _NETID_RE.match(netid):
            raise SystemExit(
                f"{netid!r} isn't a valid netID (letters and digits only), in {pair!r}"
            )
        mapping[old] = netid
    return mapping


def _planned_moves(mapping: dict[str, str]) -> list[tuple[Path, Path]]:
    """Return every (source, destination) rename this migration would perform.

    Only paths that actually exist are included, so the plan printed in a
    preview is exactly what will happen rather than a list of intentions.
    """
    moves: list[tuple[Path, Path]] = []
    for old, netid in mapping.items():
        candidates = [
            (_DATA / f"token_usage_{old}.json", _DATA / f"token_usage_{netid}.json"),
            (_DATA / "archives" / old, _DATA / "archives" / netid),
            (_DATA / "conversations" / old, _DATA / "conversations" / netid),
        ]
        moves.extend((src, dst) for src, dst in candidates if src.exists())
    return moves


def _rewrite_settings(mapping: dict[str, str], apply: bool) -> list[str]:
    """Rename the ``[professors.<old>]`` section headers in ``.settings``.

    Done as a line-level rewrite of just the header lines, rather than by
    parsing and re-emitting the file, so that every comment, blank line and
    piece of spacing in it survives exactly as written. ``.settings`` is
    hand-edited by people and full of explanatory comments; a migration that
    reformats it would be a worse outcome than one that fails.

    Returns:
        A description of each header line changed, for printing.
    """
    if not _SETTINGS.exists():
        return []
    original = _SETTINGS.read_text(encoding="utf-8")
    changed: list[str] = []
    lines = original.splitlines(keepends=True)
    for i, line in enumerate(lines):
        match = re.match(r"^(\s*\[professors\.)([^\]]+)(\]\s*)$", line)
        if not match:
            continue
        old = match.group(2).strip().strip('"').strip("'")
        if old not in mapping:
            continue
        lines[i] = f"{match.group(1)}{mapping[old]}{match.group(3)}"
        changed.append(f"[professors.{old}] -> [professors.{mapping[old]}]")
    if apply and changed:
        _SETTINGS.write_text("".join(lines), encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move this installation from name-based identifiers to netIDs.",
    )
    parser.add_argument(
        "--map", action="append", default=[], metavar="OLD=NETID",
        help="Current name and the netID replacing it, e.g. --map heller=jh43. Repeatable.",
    )
    parser.add_argument(
        "--drop", action="append", default=[], metavar="NAME",
        help="An archive folder to delete outright, for leftovers that were "
             "never a real person (e.g. testprof). Repeatable.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually make the changes. Without this, nothing is written.",
    )
    args = parser.parse_args()

    if not args.map and not args.drop:
        parser.error("nothing to do — pass at least one --map or --drop")

    mapping = _parse_mapping(args.map)
    moves = _planned_moves(mapping)

    # Refuse rather than overwrite. A destination that already exists means
    # either the migration was already run or the netID collides with
    # something real; both need a person to look, not a silent merge of two
    # people's spending records.
    clashes = [dst for _, dst in moves if dst.exists()]
    if clashes:
        print("Refusing to run — these destinations already exist:", file=sys.stderr)
        for dst in clashes:
            print(f"  {dst.relative_to(_ROOT)}", file=sys.stderr)
        return 1

    drops = [_DATA / "archives" / name for name in args.drop]
    drops = [d for d in drops if d.exists()]

    verb = "Renaming" if args.apply else "Would rename"
    print(f"{verb} {len(moves)} path(s):")
    for src, dst in moves:
        print(f"  {src.relative_to(_ROOT)}  ->  {dst.relative_to(_ROOT)}")
        if args.apply:
            src.rename(dst)

    if drops:
        verb = "Deleting" if args.apply else "Would delete"
        print(f"\n{verb} {len(drops)} leftover archive folder(s):")
        for d in drops:
            print(f"  {d.relative_to(_ROOT)}")
            if args.apply:
                shutil.rmtree(d)

    changed = _rewrite_settings(mapping, args.apply)
    verb = "Rewrote" if args.apply else "Would rewrite"
    print(f"\n{verb} {len(changed)} section header(s) in .settings:")
    for line in changed:
        print(f"  {line}")

    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to make it so.")
    else:
        print("\nDone. Check it worked:  python main.py --show-config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
