"""
Tests for the shared-write side of src/tracking/token_tracker.py (plan
docs/webui-plugin-plan.md section 1):
  - fold_usage_records / _accumulate_stats_dict
  - load_usage_tree (mutable files + archives + event files)
  - get_configured_data_roots
  - TokenTracker in shared-write mode: record_usage, get_daily_usage,
    get_monthly_usage, get_all_time_usage, list_archived_months
  - _rollover_closed_shared_months

TokenTracker's shared-write path is only reached when data_file is left as
None (matching real usage) and a shared-write source is configured for the
professor being tracked — every test here patches
src.tracking.token_tracker.get_shared_write_source directly rather than
touching the real .settings.
"""

import json
from unittest.mock import patch

import pytest

from src.settings_store import ExternalSource
from src.tracking.token_tracker import (
    TokenTracker,
    UsageStats,
    fold_usage_records,
    get_configured_data_roots,
    load_usage_tree,
)


def _pricing_patches():
    return (
        patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000),
        patch("src.tracking.token_tracker.get_model_pricing", return_value={"input": 2.0, "output": 8.0}),
    )


# ---------------------------------------------------------------------------
# fold_usage_records
# ---------------------------------------------------------------------------

class TestFoldUsageRecords:

    def test_empty_list_yields_zeroed_totals(self):
        result = fold_usage_records([], "2026-07")
        assert result["month"] == "2026-07"
        assert result["total_usage"]["total_tokens"] == 0
        assert result["session_history"] == []

    def test_sums_multiple_records(self):
        records = [
            {"model": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 50,
             "total_tokens": 150, "total_cost": 0.01, "timestamp": "2026-07-01T10:00:00"},
            {"model": "gpt-4o", "prompt_tokens": 200, "completion_tokens": 100,
             "total_tokens": 300, "total_cost": 0.02, "timestamp": "2026-07-02T10:00:00"},
        ]
        result = fold_usage_records(records, "2026-07")
        assert result["total_usage"]["total_tokens"] == 450
        assert result["total_usage"]["call_count"] == 2

    def test_groups_by_model(self):
        records = [
            {"model": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 0,
             "total_tokens": 100, "total_cost": 0.01, "timestamp": "2026-07-01T10:00:00"},
            {"model": "gpt-4o-mini", "prompt_tokens": 50, "completion_tokens": 0,
             "total_tokens": 50, "total_cost": 0.0, "timestamp": "2026-07-01T11:00:00"},
        ]
        result = fold_usage_records(records, "2026-07")
        assert set(result["model_usage"].keys()) == {"gpt-4o", "gpt-4o-mini"}

    def test_groups_by_day(self):
        records = [
            {"model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 0,
             "total_tokens": 10, "total_cost": 0.0, "timestamp": "2026-07-01T10:00:00"},
            {"model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 0,
             "total_tokens": 10, "total_cost": 0.0, "timestamp": "2026-07-02T10:00:00"},
        ]
        result = fold_usage_records(records, "2026-07")
        assert set(result["daily_usage"].keys()) == {"2026-07-01", "2026-07-02"}

    def test_session_history_sorted_chronologically(self):
        records = [
            {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1,
             "total_cost": 0.0, "timestamp": "2026-07-03T10:00:00"},
            {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1,
             "total_cost": 0.0, "timestamp": "2026-07-01T10:00:00"},
        ]
        result = fold_usage_records(records, "2026-07")
        timestamps = [r["timestamp"] for r in result["session_history"]]
        assert timestamps == sorted(timestamps)

    def test_preserves_source_tag(self):
        records = [
            {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1,
             "total_cost": 0.0, "timestamp": "2026-07-01T10:00:00", "source": "toms-mac"},
        ]
        result = fold_usage_records(records, "2026-07")
        assert result["session_history"][0]["source"] == "toms-mac"


# ---------------------------------------------------------------------------
# load_usage_tree
# ---------------------------------------------------------------------------

class TestLoadUsageTree:

    def test_missing_dir_returns_empty(self, tmp_path):
        assert load_usage_tree(tmp_path / "does_not_exist") == {}

    def test_reads_active_mutable_file(self, tmp_path):
        (tmp_path / "token_usage_heller.json").write_text(json.dumps({
            "month": "2026-07",
            "total_usage": UsageStats(total_tokens=100).to_dict(),
            "model_usage": {}, "daily_usage": {}, "session_history": [],
        }))
        tree = load_usage_tree(tmp_path)
        assert tree["heller"]["2026-07"]["total_usage"]["total_tokens"] == 100

    def test_reads_archives(self, tmp_path):
        archive_dir = tmp_path / "archives" / "heller"
        archive_dir.mkdir(parents=True)
        (archive_dir / "2026-05.json").write_text(json.dumps({
            "month": "2026-05",
            "total_usage": UsageStats(total_tokens=50).to_dict(),
            "model_usage": {}, "daily_usage": {}, "session_history": [],
        }))
        tree = load_usage_tree(tmp_path)
        assert tree["heller"]["2026-05"]["total_usage"]["total_tokens"] == 50

    def test_reads_and_folds_event_files(self, tmp_path):
        event_dir = tmp_path / "events" / "smith" / "2026-07"
        event_dir.mkdir(parents=True)
        (event_dir / "a.json").write_text(json.dumps({
            "model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 5,
            "total_tokens": 15, "total_cost": 0.01, "timestamp": "2026-07-01T10:00:00",
            "source": "toms-mac",
        }))
        tree = load_usage_tree(tmp_path)
        assert tree["smith"]["2026-07"]["total_usage"]["total_tokens"] == 15

    def test_merges_all_three_shapes_for_different_professors(self, tmp_path):
        (tmp_path / "token_usage_heller.json").write_text(json.dumps({
            "month": "2026-07", "total_usage": UsageStats(total_tokens=1).to_dict(),
            "model_usage": {}, "daily_usage": {}, "session_history": [],
        }))
        archive_dir = tmp_path / "archives" / "johnson"
        archive_dir.mkdir(parents=True)
        (archive_dir / "2026-06.json").write_text(json.dumps({
            "month": "2026-06", "total_usage": UsageStats(total_tokens=2).to_dict(),
            "model_usage": {}, "daily_usage": {}, "session_history": [],
        }))
        event_dir = tmp_path / "events" / "smith" / "2026-07"
        event_dir.mkdir(parents=True)
        (event_dir / "a.json").write_text(json.dumps({
            "model": "gpt-4o", "prompt_tokens": 3, "completion_tokens": 0,
            "total_tokens": 3, "total_cost": 0.0, "timestamp": "2026-07-01T10:00:00",
        }))
        tree = load_usage_tree(tmp_path)
        assert set(tree.keys()) == {"heller", "johnson", "smith"}

    def test_corrupt_active_file_is_skipped(self, tmp_path):
        (tmp_path / "token_usage_bad.json").write_text("{not json")
        assert load_usage_tree(tmp_path) == {}


# ---------------------------------------------------------------------------
# get_configured_data_roots
# ---------------------------------------------------------------------------

class TestGetConfiguredDataRoots:

    def test_always_includes_local_first(self):
        with patch("src.tracking.token_tracker.get_configured_sources", return_value=[]):
            roots = get_configured_data_roots()
        assert roots[0][0] == "local"

    def test_includes_configured_external_sources(self, tmp_path):
        fake_source = ExternalSource(label="Prof. Smith", path=str(tmp_path), mode="read-only")
        with patch("src.tracking.token_tracker.get_configured_sources", return_value=[fake_source]):
            roots = get_configured_data_roots()
        labels = [label for label, _ in roots]
        assert "Prof. Smith" in labels


# ---------------------------------------------------------------------------
# TokenTracker in shared-write mode
# ---------------------------------------------------------------------------

@pytest.fixture
def shared_source(tmp_path):
    return ExternalSource(label="Shared", path=str(tmp_path / "shared"), mode="shared-write", professor="smith")


def _make_shared_tracker(shared_source):
    """Build a TokenTracker that resolves to shared-write mode for 'smith'."""
    with patch("src.tracking.token_tracker.get_shared_write_source", return_value=shared_source):
        return TokenTracker("smith")


class TestSharedWriteRecordUsage:

    def test_sets_source_mode(self, shared_source):
        t = _make_shared_tracker(shared_source)
        assert t.source_mode == "shared-write"

    def test_record_usage_creates_event_file(self, shared_source, tmp_path):
        t = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2, patch("src.tracking.token_tracker.get_shared_write_source", return_value=shared_source):
            t.record_usage("gpt-4o", 100, 50, 150)
        event_dir = tmp_path / "shared" / "events" / "smith" / t._get_current_month()
        assert list(event_dir.glob("*.json"))

    def test_two_calls_create_two_distinct_files(self, shared_source, tmp_path):
        t = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
            t.record_usage("gpt-4o", 100, 50, 150)
        event_dir = tmp_path / "shared" / "events" / "smith" / t._get_current_month()
        assert len(list(event_dir.glob("*.json"))) == 2

    def test_event_file_tagged_with_source_id(self, shared_source, tmp_path):
        t = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2, patch("src.tracking.token_tracker.get_source_id", return_value="toms-mac"):
            t._source_id = "toms-mac"  # __init__ already ran; set directly for this test
            t.record_usage("gpt-4o", 100, 50, 150)
        event_dir = tmp_path / "shared" / "events" / "smith" / t._get_current_month()
        event_file = next(event_dir.glob("*.json"))
        assert "toms-mac" in event_file.name
        assert json.loads(event_file.read_text())["source"] == "toms-mac"

    def test_returns_token_usage_with_correct_totals(self, shared_source):
        t = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2:
            usage = t.record_usage("gpt-4o", 100, 50, 150)
        assert usage.total_tokens == 150

    def test_usage_data_reflects_immediately_after_write(self, shared_source):
        t = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
        assert t.usage_data["total_usage"]["total_tokens"] == 150

    def test_get_monthly_usage_sums_events_from_both_installations(self, shared_source, tmp_path):
        """Simulates two 'machines' both writing into the same shared events dir."""
        t1 = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2, patch("src.tracking.token_tracker.get_source_id", return_value="toms-mac"):
            t1._source_id = "toms-mac"
            t1.record_usage("gpt-4o", 100, 50, 150)

        t2 = _make_shared_tracker(shared_source)
        with p1, p2, patch("src.tracking.token_tracker.get_source_id", return_value="smiths-imac"):
            t2._source_id = "smiths-imac"
            t2.record_usage("gpt-4o", 200, 100, 300)

        result = t1.get_monthly_usage()
        assert result["total_tokens"] == 450
        assert result["call_count"] == 2


class TestSharedWriteReads:

    def test_get_daily_usage_current_month(self, shared_source):
        t = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
        result = t.get_daily_usage()
        assert result["total_tokens"] == 150

    def test_get_daily_usage_past_month_reads_archive(self, shared_source, tmp_path):
        archive_dir = tmp_path / "shared" / "archives" / "smith"
        archive_dir.mkdir(parents=True)
        (archive_dir / "2025-01.json").write_text(json.dumps({
            "month": "2025-01",
            "total_usage": UsageStats(total_tokens=99).to_dict(),
            "model_usage": {},
            "daily_usage": {"2025-01-15": UsageStats(total_tokens=99).to_dict()},
            "session_history": [],
        }))
        t = _make_shared_tracker(shared_source)
        result = t.get_daily_usage("2025-01-15")
        assert result["total_tokens"] == 99

    def test_get_monthly_usage_past_month_missing_archive_returns_zero(self, shared_source):
        t = _make_shared_tracker(shared_source)
        result = t.get_monthly_usage("1999-01")
        assert result["total_tokens"] == 0

    def test_get_all_time_usage_combines_current_and_archives(self, shared_source, tmp_path):
        archive_dir = tmp_path / "shared" / "archives" / "smith"
        archive_dir.mkdir(parents=True)
        (archive_dir / "2025-01.json").write_text(json.dumps({
            "month": "2025-01",
            "total_usage": UsageStats(total_tokens=100, call_count=1).to_dict(),
            "model_usage": {}, "daily_usage": {}, "session_history": [],
        }))
        t = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
        result = t.get_all_time_usage()
        assert result["total_tokens"] == 250   # 150 current + 100 archived

    def test_list_archived_months_empty_when_none(self, shared_source):
        t = _make_shared_tracker(shared_source)
        assert t.list_archived_months() == []

    def test_list_archived_months_returns_sorted(self, shared_source, tmp_path):
        archive_dir = tmp_path / "shared" / "archives" / "smith"
        archive_dir.mkdir(parents=True)
        for month in ["2026-02", "2025-11"]:
            (archive_dir / f"{month}.json").write_text("{}")
        t = _make_shared_tracker(shared_source)
        assert t.list_archived_months() == ["2025-11", "2026-02"]


class TestSharedWriteRollover:

    def test_closed_month_folded_into_archive(self, shared_source, tmp_path):
        event_dir = tmp_path / "shared" / "events" / "smith" / "2020-01"
        event_dir.mkdir(parents=True)
        (event_dir / "a.json").write_text(json.dumps({
            "model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 0,
            "total_tokens": 10, "total_cost": 0.0, "timestamp": "2020-01-15T10:00:00",
            "source": "toms-mac",
        }))
        _make_shared_tracker(shared_source)  # __init__ triggers rollover
        archive_path = tmp_path / "shared" / "archives" / "smith" / "2020-01.json"
        assert archive_path.exists()
        assert json.loads(archive_path.read_text())["total_usage"]["total_tokens"] == 10

    def test_closed_month_event_files_removed_after_rollover(self, shared_source, tmp_path):
        event_dir = tmp_path / "shared" / "events" / "smith" / "2020-01"
        event_dir.mkdir(parents=True)
        (event_dir / "a.json").write_text(json.dumps({
            "model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 0,
            "total_tokens": 10, "total_cost": 0.0, "timestamp": "2020-01-15T10:00:00",
        }))
        _make_shared_tracker(shared_source)
        assert not event_dir.exists()

    def test_current_month_events_not_rolled_over(self, shared_source, tmp_path):
        t = _make_shared_tracker(shared_source)
        p1, p2 = _pricing_patches()
        with p1, p2:
            t.record_usage("gpt-4o", 10, 0, 10)
        event_dir = tmp_path / "shared" / "events" / "smith" / t._get_current_month()
        assert event_dir.exists()
        assert list(event_dir.glob("*.json"))

    def test_rollover_skips_month_already_archived(self, shared_source, tmp_path):
        archive_dir = tmp_path / "shared" / "archives" / "smith"
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2020-01.json"
        archive_path.write_text(json.dumps({"month": "2020-01", "total_usage": {"total_tokens": 777}}))

        event_dir = tmp_path / "shared" / "events" / "smith" / "2020-01"
        event_dir.mkdir(parents=True)
        (event_dir / "a.json").write_text(json.dumps({
            "model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 0,
            "total_tokens": 1, "total_cost": 0.0, "timestamp": "2020-01-01T00:00:00",
        }))

        _make_shared_tracker(shared_source)
        # Existing archive content must be untouched (not overwritten with the folded total)
        assert json.loads(archive_path.read_text())["total_usage"]["total_tokens"] == 777


class TestRolloverPreservesUnreadableEvents:
    """Month rollover must never delete an event file it couldn't fold in.

    Shared-write mode is built for folders synced by Dropbox or OneDrive,
    where a placeholder or partially-downloaded file that fails to parse now
    reads perfectly a minute later. Rollover used to skip such a file when
    building the archive and then delete it anyway, destroying that API
    call's record and leaving the archive quietly short.
    """

    def _closed_month_dir(self, tmp_path, month="2020-01"):
        d = tmp_path / "shared" / "events" / "smith" / month
        d.mkdir(parents=True)
        return d

    def _good_event(self, path, tokens=150):
        path.write_text(json.dumps({
            "model": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 50,
            "total_tokens": tokens, "timestamp": "2020-01-05T00:00:00",
            "input_cost": 0.1, "output_cost": 0.2, "total_cost": 0.3, "source": "x",
        }))

    def test_unreadable_event_file_is_not_deleted(self, shared_source, tmp_path):
        d = self._closed_month_dir(tmp_path)
        self._good_event(d / "20200105T000000_aaa_x.json")
        corrupt = d / "20200105T000001_bbb_x.json"
        corrupt.write_text("")  # mid-sync placeholder

        t = _make_shared_tracker(shared_source)
        t._rollover_closed_shared_months()

        assert corrupt.exists(), "an unreadable event file was deleted"
        assert (d / "20200105T000000_aaa_x.json").exists(), (
            "readable files must also stay, so the month can be retried as a whole"
        )

    def test_archive_is_not_left_short_of_the_unreadable_record(self, shared_source, tmp_path):
        d = self._closed_month_dir(tmp_path)
        self._good_event(d / "20200105T000000_aaa_x.json")
        (d / "20200105T000001_bbb_x.json").write_text("{not json")

        t = _make_shared_tracker(shared_source)
        t._rollover_closed_shared_months()

        # Retry once the placeholder has finished syncing: nothing was lost,
        # so the archive ends up complete rather than permanently missing a call.
        self._good_event(d / "20200105T000001_bbb_x.json")
        t._rollover_closed_shared_months()

        archive = tmp_path / "shared" / "archives" / "smith" / "2020-01.json"
        assert archive.exists()
        assert json.loads(archive.read_text())["total_usage"]["call_count"] == 2
        assert not d.exists(), "cleanly folded months should still be tidied away"

    def test_fully_readable_month_still_rolls_over_and_is_cleaned_up(self, shared_source, tmp_path):
        """The ordinary path must be unaffected by the guard above."""
        d = self._closed_month_dir(tmp_path)
        self._good_event(d / "20200105T000000_aaa_x.json")

        t = _make_shared_tracker(shared_source)
        t._rollover_closed_shared_months()

        archive = tmp_path / "shared" / "archives" / "smith" / "2020-01.json"
        assert archive.exists()
        assert json.loads(archive.read_text())["total_usage"]["call_count"] == 1
        assert not d.exists()
