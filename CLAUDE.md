# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PU AI Sandbox is a modular CLI platform for Princeton University faculty (primarily non-CS digital humanists) providing per-professor API key management, token/budget tracking, and AI-powered document translation/transcription. Commands are implemented as plugins so capabilities can be added without touching core code.

## Commands

### Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.template .env                          # add PROF_N_NAME/KEY/BACKUP_KEY blocks
cp src/model_catalog.template.json src/model_catalog.json   # git-ignored, per-installation
```

### Running
```bash
python main.py --help
python main.py --show-config     # validates professor config, no API calls
python main.py --list-models
python main.py heller usage report --all-time
python main.py heller prompt --dry-run -s
```

### Testing
```bash
pytest                                          # whole suite (see testpaths in pytest.ini)
pytest -m "not live"                            # skip tests that hit the real PortKey pricing endpoint
pytest tests/test_token_tracker.py -k TestConcurrentRecordUsage   # single test/class
PYTHONPATH=$(pwd) pytest --cov=src/ --cov-branch --cov-report=xml # matches CI (upload-coverage.yml)
```
`pytest.ini` sets `testpaths` to `tests` plus `plugins/{translation,prompt,transcription}/tests` and `pythonpath = .`. `translation-ea`/`transcription-ea` are separate git-ignored repos with their own `pytest.ini` — run those from inside their own directory.

### Lint / type-check
```bash
ruff check .
pyright     # pyrightconfig.json ignores tests/ and plugins/{translation,transcription}/src/
```

## Architecture

Core is deliberately thin — it discovers plugins, builds an argument parser, and routes commands. All translation/transcription/prompt logic lives in `plugins/`.

```
main.py → src/cli.py::main()
             ├─ load_plugins()                    src/runtime/plugin_loader.py — scans plugins/*/plugin.py
             ├─ create_argument_parser()           calls each plugin's register_subparsers()
             └─ _plugins[args.command].run(...)    dispatches to owning plugin
                    └─ plugin builds SandboxProcessor (src/runtime/sandbox_processor.py)
                           ├─ resolves API key, creates TokenTracker
                           ├─ lazily loads plugin-owned services via __getattr__
                           └─ service extends BaseService → PortKey API call → TokenTracker.record_usage()
```

Only the `usage` subcommand is built in; everything else (`translate`, `transcribe`, `transcription_review`, `prompt`) is a plugin.

### Plugin system
- A plugin is a directory `plugins/<name>/plugin.py` exposing a module-level `plugin` object satisfying the `ModePlugin` protocol (`src/runtime/plugin.py`, structural typing — no base class needed): `commands: list[str]`, `register_subparsers(subparsers)`, `run(args, professor, model, temperature, top_p, max_tokens)`.
- `plugins/prompt/` is the canonical reference/template — copy it to start a new plugin.
- `plugins/translation/` and `plugins/transcription/` are bundled (tracked in this repo, base English only). `plugins/translation-ea/` and `plugins/transcription-ea/` are optional East Asian-language extensions living in **separate git repos** (git-ignored here) — treat them as reference only, extending the matching bundled plugin's patterns rather than editing in place.
- If two plugins claim the same command and both declare a `handles` list, `src/runtime/dispatch_plugin.py` builds a `DispatchPlugin` that routes by `args.language_code[0]` to the owning plugin (e.g. `translate` shared between `translation` and `translation-ea`), instead of raising a conflict.
- **Plugins own their service modules.** Because `src/services/` no longer ships translation/transcription logic, a plugin must inject its service file into `sys.modules` at import time (see the `_register()` pattern in `docs/architecture.md`) so `SandboxProcessor.__getattr__` can find and lazily instantiate it — e.g. attribute `translation_service` maps to `sys.modules["src.services.translation_service"].TranslationService`. This means adding a plugin never requires changing `src/`.
- **Plugins own their command-orchestration methods too** (e.g. `translate_document`, `process_image`) via the same convention under a `"src.runtime.<name>"` key instead of `"src.services.<name>"`. The registered module exports a class named `Mixin`; `SandboxProcessor`'s class statement discovers every such `Mixin` in `sys.modules` and includes it as a base class at import time (see `_discover_plugin_mixins()` in `src/runtime/sandbox_processor.py`, and "The same convention for orchestration methods" in `docs/architecture.md`). Only `_FileTypeMixin` (file-type detection, needed by every mode) and `_CommandMixin` (interactive helpers) are core and always present.
- Plugins call `register_language(code, name)` (`src/config.py`) at import time to populate `LANGUAGE_MAP`, which argparse type-hooks validate against — this is why plugins must be loaded before the parser is built (the loader guarantees ordering).

### Core layers (`src/`)
- `src/cli.py` — controller/parser; routes commands; exports `add_common_flags()`/`add_notes_flags()` for plugins.
- `src/config.py` — language registry, `load_professor_config()`, `get_api_key()` (primary key with backup-key fallback).
- `src/services/` — `BaseService` (all AI services extend this): PortKey client init, `_create_completion()` (handles `max_tokens` vs `max_completion_tokens` for reasoning models), `_run_with_retry()` (exponential backoff, transient/content-filter retry), `_record_response_usage()`.
- `src/processors/` — converts source files to lists of text pages: `PdfProcessor` (CJK LAParams; `--scanned` routes through vision), `DocxProcessor`, `TxtProcessor` (splits by `default_page_size`), `MarkdownProcessor`, `JsonProcessor` (recursively flattened), `ExcelProcessor` (requires `openpyxl`), `ImageProcessor` (base64 + blank-page detection).
- `src/output/` — writes results based on the `-o` extension (`.txt/.md/.pdf/.docx/.xlsx/.json`); Markdown tables become real tables in PDF/DOCX/XLSX; unsupported extensions and rich-format failures silently fall back to `.txt`.
- `src/tracking/token_tracker.py` — per-professor, per-calendar-month token accounting, thread-safe (`threading.Lock` around `record_usage()`).

### Professor configuration
`.env` pattern: `PROF_[ID]_NAME`, `PROF_[ID]_KEY`, `PROF_[ID]_BACKUP_KEY`. Missing/blank required vars → `ValueError` re-raised as `CLIError`, process exits 1. Names are passed through `make_safe_filename()` for token-usage filenames, CLI arg validation, and error messages.

### Token tracking
- Active: `data/token_usage_{safe_name}.json` (current month only). Archives: `data/archives/{safe_name}/{YYYY-MM}.json`, written automatically on month rollover.
- `usage report --all-time` aggregates the active file + all archives on demand (not loaded eagerly).
- Exceeding the monthly budget only logs warnings at thresholds (e.g. 80%, 100%) — it never halts processing; the only way to stop usage is revoking the professor's API key externally.

### Model catalog & alternate endpoints
- `src/model_catalog.json` (git-ignored; copy from `.template.json`) holds pricing/`supports_vision` per model, keyed by `[config.provider_map]` for provider slug quirks (e.g. `google` → `vertex-ai` for PortKey).
- `openai/model-name` or `google/model-name` passed to `-m` auto-fetches and saves pricing from PortKey on first use. Other providers must be added to the JSON by hand — there is no CLI catalog management.
- Colon syntax in `-m` (e.g. `-m my_cluster:llama-3-70b`) looks up `apis.json` and points the OpenAI-compatible client at that alternate `base_url`, bypassing the model catalog entirely.

### Configuration layering (highest precedence last)
`settings.toml` (repo root defaults) → `settings.local.toml` (git-ignored machine overrides) → `plugins/*/settings.toml` (plugin-specific defaults, each plugin's `src/settings.py` walks up to find its own) → CLI flags.

## Documentation & docstring standard

**End users are Princeton faculty from non-CS disciplines** — they understand the tool's purpose but not programming terminology. This governs every docstring and doc change:

- Open docstrings with one plain-English sentence on *what* the function does, not its mechanism. Explain each parameter and return value in terms the caller cares about, not just type. Define technical terms inline (tokens, API key, JSON) with a plain-English analogy where useful (e.g. tokens ≈ words; a lock ≈ a turn-taking mechanism preventing simultaneous writes).
- Avoid unexplained jargon: "serialize," "instantiate," "iterate," "callback," "mutex," "idempotent," etc.
- In READMEs/`docs/`, lead each section with what the user *accomplishes*; use numbered steps; show realistic example commands, not abstract flag lists; explain likely causes and next steps for documented error messages.
- See `.github/copilot-instructions.md` for a full worked docstring example — follow that style for new/meaningfully-modified functions.

## Git commit workflow

- Follow the `.gitmessage` template at repo root: subject `<type>(<scope>): <short summary>` (imperative, ≤72 chars; types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`), body sections `Why:` / `What changed:` / `Notes:`.
- After any code change, propose a commit message in this format.
- **Do not run `git commit` or `git add` — the user handles all commits themselves. However, if the user tells you otherwise, follow their instructions.**

## Further reading

- [`docs/architecture.md`](docs/architecture.md) — full request lifecycle diagram, plugin `sys.modules` injection pattern, data-flow example
- [`docs/cli-reference.md`](docs/cli-reference.md) — full flag reference
- [`docs/configuration.md`](docs/configuration.md) — `.env`/`model_catalog.json`/`settings.toml` schema
- [`docs/token-usage-guide.md`](docs/token-usage-guide.md) — token tracking and budget troubleshooting
- [`docs/plugin-authoring-guide.md`](docs/plugin-authoring-guide.md) — step-by-step new-plugin guide
