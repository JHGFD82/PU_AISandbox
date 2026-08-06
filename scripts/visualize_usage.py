#!/usr/bin/env python3
"""
Standalone usage visualizer for the PU AI Sandbox.
Reads data/ and data/archives/, generates an interactive HTML report,
and opens it in the default browser.

Usage:
    python scripts/visualize_usage.py
    python scripts/visualize_usage.py --no-open   # generate file, print path only
"""

import json
import re
import sys
import webbrowser
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Make src/ importable when this script is run directly (python scripts/visualize_usage.py),
# since a directly-run script only gets its own directory on sys.path by default.
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tracking.token_tracker import get_configured_data_roots, load_usage_tree  # noqa: E402

# The report is written into the folder the data actually lives in,
# wherever this installation keeps it — not next to this script, which
# is part of the package and gets replaced on upgrade.
#
# Asked for when it's needed rather than when this file is imported: on a
# copy that hasn't been set up there is no answer yet, and working that out
# at import time would mean nothing in this file could even be loaded.
from src.paths import data_root

# --- Color palettes -----------------------------------------------------------

PROF_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
]

MODEL_PALETTE = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
]

# --- Data loading -------------------------------------------------------------

def clean_model_name(name: str) -> str:
    """Strip date suffixes: 'gpt-4o-2024-08-06' → 'gpt-4o'."""
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", name)


def load_all_data() -> dict:
    """Return {professor_safe_name: {YYYY-MM: month_dict}}, merged across every configured data root.

    "Every configured data root" means this installation's own local
    ``data/`` folder plus any external sources registered via
    ``python main.py <professor> usage sources add`` (see
    ``src/tracking/token_tracker.py::load_usage_tree``). If the same
    professor+month somehow appears in more than one root (e.g. stale
    local data left over from before a professor switched to a
    shared-write source), the later root in the configured list wins —
    external sources are listed after "local", so a shared-write source
    naturally supersedes stale local data for that professor.
    """
    result: dict = {}
    for _label, root, only_professor in get_configured_data_roots():
        # A shared folder belongs to one person and has nothing inside it
        # naming them, so it is read as theirs. This installation's own data/
        # folder holds everybody and says so in each path, so it is not.
        tree = load_usage_tree(root, only_professor)
        for prof, months in tree.items():
            if only_professor and prof != only_professor:
                continue
            result.setdefault(prof, {}).update(months)
    return result


def get_all_months(all_data: dict) -> list:
    months: set = set()
    for prof_data in all_data.values():
        months.update(prof_data.keys())
    return sorted(months)


# --- Summary stats ------------------------------------------------------------

def compute_summary(all_data: dict) -> dict:
    total_cost = 0.0
    total_tokens = 0
    total_calls = 0
    for prof_data in all_data.values():
        for month_data in prof_data.values():
            u = month_data.get("total_usage", {})
            total_cost += u.get("total_cost", 0)
            total_tokens += u.get("total_tokens", 0)
            total_calls += u.get("call_count", 0)
    return {
        "total_cost": round(total_cost, 4),
        "total_tokens": total_tokens,
        "total_calls": total_calls,
        "professors": sorted(all_data.keys()),
        # Guarded on the month list itself, not on all_data. A professor can
        # be present with no months recorded at all (an empty usage file, or
        # one saved without a month), which makes all_data truthy while
        # leaving nothing to index — previously an IndexError that took the
        # whole report down rather than just showing a dash.
        "month_span": _format_month_span(get_all_months(all_data)),
    }


def _format_month_span(months: list) -> str:
    """Describe the range of months covered, e.g. '2026-01 → 2026-07'.

    Args:
        months: Every month with recorded usage, sorted oldest first.

    Returns:
        A dash if there are no months at all, the single month if there's
        only one, or ``'first → last'``.
    """
    if not months:
        return "—"
    if len(months) == 1:
        return months[0]
    return f"{months[0]} → {months[-1]}"


# --- Chart data builders ------------------------------------------------------

def build_charts_data(all_data: dict) -> dict:
    months = get_all_months(all_data)
    professors = sorted(all_data.keys())

    # 1. Monthly cost — one series per professor (for stacked bar)
    monthly_cost_by_prof: dict = {}
    for prof in professors:
        monthly_cost_by_prof[prof] = [
            round(all_data[prof].get(m, {}).get("total_usage", {}).get("total_cost", 0), 4)
            for m in months
        ]

    # 2. Monthly token volume — input vs output, all professors combined
    monthly_input: list = []
    monthly_output: list = []
    for m in months:
        inp = sum(
            all_data[p].get(m, {}).get("total_usage", {}).get("total_input_tokens", 0)
            for p in professors
        )
        out = sum(
            all_data[p].get(m, {}).get("total_usage", {}).get("total_output_tokens", 0)
            for p in professors
        )
        monthly_input.append(round(inp / 1000, 1))
        monthly_output.append(round(out / 1000, 1))

    # 3. Rolling 30-day cumulative cost — spans month boundaries naturally.
    from datetime import timedelta, date as date_type
    today = date_type.today()
    rolling_dates_sorted = [
        (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(29, -1, -1)
    ]

    # Look up per-day cost across whichever month the day falls in
    def _day_cost(prof: str, day: str) -> float:
        month = day[:7]
        return (
            all_data[prof]
            .get(month, {})
            .get("daily_usage", {})
            .get(day, {})
            .get("total_cost", 0.0)
        )

    daily_cost_by_prof: dict = {}
    for prof in professors:
        cumulative = 0.0
        series = []
        for d in rolling_dates_sorted:
            cumulative += _day_cost(prof, d)
            series.append(round(cumulative, 4))
        daily_cost_by_prof[prof] = series

    daily_dates_sorted = rolling_dates_sorted
    display_month = f"{rolling_dates_sorted[0][5:]} \u2192 {rolling_dates_sorted[-1][5:]}"

    # 4. Model share — all-time cost per model
    model_totals: dict = defaultdict(float)
    for prof_data in all_data.values():
        for month_data in prof_data.values():
            for raw_model, stats in month_data.get("model_usage", {}).items():
                model_totals[clean_model_name(raw_model)] += stats.get("total_cost", 0)
    model_labels = sorted(model_totals.keys())
    model_values = [round(model_totals[k], 4) for k in model_labels]

    # 5. Activity by source — who made each call. Only meaningful for professors tracked via a shared-write
    # source; calls with no source tag (older, pre-migration records) are
    # grouped under "unspecified" rather than dropped.
    source_totals: dict = defaultdict(float)
    for prof_data in all_data.values():
        for month_data in prof_data.values():
            for record in month_data.get("session_history", []):
                source = record.get("source") or "unspecified"
                source_totals[source] += record.get("total_cost", 0)
    source_labels = sorted(source_totals.keys())
    source_values = [round(source_totals[k], 4) for k in source_labels]

    return {
        "months": months,
        "professors": professors,
        "monthly_cost_by_prof": monthly_cost_by_prof,
        "monthly_input": monthly_input,
        "monthly_output": monthly_output,
        "current_month": display_month,
        "daily_dates": [d[5:] for d in daily_dates_sorted],   # strip YYYY- → MM-DD
        "daily_cost_by_prof": daily_cost_by_prof,
        "model_labels": model_labels,
        "model_values": model_values,
        "source_labels": source_labels,
        "source_values": source_values,
    }


# --- HTML generation ----------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CJK Toolkit — Usage Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f6fa;
    color: #333;
    padding: 24px;
  }}
  h1 {{
    font-size: 1.6rem;
    font-weight: 600;
    margin-bottom: 4px;
    color: #1a1a2e;
  }}
  .subtitle {{
    font-size: 0.85rem;
    color: #666;
    margin-bottom: 24px;
  }}
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 14px;
    margin-bottom: 28px;
  }}
  .stat-card {{
    background: #fff;
    border-radius: 10px;
    padding: 18px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  }}
  .stat-card .value {{
    font-size: 1.55rem;
    font-weight: 700;
    color: #4e79a7;
    line-height: 1.1;
  }}
  .stat-card .label {{
    font-size: 0.78rem;
    color: #888;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .charts-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 20px;
  }}
  @media (max-width: 900px) {{
    .charts-grid {{ grid-template-columns: 1fr; }}
  }}
  .chart-card {{
    background: #fff;
    border-radius: 10px;
    padding: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  }}
  .chart-card h2 {{
    font-size: 0.95rem;
    font-weight: 600;
    color: #555;
    margin-bottom: 14px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  canvas {{ max-height: 280px; }}
  footer {{
    margin-top: 24px;
    font-size: 0.75rem;
    color: #aaa;
    text-align: center;
  }}
</style>
</head>
<body>

<h1>PU AI Sandbox — Usage Dashboard</h1>
<p class="subtitle">Generated {generated_at} &nbsp;·&nbsp; Data range: {month_span}</p>

<div class="summary-grid">
  <div class="stat-card">
    <div class="value">${total_cost}</div>
    <div class="label">Total spend</div>
  </div>
  <div class="stat-card">
    <div class="value">{total_tokens_k}K</div>
    <div class="label">Total tokens</div>
  </div>
  <div class="stat-card">
    <div class="value">{total_calls}</div>
    <div class="label">API calls</div>
  </div>
  <div class="stat-card">
    <div class="value">{num_professors}</div>
    <div class="label">Professors</div>
  </div>
</div>

<div class="charts-grid">

  <div class="chart-card">
    <h2>Monthly cost by professor</h2>
    <canvas id="monthlyCostChart"></canvas>
  </div>

  <div class="chart-card">
    <h2>Monthly token volume (K tokens)</h2>
    <canvas id="tokenVolumeChart"></canvas>
  </div>

  <div class="chart-card">
    <h2>Cumulative cost — last 30 days ({current_month})</h2>
    <canvas id="dailyCostChart"></canvas>
  </div>

  <div class="chart-card">
    <h2>All-time cost by model</h2>
    <canvas id="modelShareChart"></canvas>
  </div>

  <div class="chart-card">
    <h2>All-time cost by source</h2>
    <canvas id="sourceShareChart"></canvas>
  </div>

</div>

<footer>Princeton University AI Sandbox</footer>

<script>
const PROF_COLORS = {prof_colors_json};
const MODEL_PALETTE = {model_palette_json};
const DATA = {charts_data_json};

// Shared tick formatter
const usdFormatter = v => '$' + v.toFixed(4);
const usdTooltip = {{
  callbacks: {{
    label: ctx => ' ' + ctx.dataset.label + ': $' + ctx.parsed.y.toFixed(4)
  }}
}};

// 1. Monthly cost stacked bar
new Chart(document.getElementById('monthlyCostChart'), {{
  type: 'bar',
  data: {{
    labels: DATA.months,
    datasets: DATA.professors.map((prof, i) => ({{
      label: prof,
      data: DATA.monthly_cost_by_prof[prof],
      backgroundColor: PROF_COLORS[i % PROF_COLORS.length],
      borderRadius: 3,
    }}))
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
      tooltip: usdTooltip,
    }},
    scales: {{
      x: {{ stacked: true, ticks: {{ font: {{ size: 10 }} }} }},
      y: {{ stacked: true, ticks: {{ callback: usdFormatter, font: {{ size: 10 }} }} }},
    }}
  }}
}});

// 2. Token volume grouped bar
new Chart(document.getElementById('tokenVolumeChart'), {{
  type: 'bar',
  data: {{
    labels: DATA.months,
    datasets: [
      {{
        label: 'Input tokens',
        data: DATA.monthly_input,
        backgroundColor: '#4e79a7cc',
        borderRadius: 3,
      }},
      {{
        label: 'Output tokens',
        data: DATA.monthly_output,
        backgroundColor: '#f28e2bcc',
        borderRadius: 3,
      }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
      tooltip: {{ callbacks: {{ label: ctx => ' ' + ctx.dataset.label + ': ' + ctx.parsed.y + 'K' }} }},
    }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 10 }} }} }},
      y: {{ ticks: {{ callback: v => v + 'K', font: {{ size: 10 }} }} }},
    }}
  }}
}});

// 3. Daily cost line chart
new Chart(document.getElementById('dailyCostChart'), {{
  type: 'line',
  data: {{
    labels: DATA.daily_dates,
    datasets: DATA.professors.map((prof, i) => ({{
      label: prof,
      data: DATA.daily_cost_by_prof[prof],
      borderColor: PROF_COLORS[i % PROF_COLORS.length],
      backgroundColor: PROF_COLORS[i % PROF_COLORS.length] + '22',
      tension: 0.3,
      fill: true,
      pointRadius: 4,
      pointHoverRadius: 6,
    }}))
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
      tooltip: usdTooltip,
    }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 10 }} }} }},
      y: {{ ticks: {{ callback: usdFormatter, font: {{ size: 10 }} }} }},
    }}
  }}
}});

// 4. Model share doughnut
new Chart(document.getElementById('modelShareChart'), {{
  type: 'doughnut',
  data: {{
    labels: DATA.model_labels,
    datasets: [{{
      data: DATA.model_values,
      backgroundColor: DATA.model_labels.map((_, i) => MODEL_PALETTE[i % MODEL_PALETTE.length]),
      borderWidth: 2,
      borderColor: '#fff',
    }}]
  }},
  options: {{
    responsive: true,
    cutout: '60%',
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ' ' + ctx.label + ': $' + ctx.parsed.toFixed(4)
        }}
      }},
    }}
  }}
}});

// 5. Source share doughnut — "your activity vs. theirs" for shared-write professors
new Chart(document.getElementById('sourceShareChart'), {{
  type: 'doughnut',
  data: {{
    labels: DATA.source_labels,
    datasets: [{{
      data: DATA.source_values,
      backgroundColor: DATA.source_labels.map((_, i) => PROF_COLORS[i % PROF_COLORS.length]),
      borderWidth: 2,
      borderColor: '#fff',
    }}]
  }},
  options: {{
    responsive: true,
    cutout: '60%',
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ' ' + ctx.label + ': $' + ctx.parsed.toFixed(4)
        }}
      }},
    }}
  }}
}});
</script>
</body>
</html>
"""


def generate_html(summary: dict, charts_data: dict) -> str:
    return HTML_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        month_span=summary["month_span"],
        total_cost=f"{summary['total_cost']:.4f}",
        total_tokens_k=f"{summary['total_tokens'] / 1000:.1f}",
        total_calls=summary["total_calls"],
        num_professors=len(summary["professors"]),
        current_month=charts_data["current_month"],
        prof_colors_json=json.dumps(PROF_COLORS),
        model_palette_json=json.dumps(MODEL_PALETTE),
        charts_data_json=json.dumps(charts_data, ensure_ascii=False),
    )


# --- Entry point --------------------------------------------------------------

def main():
    no_open = "--no-open" in sys.argv

    data_dir = data_root()

    print("Loading usage data...")
    all_data = load_all_data()

    if not all_data:
        print("No usage data found in", data_dir)
        sys.exit(1)

    professors = sorted(all_data.keys())
    total_months = len(get_all_months(all_data))
    print(f"  Found {len(professors)} professor(s): {', '.join(professors)}")
    print(f"  Covering {total_months} month(s)")

    summary = compute_summary(all_data)
    charts_data = build_charts_data(all_data)
    html = generate_html(summary, charts_data)

    out_path = data_dir / "usage_report.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"  Report written to: {out_path}")

    if not no_open:
        webbrowser.open(out_path.as_uri())
        print("  Opening in browser...")


if __name__ == "__main__":
    main()
