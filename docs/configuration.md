# Configuration & Templates

Three files control how PU_AISandbox is configured. One is git-ignored (you create it from a template, or the migration script creates it for you); the others are TOML layers, one tracked and two optional/git-ignored.

| File | Tracked | Template | Purpose |
|------|---------|----------|---------|
| `settings.toml` | ❌ git-ignored | `templates/settings.template` | Professor names/keys, endpoint credentials, webui secrets, external usage-data sources — this installation's own private configuration |
| `src/model_catalog.json` | ❌ git-ignored | `templates/model_catalog.template.json` | Model pricing and capabilities |
| `settings.default.toml` | ✅ tracked | — | Runtime defaults (edit freely) and alternate-endpoint *definitions* |
| A shared file (optional, any path) | ❌ not part of this repo | — | Runtime defaults a group wants to share (e.g. via Dropbox) |
| `settings.local.toml` | ❌ git-ignored | — | This machine's personal overrides, highest precedence |

For a quick reference on every one of these — where each can live (this machine only, or a synced/shared external location), whether a personal override exists, and how it can be edited today — see [Settings at a Glance](#settings-at-a-glance) near the end of this document.

---

## `settings.toml` — This Installation's Private Configuration

`settings.toml` is a TOML file at the repository root holding everything that is specific to this one installation and should never be synced or shared: professor names and API keys, the web UI's passphrase hash and session secret, alternate-endpoint credentials, and the list of external usage-data sources this installation reads from. It replaces four things that used to be separate files (`.env`, the credential half of `apis.json`, and `data_sources.json`).

Editing it programmatically (via the `settings` command below) is safe specifically because every edit happens locally, driven by a command typed at this machine's own keyboard — never over a network call, never as part of syncing files between machines. That reasoning does not extend to placing `settings.toml` itself in a synced folder (Dropbox, iCloud, etc.) — never do that.

### Setup

```bash
python main.py settings setup
```

Then either hand-edit `settings.toml`, or use the `settings` command below to add your first professor.

### Format

```toml
[professors.jh43]
name = "Jeff Heller"
key = "sk-...primary_key..."
backup_key = "sk-...backup_key..."   # optional

[professors.as12]
name = "Alice Smith"
key = "sk-...primary_key..."
```

- The table name (`jh43`, `as12`) is the person's **netID** — the university username they sign in with. This is what identifies them everywhere: it selects their API key, names their usage file, and is what you type on the command line.
- `name` — their display name. Shown in reports and in the web interface's person picker, and nowhere else, so write it however reads best.
- `key` — primary PortKey API key (obtained through Princeton OIT).
- `backup_key` — fallback if the primary key fails. The application prints a warning when the backup is used.

### Why netIDs

A netID is letters and digits only, which means it can be used as a filename exactly as typed. That is the whole reason it is the identifier.

The sandbox used to identify people by their display name, made filename-safe. Two parts of the code did that in two different ways — `Jeff Heller` became `jeff heller` in one place and `jeff_heller` in another — so one person's spending could be recorded as two people's, and an aggregate report would show them twice. There is nothing to make safe about `jh43`, so there is nothing for two parts of the code to disagree about.

Capitalisation doesn't matter when you type it: `JH43` and `jh43` are the same person. Anything that isn't a letter or a digit is rejected with an error, since the usual cause is a display name typed where a netID was wanted.

```bash
python main.py jh43 usage report
python main.py JH43 usage report     # same person
python main.py "Jeff Heller" usage report   # error: that's a display name
```

### Verifying the Configuration

```bash
python main.py --show-config
```

This prints all configured professors, their data-file paths, whether those files exist, and every optional setting registered below (and whether each is currently set — never the value itself).

### Editing `settings.toml` from the command line

Rather than hand-editing `settings.toml`, the built-in `settings` command can add/remove professors and set any dotted-path value directly. Unlike every other command, `settings` never needs a netID first (you need it precisely when nobody is configured yet):

```bash
python main.py settings add-professor            # prompts for netID, display name + keys (keys hidden, never a flag)
python main.py settings remove-professor jh43    # asks to confirm before deleting
python main.py settings list                     # same optional-settings list as --show-config
python main.py settings set webui.session_secret             # prompts for a value (hidden, since it's a secret)
python main.py settings set webui.session_secret --generate   # or auto-generate a random one
python main.py settings unset webui.session_secret
```

Secrets are always entered at a hidden prompt — never accepted as a command-line flag — so they can't end up in shell history or be seen by another process listing running commands.

### Optional `settings.toml` values

Beyond professor keys, a plugin can declare its own optional dotted-path setting (via `register_setting()` in `src/config.py`, the same mechanism a plugin uses to add a language) so it shows up automatically in `--show-config` and `settings list`. Currently registered:

| Dotted path | Set by | Purpose |
|----------|--------|---------|
| `webui.passphrase_hash` | `python main.py webui set-passphrase` (writes the hash directly to `settings.toml`) | Unlock-gate passphrase for the web UI |
| `webui.session_secret` | `settings set webui.session_secret` (or `--generate`) | Keeps browser sessions signed in across server restarts |
| `shared_settings.path` | `settings set shared_settings.path` | Path to a shared settings file — see [Local overrides](#local-overrides) |
| `endpoints.<name>.key` (one per `[endpoints.<name>]` table in `settings.*.toml`) | `settings set endpoints.my_cluster.key` | API key for an alternate endpoint — see [Endpoint definitions](#endpoint-definitions-alternate-ai-api-connections) |

All of these are optional — leaving them unset falls back to documented default behavior (no unlock gate, a fresh session secret each restart, no shared settings, no alternate endpoints).

---

## `src/model_catalog.json` — Model Pricing and Capabilities

### Setup

```bash
python main.py settings setup
```

This file is git-ignored so each installation can maintain its own model list and pricing without conflicting with other users. It's kept as its own file rather than folded into `settings.toml` for a practical reason: the package updates it on its own (auto-registering pricing the first time you use `-m provider/model-name`), and that kind of frequent, automatic write is exactly the kind of thing that causes conflicts if it ever ends up in a synced or shared file. Runtime settings and shared defaults change rarely enough that sharing them is safe; model pricing can change every time someone tries a new model.

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
python main.py jh43 prompt -m openai/gpt-4o-new
python main.py jh43 prompt -m google/gemini-2.5-pro
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

## `settings.default.toml` — Runtime Defaults and Endpoint Definitions

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
retry_delay_seconds = 5.0

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
| `retry_delay_seconds` | `5.0` | Seconds to wait between retries (the same every time) |

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

Settings merge from up to three layers, each optional and each overriding only the keys it mentions:

1. **`settings.default.toml`** (tracked) — the repo's defaults, same for everyone.
2. **A shared file** (optional, any path) — set `shared_settings.path` in `settings.toml` to the path of a `settings.default.toml`-format file, e.g. one synced across a group with Dropbox. Lets a group share defaults (a cluster's worker count, a group-wide font size, even a shared alternate-endpoint definition) without anyone hand-editing their own copy. Nothing changes unless this value is set.
3. **`settings.local.toml`** (git-ignored, at the repository root) — this machine's personal overrides. Still the last word: even with a shared file in play, a setting placed here wins, so one person can override just their own quirk without touching the file everyone else reads.

```toml
# settings.local.toml — machine-specific overrides (not committed)
[processing]
default_parallel_workers = 4
```

Note this local-override mechanism is specific to the *root* `settings.default.toml` — a plugin's own `settings.toml` (see [Plugin Settings](#plugin-settings) below) has no equivalent personal-override file today; it's edited directly.

### Endpoint definitions (alternate AI API connections)

The `[endpoints.<name>]` tables in `settings.default.toml` (or a shared file, or `settings.local.toml` — they merge through the same three layers as everything else) list any AI endpoints you want to reach in addition to (or instead of) the built-in service.

#### Why use this?

Use an `[endpoints]` table when you need to call an AI endpoint that isn't the built-in service — for example:

- A model running on an **HPC cluster** or other self-hosted inference server
- An **AI service provider's direct API** (many providers expose an OpenAI-compatible REST interface you can reach with just a URL and an API key)

#### Definition format

```toml
[endpoints.my_cluster]
name = "My HPC Cluster"
base_url = "http://my-cluster.internal:8000/v1"
openai_compatible = true
default_model = "llama-3-70b-instruct"
timeout = 30
verify_ssl = false
```

#### Endpoint fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | No | table name | Human-readable label shown in logs |
| `base_url` | **Yes** | — | Root URL of the API (e.g. `http://cluster:8000/v1`) |
| `openai_compatible` | No | `false` | When `true`, uses the OpenAI SDK with `base_url` for LLM calls |
| `default_model` | No | `null` | Model used when none is specified in the colon syntax |
| `timeout` | No | `30` | Request timeout in seconds |
| `verify_ssl` | No | `true` | Whether to verify SSL certificates (set `false` for internal clusters) |

Because `settings.default.toml` is tracked by git, put endpoint *definitions* you're comfortable committing there or in a shared file; anything installation-specific belongs in `settings.local.toml` instead (git-ignored).

#### API keys

Each endpoint's credential is kept separately, in `settings.toml` — never alongside the definition, since credentials are never meant to be shared or layered the way definitions are:

```toml
# settings.toml
[endpoints.my_cluster]
key = "sk-..."
```

Set it with:

```bash
python main.py settings set endpoints.my_cluster.key
```

#### Setting a default endpoint

Set `[config] default_endpoint` to an endpoint name to route **all** bare model strings there instead of the built-in service:

```toml
[config]
default_endpoint = "my_cluster"

[endpoints.my_cluster]
name = "My HPC Cluster"
base_url = "http://my-cluster.internal:8000/v1"
openai_compatible = true
```

When `default_endpoint` is unset (the default), bare model strings are handled by the built-in service as before.

#### Using a configured endpoint

Once an endpoint is defined, use it on the command line with colon syntax:

```bash
python main.py jh43 prompt -m my_cluster:llama-3-70b
```

See [CLI Reference → Specifying Models](cli-reference.md#specifying-models) for the full syntax.

---

## External/Remote Usage-Data Sources

The `[usage_sources]` table and `[[usage_sources.external]]` array in `settings.toml` list this installation's own external/remote usage-data sources. This is git-ignored, per-installation configuration — it's not hand-edited; it's managed entirely through `usage sources` commands.

### Why use this?

Lets one installation of this tool include another installation's usage data when building reports. For example: a professor runs their own copy of this tool and points it at a folder synced with Dropbox; the person who manages several professors' accounts registers that folder as a source on their own installation, so a single report shows everyone's spending without anyone copying files around by hand.

Two modes:
- **`read-only`** (default) — only the other installation ever writes there; this one just reads it.
- **`shared-write`** — both installations record usage there, one-file-per-call, so a dumb file-sync service like Dropbox never sees two conflicting edits to the same file.

### Commands

```bash
python main.py jh43 usage sources list
python main.py jh43 usage sources add --label "Prof. Smith" --path /path/to/shared/data --mode read-only
python main.py jh43 usage sources add --label "This installation" --path /path/to/shared/data --mode shared-write --for-professor heller
python main.py jh43 usage sources remove "Prof. Smith"
```

`add` prompts interactively for anything not passed as a flag. A professor name is still required on the command line for these commands for consistency with every other `usage` subcommand, even though the source configuration itself isn't scoped to that professor — see `src/settings_store.py` and `docs/webui-plugin-plan.md` (§1) for the full design.

---

## Plugin Settings

Each bundled plugin ships its own `settings.toml` in its plugin directory. The plugin's `src/settings.py` locates it by walking up from `__file__`. These files are tracked alongside their plugin (or, if the plugin is in a separate repo, tracked there).

Plugin settings are isolated from the root `settings.default.toml` — they have no overlap in section names. See [`plugin-authoring-guide.md`](plugin-authoring-guide.md) for how to add settings to a new plugin.

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

## Settings at a Glance

Every configuration surface in the project, where it can live, whether a personal override exists on top of it, and how it's edited today.

| Setting | Where it's stored | External/shared location allowed? | Personal override on top? | Edit via CLI | Edit via web UI |
|---|---|---|---|---|---|
| Professor name/keys | `settings.toml`, repo root, git-ignored | No — never sync `settings.toml` itself | N/A (this *is* the per-installation value) | ✅ `settings add-professor` / `settings remove-professor` | ✅ `/settings` page — add, remove, replace primary/backup key |
| Webui passphrase hash | `settings.toml` (`webui.passphrase_hash`) | No | N/A | ✅ `webui set-passphrase` (writes directly to `settings.toml`) | ✅ `/settings` page — hashed server-side, never stored or shown in plaintext |
| Webui session secret | `settings.toml` (`webui.session_secret`) | No | N/A | ✅ `settings set` / `settings set --generate` | ✅ `/settings` page — manual value or "generate" |
| Shared settings pointer | `settings.toml` (`shared_settings.path`) | No (it's just a path) | N/A | ✅ `settings set` / `settings unset` | ✅ `/settings` page |
| Alternate-endpoint API keys | `settings.toml` (`endpoints.<name>.key`) | No | N/A | ✅ `settings set` / `settings unset` | ✅ `/settings` page — one field per endpoint already defined in a settings layer |
| Endpoint definitions (URL, timeout, etc.) | `settings.default.toml` (or shared file, or `settings.local.toml`), repo root | Definitions may live in the tracked file or a shared file | ✅ `settings.local.toml` can override a definition | ❌ hand-edit TOML only | ❌ Read-only on the `/settings` page (shown for reference, with a copyable snippet) — deliberately not editable there; see [Endpoint definitions](#endpoint-definitions-alternate-ai-api-connections) |
| Model pricing/capabilities | `src/model_catalog.json`, git-ignored | Not designed for this — per-installation by convention, deliberately kept separate to avoid concurrent-write conflicts | N/A (whole file is the "local" copy) | Indirectly — using `-m provider/model` auto-registers pricing | Not planned |
| Runtime defaults (temperature, retries, etc.) | `settings.default.toml`, repo root, tracked by git | N/A — this is the shared baseline | ✅ `settings.local.toml` | ❌ hand-edit TOML only | Not planned |
| Shared runtime defaults | Wherever `shared_settings.path` points | **Yes — this is the point** (e.g. a Dropbox-synced `.toml` file) | ✅ `settings.local.toml` still wins over it | Pointer is CLI-editable (`settings set shared_settings.path`); file contents are hand-edited TOML | Pointer only, via `/settings` page (see above); file contents still hand-edited TOML |
| Plugin defaults (e.g. `plugins/webui/settings.toml`) | Inside each plugin's own directory, tracked by git (or in the EA plugin's separate repo) | N/A | **No** — no per-plugin local-override file exists today | ❌ hand-edit TOML only | Not planned |
| External usage-data sources | `settings.toml` (`[usage_sources]`), repo root, git-ignored | **Yes — the whole point** (points at another installation's `data/` folder, e.g. via Dropbox) | N/A (flat list, no layering) | ✅ `usage sources add/list/remove` | ✅ `/settings` page — add/remove sources |

Every "✅ `/settings` page" row above is served by the webui plugin's `/settings` route (`plugins/webui/src/templates/settings.html` and the `/api/settings/*` routes in `plugins/webui/src/app.py`), gated behind the same unlock passphrase as the rest of the web UI. A first-time visitor with no professors configured yet is redirected straight there instead of an empty chat screen; the section order on the page itself flips depending on whether any professor is already configured (professors-first on a genuinely empty installation, shared-settings-first once there's at least one professor, since that's the more likely thing someone returns to tweak).

---

## First-Run Checklist

```bash
# 1. Choose where your own files live, and create them.
#    Creates settings.toml, model_catalog.json and preferences.toml
#    in the folder you pick. Nothing to copy by hand.
python main.py settings setup

# 2. Add whoever will be using it (prompts for netID, name and keys)
python main.py settings add-professor

# 3. Verify everything
python main.py --show-config    # checks who is configured (no API call)
python main.py --list-models    # checks the model catalogue
```

Setup runs on its own the first time you use an un-set-up copy of the sandbox, so you can also just run whatever command you wanted and answer its questions.

## Migrating an Existing Installation

If you have an existing installation with the older `.env` / `apis.json` / `data_sources.json` files, run the one-time migration script instead of starting from scratch:

```bash
python scripts/migrate_config_to_settings.py            # writes settings.toml and settings.local.toml
python scripts/migrate_config_to_settings.py --dry-run   # preview without writing anything
```

It reads your existing `.env`, `apis.json`, and `data_sources.json`, writes the equivalent `settings.toml` and `settings.local.toml` content, and renames the old files to `.bak` (it never deletes anything). Refuses to overwrite an existing `settings.toml` unless `--force` is passed.
