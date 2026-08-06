# Token Usage Guide

Every call the sandbox makes to an AI model is recorded automatically: how much text went in, how much came back, and — for the Princeton sandbox service — what it cost. Each person's record is kept separately and split by calendar month, so no file grows without end and the current month's spending is always the first thing you see.

If you use an [alternate endpoint](#alternate-endpoints-are-counted-but-not-costed) of your own, its calls are counted but carry no cost, and are reported separately.

Tokens are roughly words — a little smaller, so a page of English is usually 400–600 of them. Models are priced per token in each direction: what you send costs one rate, what comes back costs another, usually higher.

---

## What the budget does, and doesn't, do

**The monthly limit is a number to watch, not a barrier.** Nothing in the sandbox stops working when you pass it. Reports print `⚠️ MONTHLY LIMIT EXCEEDED!`, warnings appear once you cross the warning threshold, and the web interface's spending sidebar shows the same — but every command still runs and still spends.

This is deliberate: a translation that stops halfway through a book because a number was crossed is worse than one that finishes and tells you. The only way to actually stop spending on a key is to have that key revoked, which happens outside this tool.

So: check `usage report` when you want to know where you stand. Don't rely on the limit to hold you there.

---

## What gets recorded

- Every call records tokens in, tokens out, and the total
- Calls to the Princeton sandbox also record what they cost
- Calls to an alternate endpoint record no cost — see [below](#alternate-endpoints-are-counted-but-not-costed)
- Each person has their own file for the current month
- The first time a call happens in a new month, the previous month's file is archived and a fresh one starts
- There are no manual steps — recording happens inside the shared service layer every AI call passes through

---

## Commands

### This month

```bash
python main.py jh43 usage report
```

Token counts, a breakdown by model, a breakdown by day, and budget status for the current calendar month.

### This month plus everything before it

```bash
python main.py jh43 usage report --all-time
```

Prints the current month, then adds up every archived month. The archives are read only when you ask for this, so it costs nothing on an ordinary report.

### One past month

```bash
python main.py jh43 usage report 2025-07
```

### Which months exist

```bash
python main.py jh43 usage months
```

### One day

```bash
python main.py jh43 usage daily              # today
python main.py jh43 usage daily 2026-02-14   # a particular date
```

---

## Where the files are

Inside the `data` folder of the folder you chose during setup — `~/PU_AISandbox_data/data` unless you picked somewhere else:

```
data/
  token_usage_jh43.json          ← the active file: this month only
  archives/
    jh43/
      2026-02.json               ← one file per past month, written automatically
      2026-03.json
      ...
```

Each file, active or archived, covers exactly one month:

```json
{
  "month": "2026-03",
  "total_usage": { ... },
  "model_usage":  { ... },
  "daily_usage":  { ... },
  "session_history": [ ... ]
}
```

The file is named with the person's netID, which is why netIDs are the identifier — they need no cleaning up to be used as a filename.

Don't edit these by hand. Where several computers share one folder (see [Configuration → Usage kept somewhere else](configuration.md#usage-kept-somewhere-else)), the sandbox writes one small file per call there instead of rewriting a shared one, so a sync service never has two conflicting edits to merge — which is why any number of computers can share a folder.

---

## Alternate endpoints are counted, but not costed

The prices the sandbox knows are Princeton's — the rates the university is charged for the one service it buys through. They describe that service and nothing else.

An alternate endpoint is a different arrangement entirely: a cluster your department runs, a subscription you hold yourself, a colleague's server. It may cost nothing, or it may be billed in a way that has no relation to what the sandbox charges. The sandbox has no way to know, so it does not guess.

So for calls to an alternate endpoint:

- **Tokens are recorded** — how much went in, how much came back, which model, and when
- **No cost is recorded.** Not zero as an estimate; no figure at all, because there is none to have
- **They do not count towards your monthly budget**, which is a limit on Princeton spending
- **They get their own section of the report**, one per endpoint, kept apart from the sandbox's figures

You will see them at the bottom of `usage report`:

```
hpc_cluster — separate service, not billed through the sandbox (this month):
----------------------------------------
Total Tokens Used: 1,700,000
  • Input Tokens:  1,300,000
  • Output Tokens: 400,000
API Calls:  2
llama-3-70b:
  • Calls:  2
  • Tokens: 1,700,000
No cost is shown: the sandbox's prices are the university's and
do not describe this service. Whatever it charges, if anything,
is between you and whoever runs it.
```

If you have never used an alternate endpoint — which is almost everyone — nothing about your report changes. See [Configuration](configuration.md#alternate-ai-endpoints) for how one is set up.

---

## Where the prices come from

`model_catalog.json`, in the same folder, holds the price of every model the sandbox knows about. The sandbox will not run without it; setup creates it for you.

```bash
python main.py settings setup
```

**OpenAI and Google models** — the price is fetched and saved the first time you name a model this way:

```bash
python main.py jh43 prompt -m openai/gpt-4o-new
python main.py jh43 prompt -m google/gemini-2.5-pro
```

**Everything else** — add the entry by hand:

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

Prices are per 1,000,000 tokens by default. See [Configuration](configuration.md#model_catalogjson--model-pricing-and-capabilities) for the full schema.

### The two budget settings

| Setting | Where | Default | Effect |
|---------|-------|---------|--------|
| `monthly_limit` | `model_catalog.json`, under `config` | `250.0` | The figure reports measure spending against |
| `warning_threshold_pct` | `settings.default.toml`, under `[budget]` | `80` | Start warning at this share of the limit |

---

## What a report looks like

### This month

```
============================================================
TOKEN USAGE REPORT - PROFESSOR JH43
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

### A past month

```
============================================================
TOKEN USAGE REPORT - PROFESSOR JH43
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

## When something goes wrong

### It won't start

1. Check `model_catalog.json` exists in your files folder. If it doesn't, run `python main.py settings setup`.
2. Check the file is valid JSON — a missing comma or bracket is the usual cause.
3. Check it has both a `config` section and a `models` section with at least one model in it.

### Usage isn't being recorded

1. Check the `data` folder can be written to.
2. Check the model name in `model_catalog.json` matches the name the API reports back. Run with `--verbose` to see the model name used on each call, and compare it with the keys in the catalogue.
3. Check `monthly_limit` is set in the catalogue's `config` section.

### Error messages

| Message | What to do |
|---------|------------|
| `Model catalog file not found` | `python main.py settings setup` |
| `Invalid JSON in model catalog file` | Fix the syntax error in `model_catalog.json` |
| `Missing required 'models' section` | Add a `"models"` key to the catalogue |
| `No models configured` | Add at least one model to `"models"` |
| `No archive found for YYYY-MM` | Nothing was recorded that month — `usage months` lists the ones that exist |
