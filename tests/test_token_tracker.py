"""
Tests for token tracking data structures and math:
  - UsageStats.add_usage, merge_dict, to_dict
  - TokenTracker.__init__ (default data_file path)
  - _accumulate_stats_dict (running totals)
  - TokenTracker._calculate_costs  (mocked pricing)
  - TokenTracker.get_monthly_budget_status  (current and past months)
  - Module-level path helpers
  - TokenUsage dataclass
  - TokenTracker._empty_usage_data
  - TokenTracker._archive_month
  - TokenTracker._load_usage_data (including month rollover)
  - TokenTracker._save_usage_data / _save_usage_data_to
  - TokenTracker.record_usage
  - TokenTracker.get_daily_usage / get_monthly_usage / get_all_time_usage
  - TokenTracker.list_archived_months
  - TokenTracker.print_usage_report (current month, archives, budget warnings, no-archive message)
  - TokenTracker.update_pricing

No API calls, no cloud I/O; disk writes are directed to tmp_path.
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.tracking.token_tracker import (
    UsageStats,
    TokenTracker,
    TokenUsage,
    _accumulate_stats_dict,
    get_usage_data_path,
    get_archive_dir,
    get_archive_path,
)


# ---------------------------------------------------------------------------
# UsageStats
# ---------------------------------------------------------------------------

class TestUsageStats:

    def test_initial_values_are_zero(self):
        stats = UsageStats()
        assert stats.total_tokens == 0
        assert stats.total_input_tokens == 0
        assert stats.total_output_tokens == 0
        assert stats.total_cost == 0.0
        assert stats.call_count == 0

    def test_add_usage_single_call(self):
        stats = UsageStats()
        stats.add_usage(prompt_tokens=100, completion_tokens=50, total_tokens=150, cost=0.01)
        assert stats.total_tokens == 150
        assert stats.total_input_tokens == 100
        assert stats.total_output_tokens == 50
        assert stats.total_cost == pytest.approx(0.01)
        assert stats.call_count == 1

    def test_add_usage_accumulates_across_calls(self):
        stats = UsageStats()
        stats.add_usage(100, 50, 150, 0.01)
        stats.add_usage(200, 100, 300, 0.02)
        assert stats.total_tokens == 450
        assert stats.total_input_tokens == 300
        assert stats.total_output_tokens == 150
        assert stats.total_cost == pytest.approx(0.03)
        assert stats.call_count == 2

    def test_merge_dict_full(self):
        stats = UsageStats()
        d = {
            "total_tokens": 100,
            "total_input_tokens": 60,
            "total_output_tokens": 40,
            "total_cost": 0.05,
            "call_count": 3,
        }
        stats.merge_dict(d)
        assert stats.total_tokens == 100
        assert stats.total_input_tokens == 60
        assert stats.total_output_tokens == 40
        assert stats.total_cost == pytest.approx(0.05)
        assert stats.call_count == 3

    def test_merge_dict_partial_uses_zero_defaults(self):
        stats = UsageStats()
        stats.merge_dict({"total_tokens": 50})
        assert stats.total_tokens == 50
        assert stats.total_input_tokens == 0
        assert stats.total_cost == 0.0

    def test_merge_dict_accumulates_with_existing(self):
        stats = UsageStats(total_tokens=10, call_count=1)
        stats.merge_dict({"total_tokens": 20, "call_count": 2})
        assert stats.total_tokens == 30
        assert stats.call_count == 3

    def test_to_dict_returns_all_fields(self):
        stats = UsageStats(
            total_tokens=10,
            total_input_tokens=6,
            total_output_tokens=4,
            total_cost=0.01,
            call_count=1,
        )
        d = stats.to_dict()
        assert d == {
            "total_tokens": 10,
            "total_input_tokens": 6,
            "total_output_tokens": 4,
            "total_cost": 0.01,
            "call_count": 1,
        }

    def test_to_dict_roundtrip_via_merge_dict(self):
        original = UsageStats(total_tokens=99, total_input_tokens=60,
                               total_output_tokens=39, total_cost=0.15, call_count=5)
        restored = UsageStats()
        restored.merge_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()


# ---------------------------------------------------------------------------
# Fixtures for TokenTracker tests (uses tmp_path to avoid touching the real data/)
# ---------------------------------------------------------------------------

@pytest.fixture
def tracker(tmp_path):
    """A TokenTracker for a synthetic professor backed by a temp file."""
    data_file = str(tmp_path / "token_usage_test.json")
    with patch("src.tracking.token_tracker.get_monthly_limit", return_value=100.0):
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)
    return t


# ---------------------------------------------------------------------------
# _accumulate_stats_dict
# ---------------------------------------------------------------------------

class TestAccumulateStatsDict:

    def test_increments_all_fields(self, tracker):
        stats = {"total_tokens": 0, "total_input_tokens": 0,
                 "total_output_tokens": 0, "total_cost": 0.0}
        _accumulate_stats_dict(stats, prompt_tokens=10, completion_tokens=5,
                              total_tokens=15, cost=0.02)
        assert stats["total_tokens"] == 15
        assert stats["total_input_tokens"] == 10
        assert stats["total_output_tokens"] == 5
        assert stats["total_cost"] == pytest.approx(0.02)
        assert stats["call_count"] == 1

    def test_accumulates_on_repeated_calls(self, tracker):
        stats = {"total_tokens": 0, "total_input_tokens": 0,
                 "total_output_tokens": 0, "total_cost": 0.0}
        _accumulate_stats_dict(stats, 10, 5, 15, 0.01)
        _accumulate_stats_dict(stats, 20, 10, 30, 0.02)
        assert stats["total_tokens"] == 45
        assert stats["call_count"] == 2

    def test_initialises_call_count_when_missing(self, tracker):
        stats = {"total_tokens": 0, "total_input_tokens": 0,
                 "total_output_tokens": 0, "total_cost": 0.0}
        # call_count key absent — it should be created, not raise
        _accumulate_stats_dict(stats, 1, 1, 2, 0.001)
        assert "call_count" in stats
        assert stats["call_count"] == 1


# ---------------------------------------------------------------------------
# TokenTracker._calculate_costs
# ---------------------------------------------------------------------------

class TestCalculateCosts:

    def test_standard_pricing(self, tracker):
        with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
             patch("src.tracking.token_tracker.get_model_pricing",
                   return_value={"input": 3.0, "output": 10.0}):
            in_cost, out_cost, total = tracker._calculate_costs(
                "gpt-4o", prompt_tokens=1_000_000, completion_tokens=500_000
            )
        assert in_cost == pytest.approx(3.0)    # 1M / 1M * 3.0
        assert out_cost == pytest.approx(5.0)   # 0.5M / 1M * 10.0
        assert total == pytest.approx(8.0)

    def test_zero_tokens_yields_zero_cost(self, tracker):
        with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
             patch("src.tracking.token_tracker.get_model_pricing",
                   return_value={"input": 3.0, "output": 10.0}):
            in_cost, out_cost, total = tracker._calculate_costs("gpt-4o", 0, 0)
        assert in_cost == 0.0
        assert out_cost == 0.0
        assert total == 0.0

    def test_total_equals_input_plus_output(self, tracker):
        with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
             patch("src.tracking.token_tracker.get_model_pricing",
                   return_value={"input": 5.0, "output": 15.0}):
            in_cost, out_cost, total = tracker._calculate_costs("gpt-4o", 200_000, 100_000)
        assert total == pytest.approx(in_cost + out_cost)


# ---------------------------------------------------------------------------
# TokenTracker.get_monthly_budget_status
# ---------------------------------------------------------------------------

class TestMonthlyBudgetStatus:

    def _set_total_cost(self, tracker, cost: float):
        """Helper: write a total_cost into the tracker's in-memory usage_data."""
        tracker.usage_data["total_usage"] = UsageStats(total_cost=cost).to_dict()

    def test_under_limit_not_exceeded(self, tracker):
        self._set_total_cost(tracker, 20.0)
        status = tracker.get_monthly_budget_status()
        assert status["is_exceeded"] is False

    def test_over_limit_is_exceeded(self, tracker):
        self._set_total_cost(tracker, 110.0)
        status = tracker.get_monthly_budget_status()
        assert status["is_exceeded"] is True

    def test_usage_percentage_calculated_correctly(self, tracker):
        self._set_total_cost(tracker, 25.0)   # 25/100 = 25%
        status = tracker.get_monthly_budget_status()
        assert status["usage_percentage"] == pytest.approx(25.0)

    def test_remaining_budget_calculated_correctly(self, tracker):
        self._set_total_cost(tracker, 30.0)
        status = tracker.get_monthly_budget_status()
        assert status["remaining_budget"] == pytest.approx(70.0)

    def test_remaining_budget_clamps_to_zero_when_exceeded(self, tracker):
        self._set_total_cost(tracker, 150.0)
        status = tracker.get_monthly_budget_status()
        assert status["remaining_budget"] == 0.0

    def test_approaching_limit_false_below_80_percent(self, tracker):
        self._set_total_cost(tracker, 79.0)
        status = tracker.get_monthly_budget_status()
        assert status["approaching_limit"] is False

    def test_approaching_limit_true_above_80_percent(self, tracker):
        self._set_total_cost(tracker, 81.0)
        status = tracker.get_monthly_budget_status()
        assert status["approaching_limit"] is True

    def test_status_contains_monthly_usage_key(self, tracker):
        self._set_total_cost(tracker, 10.0)
        status = tracker.get_monthly_budget_status()
        assert "monthly_usage" in status


# ---------------------------------------------------------------------------
# Module-level path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:

    def test_get_usage_data_path_filename(self):
        path = get_usage_data_path("jh43")
        assert path.name == "token_usage_jh43.json"

    def test_get_usage_data_path_uses_the_netid_verbatim(self):
        """No rewriting happens here — that is the point of using netIDs.

        This used to lower-case its argument while the shared-folder helpers
        ran the same name through a separate make-it-safe step, so one
        person could be recorded under two names. Capitalisation is folded
        once, in normalize_netid, before anything reaches these helpers.
        """
        assert get_usage_data_path("ab99").name == "token_usage_ab99.json"

    def test_get_archive_dir_structure(self):
        path = get_archive_dir("zz99")
        assert path.parts[-1] == "zz99"
        assert path.parts[-2] == "archives"

    def test_get_archive_path_filename(self):
        path = get_archive_path("zz99", "2026-01")
        assert path.name == "2026-01.json"
        assert path.parts[-2] == "zz99"

    def test_asking_for_a_path_does_not_create_it(self):
        """A getter that creates directories leaves folders behind for people who don't exist.

        This is how ``data/archives/testprof`` and ``data/archives/warntest``
        got into the real data directory: ``--show-config`` asked for every
        configured person's archive folder in order to list it, and the act
        of asking created it. Writers call ``ensure_archive_dir()`` instead.
        """
        path = get_archive_dir("nobodyhere")
        assert not path.exists()
        assert not get_usage_data_path("nobodyhere").parent.joinpath(
            "archives", "nobodyhere"
        ).exists()


# ---------------------------------------------------------------------------
# TokenUsage dataclass
# ---------------------------------------------------------------------------

class TestTokenUsage:

    def test_fields_stored_correctly(self):
        usage = TokenUsage(
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            timestamp="2026-03-12T10:00:00",
            input_cost=0.01,
            output_cost=0.02,
            total_cost=0.03,
        )
        assert usage.model == "gpt-4o"
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_cost == pytest.approx(0.03)

    def test_is_dataclass(self):
        from dataclasses import fields
        names = {f.name for f in fields(TokenUsage)}
        assert names == {
            "model", "prompt_tokens", "completion_tokens", "total_tokens",
            "timestamp", "input_cost", "output_cost", "total_cost", "source",
            "endpoint",
        }

    def test_source_defaults_to_empty_string(self):
        usage = TokenUsage(
            model="gpt-4o", prompt_tokens=1, completion_tokens=1, total_tokens=2,
            timestamp="2026-03-12T10:00:00", input_cost=0.0, output_cost=0.0, total_cost=0.0,
        )
        assert usage.source == ""


# ---------------------------------------------------------------------------
# TokenTracker._empty_usage_data
# ---------------------------------------------------------------------------

class TestEmptyUsageData:

    def test_has_required_keys(self, tracker):
        data = tracker._empty_usage_data()
        assert set(data.keys()) == {
            "month", "total_usage", "model_usage", "daily_usage",
            "endpoint_usage", "session_history",
        }

    def test_month_matches_current_month(self, tracker):
        from datetime import datetime
        expected = datetime.now().strftime("%Y-%m")
        assert tracker._empty_usage_data()["month"] == expected

    def test_collections_are_empty(self, tracker):
        data = tracker._empty_usage_data()
        assert data["model_usage"] == {}
        assert data["daily_usage"] == {}
        assert data["session_history"] == []

    def test_total_usage_is_zero_stats(self, tracker):
        data = tracker._empty_usage_data()
        assert data["total_usage"]["total_tokens"] == 0
        assert data["total_usage"]["total_cost"] == 0.0


# ---------------------------------------------------------------------------
# TokenTracker._archive_month / _save_usage_data_to
# ---------------------------------------------------------------------------

class TestArchiveAndSave:

    def test_archive_writes_json_file(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)

        archive_dir = tmp_path / "fake_archives"
        archive_dir.mkdir()
        archive_path = archive_dir / "2026-01.json"

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            t._archive_month({"month": "2026-01", "total": 0}, "2026-01")

        assert archive_path.exists()
        loaded = json.loads(archive_path.read_text())
        assert loaded["month"] == "2026-01"

    def test_archive_skips_if_already_exists(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)

        archive_dir = tmp_path / "fake_archives"
        archive_dir.mkdir()
        archive_path = archive_dir / "2026-01.json"
        archive_path.write_text(json.dumps({"month": "original"}))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            t._archive_month({"month": "NEW"}, "2026-01")

        # File should not have been overwritten
        assert json.loads(archive_path.read_text())["month"] == "original"

    def test_save_usage_data_to_writes_file(self, tmp_path):
        data_file = tmp_path / "token_usage_test.json"
        t = TokenTracker("testprof", data_file=str(data_file), monthly_limit=100.0)
        sample = {"month": "2026-03", "total_usage": {}, "model_usage": {}, "daily_usage": {}, "session_history": []}
        t._save_usage_data_to(sample)
        assert data_file.exists()
        assert json.loads(data_file.read_text())["month"] == "2026-03"

    def test_save_usage_data_persists_in_memory_state(self, tmp_path):
        data_file = tmp_path / "token_usage_test.json"
        t = TokenTracker("testprof", data_file=str(data_file), monthly_limit=100.0)
        t.usage_data["month"] = "2999-99"  # sentinel
        t._save_usage_data()
        assert json.loads(data_file.read_text())["month"] == "2999-99"


# ---------------------------------------------------------------------------
# TokenTracker._load_usage_data  (month rollover)
# ---------------------------------------------------------------------------

class TestLoadUsageData:

    def test_missing_file_returns_empty_structure(self, tmp_path):
        data_file = str(tmp_path / "missing.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)
        assert t.usage_data["model_usage"] == {}
        assert t.usage_data["session_history"] == []

    def test_current_month_file_loaded_as_is(self, tmp_path):
        from datetime import datetime
        current_month = datetime.now().strftime("%Y-%m")
        data = {
            "month": current_month,
            "total_usage": UsageStats(total_tokens=999).to_dict(),
            "model_usage": {},
            "daily_usage": {},
            "session_history": [],
        }
        data_file = tmp_path / "token_usage_test.json"
        data_file.write_text(json.dumps(data))
        t = TokenTracker("testprof", data_file=str(data_file), monthly_limit=100.0)
        assert t.usage_data["total_usage"]["total_tokens"] == 999

    def test_old_month_triggers_rollover(self, tmp_path):
        stale_data = {
            "month": "2020-01",
            "total_usage": UsageStats(total_tokens=42).to_dict(),
            "model_usage": {},
            "daily_usage": {},
            "session_history": [],
        }
        data_file = tmp_path / "token_usage_test.json"
        data_file.write_text(json.dumps(stale_data))

        archive_path = tmp_path / "2020-01.json"
        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            t = TokenTracker("testprof", data_file=str(data_file), monthly_limit=100.0)

        # Active file should now be a fresh empty month
        assert t.usage_data["total_usage"]["total_tokens"] == 0
        # Archive should have been written with the old data
        assert archive_path.exists()
        assert json.loads(archive_path.read_text())["total_usage"]["total_tokens"] == 42


# ---------------------------------------------------------------------------
# TokenTracker.record_usage
# ---------------------------------------------------------------------------

class TestRecordUsage:

    def _make_tracker(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        return TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)

    def _mock_pricing(self):
        return (
            patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000),
            patch("src.tracking.token_tracker.get_model_pricing",
                  return_value={"input": 2.0, "output": 8.0}),
        )

    def test_returns_token_usage_object(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            result = t.record_usage("gpt-4o", 100, 50, 150)
        assert isinstance(result, TokenUsage)
        assert result.model == "gpt-4o"
        assert result.prompt_tokens == 100
        assert result.completion_tokens == 50

    def test_updates_total_usage(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            t.record_usage("gpt-4o", 200, 100, 300)
        assert t.usage_data["total_usage"]["total_tokens"] == 300
        assert t.usage_data["total_usage"]["call_count"] == 1

    def test_updates_model_usage(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            t.record_usage("gpt-4o", 200, 100, 300)
        assert "gpt-4o" in t.usage_data["model_usage"]
        assert t.usage_data["model_usage"]["gpt-4o"]["total_tokens"] == 300

    def test_updates_daily_usage(self, tmp_path):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        t = self._make_tracker(tmp_path)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
        assert today in t.usage_data["daily_usage"]
        assert t.usage_data["daily_usage"][today]["total_tokens"] == 150

    def test_appends_to_session_history(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
            t.record_usage("gpt-4o", 200, 100, 300)
        assert len(t.usage_data["session_history"]) == 2

    def test_persists_to_disk(self, tmp_path):
        data_file = tmp_path / "token_usage_test.json"
        t = TokenTracker("testprof", data_file=str(data_file), monthly_limit=100.0)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
        on_disk = json.loads(data_file.read_text())
        assert on_disk["total_usage"]["total_tokens"] == 150

    def test_uses_requested_model_for_pricing(self, tmp_path):
        t = self._make_tracker(tmp_path)
        pricing_call_args = []

        def fake_pricing(model):
            pricing_call_args.append(model)
            return {"input": 2.0, "output": 8.0}

        with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
             patch("src.tracking.token_tracker.get_model_pricing", side_effect=fake_pricing):
            t.record_usage("gpt-4o-2026-03-12", 100, 50, 150, requested_model="gpt-4o")

        assert "gpt-4o" in pricing_call_args
        assert "gpt-4o-2026-03-12" not in pricing_call_args

    def test_multiple_calls_same_model_accumulate(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
            t.record_usage("gpt-4o", 100, 50, 150)
        assert t.usage_data["model_usage"]["gpt-4o"]["call_count"] == 2
        assert t.usage_data["model_usage"]["gpt-4o"]["total_tokens"] == 300


class TestSessionUsage:
    """get_session_usage() — a running total scoped to one TokenTracker
    instance's own lifetime, separate from the persisted monthly/daily/
    all-time totals. See its docstring: this is what a multi-page
    translate job (one API call per page, all through the same
    SandboxProcessor/TokenTracker) reads to report its total spend."""

    def _make_tracker(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        return TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)

    def _mock_pricing(self):
        return (
            patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000),
            patch("src.tracking.token_tracker.get_model_pricing",
                  return_value={"input": 2.0, "output": 8.0}),
        )

    def test_starts_at_zero(self, tmp_path):
        t = self._make_tracker(tmp_path)
        usage = t.get_session_usage()
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "total_cost": 0.0}

    def test_accumulates_across_multiple_calls(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
            t.record_usage("gpt-4o", 200, 100, 300)
        usage = t.get_session_usage()
        assert usage["prompt_tokens"] == 300
        assert usage["completion_tokens"] == 150
        assert usage["total_tokens"] == 450
        assert usage["total_cost"] > 0

    def test_two_trackers_do_not_share_session_totals(self, tmp_path):
        # Each SandboxProcessor gets its own TokenTracker (one per CLI run
        # or webui request/job) — a second instance's calls must not leak
        # into the first's session total, even though they may write to the
        # same underlying persisted monthly file.
        t1 = self._make_tracker(tmp_path)
        t2 = self._make_tracker(tmp_path)
        p1, p2 = self._mock_pricing()
        with p1, p2:
            t1.record_usage("gpt-4o", 100, 50, 150)
        assert t1.get_session_usage()["total_tokens"] == 150
        assert t2.get_session_usage()["total_tokens"] == 0

    def test_returned_dict_is_a_copy(self, tmp_path):
        t = self._make_tracker(tmp_path)
        usage = t.get_session_usage()
        usage["prompt_tokens"] = 9999
        assert t.get_session_usage()["prompt_tokens"] == 0


# ---------------------------------------------------------------------------
# TokenTracker.get_daily_usage
# ---------------------------------------------------------------------------

class TestGetDailyUsage:

    def test_returns_zero_stats_on_empty_day(self, tracker):
        result = tracker.get_daily_usage("2099-12-31")
        assert result["total_tokens"] == 0
        assert result["call_count"] == 0

    def test_returns_recorded_usage_for_date(self, tracker):
        tracker.usage_data["daily_usage"]["2026-03-12"] = {
            "total_tokens": 500,
            "total_input_tokens": 300,
            "total_output_tokens": 200,
            "total_cost": 0.05,
            "call_count": 2,
        }
        result = tracker.get_daily_usage("2026-03-12")
        assert result["total_tokens"] == 500

    def test_defaults_to_today(self, tracker):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        tracker.usage_data["daily_usage"][today] = {
            "total_tokens": 42,
            "total_input_tokens": 20,
            "total_output_tokens": 22,
            "total_cost": 0.01,
            "call_count": 1,
        }
        result = tracker.get_daily_usage()
        assert result["total_tokens"] == 42


# ---------------------------------------------------------------------------
# TokenTracker.get_monthly_usage
# ---------------------------------------------------------------------------

class TestGetMonthlyUsage:

    def test_current_month_returns_in_memory_totals(self, tracker):
        tracker.usage_data["total_usage"]["total_tokens"] = 777
        result = tracker.get_monthly_usage()
        assert result["total_tokens"] == 777

    def test_past_month_reads_archive(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)

        archive_data = {
            "month": "2025-11",
            "total_usage": UsageStats(total_tokens=1234).to_dict(),
            "model_usage": {},
            "daily_usage": {},
            "session_history": [],
        }
        archive_path = tmp_path / "2025-11.json"
        archive_path.write_text(json.dumps(archive_data))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            result = t.get_monthly_usage("2025-11")
        assert result["total_tokens"] == 1234

    def test_missing_archive_returns_zero_stats(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)

        non_existent = tmp_path / "1999-01.json"
        with patch("src.tracking.token_tracker.get_archive_path", return_value=non_existent):
            result = t.get_monthly_usage("1999-01")
        assert result["total_tokens"] == 0


# ---------------------------------------------------------------------------
# TokenTracker.get_all_time_usage
# ---------------------------------------------------------------------------

class TestGetAllTimeUsage:

    def test_no_archives_returns_current_month_only(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)
        t.usage_data["total_usage"]["total_tokens"] = 100
        t.usage_data["total_usage"]["total_cost"] = 1.0

        empty_archive_dir = tmp_path / "archives"
        empty_archive_dir.mkdir()
        with patch("src.tracking.token_tracker.get_archive_dir", return_value=empty_archive_dir):
            result = t.get_all_time_usage()
        assert result["total_tokens"] == 100

    def test_aggregates_archives_with_current_month(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)
        t.usage_data["total_usage"] = UsageStats(total_tokens=100, call_count=1).to_dict()

        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()
        for month, tokens in [("2026-01", 200), ("2026-02", 300)]:
            arc = {
                "month": month,
                "total_usage": UsageStats(total_tokens=tokens, call_count=1).to_dict(),
            }
            (archive_dir / f"{month}.json").write_text(json.dumps(arc))

        with patch("src.tracking.token_tracker.get_archive_dir", return_value=archive_dir):
            result = t.get_all_time_usage()
        assert result["total_tokens"] == 600   # 100 + 200 + 300
        assert result["call_count"] == 3

    def test_corrupt_archive_is_skipped(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)
        t.usage_data["total_usage"] = UsageStats(total_tokens=50).to_dict()

        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()
        (archive_dir / "2026-01.json").write_text("{bad json}")

        with patch("src.tracking.token_tracker.get_archive_dir", return_value=archive_dir):
            result = t.get_all_time_usage()
        assert result["total_tokens"] == 50   # corrupt archive silently skipped


# ---------------------------------------------------------------------------
# TokenTracker.list_archived_months
# ---------------------------------------------------------------------------

class TestListArchivedMonths:

    def test_empty_archive_dir_returns_empty_list(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)

        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()
        with patch("src.tracking.token_tracker.get_archive_dir", return_value=archive_dir):
            assert t.list_archived_months() == []

    def test_returns_sorted_month_stems(self, tmp_path):
        data_file = str(tmp_path / "token_usage_test.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=100.0)

        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()
        for month in ["2026-02", "2025-11", "2026-01"]:
            (archive_dir / f"{month}.json").write_text("{}")

        with patch("src.tracking.token_tracker.get_archive_dir", return_value=archive_dir):
            result = t.list_archived_months()
        assert result == ["2025-11", "2026-01", "2026-02"]


# ---------------------------------------------------------------------------
# Shared realistic fixture data (placeholder names, values from real archives)
# ---------------------------------------------------------------------------

# Represents a current-month active file (professor "testprof")
_THIS_MONTH = datetime.now().strftime("%Y-%m")

CURRENT_MONTH_DATA = {
    "month": _THIS_MONTH,
    "total_usage": {
        "total_tokens": 18177,
        "total_input_tokens": 13878,
        "total_output_tokens": 4299,
        "total_cost": 0.08545,
        "call_count": 8,
    },
    "model_usage": {
        "gpt-4o-2026-03-01": {
            "total_tokens": 18177,
            "total_input_tokens": 13878,
            "total_output_tokens": 4299,
            "total_cost": 0.08545,
            "call_count": 8,
        }
    },
    "daily_usage": {
        "2026-03-05": {
            "total_tokens": 3731,
            "total_input_tokens": 2716,
            "total_output_tokens": 1015,
            "total_cost": 0.01863,
            "call_count": 2,
        },
        "2026-03-11": {
            "total_tokens": 14446,
            "total_input_tokens": 11162,
            "total_output_tokens": 3284,
            "total_cost": 0.06682,
            "call_count": 6,
        },
    },
    "session_history": [
        {
            "model": "gpt-4o-2026-03-01",
            "prompt_tokens": 1358,
            "completion_tokens": 514,
            "total_tokens": 1872,
            "timestamp": "2026-03-05T11:49:31",
            "input_cost": 0.00373,
            "output_cost": 0.00565,
            "total_cost": 0.00939,
        }
    ],
}

# Represents an archived month file  (February 2026, multi-model)
ARCHIVE_FEB_DATA = {
    "month": "2026-02",
    "total_usage": {
        "total_tokens": 219991,
        "total_input_tokens": 147581,
        "total_output_tokens": 72410,
        "total_cost": 0.88636,
        "call_count": 32,
    },
    "model_usage": {
        "gpt-4o-2026-02-01": {
            "total_tokens": 36321,
            "total_input_tokens": 21003,
            "total_output_tokens": 15318,
            "total_cost": 0.22626,
            "call_count": 16,
        },
        "gpt-4o-mini-2026-02-01": {
            "total_tokens": 112107,
            "total_input_tokens": 111408,
            "total_output_tokens": 699,
            "total_cost": 0.01884,
            "call_count": 3,
        },
        "gpt-5-2026-02-01": {
            "total_tokens": 71563,
            "total_input_tokens": 15170,
            "total_output_tokens": 56393,
            "total_cost": 0.64126,
            "call_count": 13,
        },
    },
    "daily_usage": {
        "2026-02-24": {
            "total_tokens": 136905,
            "total_input_tokens": 122399,
            "total_output_tokens": 14506,
            "total_cost": 0.20095,
            "call_count": 11,
        },
        "2026-02-27": {
            "total_tokens": 83086,
            "total_input_tokens": 25182,
            "total_output_tokens": 57904,
            "total_cost": 0.68541,
            "call_count": 21,
        },
    },
    "session_history": [],
}


@pytest.fixture()
def loaded_tracker(tmp_path):
    """TokenTracker pre-loaded with CURRENT_MONTH_DATA."""
    import json
    data_file = tmp_path / "token_usage_testprof.json"
    data_file.write_text(json.dumps(CURRENT_MONTH_DATA))
    return TokenTracker("testprof", data_file=str(data_file), monthly_limit=250.0)


# ---------------------------------------------------------------------------
# TokenTracker.get_monthly_budget_status  (past month via archive)
# ---------------------------------------------------------------------------

class TestMonthlyBudgetStatusPastMonth:
    """Exercises the branch that reads a past month from an archive file."""

    def test_past_month_usage_reflected_in_status(self, tmp_path):
        import json
        data_file = str(tmp_path / "token_usage_testprof.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=250.0)

        archive_path = tmp_path / "2026-02.json"
        archive_path.write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            status = t.get_monthly_budget_status(month="2026-02")

        assert status["monthly_usage"]["total_tokens"] == 219991
        assert status["monthly_usage"]["call_count"] == 32

    def test_past_month_usage_percentage_calculated(self, tmp_path):
        import json
        data_file = str(tmp_path / "token_usage_testprof.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=250.0)

        archive_path = tmp_path / "2026-02.json"
        archive_path.write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            status = t.get_monthly_budget_status(month="2026-02")

        # 0.88636 / 250.0 * 100 ≈ 0.35 %
        assert status["usage_percentage"] == pytest.approx(
            (0.88636 / 250.0) * 100, rel=1e-3
        )

    def test_past_month_not_exceeded(self, tmp_path):
        import json
        data_file = str(tmp_path / "token_usage_testprof.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=250.0)

        archive_path = tmp_path / "2026-02.json"
        archive_path.write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            status = t.get_monthly_budget_status(month="2026-02")

        assert status["is_exceeded"] is False


# ---------------------------------------------------------------------------
# TokenTracker.print_usage_report
# ---------------------------------------------------------------------------

class TestPrintUsageReport:

    # --- current month -------------------------------------------------------

    def test_current_month_report_contains_professor(self, loaded_tracker, capsys):
        loaded_tracker.print_usage_report()
        out = capsys.readouterr().out
        assert "TESTPROF" in out

    def test_current_month_total_tokens_shown(self, loaded_tracker, capsys):
        loaded_tracker.print_usage_report()
        out = capsys.readouterr().out
        assert "18,177" in out

    def test_current_month_total_cost_shown(self, loaded_tracker, capsys):
        loaded_tracker.print_usage_report()
        out = capsys.readouterr().out
        assert "0.0855" in out or "0.0854" in out  # allow rounding

    def test_current_month_model_breakdown_shown(self, loaded_tracker, capsys):
        loaded_tracker.print_usage_report()
        out = capsys.readouterr().out
        assert "gpt-4o-2026-03-01" in out

    def test_current_month_no_all_time_section_by_default(self, loaded_tracker, capsys, tmp_path):
        archive_dir = tmp_path / "no_archives"
        archive_dir.mkdir()
        with patch("src.tracking.token_tracker.get_archive_dir", return_value=archive_dir):
            loaded_tracker.print_usage_report(include_all_time=False)
        out = capsys.readouterr().out
        assert "All-Time" not in out

    def test_current_month_all_time_section_shown_when_requested(self, loaded_tracker, capsys, tmp_path):
        import json
        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()
        (archive_dir / "2026-02.json").write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_dir", return_value=archive_dir):
            loaded_tracker.print_usage_report(include_all_time=True)
        out = capsys.readouterr().out
        assert "All-Time" in out

    def test_current_month_budget_section_shown(self, loaded_tracker, capsys):
        loaded_tracker.print_usage_report()
        out = capsys.readouterr().out
        assert "Monthly Budget" in out or "Monthly Limit" in out

    def test_today_daily_section_shown_when_usage_exists(self, loaded_tracker, capsys):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        # inject today's usage so the daily block is printed
        loaded_tracker.usage_data["daily_usage"][today] = {
            "total_tokens": 500,
            "total_input_tokens": 300,
            "total_output_tokens": 200,
            "total_cost": 0.02,
            "call_count": 1,
        }
        loaded_tracker.print_usage_report()
        out = capsys.readouterr().out
        assert "500" in out

    # --- archived month ------------------------------------------------------

    def test_archived_month_report_contains_month_label(self, tmp_path, capsys):
        import json
        data_file = str(tmp_path / "token_usage_testprof.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=250.0)

        archive_path = tmp_path / "2026-02.json"
        archive_path.write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            t.print_usage_report(month="2026-02")
        out = capsys.readouterr().out
        assert "2026-02" in out

    def test_archived_month_total_tokens_shown(self, tmp_path, capsys):
        import json
        data_file = str(tmp_path / "token_usage_testprof.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=250.0)

        archive_path = tmp_path / "2026-02.json"
        archive_path.write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            t.print_usage_report(month="2026-02")
        out = capsys.readouterr().out
        assert "219,991" in out

    def test_archived_month_multi_model_breakdown_shown(self, tmp_path, capsys):
        import json
        data_file = str(tmp_path / "token_usage_testprof.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=250.0)

        archive_path = tmp_path / "2026-02.json"
        archive_path.write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            t.print_usage_report(month="2026-02")
        out = capsys.readouterr().out
        assert "gpt-5-2026-02-01" in out
        assert "gpt-4o-mini-2026-02-01" in out

    def test_archived_month_daily_breakdown_shown(self, tmp_path, capsys):
        import json
        data_file = str(tmp_path / "token_usage_testprof.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=250.0)

        archive_path = tmp_path / "2026-02.json"
        archive_path.write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_path", return_value=archive_path):
            t.print_usage_report(month="2026-02")
        out = capsys.readouterr().out
        assert "2026-02-24" in out
        assert "2026-02-27" in out

    def test_archived_month_missing_prints_not_found(self, tmp_path, capsys):
        data_file = str(tmp_path / "token_usage_testprof.json")
        t = TokenTracker("testprof", data_file=data_file, monthly_limit=250.0)

        non_existent = tmp_path / "1999-01.json"
        archive_dir = tmp_path / "archives"
        archive_dir.mkdir()

        with patch("src.tracking.token_tracker.get_archive_path", return_value=non_existent), \
             patch("src.tracking.token_tracker.get_archive_dir", return_value=archive_dir):
            t.print_usage_report(month="1999-01")
        out = capsys.readouterr().out
        assert "No archive found" in out


# ---------------------------------------------------------------------------
# TokenTracker.__init__ — else branch (line 133):
#   self.data_file = get_usage_data_path(professor)
# ---------------------------------------------------------------------------


class TestInitDefaultDataFilePath:
    """Verify that omitting data_file triggers the else branch (line 133)."""

    def test_data_file_set_via_get_usage_data_path_when_none_provided(self, tmp_path):
        expected_path = tmp_path / "token_usage_testprof.json"

        with patch("src.tracking.token_tracker.get_usage_data_path",
                   return_value=expected_path) as mock_path, \
             patch("src.tracking.token_tracker.get_monthly_limit", return_value=100.0):
            t = TokenTracker("testprof")  # no data_file argument

        mock_path.assert_called_once_with("testprof")
        assert t.data_file == expected_path

    def test_data_file_is_path_object_when_not_provided(self, tmp_path):
        expected_path = tmp_path / "token_usage_testprof.json"

        with patch("src.tracking.token_tracker.get_usage_data_path",
                   return_value=expected_path), \
             patch("src.tracking.token_tracker.get_monthly_limit", return_value=100.0):
            t = TokenTracker("testprof")

        assert isinstance(t.data_file, Path)


# ---------------------------------------------------------------------------
# print_usage_report — budget warning lines (425, 427)
# ---------------------------------------------------------------------------


class TestPrintUsageReportBudgetWarnings:

    def _make_tracker_with_cost(self, tmp_path, cost: float, limit: float) -> TokenTracker:
        import json
        data = {
            "month": CURRENT_MONTH_DATA["month"],
            "total_usage": {
                "total_tokens": 1000,
                "total_input_tokens": 600,
                "total_output_tokens": 400,
                "total_cost": cost,
                "call_count": 5,
            },
            "model_usage": {},
            "daily_usage": {},
            "session_history": [],
        }
        data_file = tmp_path / "token_usage_warntest.json"
        data_file.write_text(json.dumps(data))
        return TokenTracker("warntest", data_file=str(data_file), monthly_limit=limit)

    def test_exceeded_warning_printed_when_cost_exceeds_limit(self, tmp_path, capsys):
        t = self._make_tracker_with_cost(tmp_path, cost=110.0, limit=100.0)
        t.print_usage_report()
        out = capsys.readouterr().out
        assert "MONTHLY LIMIT EXCEEDED" in out

    def test_approaching_warning_printed_when_above_80_percent(self, tmp_path, capsys):
        # 85 out of 100 → 85 % → approaching but not exceeded
        t = self._make_tracker_with_cost(tmp_path, cost=85.0, limit=100.0)
        t.print_usage_report()
        out = capsys.readouterr().out
        assert "Approaching monthly limit" in out

    def test_no_warning_when_well_under_limit(self, tmp_path, capsys):
        t = self._make_tracker_with_cost(tmp_path, cost=10.0, limit=100.0)
        t.print_usage_report()
        out = capsys.readouterr().out
        assert "EXCEEDED" not in out
        assert "Approaching" not in out


# ---------------------------------------------------------------------------
# print_usage_report — "no archived months" message (line 443)
# ---------------------------------------------------------------------------


class TestPrintUsageReportNoArchivedMonths:

    def test_no_archives_message_printed_when_include_all_time(self, loaded_tracker, capsys, tmp_path):
        empty_archive_dir = tmp_path / "empty_archives"
        empty_archive_dir.mkdir()

        with patch("src.tracking.token_tracker.get_archive_dir", return_value=empty_archive_dir):
            loaded_tracker.print_usage_report(include_all_time=True)

        out = capsys.readouterr().out
        assert "No archived months yet" in out

    def test_no_archives_message_not_printed_when_archives_exist(self, loaded_tracker, capsys, tmp_path):
        import json
        archive_dir = tmp_path / "has_archives"
        archive_dir.mkdir()
        (archive_dir / "2026-02.json").write_text(json.dumps(ARCHIVE_FEB_DATA))

        with patch("src.tracking.token_tracker.get_archive_dir", return_value=archive_dir):
            loaded_tracker.print_usage_report(include_all_time=True)

        out = capsys.readouterr().out
        assert "No archived months yet" not in out


# ---------------------------------------------------------------------------
# Thread safety — concurrent record_usage calls must not drop token counts
# ---------------------------------------------------------------------------

class TestConcurrentRecordUsage:
    """Verify that the threading.Lock in TokenTracker prevents token count loss
    when multiple parallel translation workers call record_usage simultaneously.

    Strategy: fire N threads, each calling record_usage once with a fixed token
    count. After all threads complete the in-memory total_tokens must equal
    N × per_call_tokens exactly.  Without the lock this test is highly likely
    to fail due to read-modify-write races on the shared usage_data dict.
    """

    N_THREADS = 16
    TOKENS_PER_CALL = 100

    @pytest.fixture
    def concurrent_tracker(self, tmp_path):
        data_file = str(tmp_path / "concurrent.json")
        return TokenTracker("concprof", data_file=data_file, monthly_limit=1000.0)

    def _build_mock_usage(self):
        """Return a mock API response object with fixed token counts."""
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = self.TOKENS_PER_CALL
        mock_usage.completion_tokens = 0
        mock_usage.total_tokens = self.TOKENS_PER_CALL
        return mock_usage

    def test_no_tokens_dropped_under_concurrent_load(self, concurrent_tracker):
        """All N concurrent calls must be reflected exactly in the final total."""
        import threading

        errors: list[Exception] = []

        def worker():
            try:
                with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
                     patch("src.tracking.token_tracker.get_model_pricing",
                           return_value={"input": 0.0, "output": 0.0}):
                    concurrent_tracker.record_usage(
                        model="gpt-4o",
                        prompt_tokens=self.TOKENS_PER_CALL,
                        completion_tokens=0,
                        total_tokens=self.TOKENS_PER_CALL,
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(self.N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Worker threads raised exceptions: {errors}"

        expected = self.N_THREADS * self.TOKENS_PER_CALL
        actual = concurrent_tracker.usage_data["total_usage"]["total_tokens"]
        assert actual == expected, (
            f"Token count mismatch: expected {expected}, got {actual}. "
            f"{expected - actual} token(s) were dropped (race condition)."
        )

    def test_call_count_matches_n_threads(self, concurrent_tracker):
        """call_count in total_usage must equal the number of concurrent calls."""
        import threading

        def worker():
            with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
                 patch("src.tracking.token_tracker.get_model_pricing",
                       return_value={"input": 0.0, "output": 0.0}):
                concurrent_tracker.record_usage(
                    model="gpt-4o",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                )

        threads = [threading.Thread(target=worker) for _ in range(self.N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = self.N_THREADS
        actual = concurrent_tracker.usage_data["total_usage"].get("call_count", 0)
        assert actual == expected

    def test_session_history_length_matches_n_threads(self, concurrent_tracker):
        """Every concurrent call must produce exactly one session_history entry."""
        import threading

        def worker():
            with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
                 patch("src.tracking.token_tracker.get_model_pricing",
                       return_value={"input": 0.0, "output": 0.0}):
                concurrent_tracker.record_usage(
                    model="gpt-4o",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                )

        threads = [threading.Thread(target=worker) for _ in range(self.N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(concurrent_tracker.usage_data["session_history"]) == self.N_THREADS


# ---------------------------------------------------------------------------
# Cross-process safety: two programs recording for the same professor
# ---------------------------------------------------------------------------

class TestConcurrentProcessesDoNotClobber:
    """A second program's spending must survive this one's next write.

    The web interface runs for hours while the professor may also use the
    command line, so two programs recording usage for the same professor at
    once is ordinary. Each used to keep the totals from whenever it started
    and write that copy back after every call, so whichever saved last
    silently erased the other's spending.
    """

    def _tracker(self, data_file):
        with patch("src.tracking.token_tracker.get_monthly_limit", return_value=100.0):
            return TokenTracker("testprof", data_file=str(data_file), monthly_limit=100.0)

    def _pricing(self):
        return (
            patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000),
            patch("src.tracking.token_tracker.get_model_pricing",
                  return_value={"input": 2.0, "output": 8.0}),
        )

    def test_second_trackers_usage_is_not_erased(self, tmp_path):
        data_file = tmp_path / "token_usage_testprof.json"
        # Both exist at once, each having loaded the file at startup —
        # exactly the webui-plus-terminal situation.
        webui = self._tracker(data_file)
        cli = self._tracker(data_file)

        p1, p2 = self._pricing()
        with p1, p2:
            webui.record_usage("gpt-4o", 100, 50, 150)
            cli.record_usage("gpt-4o", 200, 100, 300)
            # The webui records again from its now-stale in-memory copy.
            webui.record_usage("gpt-4o", 100, 50, 150)

        on_disk = json.loads(data_file.read_text())
        assert on_disk["total_usage"]["call_count"] == 3, "a recorded call was lost"
        assert on_disk["total_usage"]["total_tokens"] == 600
        assert len(on_disk["session_history"]) == 3

    def test_interleaved_recording_keeps_every_call(self, tmp_path):
        data_file = tmp_path / "token_usage_testprof.json"
        first = self._tracker(data_file)
        second = self._tracker(data_file)

        p1, p2 = self._pricing()
        with p1, p2:
            for _ in range(5):
                first.record_usage("gpt-4o", 10, 5, 15)
                second.record_usage("gpt-4o", 10, 5, 15)

        on_disk = json.loads(data_file.read_text())
        assert on_disk["total_usage"]["call_count"] == 10
        assert on_disk["total_usage"]["total_tokens"] == 150

    def test_unreadable_file_does_not_lose_the_call_being_recorded(self, tmp_path):
        """If the re-read fails, fall back to the in-memory totals rather than
        dropping the usage entirely."""
        data_file = tmp_path / "token_usage_testprof.json"
        t = self._tracker(data_file)
        p1, p2 = self._pricing()
        with p1, p2:
            with patch.object(TokenTracker, "_load_usage_data", side_effect=OSError("disk hiccup")):
                usage = t.record_usage("gpt-4o", 100, 50, 150)
        assert usage.total_tokens == 150
        assert t.usage_data["total_usage"]["call_count"] == 1


class TestUsageFileWithNoMonthRecorded:
    """A usage file missing its 'month' key must not be archived to '.json'.

    Comparing "" against a real month reads as "older than this month", which
    sent the file into rollover: archived under an empty name, and the
    professor's current totals silently zeroed.
    """

    def test_month_is_stamped_and_totals_survive(self, tmp_path, caplog):
        import logging as _logging
        data_file = tmp_path / "token_usage_testprof.json"
        data_file.write_text(json.dumps({
            "total_usage": UsageStats(
                total_tokens=500, total_input_tokens=400, total_output_tokens=100,
                total_cost=1.25, call_count=4,
            ).to_dict(),
            "model_usage": {}, "daily_usage": {}, "session_history": [],
        }))

        with caplog.at_level(_logging.WARNING):
            with patch("src.tracking.token_tracker.get_monthly_limit", return_value=100.0):
                t = TokenTracker("testprof", data_file=str(data_file), monthly_limit=100.0)

        assert t.usage_data["total_usage"]["call_count"] == 4, "existing totals were discarded"
        assert t.usage_data["month"] == t._get_current_month()
        assert "no month recorded" in caplog.text
        # Nothing should have been archived, least of all under an empty name.
        assert not (tmp_path / ".json").exists()


class TestAlternateEndpointsAreNotPriced:
    """An endpoint that isn't the sandbox has no prices the sandbox can apply.

    The sandbox's rates describe the university's arrangement with the one
    service it buys through. A cluster or subscription someone has set up
    themselves is charged — or not charged — on terms this program never sees,
    so its calls are recorded with their tokens and no money.
    """

    def _make_tracker(self, tmp_path):
        return TokenTracker(
            "testprof", data_file=str(tmp_path / "token_usage_test.json"), monthly_limit=100.0
        )

    def _priced(self):
        return (
            patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000),
            patch("src.tracking.token_tracker.get_model_pricing",
                  return_value={"input": 2.0, "output": 8.0}),
        )

    def test_a_call_to_an_endpoint_costs_nothing(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._priced()
        with p1, p2:
            usage = t.record_usage("llama-3-70b", 1000, 500, 1500, endpoint="my_cluster")
        assert usage.total_cost == 0.0
        assert usage.input_cost == 0.0
        assert usage.output_cost == 0.0

    def test_the_price_list_is_never_even_consulted(self, tmp_path):
        """Not merely zeroed afterwards.

        Looking the model up would log a warning telling someone to add it to
        the catalog — advice that makes no sense for a service the catalog
        does not describe.
        """
        t = self._make_tracker(tmp_path)
        with patch("src.tracking.token_tracker.get_model_pricing") as pricing:
            t.record_usage("llama-3-70b", 1000, 500, 1500, endpoint="my_cluster")
        pricing.assert_not_called()

    def test_a_sandbox_call_is_still_priced(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._priced()
        with p1, p2:
            usage = t.record_usage("gpt-4o", 1_000_000, 1_000_000, 2_000_000)
        assert usage.total_cost == 10.0

    def test_endpoint_usage_is_kept_out_of_the_sandbox_totals(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._priced()
        with p1, p2:
            t.record_usage("gpt-4o", 100, 50, 150)
            t.record_usage("llama-3-70b", 900, 400, 1300, endpoint="my_cluster")
        sandbox = t.usage_data["total_usage"]
        assert sandbox["total_tokens"] == 150, "the endpoint's tokens leaked into the sandbox's total"
        assert sandbox["call_count"] == 1
        assert "llama-3-70b" not in t.usage_data["model_usage"]

    def test_each_endpoint_gets_its_own_totals(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._priced()
        with p1, p2:
            t.record_usage("llama-3-70b", 100, 50, 150, endpoint="my_cluster")
            t.record_usage("llama-3-70b", 100, 50, 150, endpoint="my_cluster")
            t.record_usage("mistral", 10, 5, 15, endpoint="a_colleagues_server")
        endpoints = t.usage_data["endpoint_usage"]
        assert set(endpoints) == {"my_cluster", "a_colleagues_server"}
        assert endpoints["my_cluster"]["total_usage"]["call_count"] == 2
        assert endpoints["my_cluster"]["total_usage"]["total_tokens"] == 300
        assert endpoints["a_colleagues_server"]["total_usage"]["total_tokens"] == 15
        assert endpoints["my_cluster"]["total_usage"]["total_cost"] == 0.0

    def test_the_budget_counts_only_what_the_university_pays_for(self, tmp_path):
        """A budget is about money, and there is no money in these calls."""
        t = self._make_tracker(tmp_path)
        p1, p2 = self._priced()
        with p1, p2:
            t.record_usage("llama-3-70b", 10_000_000, 10_000_000, 20_000_000, endpoint="my_cluster")
        status = t.get_monthly_budget_status()
        assert status["usage_percentage"] == 0.0
        assert not status["is_exceeded"]

    def test_the_record_says_which_service_answered(self, tmp_path):
        t = self._make_tracker(tmp_path)
        p1, p2 = self._priced()
        with p1, p2:
            t.record_usage("gpt-4o", 1, 1, 2)
            t.record_usage("llama-3-70b", 1, 1, 2, endpoint="my_cluster")
        history = t.usage_data["session_history"]
        assert history[0]["endpoint"] == ""
        assert history[1]["endpoint"] == "my_cluster"


class TestFoldingRecordsKeepsServicesApart:
    """Shared-write mode rebuilds its totals from the saved records each time."""

    @staticmethod
    def _fold(records, month):
        from src.tracking.token_tracker import fold_usage_records

        return fold_usage_records(records, month)

    def _record(self, endpoint="", cost=1.0, tokens=100, model="gpt-4o"):
        return {
            "model": model, "prompt_tokens": tokens, "completion_tokens": tokens,
            "total_tokens": tokens * 2, "timestamp": "2026-07-15T10:00:00",
            "input_cost": 0.0, "output_cost": 0.0, "total_cost": cost,
            "source": "", "endpoint": endpoint,
        }

    def test_endpoint_records_do_not_reach_the_sandbox_totals(self):
        folded = self._fold(
            [self._record(), self._record(endpoint="my_cluster", model="llama-3-70b")], "2026-07"
        )
        assert folded["total_usage"]["call_count"] == 1
        assert folded["endpoint_usage"]["my_cluster"]["total_usage"]["call_count"] == 1
        assert "llama-3-70b" not in folded["model_usage"]

    def test_a_cost_saved_against_an_endpoint_is_dropped(self):
        """Files written before this was fixed hold invented figures.

        Those calls were priced against the sandbox's rates, which never
        applied to them. Re-reading such a file should not carry the invented
        money forward.
        """
        folded = self._fold([self._record(endpoint="my_cluster", cost=7.77)], "2026-07")
        assert folded["endpoint_usage"]["my_cluster"]["total_usage"]["total_cost"] == 0.0

    def test_older_records_with_no_endpoint_named_count_as_the_sandbox(self):
        """Every record written before this existed is a sandbox call."""
        old = self._record()
        del old["endpoint"]
        folded = self._fold([old], "2026-07")
        assert folded["total_usage"]["call_count"] == 1
        assert folded["endpoint_usage"] == {}


class TestSeparateReports:
    """The sandbox's report and each endpoint's are separate things."""

    def _tracker_with(self, tmp_path, endpoints):
        t = TokenTracker(
            "testprof", data_file=str(tmp_path / "token_usage_test.json"), monthly_limit=100.0
        )
        with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
             patch("src.tracking.token_tracker.get_model_pricing",
                   return_value={"input": 2.0, "output": 8.0}):
            t.record_usage("gpt-4o", 100, 50, 150)
            for name, model in endpoints:
                t.record_usage(model, 1000, 500, 1500, endpoint=name)
        return t

    def test_each_endpoint_gets_its_own_section(self, tmp_path, capsys):
        t = self._tracker_with(tmp_path, [("my_cluster", "llama-3-70b"),
                                          ("a_colleagues_server", "mistral")])
        t.print_usage_report()
        out = capsys.readouterr().out
        assert "my_cluster" in out
        assert "a_colleagues_server" in out
        assert "llama-3-70b" in out

    def test_an_endpoint_section_shows_no_money(self, tmp_path, capsys):
        t = self._tracker_with(tmp_path, [("my_cluster", "llama-3-70b")])
        t.print_usage_report()
        out = capsys.readouterr().out
        section = out[out.index("my_cluster"):]
        assert "$" not in section, f"a cost was shown for a service with no known prices:\n{section}"

    def test_it_says_why_there_is_no_cost(self, tmp_path, capsys):
        """A missing number reads as an omission unless it is explained."""
        t = self._tracker_with(tmp_path, [("my_cluster", "llama-3-70b")])
        t.print_usage_report()
        out = capsys.readouterr().out
        assert "No cost is shown" in out

    def test_the_sandbox_section_still_shows_money(self, tmp_path, capsys):
        t = self._tracker_with(tmp_path, [("my_cluster", "llama-3-70b")])
        t.print_usage_report()
        out = capsys.readouterr().out
        assert "Total Cost: $" in out[:out.index("my_cluster")]

    def test_nothing_extra_is_printed_when_no_endpoint_was_used(self, tmp_path, capsys):
        """Almost everyone is on the sandbox alone; their report is unchanged."""
        t = self._tracker_with(tmp_path, [])
        t.print_usage_report()
        out = capsys.readouterr().out
        assert "separate service" not in out
        assert "No cost is shown" not in out

    def test_an_archived_month_reports_its_endpoints_too(self, tmp_path, capsys):
        import json

        t = self._tracker_with(tmp_path, [("my_cluster", "llama-3-70b")])
        archive = t._archive_path_for("2026-05")
        archive.parent.mkdir(parents=True, exist_ok=True)
        with open(archive, "w") as f:
            json.dump({
                "month": "2026-05",
                "total_usage": UsageStats().to_dict(),
                "model_usage": {},
                "daily_usage": {},
                "endpoint_usage": {
                    "my_cluster": {
                        "total_usage": {"total_tokens": 42, "total_input_tokens": 21,
                                        "total_output_tokens": 21, "total_cost": 0.0,
                                        "call_count": 1},
                        "model_usage": {}, "daily_usage": {},
                    }
                },
                "session_history": [],
            }, f)
        t.print_usage_report(month="2026-05")
        out = capsys.readouterr().out
        assert "my_cluster" in out
        assert "42" in out
