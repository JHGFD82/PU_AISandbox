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
  → src/processors/       document ingestion (PDF, DOCX, TXT, MD, JSON, XLSX, image)
  → src/tracking/         per-professor token accounting + pricing (model_catalog.json)
  → src/output/           text / Markdown / PDF / Word / Excel / JSON output
  → plugins/
      prompt/             bundled plugin (ships with this repo)
      translation/        bundled plugin — base English translation (ships with this repo)
      transcription/      bundled plugin — base English OCR (ships with this repo)
      translation-ea/     optional: separate git repo — EA language translation
      transcription-ea/   optional: separate git repo — EA language OCR
```

`src/cli.py` decides *what* to run. Plugins decide *how* to run it. Only the `usage` subcommand is built in.

For deeper documentation, see the `docs/` folder:

- [`docs/architecture.md`](docs/architecture.md) — request lifecycle, component descriptions, data-flow diagrams
- [`docs/cli-reference.md`](docs/cli-reference.md) — full flag reference for all commands
- [`docs/configuration.md`](docs/configuration.md) — `.settings`, `model_catalog.json`, `settings.default.toml` template and schema reference
- [`docs/token-usage-guide.md`](docs/token-usage-guide.md) — token tracking, usage commands, budget settings, and troubleshooting
- [`docs/plugin-authoring-guide.md`](docs/plugin-authoring-guide.md) — step-by-step guide to writing new plugins

---

## First-Run Setup

### 1. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure professors

Copy the template, then add your first professor with the built-in `env` command (it prompts for the name and keys, keys hidden at entry):

```bash
cp .settings.template .settings
python main.py env add-professor
```

Or hand-edit `.settings` directly — add one table per professor:

```toml
[professors.heller]
name = "Heller"
key = "your_primary_api_key_here"
backup_key = "your_backup_api_key_here"

[professors.smith]
name = "Smith"
key = "another_primary_key_here"
backup_key = "another_backup_key_here"
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

For OpenAI or Google models, pricing is fetched automatically on first use — no manual edits needed. For all other providers, add entries directly to `src/model_catalog.json` following the template schema. See [`docs/configuration.md`](docs/configuration.md) for the full schema.

### 4. Install optional plugins

The `prompt`, `translation`, and `transcription` plugins are bundled in this repo and load automatically.

For East Asian language support (Japanese, Chinese, Korean), clone the EA extension plugins:

```bash
git clone https://github.com/JHGFD82/PU_AISandbox_Translation_EA plugins/translation-ea
git clone https://github.com/JHGFD82/PU_AISandbox_Transcription_EA plugins/transcription-ea
```

Each EA plugin repo has its own README with command-specific setup and usage examples.

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

**Input formats:** `.pdf`, `.docx`, `.txt`, `.md`, `.json`, `.xlsx`/`.xls`, and image files (`.png`, `.jpg`, etc.)  
**Output formats:** `.txt`, `.md`, `.pdf`, `.docx`, `.xlsx` (Excel), `.json` — inferred from the `-o` extension.

```bash
python main.py heller translate jp-en -i paper.pdf -o output.pdf          # PDF to PDF
python main.py heller translate zh-en -i article.docx -o translated.docx  # DOCX to DOCX
python main.py heller translate jp-en -i data.json -o result.md           # JSON in, Markdown out
python main.py heller translate jp-en -i data.xlsx -o result.xlsx         # Excel in, Excel out
python main.py heller translate jp-en -i paper.pdf -p "1-10"              # page range
python main.py heller translate jp-en -i paper.pdf --scanned              # scanned PDF
python main.py heller translate jp-en -c                                  # paste text

python main.py heller transcribe en -i scan.png -o result.txt             # single image
python main.py heller transcribe en -i scans/                             # folder of images
```

To use an alternate AI endpoint (HPC cluster or third-party provider), use colon syntax with a key from an `[endpoints.<name>]` table (defined in `settings.default.toml`, a shared file, or `settings.local.toml`; credentialed via `.settings`):

```bash
python main.py heller translate jp-en -i paper.pdf -m my_cluster:llama-3-70b
python main.py heller prompt -m cloud_provider:some-model
```

See [`docs/cli-reference.md`](docs/cli-reference.md) for the full flag reference and [`docs/configuration.md`](docs/configuration.md) for endpoint setup.

---

## Token Usage Tracking

Each professor has isolated, month-scoped tracking:

- **Active file**: `data/token_usage_{name}.json` — current calendar month only
- **Archives**: `data/archives/{name}/{YYYY-MM}.json` — one file per past month; written automatically on the first use of a new month
- All totals in each file cover that month only — no file grows indefinitely
- Monthly budget limits and warning threshold are configurable in `settings.default.toml` and `src/model_catalog.json`
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

`settings.default.toml` at the repo root controls defaults without touching code. Create `settings.local.toml` (git-ignored) to override individual keys for your machine only; one person can also share a settings file (pointed to via `.settings`'s `shared_settings.path`) that sits between the two. See [`docs/configuration.md`](docs/configuration.md#local-overrides) for how the three layers merge.

| Section | Key | Default | Effect |
|---------|-----|---------|--------|
| `[prompt]` | `temperature` | `0.7` | Sampling temperature for prompt command |
| `[prompt]` | `top_p` | `1.0` | Nucleus sampling top-p for prompt command |
| `[prompt]` | `max_tokens` | `4000` | Max response tokens for prompt command |
| `[prompt]` | `default_system_prompt` | `"You are a helpful assistant."` | System prompt used when `-s` is not passed |
| `[retry]` | `page_delay_seconds` | `3.0` | Pause between pages in sequential mode |
| `[retry]` | `max_retries` | `10` | Max retry attempts on transient errors |
| `[retry]` | `base_retry_delay` | `3.0` | Base seconds for exponential backoff (`delay = base × 2^attempt`) |
| `[processing]` | `default_parallel_workers` | `1` | Default `-w` value (1 = sequential) |
| `[processing]` | `default_ocr_passes` | `1` | Default `-P` value for EA OCR; > 1 enables multi-pass refinement |
| `[processing]` | `default_page_size` | `2000` | Target characters per page when splitting DOCX/TXT |
| `[processing]` | `max_parallel_workers` | `50` | Hard cap on concurrent workers |
| `[output]` | `default_font_size` | `9` | Body font size (pt) for PDF/Word output |
| `[budget]` | `warning_threshold_pct` | `80` | Warn when spend exceeds this % of monthly limit |

See [`docs/configuration.md`](docs/configuration.md) for plugin-level settings (`[translation]`, `[ocr]`, etc.).

---

## Writing a New Plugin

1. Copy the template:
   ```bash
   mkdir plugins/myplugin
   cp plugin.py.template plugins/myplugin/plugin.py
   ```
2. Edit `plugin.py`: rename `MyPlugin`, set `commands`, implement `register_subparsers` and `run`.
3. Inside `run()`, pass `professor` to services that need it — they handle token tracking internally.
4. No changes to `src/` are needed — the plugin is discovered automatically at startup.

See `plugins/prompt/plugin.py` for the canonical working example.
