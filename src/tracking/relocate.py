"""Moving one person's work when the folder it belongs in changes.

Setting a shared folder on somebody, or taking one away, changes where their
work is written **from then on**. What is already on disk does not follow by
itself, and nothing looks for it in the old place afterwards — so without this,
changing that one setting quietly costs them everything recorded before it.

Two ways it showed:

- A month that was already under way stopped being counted. Reports let a
  shared folder supersede the local record for the same person and month, which
  is right when the local copy is genuinely left over — and wrong halfway
  through a month, where it is simply the earlier half.
- Conversations stayed in the folder they were written to and stopped
  appearing at all.

So the work moves with the setting. What "their work" means is not only this
module's business: usage is, conversations belong to the web interface, and
anything added later will have its own. Each part registers a mover here, and
this decides when they all run — see ``register_mover()``.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..settings_store import ExternalSource
from .token_tracker import (
    ARCHIVES_SUBDIR,
    CALLS_SUBDIR,
    fold_usage_records,
    get_archive_dir,
    get_usage_data_path,
)

logger = logging.getLogger(__name__)

# Given a netID and where their work was and now is, move it and report what
# happened. ``None`` for a location means this installation's own files folder.
# A mover is called only when the two differ.
Mover = Callable[[str, Optional[ExternalSource], Optional[ExternalSource]], "Moved"]

_MOVERS: list[tuple[str, Mover]] = []


def register_mover(mover: Mover, name: str) -> None:
    """Say that something of a person's needs moving when their folder changes.

    Core registers what it owns — what each call cost, and finished months.
    Anything else belongs to whoever owns it: the web interface registers its
    conversations this way, so that this module never has to know they exist.

    Args:
        mover: Called with the netID, where their work was, and where it now
               is, in that order. A location is an ``ExternalSource`` for a
               shared folder or ``None`` for this installation's own folder.
               Returns a ``Moved`` saying what it did — it names its own
               counts, so what a person is told uses its words and not this
               module's.
        name: What this mover looks after, in a word — ``'conversations'``.
              Used only to say which part failed when one does.
    """
    _MOVERS.append((name, mover))


@dataclass
class Moved:
    """What a move actually did, in terms somebody can be told.

    Attributes:
        counts: How many of each thing moved, keyed by the names movers were
                registered under — e.g. ``{'calls': 6, 'conversations': 28}``.
                A thing that had nothing to move is left out.
        left_behind: Anything that could not be moved, each with a plain
                     sentence saying why. Empty when everything moved.
    """

    counts: dict[str, int] = field(default_factory=dict)
    left_behind: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """True when anything at all moved."""
        return bool(self.counts)

    def summary(self) -> str:
        """One sentence describing the move, for showing to a person."""
        if not self.counts:
            return "There was nothing to move."
        parts = [f"{n} {name}" if n != 1 else f"1 {name.rstrip('s')}"
                 for name, n in self.counts.items()]
        moved = ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
        sentence = f"Moved {moved}."
        if self.left_behind:
            sentence += f" {len(self.left_behind)} thing(s) were left where they were."
        return sentence


def work_folder(source: Optional[ExternalSource]) -> Optional[ExternalSource]:
    """Return where a person's work is written, given the folder set on them.

    A folder set to read only is never written to — it is somebody else's
    record being watched, and writing this installation's work into it would
    be doing work in it. So only a shared-write folder is a work folder;
    anything else means this installation's own files folder.

    Args:
        source: The folder set on them, or ``None`` if none is.

    Returns:
        The source when it is written to, and ``None`` for "kept here".
    """
    if source is not None and source.mode == "shared-write":
        return source
    return None


def move_a_persons_work(
    netid: str,
    was: Optional[ExternalSource],
    now: Optional[ExternalSource],
) -> Moved:
    """Move everything of one person's from where it was to where it now goes.

    Call this after the setting has been changed, with the folder that was set
    before and the one set now. Both are read through ``work_folder()``, so
    turning a folder from read-only to shared-write counts as a change and
    turning it the other way counts as a change back.

    Nothing is deleted until the copy has been made and checked, and nothing
    already at the destination is written over — where both sides hold the same
    month, the two are added together rather than one replacing the other.

    Args:
        netid: Whose work to move.
        was: The folder set on them before, or ``None``.
        now: The folder set on them now, or ``None``.

    Returns:
        What moved, and anything that could not — see ``Moved``.
    """
    old, new = work_folder(was), work_folder(now)
    if _same_place(old, new):
        return Moved()

    result = Moved()
    for what, mover in [("usage", _move_usage)] + _MOVERS:
        try:
            part = mover(netid, old, new)
        except Exception as e:  # noqa: BLE001 — one part failing must not
            # strand the others, and the person has to be told which failed.
            logger.warning("Could not move %s for %s: %s", what, netid, e)
            result.left_behind.append(f"{what} could not be moved: {e}")
            continue
        for name, n in part.counts.items():
            result.counts[name] = result.counts.get(name, 0) + n
        result.left_behind.extend(part.left_behind)
    return result


def _same_place(old: Optional[ExternalSource], new: Optional[ExternalSource]) -> bool:
    """Whether two work folders are the same one."""
    if old is None and new is None:
        return True
    if old is None or new is None:
        return False
    return old.resolved_path() == new.resolved_path()


# ── What core itself moves: what each call cost, and finished months ────────
#
# The two places keep the same facts in different shapes. This installation's
# own folder holds everybody, so each person's records are filed under their
# netID, and the month under way is one summary file rewritten as it goes. A
# shared folder holds one person, so nothing in it is filed under a netID, and
# the month under way is one small file per call that is never edited again.
#
# Rather than four conversions between them, everything is read out into the
# same two piles — the individual calls of each month, and the months already
# folded up — and then written into whichever shape the destination keeps.


def _move_usage(netid: str, old: Optional[ExternalSource],
                new: Optional[ExternalSource]) -> Moved:
    """Move what each call cost, and every finished month."""
    calls, finished, sources = _read_usage(netid, old)
    if not calls and not finished:
        # Nothing to carry across — but there may still be an emptied file
        # sitting where their work used to be. Left alone it is read as a
        # month with nothing in it, which is the answer a report falls back to
        # when the folder their work actually moved to cannot be reached: a
        # confident zero instead of a missing figure. Only ever files this
        # already found to hold nothing.
        for path in sources:
            logger.info("Removing %s, which holds nothing, now that %s's work "
                        "is kept elsewhere.", path.name, netid)
            _discard(path)
        return Moved()

    _write_usage(netid, new, calls, finished)
    # Only now that it is all safely at the other end.
    for path in sources:
        _discard(path)

    counts = {}
    if calls:
        counts["calls"] = sum(len(r) for r in calls.values())
    if finished:
        counts["finished months"] = len(finished)
    return Moved(counts=counts)



def _read_usage(
    netid: str, where: Optional[ExternalSource]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]], list[Path]]:
    """Read one person's usage out of wherever it is.

    Returns:
        The individual calls of each month still open, the summary of each
        month already finished, and every file the two came out of — which is
        what gets removed once the move has succeeded.
    """
    calls: dict[str, list[dict[str, Any]]] = {}
    finished: dict[str, dict[str, Any]] = {}
    read_from: list[Path] = []

    if where is None:
        active = get_usage_data_path(netid)
        summary = _read_json(active)
        if summary:
            month = str(summary.get("month") or "")
            history = summary.get("session_history") or []
            if month and history:
                calls.setdefault(month, []).extend(history)
                read_from.append(active)
            elif month and summary.get("total_usage", {}).get("total_tokens"):
                # Totals with nothing behind them cannot be taken apart into
                # calls, and guessing would invent records. Left where it is.
                raise ValueError(
                    f"{active.name} records spending for {month} but not the "
                    "individual calls behind it, so it cannot be moved without "
                    "inventing them. Move or delete it by hand."
                )
            else:
                read_from.append(active)
        archive_dir = get_archive_dir(netid)
    else:
        root = where.resolved_path()
        calls_root = root / CALLS_SUBDIR
        if calls_root.is_dir():
            for month_dir in sorted(calls_root.iterdir()):
                if not month_dir.is_dir():
                    continue
                for call_file in sorted(month_dir.glob("*.json")):
                    record = _read_json(call_file)
                    if record is not None:
                        calls.setdefault(month_dir.name, []).append(record)
                        read_from.append(call_file)
        archive_dir = root / ARCHIVES_SUBDIR

    if archive_dir.is_dir():
        for archive in sorted(archive_dir.glob("*.json")):
            summary = _read_json(archive)
            if summary is not None:
                finished[archive.stem] = summary
                read_from.append(archive)

    return calls, finished, read_from


def _write_usage(
    netid: str,
    where: Optional[ExternalSource],
    calls: dict[str, list[dict[str, Any]]],
    finished: dict[str, dict[str, Any]],
) -> None:
    """Write usage into whichever shape *where* keeps.

    A month already at the destination is added to rather than replaced: this
    is a move, and the other side's records are as real as these.
    """
    this_month = datetime.now().strftime("%Y-%m")

    if where is None:
        # Here, the month under way is one summary file and every finished
        # month is an archive. A month still open at the other end but over by
        # now is folded up on arrival rather than left looking current.
        for month, records in calls.items():
            if month == this_month:
                path = get_usage_data_path(netid)
            else:
                path = get_archive_dir(netid) / f"{month}.json"
            _merge_summary_into(path, month, records)
        for month, summary in finished.items():
            _merge_summary_into(get_archive_dir(netid) / f"{month}.json", month, [],
                                summary=summary)
    else:
        root = where.resolved_path()
        for month, records in calls.items():
            month_dir = root / CALLS_SUBDIR / month
            month_dir.mkdir(parents=True, exist_ok=True)
            for record in records:
                _write_call_record(month_dir, record)
        for month, summary in finished.items():
            _merge_summary_into(root / ARCHIVES_SUBDIR / f"{month}.json", month, [],
                                summary=summary)


def _write_call_record(month_dir: Path, record: dict[str, Any]) -> None:
    """Write one call into a shared folder, named the way the sandbox names them."""
    import secrets

    stamp = str(record.get("timestamp") or "")
    try:
        when = datetime.fromisoformat(stamp).strftime("%Y%m%dT%H%M%S%f")
    except ValueError:
        when = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    source = str(record.get("source") or "moved")
    path = month_dir / f"{when}_{secrets.token_hex(3)}_{source}.json"
    path.write_text(json.dumps(record, indent=2))


def _merge_summary_into(
    path: Path,
    month: str,
    records: list[dict[str, Any]],
    summary: Optional[dict[str, Any]] = None,
) -> None:
    """Add records, or another month's summary, to a summary file.

    Where the file is not there yet this simply writes it. Where it is, the
    calls behind both are put together and the totals worked out again from
    them, so nothing is counted twice and nothing is dropped.

    Raises:
        ValueError: If either side records totals without the calls behind
                    them, which cannot be added up without inventing records.
    """
    incoming = list(records)
    if summary is not None:
        history = summary.get("session_history")
        if history is None and summary.get("total_usage", {}).get("total_tokens"):
            raise ValueError(
                f"the {month} summary records spending but not the calls behind "
                "it, so it cannot be added to what is already there"
            )
        incoming.extend(history or [])

    existing = _read_json(path)
    if existing is not None:
        history = existing.get("session_history")
        if history is None and existing.get("total_usage", {}).get("total_tokens"):
            raise ValueError(
                f"{path.name} already records spending for {month} but not the "
                "calls behind it, so the two cannot be added together"
            )
        incoming.extend(history or [])

    path.parent.mkdir(parents=True, exist_ok=True)
    folded = fold_usage_records(incoming, month)
    path.write_text(json.dumps(folded, indent=2))


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    """Read one JSON file, or return ``None`` if it isn't there or won't parse."""
    try:
        with open(path, "r") as f:
            loaded = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s: %s", path, e)
        return None
    return loaded if isinstance(loaded, dict) else None


def _discard(path: Path) -> None:
    """Remove a file that has been copied to its new home."""
    try:
        path.unlink()
    except OSError as e:
        logger.warning("Moved %s but could not remove the original: %s", path, e)


def move_a_folder_of_things(old: Path, new: Path) -> tuple[int, list[str]]:
    """Move every item in one folder into another, keeping what is already there.

    Written for whole folders that stand for one thing each — a conversation,
    with everything it gathered inside it — which is why nothing is merged: an
    item already at the destination is left alone and reported rather than
    written over.

    Args:
        old: The folder to empty. Nothing happens if it isn't there.
        new: The folder to fill. Made if it isn't there.

    Returns:
        How many items moved, and a sentence about each one that did not.
    """
    if not old.is_dir() or old.resolve() == new.resolve():
        return 0, []

    moved, left = 0, []
    new.mkdir(parents=True, exist_ok=True)
    for item in sorted(old.iterdir()):
        if item.name.startswith("."):
            continue
        target = new / item.name
        if target.exists():
            left.append(f"{item.name} is already in the new folder, so it was left where it was")
            continue
        try:
            shutil.move(str(item), str(target))
            moved += 1
        except OSError as e:
            left.append(f"{item.name} could not be moved: {e}")
    return moved, left
