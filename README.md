# PU AI Sandbox

Modular AI platform for Princeton University faculty. Commands are implemented as plugins, so capabilities can be added or updated independently without touching core code.

> **Access requirement:** Each user must have a valid Princeton University AI Sandbox API key (available through OIT). This tool is for Princeton faculty and authorized delegates only.

## Architecture

```text
main.py
  → src/cli.py            controller + argument parser; loads plugins, routes commands
    → src/config.py       shared config, model catalog, professor parsing helpers
  → src/runtime/
      plugin_loader.py    discovers plugins/*/plugin.py at startup
      info_commands.py    built-in: --list-models, usage subcommands
      sandbox_processor.py  shared service wiring used by plugins
  → src/services/         API-facing operations (TranslationService, ImageProcessorService)
  → src/processors/       document preprocessing (PDF, DOCX, TXT, image)
  → src/tracking/         per-professor token accounting + pricing (model_catalog.json)
  → src/output/           text / PDF / Word document output
  → plugins/
      prompt/             bundled reference plugin (ships with this repo)
      translation/        separate git repo — clone into plugins/translation/
      transcription/      separate git repo — clone into plugins/transcription/
```

`src/cli.py` decides *what* to run. Plugins decide *how* to run it. Only the `usage` subcommand is built in.

---

## First-Run Setup

### 1. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure professors

Copy the template and edit it:

```bash
cp .env.template .env
```

Edit `.env` — add one block per professor:

```env
PROF_1_NAME=Heller
PROF_1_KEY=your_primary_api_key_here
PROF_1_BACKUP_KEY=your_backup_api_key_here

PROF_2_NAME=Smith
PROF_2_KEY=another_primary_key_here
PROF_2_BACKUP_KEY=another_backup_key_here
```

Princeton faculty obtain API keys through OIT. Each faculty member registers independently.

Verify configuration (no API call made):

```bash
python main.py --show-config
```

### 3. Set up the model catalog

The catalog (`src/model_catalog.json`) is not tracked by git. Copy the template:

```bash
cp src/model_catalog.template.json src/model_catalog.json
```

For OpenAI or Google models, pricing is fetched automatically on first use — no manual edits needed. For all other providers, add entries directly to `src/model_catalog.json` following the template schema.

### 4. Install plugins

The `prompt` plugin is bundled in this repo and loads automatically. For translation and transcription, clone their repos into `plugins/`:

```bash
git clone <translation-repo-url> plugins/translation
git clone <transcription-repo-url> plugins/transcription
```

Each plugin repo has its own README with command-specific setup and usage examples.

### 5. Verify everything works

```bash
python main.py --help          # lists all loaded commands
python main.py --list-models   # shows catalog models with pricing
```

---

## Usage

### Global commands (no professor required)

```bash
python main.py --help
python main.py --show-config
python main.py --list-models
```

### Usage reporting

```bash
python main.py heller usage report              # current month + budget status
python main.py heller usage report --all-time   # above + all-time totals
python main.py heller usage report 2025-07      # a specific archived month
python main.py heller usage months              # list all archived month files
python main.py heller usage daily               # today's usage
python main.py heller usage daily 2026-03-01    # specific date
```

### Prompt (built-in plugin)

Send a freeform prompt to the AI without any translation or OCR framing. Text is entered interactively and terminated with `---` on its own line.

```bash
python main.py heller prompt                    # user prompt only
python main.py heller prompt -s                 # system prompt first, then user prompt
python main.py heller prompt -o response.txt    # save response to file
python main.py heller prompt -m gpt-4o-mini     # specific model
python main.py heller prompt -s --dry-run       # preview without API call
```

### Translation and transcription

See the README in each plugin repo for full command examples, flag references, language codes, and input/output format details.

---

## Token Usage Tracking

Each professor has isolated, month-scoped tracking:

- **Active file**: `data/token_usage_{name}.json` — current calendar month only
- **Archives**: `data/archives/{name}/{YYYY-MM}.json` — one file per past month; written automatically on the first use of a new month
- All totals in each file cover that month only — no file grows indefinitely
- Monthly budget limits and warning threshold are configurable in `settings.toml` and `src/model_catalog.json`
- All-time totals are computed on demand by aggregating the active file with all archives (`usage report --all-time`)

---

## Model Catalog

`src/model_catalog.json` is the local pricing and capabilities registry (git-ignored; each installation has its own copy).

**Adding models:**
- **OpenAI or Google**: Use `openai/model-name` or `google/model-name` with `-m` on the first invocation — pricing is fetched automatically from [PortKey](https://api.portkey.ai) and saved.
- **All other providers**: Edit `src/model_catalog.json` directly. Minimal entry format:

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

Prices are per 1,000,000 tokens (default `pricing_unit`). Set `"supports_vision": true` for vision-capable models.

---

## Runtime Settings

`settings.toml` at the repo root controls defaults without touching code:

| Section | Key | Default | Effect |
|---------|-----|---------|--------|
| `[prompt]` | `temperature` | `0.7` | Sampling temperature for prompt command |
| `[retry]` | `max_retries` | `10` | Max retry attempts on transient errors |
| `[retry]` | `base_retry_delay` | `3.0` | Base seconds for exponential backoff |
| `[processing]` | `default_parallel_workers` | `1` | Default `-w` value (1 = sequential) |
| `[output]` | `default_font_size` | `9` | Body font size (pt) for PDF/Word output |
| `[budget]` | `warning_threshold_pct` | `80` | Warn when spend exceeds this % of monthly limit |

---

## Writing a New Plugin

1. Copy the template:
   ```bash
   mkdir plugins/myplugin
   cp plugin.py.template plugins/myplugin/plugin.py
   ```
2. Edit `plugin.py`: rename `MyPlugin`, set `commands`, implement `register_subparsers` and `run`.
3. Inside `run()`, create `TokenTracker(professor=professor)` and pass it to every service call.
4. No changes to `src/` are needed — the plugin is discovered automatically at startup.

See `plugins/prompt/plugin.py` for the canonical working example.
