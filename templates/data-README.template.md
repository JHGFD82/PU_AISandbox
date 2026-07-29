# Data Directory

This folder holds each person's record of what they've spent on AI calls.

## Structure

```
data/
  token_usage_{netid}.json       ← active file, current calendar month only
  archives/
    {netid}/
      2026-02.json               ← one file per past month, created automatically
      2026-03.json
      ...
```

`{netid}` is the person's university username — `jh43`, not their display name. That's what makes it usable as a filename exactly as typed.

## Active files

- `token_usage_{netid}.json` is created automatically the first time each person makes a call
- It covers the **current month only**; totals start again at the beginning of each month
- On the first call of a new month the old file is archived and a fresh one started

## Archives

- `archives/{netid}/{YYYY-MM}.json` — one self-contained file per past month
- An archive has the same shape as the active file and is never changed after it's written
- All-time totals are worked out on demand by adding the active file to all the archives

## A note

Don't edit these by hand. The sandbox manages them entirely.

If this folder is shared between two installations (see `usage sources` in the CLI reference), each call writes its own small file here instead of rewriting a shared one, so a file-sync service never has two conflicting edits to merge.
