# Configuration & Templates

Four files control how PU_AISandbox is configured. Two are git-ignored (you create them from templates); two are tracked.

| File | Tracked | Template | Purpose |
|------|---------|----------|---------|
| `.env` | ❌ git-ignored | `.env.template` | Professor names and API keys |
| `src/model_catalog.json` | ❌ git-ignored | `src/model_catalog.template.json` | Model pricing and capabilities |
| `settings.toml` | ✅ tracked | — | Runtime defaults (edit freely) |
| `apis.json` | ✅ tracked | — | Alternate AI endpoint connections (add your own) |

---

## `.env` — Professor and API Key Configuration

### Setup

```bash
cp .env.template .env
```

Then edit `.env` to add your professors.

### Format

Each professor is defined by three environment variables with a shared numeric ID:

```env
PROF_1_NAME=Heller
PROF_1_KEY=sk-...primary_key...
PROF_1_BACKUP_KEY=sk-...backup_key...

PROF_2_NAME=Smith
PROF_2_KEY=sk-...primary_key...
PROF_2_BACKUP_KEY=sk-...backup_key...
```

- `PROF_N_NAME` — display name and CLI identifier. The CLI accepts a lowercased, safe-filename form: `Heller` → `heller`, `Van Dyke` → `van_dyke`.
- `PROF_N_KEY` — primary PortKey API key (obtained through Princeton OIT).
- `PROF_N_BACKUP_KEY` — fallback if the primary key fails. The application prints a warning when the backup is used.

The ID (`1`, `2`, …) is only used to group the three variables together. IDs do not need to be sequential, but they must be consistent within a group.

### How Professor Names Work

`get_api_key(professor)` in `src/config.py`:
1. Computes a safe filename from `PROF_N_NAME` (lowercase, non-word chars → `_`)
2. Matches the CLI argument against both the safe name and the original display name (case-insensitive)
3. Returns the primary key, or the backup key with a printed warning

```bash
# These are all equivalent given PROF_1_NAME=Heller
python main.py heller usage report
python main.py Heller usage report
```

### Verifying the Configuration

```bash
python main.py --show-config
```

This prints all configured professors, their data-file paths, and whether those files exist. No API call is made.

---

## `src/model_catalog.json` — Model Pricing and Capabilities

### Setup

```bash
cp src/model_catalog.template.json src/model_catalog.json
```

This file is git-ignored so each installation can maintain its own model list and pricing without conflicting with other users.

### Schema

```json
{
  "config": {
    "pricing_unit": 1000000,
    "monthly_limit": 250.0,
    "defaults": {
      "translation": "gpt-4o",
      "ocr": "gpt-4o",
      "image_translation": "gpt-5"
    },
    "provider_map": {
      "google": "vertex-ai",
      "mistral": "mistral-ai"
    }
  },
  "models": {
    "gpt-4o": {
      "input": 2.5,
      "output": 10.0,
      "supports_vision": true,
      "portkey_id": "openai/gpt-4o",
      "last_sync": "2026-05-11T13:33:46"
    }
  }
}
```

#### `config` section

| Key | Type | Description |
|-----|------|-------------|
| `pricing_unit` | int | Token denominator for prices — `1000000` means prices are per 1M tokens |
| `monthly_limit` | float | Monthly spending limit in USD shown in usage reports |
| `defaults.translation` | string | Default model for `translate` command |
| `defaults.ocr` | string | Default model for `transcribe` command |
| `defaults.image_translation` | string | Default model for image translation |
| `provider_map` | object | Maps provider shorthand to PortKey virtual key names |

#### `models` entries

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `input` | float | ✅ | Input token price per `pricing_unit` tokens |
| `output` | float | ✅ | Output token price per `pricing_unit` tokens |
| `supports_vision` | bool | — | Set `true` for vision-capable models (default: `false`) |
| `portkey_id` | string | — | PortKey routing identifier (e.g. `"openai/gpt-4o"`) |
| `last_sync` | string | — | ISO timestamp set automatically after a pricing sync |
| `uses_max_completion_tokens` | bool | — | Set `true` for reasoning/o-series models that use `max_completion_tokens` instead of `max_tokens` |
| `fixed_parameters` | bool | — | Set `true` for models that reject `temperature` and `top_p` (e.g. `o1`) |
| `max_completion_tokens` | int | — | Per-model token cap override |
| `system_role` | string | — | Override the system message role (defaults to `"system"`) |

### Adding Models

**OpenAI or Google** — use `provider/model-name` with `-m` on any command. Pricing is fetched from PortKey and saved automatically:

```bash
python main.py heller prompt -m openai/gpt-4o-new
python main.py heller prompt -m google/gemini-2.5-pro
```

**All other providers** — add an entry manually:

```json
"mistral-small": {
  "input": 0.1,
  "output": 0.3,
  "supports_vision": false
}
```

### Viewing the Catalog

```bash
python main.py --list-models
```

Prints all models with their pricing, vision support, and any special flags.

---

## `settings.toml` — Runtime Defaults

This file is tracked by git. Edit it freely — changes take effect on the next run without touching any Python code.

```toml
[prompt]
temperature = 0.7
top_p = 1.0
max_tokens = 4000
default_system_prompt = "You are a helpful assistant."

[retry]
page_delay_seconds = 3.0
max_retries = 10
base_retry_delay = 3.0

[processing]
default_parallel_workers = 1
default_ocr_passes = 1
default_page_size = 2000
max_parallel_workers = 50

[output]
default_font_size = 9

[budget]
warning_threshold_pct = 80
```

### Reference

#### `[prompt]`

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.7` | Sampling temperature for the `prompt` command |
| `top_p` | `1.0` | Nucleus sampling top-p for the `prompt` command |
| `max_tokens` | `4000` | Maximum response tokens for the `prompt` command |
| `default_system_prompt` | `"You are a helpful assistant."` | System prompt used when `-s` is not passed |

#### `[retry]`

| Key | Default | Effect |
|-----|---------|--------|
| `page_delay_seconds` | `3.0` | Pause between pages in sequential processing mode |
| `max_retries` | `10` | Maximum retry attempts on transient errors or content filter hits |
| `base_retry_delay` | `3.0` | Base seconds for exponential backoff (`delay = base × 2^attempt`) |

#### `[processing]`

| Key | Default | Effect |
|-----|---------|--------|
| `default_parallel_workers` | `1` | Default `-w` value; `1` = sequential (safe default) |
| `default_ocr_passes` | `1` | Default `-P` value; values > 1 enable multi-pass OCR refinement |
| `default_page_size` | `2000` | Target characters per logical page when splitting DOCX/TXT files |
| `max_parallel_workers` | `50` | Hard cap on concurrent workers to avoid OS file-descriptor exhaustion |

#### `[output]`

| Key | Default | Effect |
|-----|---------|--------|
| `default_font_size` | `9` | Body font size (points) for PDF and Word output |

#### `[budget]`

| Key | Default | Effect |
|-----|---------|--------|
| `warning_threshold_pct` | `80` | Print a budget warning when monthly spend exceeds this percentage of `monthly_limit` |

CLI flags (`-t`, `-T`, `-M`, `-w`, etc.) always override these defaults for the current run only.

### Local overrides

Create `settings.local.toml` at the repository root to override individual keys without modifying the tracked `settings.toml`. Only the keys you specify are overridden — everything else falls back to `settings.toml`. The file is git-ignored (add it to `.gitignore` if not already present).

```toml
# settings.local.toml — machine-specific overrides (not committed)
[processing]
default_parallel_workers = 4
```

---

## `apis.json` — Alternate AI Endpoint Connections

`apis.json` lives at the repository root and is tracked by git. It lists any AI
endpoints you want to reach in addition to (or instead of) the built-in service.

### Why use this?

Use `apis.json` when you need to call an AI endpoint that isn't the built-in
service — for example:

- A model running on an **HPC cluster** or other self-hosted inference server
- An **AI service provider's direct API** (many providers expose an
  OpenAI-compatible REST interface you can reach with just a URL and an API key)

### File format

```json
{
  "_doc": "Human-readable description (ignored by the loader)",
  "_examples": { "...copy an entry here to try it..." },
  "default": null,
  "endpoints": {
    "my_cluster": {
      "name": "My HPC Cluster",
      "base_url": "http://my-cluster.internal:8000/v1",
      "openai_compatible": true,
      "default_model": "llama-3-70b-instruct",
      "timeout": 30,
      "verify_ssl": false
    }
  }
}
```

Keys starting with `_` are documentation only — the loader ignores them.

### Endpoint fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | No | key name | Human-readable label shown in logs |
| `base_url` | **Yes** | — | Root URL of the API (e.g. `http://cluster:8000/v1`) |
| `openai_compatible` | No | `false` | When `true`, uses the OpenAI SDK with `base_url` for LLM calls |
| `default_model` | No | `null` | Model used when none is specified in the colon syntax |
| `timeout` | No | `30` | Request timeout in seconds |
| `verify_ssl` | No | `true` | Whether to verify SSL certificates (set `false` for internal clusters) |

### API keys

Each endpoint's API key is read from an environment variable:

```
API_<UPPERCASE_ENDPOINT_KEY>_KEY
```

Examples:
- Endpoint key `my_cluster` → `API_MY_CLUSTER_KEY`
- Endpoint key `cloud-provider` → `API_CLOUD_PROVIDER_KEY`

Add these to your `.env` file (see `.env.template` for the pattern).

### Setting a default endpoint

Set `"default"` to an endpoint key to route **all** bare model strings there
instead of the built-in service:

```json
{
  "default": "my_cluster",
  "endpoints": { "my_cluster": { ... } }
}
```

When `"default"` is `null` (the default), bare model strings are handled by the
built-in service as before.

### Built-in examples

The `_examples` section in `apis.json` shows two ready-to-use patterns:

- **`hpc_cluster`** — OpenAI-compatible endpoint on an on-premises or HPC cluster
- **`cloud_provider`** — Direct connection to a commercial AI service provider
  that exposes an OpenAI-compatible REST API

Copy the relevant example into `"endpoints"` and fill in your URL.

### Using a configured endpoint

Once an endpoint is in `"endpoints"`, use it on the command line with colon syntax:

```bash
python main.py heller prompt -m my_cluster:llama-3-70b
```

See [CLI Reference → Specifying Models](cli-reference.md#specifying-models) for
the full syntax.

---

## Plugin Settings

Each bundled plugin ships its own `settings.toml` in its plugin directory. The plugin's `src/settings.py` locates it by walking up from `__file__`. These files are tracked alongside their plugin (or, if the plugin is in a separate repo, tracked there).

Plugin settings are isolated from the root `settings.toml` — they have no overlap in section names. See [`plugin-authoring-guide.md`](plugin-authoring-guide.md) for how to add settings to a new plugin.

### `translation` plugin — `plugins/translation/settings.toml`

Also used by `translation-ea` (which ships an identical file).

#### `[translation]`

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.5` | Sampling temperature for text-based translation |
| `top_p` | `0.5` | Nucleus sampling top-p for text-based translation |
| `max_tokens` | `4000` | Maximum response tokens per page |
| `context_percentage` | `0.65` | Fraction of the previous page passed as rolling context (0.0–1.0) |

#### `[image_translation]`

Used when `--scanned` or an image file is the input.

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.3` | Slightly creative to handle ambiguous characters from context |
| `max_tokens` | `8000` | Higher budget: output includes both transcript and translation |

### `transcription` plugin — `plugins/transcription/settings.toml`

Also used by `transcription-ea`.

#### `[ocr]`

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.0` | Fully deterministic — minimises hallucination |
| `top_p` | `0.1` | Very low to prevent the model from inventing characters |
| `max_tokens` | `4000` | Maximum response tokens per image |
| `frequency_penalty` | `0.5` | Penalises repeated tokens |
| `presence_penalty` | `0.3` | Encourages output diversity |

#### `[transcription_review]`

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.1` | Low temperature for precise, analytical error detection |
| `top_p` | `0.5` | Focused nucleus sampling |
| `max_tokens` | `4000` | JSON output; increase for very long transcriptions |

---

## Optional Dependencies

Some output and input formats require packages not listed in the core `requirements.txt`. These degrade gracefully if missing.

| Package | Required for | Fallback |
|---------|-------------|---------|
| `openpyxl` | `.xlsx` output (Excel) and `.xlsx`/`.xls` input | Falls back to `.txt` output; input raises an error |

Install as needed:

```bash
pip install openpyxl
```

---

## First-Run Checklist

```bash
# 1. Copy and populate .env
cp .env.template .env
# edit .env — add your professors

# 2. Copy the model catalog
cp src/model_catalog.template.json src/model_catalog.json
# edit src/model_catalog.json if needed (pricing, defaults)

# 3. Verify everything
python main.py --show-config    # checks professor setup (no API call)
python main.py --list-models    # checks model catalog
```
