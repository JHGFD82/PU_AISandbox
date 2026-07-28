# Token Usage Guide

PU_AISandbox tracks every API call automatically. Each professor's usage is isolated per calendar month so the active file stays small and the monthly budget limit is always front-and-center.

---

## How Tracking Works

- Every service call records prompt tokens, completion tokens, total tokens, and cost
- Each professor has their own active data file for the current month
- At the start of a new month the previous file is automatically archived and a fresh file begins
- No manual steps are required — tracking is wired into `BaseService._record_response_usage()`

---

## Usage Commands

### Current month report

```bash
python main.py jh43 usage report
```

Prints token counts, model breakdown, daily breakdown, and budget status for the current calendar month.

### Current month + all-time totals

```bash
python main.py jh43 usage report --all-time
```

Prints the current month report, then aggregates all archived months on demand.

### Specific archived month

```bash
python main.py jh43 usage report 2025-07
```

### List archived months

```bash
python main.py jh43 usage months
```

### Daily usage

```bash
python main.py jh43 usage daily              # today
python main.py jh43 usage daily 2026-02-14   # specific date
```

---

## File Layout

```
data/
  token_usage_{name}.json        ← active file, current month only
  archives/
    {name}/
      2026-02.json               ← one file per past month (auto-created)
      2026-03.json
      ...
```

Each file (active or archive) covers exactly one calendar month:

```json
{
  "month": "2026-03",
  "total_usage": { ... },
  "model_usage":  { ... },
  "daily_usage":  { ... },
  "session_history": [ ... ]
}
```

Files grow only within a month, then are archived and replaced. No single file accumulates indefinitely.

---

## Model Catalog

`src/model_catalog.json` is the pricing and capabilities registry. The application will not run without it. It is git-ignored — each installation maintains its own copy.

### Setup

Copy the tracked template on first install:

```bash
python main.py settings setup
```

See [`configuration.md`](configuration.md) for the full schema and customization options.

### Keeping Pricing Current

**OpenAI and Google models** — pricing is fetched automatically from PortKey on first use of a new model name. Use the `provider/model` syntax:

```bash
python main.py jh43 prompt -m openai/gpt-4o-new
python main.py jh43 prompt -m google/gemini-2.5-pro
```

The fetched price is saved to `src/model_catalog.json` automatically.

**All other providers** — edit `src/model_catalog.json` directly. Minimal entry:

```json
{
  "models": {
    "mistral-small": {
      "input": 0.1,
      "output": 0.3,
      "supports_vision": false
    }
  }
}
```

Prices are per 1,000,000 tokens (the default `pricing_unit`).

---

## Budget Settings

The monthly spending limit and warning threshold are configured in two places:

| Setting | Location | Default | Effect |
|---------|----------|---------|--------|
| `monthly_limit` | `src/model_catalog.json` → `config` | `250.0` | Hard limit shown in usage reports |
| `warning_threshold_pct` | `settings.default.toml` → `[budget]` | `80` | Warn when spend exceeds this % of the limit |

---

## Sample Output

### Current month report

```
============================================================
TOKEN USAGE REPORT - PROFESSOR HELLER
============================================================

Current Month (2026-03):
----------------------------------------
Total Tokens Used: 3,731
  • Input Tokens:  2,716
  • Output Tokens: 1,015
Total Cost: $0.0186

Model Breakdown (this month):
----------------------------------------
gpt-4o-2024-08-06:
  • Calls:  2
  • Tokens: 3,731
  • Cost:   $0.0186

Monthly Budget (2026-03):
----------------------------------------
Monthly Limit: $250.00
Used:          $0.0186 (0.0%)
Remaining:     $249.98
============================================================
```

### Archived month report

```
============================================================
TOKEN USAGE REPORT - PROFESSOR HELLER
============================================================

Archived Month (2026-02):
----------------------------------------
Total Tokens Used: 223,722
  • Input Tokens:  150,297
  • Output Tokens: 73,425
Total Cost: $0.9050
API Calls:  34

Model Breakdown:
----------------------------------------
gpt-4o-2024-08-06:
  • Calls:  18
  • Tokens: 40,052
  • Cost:   $0.2449

Daily Breakdown:
----------------------------------------
2026-02-24: 136,905 tokens  $0.2009  (11 calls)
2026-02-27: 83,086  tokens  $0.6854  (21 calls)
============================================================
```

---

## Troubleshooting

### Application won't start

1. Confirm `src/model_catalog.json` exists — copy from the template if needed:
   ```bash
   python main.py settings setup
   ```
2. Validate the JSON (no syntax errors)
3. Confirm the file contains both a `"config"` section and a non-empty `"models"` section

### Usage tracking gaps

1. Verify `data/` is writable
2. Check that the model name in `model_catalog.json` matches the name returned by the API (inspect `src/model_catalog.json` keys vs. the model name logged during a run)
3. Confirm `monthly_limit` is set in the catalog `config` section

### Common error messages

| Message | Fix |
|---------|-----|
| `Model catalog file not found` | `python main.py settings setup` |
| `Invalid JSON in model catalog file` | Fix syntax errors in `src/model_catalog.json` |
| `Missing required 'models' section` | Add a `"models"` key to the catalog |
| `No models configured` | Add at least one model entry to `"models"` |
| `No archive found for YYYY-MM` | That month has no data — run `usage months` to see available archives |
