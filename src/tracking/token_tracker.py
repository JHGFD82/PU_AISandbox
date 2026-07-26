"""Per-professor token usage tracking with monthly isolation and automatic archive rollover.

Every professor is tracked in one of two ways, chosen automatically and
invisibly to callers:

- **local** (the default, unchanged from before): one mutable JSON file per
  month, rewritten in place on every call, exactly as this module has
  always worked.
- **shared-write**: for a professor configured in ``.settings``
  (see ``src/settings_store.py``) as sharing usage tracking with
  another installation over a synced folder like Dropbox. Instead of
  rewriting one file — unsafe once two machines might do it near-
  simultaneously, since a plain file-sync service like Dropbox has no way
  to merge two conflicting edits — every API call writes its own small,
  uniquely-named file that's never edited again. Monthly/daily/model
  totals are computed by summing whatever event files exist. See
  ``docs/webui-plugin-plan.md`` section 1 for the full design and the
  reasoning behind it.

Every public method on ``TokenTracker`` (``record_usage``,
``get_daily_usage``, ``get_monthly_usage``, ``get_all_time_usage``,
``list_archived_months``) behaves identically from the caller's point of
view in both modes — a plugin never needs to know or care which mode a
given professor is in.
"""

import json
import logging
import os
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import make_safe_filename
from ..console import print_banner, print_subsection
from ..models.catalog import (
    get_model_pricing,
    get_monthly_limit,
    get_pricing_unit,
)
from ..settings import BUDGET_WARNING_THRESHOLD
from ..settings_store import (
    ExternalSource,
    get_configured_sources,
    get_shared_write_source,
    get_source_id,
)

# Constants
USAGE_DATA_DIR = "data"
ARCHIVES_SUBDIR = "archives"
EVENTS_SUBDIR = "events"


def get_usage_data_path(professor: str) -> Path:
    """Return the active (current-month) data file path for a professor."""
    project_root = Path(__file__).parent.parent.parent
    base_dir = project_root / USAGE_DATA_DIR
    base_dir.mkdir(exist_ok=True)
    return base_dir / f"token_usage_{professor.lower()}.json"


def get_archive_dir(professor: str) -> Path:
    """Return (and create if needed) the archive directory for a professor."""
    project_root = Path(__file__).parent.parent.parent
    archive_dir = project_root / USAGE_DATA_DIR / ARCHIVES_SUBDIR / professor.lower()
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir


def get_archive_path(professor: str, month: str) -> Path:
    """Return the archive file path for a professor and month string (e.g. '2026-02')."""
    return get_archive_dir(professor) / f"{month}.json"


def _shared_event_dir(source: "ExternalSource", professor: str, month: str) -> Path:
    """Return the event-file directory for one professor/month inside a shared-write source."""
    return source.resolved_path() / EVENTS_SUBDIR / make_safe_filename(professor) / month


def _shared_events_root(source: "ExternalSource", professor: str) -> Path:
    """Return the directory holding every month's event files for one professor."""
    return source.resolved_path() / EVENTS_SUBDIR / make_safe_filename(professor)


def _shared_archive_dir(source: "ExternalSource", professor: str) -> Path:
    """Return the archive directory for one professor inside a shared-write source."""
    return source.resolved_path() / ARCHIVES_SUBDIR / make_safe_filename(professor)


def _shared_archive_path(source: "ExternalSource", professor: str, month: str) -> Path:
    """Return the archive file path for one professor/month inside a shared-write source."""
    return _shared_archive_dir(source, professor) / f"{month}.json"


def get_configured_data_roots() -> list[tuple[str, Path]]:
    """Return every data-shaped directory that should be scanned when building an aggregate usage report.

    This is this installation's own local ``data/`` folder plus every
    external source configured in ``.settings`` — see
    ``src/settings_store.py`` and ``docs/webui-plugin-plan.md``
    section 1. Used by ``data/visualize_usage.py`` and, eventually, the web
    UI's spend sidebar, so that logic to combine multiple installations'
    usage history lives in exactly one place.

    Returns:
        A list of ``(label, path)`` pairs, always starting with
        ``("local", <this installation's data/ folder>)``.
    """
    project_root = Path(__file__).parent.parent.parent
    roots: list[tuple[str, Path]] = [("local", project_root / USAGE_DATA_DIR)]
    for source in get_configured_sources():
        roots.append((source.label, source.resolved_path()))
    return roots


def _read_event_files_with_failures(event_dir: Path) -> tuple[list[dict[str, Any]], list[Path]]:
    """Read every per-call usage record in a shared-write event-file directory.

    Same as ``_read_event_files()``, but also reports which files couldn't be
    read. Callers that only display totals don't care — a skipped file just
    means a slightly low number this time round. The one caller that *must*
    care is the month-rollover step, which deletes these files once it has
    folded them into an archive: deleting a file whose contents never made it
    into that archive destroys the record for good.

    Args:
        event_dir: The directory to read (e.g. one professor's current
                   month under a shared source's ``events/`` tree).
                   Missing directories simply yield no records.

    Returns:
        A ``(records, unreadable_paths)`` pair. ``unreadable_paths`` is empty
        when every file was read successfully.
    """
    records: list[dict[str, Any]] = []
    unreadable: list[Path] = []
    if not event_dir.exists():
        return records, unreadable
    for event_file in sorted(event_dir.glob("*.json")):
        try:
            with open(event_file, "r") as f:
                records.append(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Could not read usage event file {event_file.name}: {e}")
            unreadable.append(event_file)
    return records, unreadable


def _read_event_files(event_dir: Path) -> list[dict[str, Any]]:
    """Read every per-call usage record in a shared-write event-file directory.

    Each file holds one API call's record (the same shape as a
    ``TokenUsage``, saved as JSON). Files that can't be parsed are skipped
    with a warning rather than aborting the whole read, matching how
    corrupt archive files are already handled elsewhere in this module.

    Args:
        event_dir: The directory to read (e.g. one professor's current
                   month under a shared source's ``events/`` tree).
                   Missing directories simply yield no records.
    """
    records, _ = _read_event_files_with_failures(event_dir)
    return records


def fold_usage_records(records: list[dict[str, Any]], month: str) -> dict[str, Any]:
    """Build the standard month-summary shape from a flat list of individual call records.

    This is the one place that turns "a pile of individual API-call
    records" into the same ``{total_usage, model_usage, daily_usage,
    session_history}`` shape every local monthly file and archive already
    uses — needed because shared-write mode stores one file per call rather
    than one continuously-updated summary file (see the module docstring).
    Reused for the in-memory totals a live ``TokenTracker`` shows, for
    folding a closed month's event files into an archive, and by
    ``load_usage_tree()`` below when reading someone else's shared-write
    directory for a report.

    Args:
        records: A list of plain dictionaries, each shaped like a
                 ``TokenUsage`` (as read from event files or a
                 ``session_history`` list).
        month: The month string (``'YYYY-MM'``) this batch of records
               belongs to, stamped onto the returned summary.

    Returns:
        A dictionary with the same keys as ``TokenTracker._empty_usage_data()``:
        ``month``, ``total_usage``, ``model_usage``, ``daily_usage``, and
        ``session_history`` (sorted chronologically).
    """
    total = UsageStats()
    model_usage: dict[str, Any] = {}
    daily_usage: dict[str, Any] = {}
    session_history = sorted(records, key=lambda r: r.get("timestamp", ""))

    for rec in session_history:
        prompt_tokens = rec.get("prompt_tokens", 0)
        completion_tokens = rec.get("completion_tokens", 0)
        total_tokens = rec.get("total_tokens", 0)
        cost = rec.get("total_cost", 0.0)

        total.add_usage(prompt_tokens, completion_tokens, total_tokens, cost)

        model = rec.get("model", "unknown")
        model_usage.setdefault(model, UsageStats().to_dict())
        _accumulate_stats_dict(model_usage[model], prompt_tokens, completion_tokens, total_tokens, cost)

        day = rec.get("timestamp", "")[:10]
        if day:
            daily_usage.setdefault(day, UsageStats().to_dict())
            _accumulate_stats_dict(daily_usage[day], prompt_tokens, completion_tokens, total_tokens, cost)

    return {
        "month": month,
        "total_usage": total.to_dict(),
        "model_usage": model_usage,
        "daily_usage": daily_usage,
        "session_history": session_history,
    }


def _accumulate_stats_dict(stats: dict[str, Any], prompt_tokens: int, completion_tokens: int,
                            total_tokens: int, cost: float) -> None:
    """Add one call's numbers into a plain stats dictionary in place (module-level twin of ``TokenTracker._update_stats``)."""
    stats["total_tokens"] += total_tokens
    stats["total_input_tokens"] += prompt_tokens
    stats["total_output_tokens"] += completion_tokens
    stats["total_cost"] += cost
    stats["call_count"] = stats.get("call_count", 0) + 1


def load_usage_tree(base_dir: Path) -> dict[str, dict[str, Any]]:
    """Read one data-shaped directory into ``{professor: {month: month_data}}``.

    Understands every on-disk shape this project produces: a current-month
    mutable file (``token_usage_{professor}.json``), closed-month archive
    files (``archives/{professor}/{month}.json``), and still-open
    shared-write event files (``events/{professor}/{month}/*.json``,
    folded into the same shape on the fly via ``fold_usage_records()``).
    This is the one place this logic lives, reused by
    ``data/visualize_usage.py`` and, eventually, the web UI's spend
    sidebar — see ``docs/webui-plugin-plan.md`` section 1.

    Args:
        base_dir: A directory shaped like this project's ``data/`` folder —
                  typically either this installation's own local ``data/``,
                  or one entry from ``get_configured_data_roots()``.

    Returns:
        A nested dictionary: outer keys are professor safe-names, inner
        keys are ``'YYYY-MM'`` month strings, values are month-summary
        dictionaries in the standard shape.
    """
    result: dict[str, dict[str, Any]] = {}
    if not base_dir.exists():
        return result

    # 1. Current-month mutable files (local / read-only professors)
    for active_file in sorted(base_dir.glob("token_usage_*.json")):
        prof = active_file.stem.replace("token_usage_", "")
        try:
            with open(active_file, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Could not read usage file {active_file.name}: {e}")
            continue
        result.setdefault(prof, {})[data.get("month", "")] = data

    # 2. Closed-month archives — works for both local rollover and shared-write rollover output
    archives_root = base_dir / ARCHIVES_SUBDIR
    if archives_root.exists():
        for prof_dir in sorted(archives_root.iterdir()):
            if not prof_dir.is_dir():
                continue
            prof = prof_dir.name
            for archive_file in sorted(prof_dir.glob("*.json")):
                month = archive_file.stem
                try:
                    with open(archive_file, "r") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    logging.warning(f"Could not read archive {archive_file.name}: {e}")
                    continue
                result.setdefault(prof, {})[month] = data

    # 3. Still-open shared-write event files (usually just the current month)
    events_root = base_dir / EVENTS_SUBDIR
    if events_root.exists():
        for prof_dir in sorted(events_root.iterdir()):
            if not prof_dir.is_dir():
                continue
            prof = prof_dir.name
            for month_dir in sorted(prof_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                month = month_dir.name
                records = _read_event_files(month_dir)
                if records:
                    result.setdefault(prof, {})[month] = fold_usage_records(records, month)

    return result


@dataclass
class TokenUsage:
    """A record of the tokens and cost consumed by a single API call.

    Instances are created by ``TokenTracker.record_usage()`` and stored in the
    professor's usage file under ``session_history`` so that every call can be
    reviewed after the fact.
    """
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    timestamp: str
    input_cost: float
    output_cost: float
    total_cost: float
    source: str = ""  # which installation made this call — see module docstring


@dataclass
class UsageStats:
    """Running totals for a group of API calls — used for per-model, per-day, and per-month summaries."""
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    call_count: int = 0

    def add_usage(self, prompt_tokens: int, completion_tokens: int, total_tokens: int, cost: float):
        """Add the token counts and cost from one API call to the running totals."""
        self.total_tokens += total_tokens
        self.total_input_tokens += prompt_tokens
        self.total_output_tokens += completion_tokens
        self.total_cost += cost
        self.call_count += 1

    def merge_dict(self, d: dict[str, Any]):
        """Add the totals from a raw dictionary (as read from a usage file) into this object."""
        self.total_tokens += d.get("total_tokens", 0)
        self.total_input_tokens += d.get("total_input_tokens", 0)
        self.total_output_tokens += d.get("total_output_tokens", 0)
        self.total_cost += d.get("total_cost", 0.0)
        self.call_count += d.get("call_count", 0)

    def to_dict(self) -> dict[str, Any]:
        """Convert this object to a plain dictionary suitable for saving to a JSON file."""
        return asdict(self)


class TokenTracker:
    """Tracks and manages token usage and costs for a specific professor.

    Each active file covers a single calendar month.  When a new month
    begins the previous file is automatically moved to the archives folder
    (``data/archives/{professor}/{YYYY-MM}.json``) and a fresh file is
    started.  All-time totals are computed on demand by aggregating the
    current file with every archive file.
    """

    def __init__(self, professor: str, data_file: str | None = None,
                 monthly_limit: float | None = None):
        """Set up token tracking for a professor, loading any existing usage data from disk.

        On startup, checks whether the stored usage file belongs to the current
        calendar month. If the file is from a previous month it is automatically
        moved to the archives folder and a fresh file is started (month
        rollover). All subsequent calls to ``record_usage()`` write to the new
        file.

        When *data_file* is left as ``None`` (normal operation — every real
        caller in this project does this), this also checks whether
        *professor* is configured for shared-write tracking in
        ``.settings`` (see ``src/settings_store.py``). If so,
        this tracker records usage as individual event files in the shared
        location instead of the local mutable file — see the module
        docstring for why. Tests that pass an explicit *data_file* always get
        the local, single-file behavior, since they're testing a specific
        file directly.

        Args:
            professor: The professor's safe-filename identifier, used to locate
                       and name the usage file (e.g. ``'heller'`` maps to
                       ``data/token_usage_heller.json``).
            data_file: Full path to an alternative usage file. ``None`` in
                       normal operation, which causes the path to be derived
                       automatically from the professor's safe filename.
                       Redirected to a temporary file in tests.
            monthly_limit: Override the monthly spending cap (in dollars) set
                           in ``model_catalog.json``. ``None`` in normal
                           operation, which causes the limit to be read from
                           the catalog. Only non-``None`` in tests.
        """
        self.professor = professor
        self.source_mode = "local"
        self._shared_source: ExternalSource | None = None
        self._source_id = get_source_id()

        if data_file:
            self.data_file = Path(data_file)
        else:
            shared_source = get_shared_write_source(professor)
            if shared_source is not None:
                self.source_mode = "shared-write"
                self._shared_source = shared_source
                self.data_file = None  # not used in shared-write mode
            else:
                self.data_file = get_usage_data_path(professor)

        self.monthly_limit = monthly_limit if monthly_limit is not None else get_monthly_limit()

        self._lock = threading.Lock()

        # Running total for calls made through this one TokenTracker
        # instance specifically — separate from the persisted monthly/daily/
        # all-time totals above, which mix in everything else this
        # professor has ever done. Since a fresh TokenTracker is created
        # per CLI run and per webui request/background job (see
        # SandboxProcessor.__init__), this is exactly "how much this one
        # run has spent so far" — e.g. so a multi-page translate job can
        # report its total token spend without diffing before/after
        # snapshots of the shared monthly file (which would be racy against
        # other concurrent activity for the same professor).
        self._session_lock = threading.Lock()
        self.session_usage: dict[str, Any] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "total_cost": 0.0,
        }

        if self.source_mode == "shared-write":
            self._rollover_closed_shared_months()
            self._refresh_shared_usage_data()
        else:
            self.usage_data = self._load_usage_data()

        logging.debug(
            f"Token tracking initialized for Professor {professor.title()} "
            f"(mode={self.source_mode}): "
            f"{self.data_file if self.data_file else self._shared_source.resolved_path()}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_current_date() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _get_current_month() -> str:
        return datetime.now().strftime("%Y-%m")

    def _empty_usage_data(self) -> dict[str, Any]:
        """Return a fresh, empty monthly data structure stamped with the current month."""
        return {
            "month": self._get_current_month(),
            "total_usage": UsageStats().to_dict(),
            "model_usage": {},
            "daily_usage": {},
            "session_history": [],
        }

    def _archive_month(self, data: dict[str, Any], month: str) -> None:
        """Write *data* to the archive file for *month*, skipping if already archived."""
        archive_path = get_archive_path(self.professor, month)
        if archive_path.exists():
            logging.info(f"Archive already exists for {month}, skipping: {archive_path.name}")
            return
        with open(archive_path, "w") as f:
            json.dump(data, f, indent=2)
        logging.info(f"Archived {self.professor} month {month} → {archive_path.name}")

    def _load_usage_data(self) -> dict[str, Any]:
        """Load usage data, handling month rollover."""
        if not self.data_file.exists():
            return self._empty_usage_data()

        with open(self.data_file, "r") as f:
            data = json.load(f)

        # Rollover: file belongs to a past month → archive it and start fresh
        stored_month = data.get("month", "")
        current_month = self._get_current_month()
        if stored_month < current_month:
            logging.info(f"Month rollover detected for {self.professor}: {stored_month} → {current_month}")
            self._archive_month(data, stored_month)
            fresh = self._empty_usage_data()
            self._save_usage_data_to(fresh)
            return fresh

        return data

    def _save_usage_data_to(self, data: dict[str, Any]) -> None:
        """Write *data* to self.data_file, replacing the old contents in one step.

        The new contents are written to a temporary file alongside the real
        one and only then moved into place, rather than opening the real file
        and overwriting it directly. Writing directly would empty the file
        first and fill it back in afterwards, leaving a brief moment where it
        is half-written — and this file is read by other things while a run is
        in progress: ``usage report`` from a second terminal, and the web
        interface's spending sidebar, which polls it. A reader landing in that
        moment sees an incomplete file and fails. Moving a finished file into
        place is a single, indivisible step, so a reader always sees either
        the complete old version or the complete new one.

        This also means an interrupted run — a crash, a closed laptop, a
        power cut — can no longer leave the month's accounting truncated:
        the original file is untouched until the replacement is complete.

        (This is the same approach ``ConversationStore.save()`` and
        ``save_model_catalog()`` already use, for the same reason.)

        Args:
            data: The complete usage record to write, in the same shape
                  ``_load_usage_data()`` returns.
        """
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        # The temporary file goes in the same folder as the real one, because
        # moving a file into place is only guaranteed to be a single step when
        # both are on the same disk. Its name includes the process and thread
        # doing the writing so two writers never reuse the same scratch file.
        tmp_path = self.data_file.with_name(
            f"{self.data_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.data_file)
        except Exception:
            # Don't leave scratch files behind in data/ if the write failed.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _save_usage_data(self) -> None:
        """Save the current in-memory usage data."""
        self._save_usage_data_to(self.usage_data)

    def _update_stats(self, stats: dict[str, Any], prompt_tokens: int, completion_tokens: int,
                      total_tokens: int, cost: float) -> None:
        """Mutate a stats dictionary in-place."""
        stats["total_tokens"] += total_tokens
        stats["total_input_tokens"] += prompt_tokens
        stats["total_output_tokens"] += completion_tokens
        stats["total_cost"] += cost
        stats.setdefault("call_count", 0)
        stats["call_count"] += 1

    def _calculate_costs(self, model: str, prompt_tokens: int,
                         completion_tokens: int) -> tuple[float, float, float]:
        """Return (input_cost, output_cost, total_cost) for the given token counts."""
        pricing_unit = get_pricing_unit()
        pricing = get_model_pricing(model)
        input_cost = (prompt_tokens / pricing_unit) * pricing["input"]
        output_cost = (completion_tokens / pricing_unit) * pricing["output"]
        return input_cost, output_cost, input_cost + output_cost

    # ------------------------------------------------------------------
    # Shared-write mode — event-file recording and reading
    #
    # Every method here is the shared-write counterpart of a same-named
    # public method above (e.g. _record_usage_shared / record_usage). See
    # the module docstring for why this mode exists and how it differs
    # from the default local mode.
    # ------------------------------------------------------------------

    def _refresh_shared_usage_data(self) -> None:
        """Recompute ``self.usage_data`` for the current month from whatever event files exist right now.

        Shared-write mode has no single mutable file to be the source of
        truth, since another installation could be adding files to the same
        folder at any moment — so instead of trusting an in-memory cache,
        this re-reads every event file for the current month each time it's
        called and rebuilds the same ``{total_usage, model_usage,
        daily_usage, session_history}`` shape local mode keeps in memory.
        Keeping ``self.usage_data`` populated this way (rather than leaving
        it unset in this mode) means ``print_usage_report()`` and anything
        else that reads ``self.usage_data`` directly keep working unchanged
        in either mode.
        """
        month = self._get_current_month()
        event_dir = _shared_event_dir(self._shared_source, self.professor, month)
        records = _read_event_files(event_dir)
        self.usage_data = fold_usage_records(records, month)

    def _record_usage_shared(self, model: str, prompt_tokens: int, completion_tokens: int,
                              total_tokens: int, requested_model: str | None = None) -> TokenUsage:
        """Shared-write version of ``record_usage()`` — writes one new event file instead of rewriting a shared file.

        No read-modify-write happens here at all: the filename itself
        (timestamp + a short random suffix + this installation's source id)
        is guaranteed unique, so there's nothing for a concurrent write from
        another installation to collide with — this is what makes it safe
        over a plain file-sync service like Dropbox, which has no way to
        merge two conflicting edits to the same file. See the module
        docstring.
        """
        with self._lock:
            timestamp = datetime.now().isoformat()
            pricing_model = requested_model if requested_model else model
            if requested_model and requested_model != model:
                logging.debug(f"Using requested model '{requested_model}' for pricing instead of API model '{model}'")

            input_cost, output_cost, total_cost = self._calculate_costs(pricing_model, prompt_tokens, completion_tokens)

            usage = TokenUsage(
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                timestamp=timestamp,
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
                source=self._source_id,
            )

            month = self._get_current_month()
            event_dir = _shared_event_dir(self._shared_source, self.professor, month)
            event_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}_{secrets.token_hex(3)}_{self._source_id}.json"
            with open(event_dir / filename, "w") as f:
                json.dump(asdict(usage), f, indent=2)

            self._refresh_shared_usage_data()

        return usage

    def _get_daily_usage_shared(self, date: str | None = None) -> dict[str, Any]:
        """Shared-write version of ``get_daily_usage()``."""
        if date is None:
            date = self._get_current_date()
        month = date[:7]
        if month == self._get_current_month():
            self._refresh_shared_usage_data()
            return self.usage_data["daily_usage"].get(date, UsageStats().to_dict())

        archive_path = _shared_archive_path(self._shared_source, self.professor, month)
        if archive_path.exists():
            with open(archive_path, "r") as f:
                archive = json.load(f)
            return archive.get("daily_usage", {}).get(date, UsageStats().to_dict())
        return UsageStats().to_dict()

    def _get_monthly_usage_shared(self, month: str | None = None) -> dict[str, Any]:
        """Shared-write version of ``get_monthly_usage()``."""
        if month is None:
            month = self._get_current_month()

        if month == self._get_current_month():
            self._refresh_shared_usage_data()
            return self.usage_data["total_usage"]

        archive_path = _shared_archive_path(self._shared_source, self.professor, month)
        if archive_path.exists():
            with open(archive_path, "r") as f:
                archive = json.load(f)
            return archive.get("total_usage", UsageStats().to_dict())
        return UsageStats().to_dict()

    def _get_all_time_usage_shared(self) -> dict[str, Any]:
        """Shared-write version of ``get_all_time_usage()``."""
        combined = UsageStats()
        self._refresh_shared_usage_data()
        combined.merge_dict(self.usage_data["total_usage"])

        archive_dir = _shared_archive_dir(self._shared_source, self.professor)
        if archive_dir.exists():
            for archive_file in sorted(archive_dir.glob("*.json")):
                try:
                    with open(archive_file, "r") as f:
                        arc = json.load(f)
                    combined.merge_dict(arc.get("total_usage", {}))
                except (json.JSONDecodeError, KeyError) as e:
                    logging.warning(f"Could not read shared archive {archive_file.name}: {e}")

        return combined.to_dict()

    def _rollover_closed_shared_months(self) -> None:
        """Fold any event-file months older than the current month into a single archive file.

        Only ever touches months nobody is still writing to, which is what
        makes this safe to run from either installation without
        coordination: whichever installation gets to a given closed month
        first writes its archive file (skipped if one already exists,
        mirroring ``_archive_month()``'s existing-file check), and cleanup
        of the now-redundant event files is best-effort — if another
        installation already deleted one, that's not treated as an error,
        since the archive is correct either way. Note this can't be made
        fully airtight against genuinely simultaneous rollover from two
        machines, since Dropbox-style sync is eventually consistent rather
        than a live shared filesystem — an acceptable limitation for a
        once-a-month, already-closed-data operation.
        """
        events_root = _shared_events_root(self._shared_source, self.professor)
        if not events_root.exists():
            return

        current_month = self._get_current_month()
        for month_dir in sorted(events_root.iterdir()):
            if not month_dir.is_dir() or month_dir.name >= current_month:
                continue
            month = month_dir.name
            archive_path = _shared_archive_path(self._shared_source, self.professor, month)
            records, unreadable = _read_event_files_with_failures(month_dir)

            # A month whose event files couldn't all be read is left entirely
            # alone — not archived, not deleted. Both halves matter:
            #
            # Deleting a file that wasn't folded in would destroy that API
            # call's record for good. But writing the archive from only the
            # files that *did* read is just as damaging in a quieter way:
            # every later run sees an archive already exists, skips folding
            # it again, and the missing calls are never recovered even once
            # the unreadable files become readable.
            #
            # And they usually do. This is routine for the storage this mode
            # is built for — a folder synced by Dropbox or OneDrive regularly
            # holds a placeholder or partially-downloaded file that fails to
            # parse now and reads perfectly a minute later. So the whole
            # month is deferred and retried on the next run, by which point
            # the sync has normally caught up.
            if unreadable:
                logging.warning(
                    "Shared-write month %s for %s: leaving it untouched because "
                    "%d of its usage files could not be read (%s). They may still "
                    "be syncing — this month will be archived on a later run once "
                    "all of them can be read. Nothing has been deleted.",
                    month, self.professor, len(unreadable),
                    ", ".join(p.name for p in unreadable[:5])
                    + (", ..." if len(unreadable) > 5 else ""),
                )
                continue

            if not archive_path.exists():
                if records:
                    folded = fold_usage_records(records, month)
                    archive_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp_path = archive_path.with_suffix(".tmp")
                    with open(tmp_path, "w") as f:
                        json.dump(folded, f, indent=2)
                    os.replace(tmp_path, archive_path)
                    logging.info(f"Folded shared-write month {month} for {self.professor} → {archive_path.name}")
                else:
                    # Nothing readable and nothing archived: an empty
                    # directory, or one whose files vanished mid-read. Leave
                    # it alone rather than tidying away something not
                    # understood.
                    continue

            for event_file in month_dir.glob("*.json"):
                try:
                    event_file.unlink()
                except FileNotFoundError:
                    pass  # another installation already cleaned this one up
            try:
                month_dir.rmdir()
            except OSError:
                pass  # not empty (rare race) or already removed — fine either way

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_usage(self, model: str, prompt_tokens: int, completion_tokens: int,
                     total_tokens: int, requested_model: str | None = None) -> TokenUsage:
        """Record that an API call was made and update all running totals in the usage file.

        Updates the professor's totals for the current month, today's date, and
        the specific model used, then immediately writes the updated file to
        disk so no data is lost if the program exits unexpectedly.

        To prevent two background workers from writing at the same time and
        overwriting each other's data, this method acquires an exclusive turn
        before updating — only one call can proceed at a time (thread safety
        via a lock).

        Args:
            model: The model name as reported back by the API — may include a
                   date suffix (e.g. ``'gpt-4o-2024-08-06'``).
            prompt_tokens: Number of tokens in the input sent to the model.
            completion_tokens: Number of tokens in the model's response.
            total_tokens: Combined input and output token count.
            requested_model: The model name used when making the request. When
                             the API returns a different name than was requested
                             (e.g. a dated alias), this value is used for
                             pricing lookup instead. ``None`` when the
                             requested and returned model names are the same.

        Returns:
            A ``TokenUsage`` record with the full breakdown of tokens and costs
            for this call.
        """
        if self.source_mode == "shared-write":
            usage = self._record_usage_shared(model, prompt_tokens, completion_tokens, total_tokens, requested_model)
            self._accumulate_session_usage(usage)
            return usage

        with self._lock:
            timestamp = datetime.now().isoformat()
            pricing_model = requested_model if requested_model else model
            if requested_model and requested_model != model:
                logging.debug(f"Using requested model '{requested_model}' for pricing instead of API model '{model}'")

            input_cost, output_cost, total_cost = self._calculate_costs(pricing_model, prompt_tokens, completion_tokens)

            usage = TokenUsage(
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                timestamp=timestamp,
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
                source=self._source_id,
            )

            self._update_stats(self.usage_data["total_usage"], prompt_tokens, completion_tokens, total_tokens, total_cost)

            if model not in self.usage_data["model_usage"]:
                self.usage_data["model_usage"][model] = UsageStats().to_dict()
            self._update_stats(self.usage_data["model_usage"][model], prompt_tokens, completion_tokens, total_tokens, total_cost)

            date_str = self._get_current_date()
            if date_str not in self.usage_data["daily_usage"]:
                self.usage_data["daily_usage"][date_str] = UsageStats().to_dict()
            self._update_stats(self.usage_data["daily_usage"][date_str], prompt_tokens, completion_tokens, total_tokens, total_cost)

            self.usage_data["session_history"].append(asdict(usage))
            self._save_usage_data()

        self._accumulate_session_usage(usage)
        return usage

    def _accumulate_session_usage(self, usage: TokenUsage) -> None:
        """Add one API call's usage to this instance's running session total.

        Args:
            usage: The ``TokenUsage`` just recorded, from either code path
                   in ``record_usage()``.
        """
        with self._session_lock:
            self.session_usage["prompt_tokens"] += usage.prompt_tokens
            self.session_usage["completion_tokens"] += usage.completion_tokens
            self.session_usage["total_tokens"] += usage.total_tokens
            self.session_usage["total_cost"] += usage.total_cost

    def get_session_usage(self) -> dict[str, Any]:
        """Return the running token/cost total for everything recorded through this TokenTracker instance.

        Unlike ``get_monthly_usage()``/``get_all_time_usage()``, this has
        nothing to do with calendar months or this professor's overall
        history — it's scoped to this one instance's lifetime, which in
        practice means "this one CLI run" or "this one webui request/
        background job" (see ``SandboxProcessor.__init__``, which creates
        exactly one ``TokenTracker`` per run). Useful for reporting how much
        a single multi-call operation (e.g. translating a multi-page
        document, one API call per page) spent in total, without needing to
        diff before/after snapshots of the shared persisted totals.

        Returns:
            A dict with ``'prompt_tokens'``, ``'completion_tokens'``,
            ``'total_tokens'`` (all ``int``), and ``'total_cost'``
            (``float``) — all ``0``/``0.0`` if nothing has been recorded
            yet through this instance.
        """
        with self._session_lock:
            return dict(self.session_usage)

    def get_daily_usage(self, date: str | None = None) -> dict[str, Any]:
        """Return the token totals for a single day from the current month's usage file.

        Args:
            date: The date to look up in ``YYYY-MM-DD`` format
                  (e.g. ``'2026-06-23'``). Defaults to today if not provided.

        Returns:
            A stats dictionary with ``total_tokens``, ``total_cost``,
            ``call_count``, and related fields. Returns a zeroed-out stats
            dictionary if no usage was recorded for that date.
        """
        if self.source_mode == "shared-write":
            return self._get_daily_usage_shared(date)

        if date is None:
            date = self._get_current_date()
        return self.usage_data["daily_usage"].get(date, UsageStats().to_dict())

    def get_monthly_usage(self, month: str | None = None) -> dict[str, Any]:
        """Return the token totals for an entire calendar month.

        For the current month, reads directly from the in-memory totals.
        For any past month, reads the corresponding file from the archives
        folder (``data/archives/{professor}/{YYYY-MM}.json``).

        Args:
            month: The month to look up in ``YYYY-MM`` format
                   (e.g. ``'2026-05'``). Defaults to the current month if not
                   provided.

        Returns:
            A stats dictionary with ``total_tokens``, ``total_cost``,
            ``call_count``, and related fields. Returns a zeroed-out stats
            dictionary if no archive file exists for the requested month.
        """
        if self.source_mode == "shared-write":
            return self._get_monthly_usage_shared(month)

        if month is None:
            month = self._get_current_month()

        if month == self._get_current_month():
            return self.usage_data["total_usage"]

        # Load from archive when requesting a past month
        archive_path = get_archive_path(self.professor, month)
        if archive_path.exists():
            with open(archive_path, "r") as f:
                archive = json.load(f)
            return archive.get("total_usage", UsageStats().to_dict())

        return UsageStats().to_dict()

    def get_all_time_usage(self) -> dict[str, Any]:
        """Return the cumulative token totals across every month on record.

        Adds together the current month's totals and every archived month file
        found in ``data/archives/{professor}/``. Archive files that cannot be
        read are skipped with a warning rather than causing the whole call to fail.

        Returns:
            A stats dictionary with the combined ``total_tokens``,
            ``total_cost``, ``call_count``, and related fields across all time.
        """
        if self.source_mode == "shared-write":
            return self._get_all_time_usage_shared()

        combined = UsageStats()
        combined.merge_dict(self.usage_data["total_usage"])

        archive_dir = get_archive_dir(self.professor)
        for archive_file in sorted(archive_dir.glob("*.json")):
            try:
                with open(archive_file, "r") as f:
                    arc = json.load(f)
                combined.merge_dict(arc.get("total_usage", {}))
            except (json.JSONDecodeError, KeyError) as e:
                logging.warning(f"Could not read archive {archive_file.name}: {e}")

        return combined.to_dict()

    def _archive_path_for(self, month: str) -> Path:
        """Return the archive file for *month*, wherever this tracker keeps its archives.

        Where archives live depends on how this professor's usage is being
        recorded: normally under this installation's own ``data/archives/``,
        but in shared-write mode inside the shared folder instead. Everything
        that reads an archive goes through here, so no caller has to remember
        the distinction — forgetting it produced reports that said
        ``No archive found for 2026-05.  Available: 2026-05``, having looked
        for the file in one place and listed the other.

        Args:
            month: The month to locate, as ``YYYY-MM`` (e.g. ``'2026-05'``).

        Returns:
            The path the archive for that month would have, whether or not
            it exists yet.
        """
        if self.source_mode == "shared-write":
            return _shared_archive_path(self._shared_source, self.professor, month)
        return get_archive_path(self.professor, month)

    def list_archived_months(self) -> list[str]:
        """Return a sorted list of month strings that have been archived."""
        if self.source_mode == "shared-write":
            archive_dir = _shared_archive_dir(self._shared_source, self.professor)
        else:
            archive_dir = get_archive_dir(self.professor)
        return sorted(p.stem for p in archive_dir.glob("*.json")) if archive_dir.exists() else []

    def _get_monthly_budget_status(self, month: str | None = None) -> dict[str, Any]:
        """Return a dict summarising budget consumption for *month*."""
        monthly_usage = self.get_monthly_usage(month)
        usage_pct = (monthly_usage["total_cost"] / self.monthly_limit) * 100 if self.monthly_limit > 0 else 0.0
        remaining = max(0.0, self.monthly_limit - monthly_usage["total_cost"])
        return {
            "monthly_usage": monthly_usage,
            "usage_percentage": usage_pct,
            "remaining_budget": remaining,
            "is_exceeded": monthly_usage["total_cost"] >= self.monthly_limit,
            "approaching_limit": usage_pct > BUDGET_WARNING_THRESHOLD,
        }

    def print_usage_report(self, month: str | None = None, include_all_time: bool = False):
        """Print a formatted usage report to the terminal.

        For the current month, shows token totals, a per-model breakdown,
        today's usage, and remaining monthly budget. For a past month, shows
        the archived totals and a daily breakdown for that month.

        Args:
            month: A past month to report on in ``YYYY-MM`` format
                   (e.g. ``'2025-07'``). Omit to report on the current month.
            include_all_time: When ``True`` (and reporting on the current
                              month), also prints cumulative totals across every
                              archived month plus the current one.
        """
        current_month = self._get_current_month()

        # ── Archived month report ──────────────────────────────────
        if month and month != current_month:
            # _archive_path_for(), not get_archive_path(): in shared-write
            # mode the archives live in the shared folder, and looking in the
            # local one produced the contradictory "No archive found for
            # 2026-05.  Available: 2026-05" — the "Available" list below is
            # built by list_archived_months(), which always looked in the
            # right place.
            archive_path = self._archive_path_for(month)
            if not archive_path.exists():
                archived = self.list_archived_months()
                hint = f"  Available: {', '.join(archived)}" if archived else "  No archives found."
                print(f"No archive found for {month}.\n{hint}")
                return
            with open(archive_path, "r") as f:
                arc = json.load(f)

            total = arc["total_usage"]
            print_banner(f"TOKEN USAGE REPORT - PROFESSOR {self.professor.upper()}")
            print_subsection(f"Archived Month ({month})")
            print(f"Total Tokens Used: {total['total_tokens']:,}")
            print(f"  • Input Tokens:  {total['total_input_tokens']:,}")
            print(f"  • Output Tokens: {total['total_output_tokens']:,}")
            print(f"Total Cost: ${total['total_cost']:.4f}")
            print(f"API Calls:  {total['call_count']}")

            print_subsection("Model Breakdown")
            for mdl, data in arc.get("model_usage", {}).items():
                print(f"{mdl}:")
                print(f"  • Calls:  {data['call_count']}")
                print(f"  • Tokens: {data['total_tokens']:,}")
                print(f"  • Cost:   ${data['total_cost']:.4f}")

            print_subsection("Daily Breakdown")
            for day in sorted(arc.get("daily_usage", {}).keys()):
                d = arc["daily_usage"][day]
                calls = d.get("call_count", "?")
                print(f"{day}: {d['total_tokens']:,} tokens  ${d['total_cost']:.4f}  ({calls} calls)")

            print("=" * 60)
            return

        # ── Current month report ───────────────────────────────────
        monthly_total = self.usage_data["total_usage"]

        print_banner(f"TOKEN USAGE REPORT - PROFESSOR {self.professor.upper()}")
        print_subsection(f"Current Month ({current_month})")
        print(f"Total Tokens Used: {monthly_total['total_tokens']:,}")
        print(f"  • Input Tokens:  {monthly_total['total_input_tokens']:,}")
        print(f"  • Output Tokens: {monthly_total['total_output_tokens']:,}")
        print(f"Total Cost: ${monthly_total['total_cost']:.4f}")

        print_subsection("Model Breakdown (this month)")
        for model, data in self.usage_data["model_usage"].items():
            print(f"{model}:")
            print(f"  • Calls:  {data['call_count']}")
            print(f"  • Tokens: {data['total_tokens']:,}")
            print(f"  • Cost:   ${data['total_cost']:.4f}")

        # Today's usage
        today_usage = self.get_daily_usage()
        if today_usage["total_tokens"] > 0:
            print_subsection(f"Today's Usage ({self._get_current_date()})")
            print(f"Tokens: {today_usage['total_tokens']:,}")
            print(f"Cost:   ${today_usage['total_cost']:.4f}")

        # Monthly budget
        budget_status = self._get_monthly_budget_status()
        print_subsection(f"Monthly Budget ({current_month})")
        print(f"Monthly Limit: ${self.monthly_limit:.2f}")
        print(f"Used:          ${monthly_total['total_cost']:.4f} ({budget_status['usage_percentage']:.1f}%)")
        print(f"Remaining:     ${budget_status['remaining_budget']:.2f}")

        if budget_status["is_exceeded"]:
            print("⚠️  MONTHLY LIMIT EXCEEDED!")
        elif budget_status["approaching_limit"]:
            print("⚠️  Approaching monthly limit!")

        # ── All-time totals (optional) ─────────────────────────────
        if include_all_time:
            archived = self.list_archived_months()
            if archived:
                all_time = self.get_all_time_usage()
                print_subsection(f"All-Time Totals (across {len(archived)} archived month(s) + current)")
                print(f"Total Tokens: {all_time['total_tokens']:,}")
                print(f"  • Input:    {all_time['total_input_tokens']:,}")
                print(f"  • Output:   {all_time['total_output_tokens']:,}")
                print(f"Total Cost:   ${all_time['total_cost']:.4f}")
                print(f"Total Calls:  {all_time['call_count']}")
                print(f"Archived months: {', '.join(archived)}")
            else:
                print("\n(No archived months yet — all usage is in the current month.)")

        print("=" * 60)

