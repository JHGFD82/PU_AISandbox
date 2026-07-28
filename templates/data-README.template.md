# Data Directory

This directory contains professor-specific token usage data files.

## Structure

```
data/
  token_usage_{professor}.json   ← active file, current calendar month only
  archives/
    {professor}/
      2026-02.json               ← one file per past month (auto-created)
      2026-03.json
      ...
```

## Active Files
- `token_usage_{professor}.json` — created automatically on first run for each professor
- Covers the **current month only**; totals reset at the start of each new month
- At month rollover the file is archived automatically and a fresh one is started

## Archives
- `archives/{professor}/{YYYY-MM}.json` — one self-contained file per past month
- Each archive file has the same structure as the active file and is never modified after creation
- All-time totals are computed on demand by aggregating the active file with all archive files

## Note
Do not manually edit these files. They are managed entirely by the token tracking system.
