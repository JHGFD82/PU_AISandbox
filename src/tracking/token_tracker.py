"""Per-professor token usage tracking with monthly isolation and automatic archive rollover."""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

from ..models.catalog import (
    get_model_pricing, get_pricing_unit,
    get_monthly_limit,
)
from ..console import print_banner, print_subsection
from ..settings import BUDGET_WARNING_THRESHOLD


# Constants
USAGE_DATA_DIR = "data"
ARCHIVES_SUBDIR = "archives"


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


@dataclass
class TokenUsage:
    """Token usage data for a single API call."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    timestamp: str
    input_cost: float
    output_cost: float
    total_cost: float


@dataclass
class UsageStats:
    """Usage statistics structure."""
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    call_count: int = 0

    def add_usage(self, prompt_tokens: int, completion_tokens: int, total_tokens: int, cost: float):
        """Add usage data to the statistics."""
        self.total_tokens += total_tokens
        self.total_input_tokens += prompt_tokens
        self.total_output_tokens += completion_tokens
        self.total_cost += cost
        self.call_count += 1

    def merge_dict(self, d: Dict[str, Any]):
        """Merge a stats dictionary into this object."""
        self.total_tokens += d.get("total_tokens", 0)
        self.total_input_tokens += d.get("total_input_tokens", 0)
        self.total_output_tokens += d.get("total_output_tokens", 0)
        self.total_cost += d.get("total_cost", 0.0)
        self.call_count += d.get("call_count", 0)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return asdict(self)


class TokenTracker:
    """Tracks and manages token usage and costs for a specific professor.

    Each active file covers a single calendar month.  When a new month
    begins the previous file is automatically moved to the archives folder
    (``data/archives/{professor}/{YYYY-MM}.json``) and a fresh file is
    started.  All-time totals are computed on demand by aggregating the
    current file with every archive file.
    """

    def __init__(self, professor: str, data_file: Optional[str] = None,
                 monthly_limit: Optional[float] = None):
        """Initialize the token tracker.

        Args:
            professor:     Professor name used for file naming and archive paths.
            data_file:     Override the default data file path entirely.
            monthly_limit: Override the configured monthly spending limit.
        """
        self.professor = professor

        if data_file:
            self.data_file = Path(data_file)
        else:
            self.data_file = get_usage_data_path(professor)

        self.monthly_limit = monthly_limit if monthly_limit is not None else get_monthly_limit()

        self._lock = threading.Lock()

        self.usage_data = self._load_usage_data()

        logging.debug(f"Token tracking initialized for Professor {professor.title()}: {self.data_file}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_current_date() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _get_current_month() -> str:
        return datetime.now().strftime("%Y-%m")

    def _empty_usage_data(self) -> Dict[str, Any]:
        """Return a fresh, empty monthly data structure stamped with the current month."""
        return {
            "month": self._get_current_month(),
            "total_usage": UsageStats().to_dict(),
            "model_usage": {},
            "daily_usage": {},
            "session_history": [],
        }

    def _archive_month(self, data: Dict[str, Any], month: str) -> None:
        """Write *data* to the archive file for *month*, skipping if already archived."""
        archive_path = get_archive_path(self.professor, month)
        if archive_path.exists():
            logging.info(f"Archive already exists for {month}, skipping: {archive_path.name}")
            return
        with open(archive_path, "w") as f:
            json.dump(data, f, indent=2)
        logging.info(f"Archived {self.professor} month {month} → {archive_path.name}")

    def _load_usage_data(self) -> Dict[str, Any]:
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

    def _save_usage_data_to(self, data: Dict[str, Any]) -> None:
        """Write *data* to self.data_file."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.data_file, "w") as f:
            json.dump(data, f, indent=2)

    def _save_usage_data(self) -> None:
        """Save the current in-memory usage data."""
        self._save_usage_data_to(self.usage_data)

    def _update_stats(self, stats: Dict[str, Any], prompt_tokens: int, completion_tokens: int,
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
    # Public API
    # ------------------------------------------------------------------

    def record_usage(self, model: str, prompt_tokens: int, completion_tokens: int,
                     total_tokens: int, requested_model: Optional[str] = None) -> TokenUsage:
        """Record token usage for a single API call.

        Thread-safe: acquires the instance lock for the full read-modify-write
        cycle so that concurrent page workers cannot interleave their updates
        and silently drop token counts.

        Args:
            model:           Actual model name returned by the API (may carry a date suffix).
            prompt_tokens:   Input token count.
            completion_tokens: Output token count.
            total_tokens:    Combined token count.
            requested_model: Model name used in the request (for pricing lookup when different).
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

        return usage

    def get_daily_usage(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Return usage stats for *date* (default: today) from the current month's file."""
        if date is None:
            date = self._get_current_date()
        return self.usage_data["daily_usage"].get(date, UsageStats().to_dict())

    def get_monthly_usage(self, month: Optional[str] = None) -> Dict[str, Any]:
        """Return usage stats for *month* (default: current month).

        For the current month the in-memory totals are returned directly.
        For past months the corresponding archive file is read.
        """
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

    def get_all_time_usage(self) -> Dict[str, Any]:
        """Aggregate total usage across all archived months plus the current month."""
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

    def list_archived_months(self) -> List[str]:
        """Return a sorted list of month strings that have been archived."""
        archive_dir = get_archive_dir(self.professor)
        return sorted(p.stem for p in archive_dir.glob("*.json"))

    def _get_monthly_budget_status(self, month: Optional[str] = None) -> Dict[str, Any]:
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

    def print_usage_report(self, month: Optional[str] = None, include_all_time: bool = False):
        """Print a formatted usage report.

        Args:
            month:           If given (e.g. '2025-07'), print a report for that
                             archived month instead of the current month.
            include_all_time: When True (current month only), also print all-time
                              totals aggregated from all archived months plus the
                              current month.
        """
        current_month = self._get_current_month()

        # ── Archived month report ──────────────────────────────────
        if month and month != current_month:
            archive_path = get_archive_path(self.professor, month)
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

