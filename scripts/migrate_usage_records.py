#!/usr/bin/env python3
"""One-time migration: backfill the 'source' field onto existing usage records.

Why this exists
----------------
The external/remote usage-data sources feature tags every usage record with a
``source`` field — which installation/machine made that API call — so a
shared-write report can tell "your activity on this account" apart from
"theirs." Every usage record written from now on always has this field.
Records written before this feature existed don't.

Rather than teaching every reader in this project ("does this record have
a source field or not?") to tolerate both shapes forever, this script
converts what already exists, once. After it's been run, nothing in
``src/`` needs to handle the old shape — there isn't one anymore.

This script is standalone on purpose — it is *not* a permanent CLI
subcommand. Run it once, and it's done; there's nothing to maintain
afterwards.

Usage
-----
Run from the repository root::

    python scripts/migrate_usage_records.py            # backs up, then migrates
    python scripts/migrate_usage_records.py --dry-run   # show what would change, write nothing

A backup of data/ is written to data/_pre_migration_backup/ before anything
is changed. If that backup directory already exists (e.g. you've run this
before), a fresh backup is skipped rather than overwritten — remove it by
hand first if you want a new one.

Note: this only touches this installation's own local data/ folder. If a
professor's usage tracking has already been switched to a shared-write
external source (configured in settings.toml), that professor's records live
under the external source's own path instead and aren't touched by this
script — run it there too if that installation also has old-format records
to convert.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.settings_store import get_source_id

DATA_DIR = _ROOT / "data"
BACKUP_DIR = DATA_DIR / "_pre_migration_backup"


def _iter_usage_files():
    """Yield every local usage-data file this installation owns: the active per-professor files and every archive."""
    yield from sorted(DATA_DIR.glob("token_usage_*.json"))
    archives_dir = DATA_DIR / "archives"
    if archives_dir.exists():
        yield from sorted(archives_dir.glob("*/*.json"))


def _backup(files: list[Path]) -> None:
    """Copy every file about to be touched into data/_pre_migration_backup/, preserving its relative path."""
    if BACKUP_DIR.exists():
        print(f"Backup already exists at {BACKUP_DIR} — not overwriting it.")
        print("Remove it by hand first if you want a fresh backup, then re-run this script.")
        return
    BACKUP_DIR.mkdir(parents=True)
    for f in files:
        dest = BACKUP_DIR / f.relative_to(DATA_DIR)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
    print(f"Backed up {len(files)} file(s) to {BACKUP_DIR}")


def migrate(dry_run: bool = False) -> None:
    """Backfill the 'source' field onto every existing usage record under data/.

    Args:
        dry_run: When True, print what would change without writing
                 anything (and without creating a backup, since nothing is
                 being risked).
    """
    source_id = get_source_id()
    files = list(_iter_usage_files())

    if not files:
        print("No usage files found under data/ — nothing to migrate.")
        return

    print(f"Found {len(files)} usage file(s) under {DATA_DIR}.")

    if not dry_run:
        _backup(files)

    files_touched = 0
    records_updated = 0

    for f in files:
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"  SKIP (invalid JSON): {f} — {e}")
            continue

        changed = False
        for record in data.get("session_history", []):
            if "source" not in record:
                record["source"] = source_id
                records_updated += 1
                changed = True

        if not changed:
            continue

        files_touched += 1
        display_path = f.relative_to(DATA_DIR.parent) if DATA_DIR.parent in f.parents else f
        if dry_run:
            print(f"  Would update: {display_path}")
        else:
            f.write_text(json.dumps(data, indent=2))
            print(f"  Updated: {display_path}")

    verb = "Would update" if dry_run else "Updated"
    print(f"\n{verb} {records_updated} record(s) across {files_touched} file(s) (source='{source_id}').")
    if dry_run:
        print("Dry run — nothing was written. Re-run without --dry-run to apply.")
    elif records_updated:
        print(f"Backup of the pre-migration files is at {BACKUP_DIR} if you ever need to check the originals.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing anything or creating a backup",
    )
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
