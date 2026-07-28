"""Tests for scripts/visualize_usage.py — the standalone usage-report script.

This script isn't imported by the package (it's run directly, as
``python scripts/visualize_usage.py``), so it needs its module loaded by path
rather than a normal import.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


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
