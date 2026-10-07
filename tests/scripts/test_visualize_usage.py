"""Tests for scripts/visualize_usage.py — the standalone usage-report script.

This script isn't imported by the package (it's run directly, as
``python scripts/visualize_usage.py``), so it needs its module loaded by path
rather than a normal import.
"""

import importlib.util
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def viz():
    """Load scripts/visualize_usage.py as a module."""
    path = _REPO_ROOT / "scripts" / "visualize_usage.py"
    spec = importlib.util.spec_from_file_location("_viz_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_viz_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _month(cost=1.0, tokens=100, calls=2):
    return {"total_usage": {"total_cost": cost, "total_tokens": tokens, "call_count": calls}}


class TestComputeSummaryMonthSpan:
    """compute_summary must survive a professor with no months recorded.

    A professor present in the data with an empty month dict makes all_data
    truthy while leaving the month list empty — previously an IndexError that
    took the whole report down instead of showing a dash.
    """

    def test_professor_with_no_months_does_not_crash(self, viz):
        summary = viz.compute_summary({"heller": {}})
        assert summary["month_span"] == "—"
        assert summary["professors"] == ["heller"]
        assert summary["total_cost"] == 0

    def test_mixed_professors_where_one_has_no_months(self, viz):
        summary = viz.compute_summary({
            "heller": {},
            "smith": {"2026-06": _month(), "2026-07": _month()},
        })
        assert summary["month_span"] == "2026-06 → 2026-07"

    def test_no_data_at_all(self, viz):
        assert viz.compute_summary({})["month_span"] == "—"

    def test_single_month_is_not_shown_as_a_range(self, viz):
        summary = viz.compute_summary({"smith": {"2026-07": _month()}})
        assert summary["month_span"] == "2026-07"

    def test_totals_still_add_up(self, viz):
        summary = viz.compute_summary({
            "smith": {"2026-06": _month(cost=1.5, tokens=10, calls=1),
                      "2026-07": _month(cost=2.5, tokens=20, calls=3)},
        })
        assert summary["total_cost"] == 4.0
        assert summary["total_tokens"] == 30
        assert summary["total_calls"] == 4


def _usage(day, cost, model="gpt-4o-2024-08-06", source=None, tokens_in=1000, tokens_out=500):
    """One month of one person's usage, with all of it on *day*."""
    record = {"total_cost": cost}
    if source:
        record["source"] = source
    return {
        "total_usage": {"total_cost": cost, "total_tokens": tokens_in + tokens_out, "call_count": 1,
                        "total_input_tokens": tokens_in, "total_output_tokens": tokens_out},
        "daily_usage": {day: {"total_cost": cost}},
        "model_usage": {model: {"total_cost": cost}},
        "session_history": [record],
    }


class TestTheChartData:
    @pytest.fixture
    def charts(self, viz):
        from datetime import date

        today = date.today().strftime("%Y-%m-%d")
        return viz.build_charts_data({
            "heller": {today[:7]: _usage(today, 2.0, source="lab-mac")},
            "smith": {"2026-01": _usage("2026-01-15", 1.0, model="gpt-4o-mini")},
        })

    def test_each_person_has_a_cost_for_every_month(self, charts):
        assert charts["professors"] == ["heller", "smith"]
        assert all(len(series) == len(charts["months"]) for series in charts["monthly_cost_by_prof"].values())

    def test_tokens_are_counted_in_thousands_across_everyone(self, charts):
        assert sum(charts["monthly_input"]) == 2.0 and sum(charts["monthly_output"]) == 1.0

    def test_the_last_thirty_days_add_up_day_by_day(self, charts):
        assert len(charts["daily_dates"]) == 30
        assert charts["daily_cost_by_prof"]["heller"][-1] == 2.0
        assert charts["daily_cost_by_prof"]["smith"][-1] == 0.0

    def test_dated_model_names_are_counted_as_one_model(self, charts):
        assert dict(zip(charts["model_labels"], charts["model_values"], strict=True)) == {"gpt-4o": 2.0, "gpt-4o-mini": 1.0}

    def test_calls_with_no_source_are_grouped_rather_than_dropped(self, charts):
        assert dict(zip(charts["source_labels"], charts["source_values"], strict=True)) == {
            "lab-mac": 2.0, "unspecified": 1.0}


class TestThePage:
    def test_it_carries_the_summary_and_the_chart_data(self, viz):
        data = {"smith": {"2026-01": _usage("2026-01-15", 1.25)}}
        html = viz.generate_html(viz.compute_summary(data), viz.build_charts_data(data))
        assert "1.2500" in html
        assert '"professors": ["smith"]' in html


class TestRunningIt:
    @pytest.fixture
    def run(self, viz, monkeypatch, tmp_path):
        opened = []
        monkeypatch.setattr(viz, "data_root", lambda: tmp_path)
        monkeypatch.setattr(viz.webbrowser, "open", opened.append)
        monkeypatch.setattr("src.tracking.token_tracker.unreadable_folders", lambda: [])

        def run(data, *flags):
            monkeypatch.setattr(viz, "load_all_data", lambda: data)
            monkeypatch.setattr(viz.sys, "argv", ["visualize_usage.py", *flags])
            viz.main()
            return opened
        return run

    def test_the_report_is_written_and_opened(self, run, tmp_path):
        opened = run({"smith": {"2026-01": _usage("2026-01-15", 1.0)}})
        assert (tmp_path / "usage_report.html").exists()
        assert opened == [(tmp_path / "usage_report.html").as_uri()]

    def test_no_open_leaves_the_browser_alone(self, run, tmp_path):
        assert run({"smith": {"2026-01": _usage("2026-01-15", 1.0)}}, "--no-open") == []
        assert (tmp_path / "usage_report.html").exists()

    def test_no_usage_at_all_stops_with_a_reason(self, run, capsys):
        with pytest.raises(SystemExit):
            run({})
        assert "No usage data found" in capsys.readouterr().out

    def test_a_folder_that_is_not_there_is_warned_about_first(self, viz, run, monkeypatch, capsys):
        """Its figures are missing, and a quiet report would read as nobody having spent anything."""
        from types import SimpleNamespace

        gone = SimpleNamespace(professor="heller", resolved_path=lambda: "/Volumes/lab/heller")
        monkeypatch.setattr("src.tracking.token_tracker.unreadable_folders", lambda: [gone])
        with pytest.raises(SystemExit):
            run({})
        out = capsys.readouterr().out
        assert "heller's work is kept in /Volumes/lab/heller" in out
        assert "Every folder that was supposed to hold some is unreadable" in out


class TestGatheringEveryonesUsage:
    def test_folders_are_merged_and_a_shared_one_counts_only_for_its_owner(self, viz, monkeypatch):
        trees = {
            "local": {"heller": {"2026-01": "local jan"}, "smith": {"2026-01": "smith jan"}},
            "shared": {"heller": {"2026-01": "shared jan", "2026-02": "shared feb"},
                       "intruder": {"2026-01": "not theirs"}},
        }
        monkeypatch.setattr(viz, "get_configured_data_roots",
                            lambda: [("local", "local", None), ("shared", "shared", "heller")])
        monkeypatch.setattr(viz, "load_usage_tree", lambda root, only: trees[root])
        assert viz.load_all_data() == {
            "heller": {"2026-01": "shared jan", "2026-02": "shared feb"},
            "smith": {"2026-01": "smith jan"},
        }
