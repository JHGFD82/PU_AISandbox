# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PU AI Sandbox is a modular CLI platform for Princeton University faculty (primarily non-CS digital humanists) providing per-professor API key management, token/budget tracking, and AI-powered document translation/transcription. Commands are implemented as plugins so capabilities can be added without touching core code.

## Commands

### Setup
```bash
python3 start.py    # end-user path: finds a suitable Python, builds .venv, runs setup, opens the web UI
```
`start.py` is deliberately written to parse on Python 3.9 (what macOS ships) so it can find and use a newer one — see its module docstring. For working on the code:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python main.py settings setup          # where to keep your files; creates settings.toml + model_catalog.json + preferences.toml
python main.py settings add-professor  # then add whoever will use it
```

### Running
```bash
python main.py --help
python main.py --show-config     # validates professor config, no API calls
python main.py --list-models
python main.py jh43 usage report --all-time
python main.py jh43 prompt --dry-run -s
```

### Testing
```bash
pytest                                          # whole suite (see testpaths in pytest.ini)
pytest -m "not live"                            # skip tests that hit the real PortKey pricing endpoint
pytest tests/test_token_tracker.py -k TestConcurrentRecordUsage   # single test/class
PYTHONPATH=$(pwd) pytest --cov=src/ --cov-branch --cov-report=xml # matches CI (upload-coverage.yml)
```
`pytest.ini` sets `testpaths` to `tests` plus `plugins/{translation,prompt,transcription,webui}/tests`, and `pythonpath = .`. Discovery is by explicit list, so a new plugin's tests must be added there. `translation-ea`/`transcription-ea` are separate git-ignored repos with their own `pytest.ini` — run those from inside their own directory.

### Lint / type-check
```bash
ruff check .
pyright     # config in pyproject.toml [tool.pyright]; should report 0 errors
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

Only `usage` and `settings` are built in; everything else (`translate`, `transcribe`, `transcription_review`, `prompt`, `webui`) is a plugin.

### Plugin system
- A plugin is a directory `plugins/<name>/plugin.py` exposing a module-level `plugin` object satisfying the `ModePlugin` protocol (`src/runtime/plugin.py`, structural typing — no base class needed): `commands: list[str]`, `register_subparsers(subparsers)`, `run(args, professor, model, temperature, top_p, max_tokens)`.
- `templates/plugin.py.template` is the annotated skeleton to copy; `plugins/prompt/` is the complete working reference. `docs/plugin-authoring-guide.md` is the full walkthrough.
- `plugins/translation/`, `plugins/transcription/` and `plugins/webui/` are bundled (tracked in this repo; translation and transcription cover base English only). `plugins/translation-ea/` and `plugins/transcription-ea/` are optional East Asian-language extensions living in **separate git repos** (git-ignored here) — treat them as reference only, extending the matching bundled plugin's patterns rather than editing in place.
- If two plugins claim the same command and both declare a `handles` list, `src/runtime/dispatch_plugin.py` builds a `DispatchPlugin` that routes by `args.language_code[0]` to the owning plugin (e.g. `translate` shared between `translation` and `translation-ea`), instead of raising a conflict.
- **Plugins own their service modules.** Because `src/services/` no longer ships translation/transcription logic, a plugin must inject its service file into `sys.modules` at import time (see the `_register()` pattern in `docs/architecture.md`) so `SandboxProcessor.__getattr__` can find and lazily instantiate it — e.g. attribute `translation_service` maps to `sys.modules["src.services.translation_service"].TranslationService`. This means adding a plugin never requires changing `src/`.
- **Plugins own their command-orchestration methods too** (e.g. `translate_document`, `process_image`) via the same convention under a `"src.runtime.<name>"` key instead of `"src.services.<name>"`. The registered module exports a class named `Mixin`; `SandboxProcessor`'s class statement discovers every such `Mixin` in `sys.modules` and includes it as a base class at import time (see `_discover_plugin_mixins()` in `src/runtime/sandbox_processor.py`, and "The same convention for orchestration methods" in `docs/architecture.md`). Only `_FileTypeMixin` (file-type detection, needed by every mode) and `_CommandMixin` (interactive helpers) are core and always present.
- A third registration name uses the same mechanism: a module registered as `pu_plugin.<name>.settings` has its constants exposed through `src/settings.py`'s `__getattr__`, so `from src.settings import SOME_CONSTANT` reaches a plugin's own settings without `src/` naming any plugin.
- Plugins call `register_language(code, name)` (`src/config.py`) at import time to populate `LANGUAGE_MAP`, which argparse type-hooks validate against — this is why plugins must be loaded before the parser is built (the loader guarantees ordering).
- `SandboxProcessor` must be imported *inside* a plugin's `run()`, never at module scope: its class statement runs mixin discovery the moment it is first imported.
- Optional plugin members, all read with `getattr()` rather than declared on the protocol: `requires_professor`, `handles`, `register_command_flags`, `get_peer_guidance`, and the web-interface trio `ui_action` / `run_ui_action` / `preview_ui_action` (`src/runtime/ui_action.py`).

### Core layers (`src/`)
- `src/cli.py` — controller/parser; routes commands; exports `add_common_flags()`/`add_notes_flags()` for plugins.
- `src/config.py` — language registry, optional-setting registry, `normalize_netid()`, `load_professor_config()`, `get_api_key()` (primary key with backup-key fallback).
- `src/paths.py` — the only module that answers where this person's own files live; `src/first_run.py` and `src/setup_prompts.py` create them (the browser route in `plugins/webui/src/setup_web.py` goes through the same `first_run.py`).
- `src/services/` — `BaseService` (all AI services extend this): PortKey client init, `_create_completion()` (handles `max_tokens` vs `max_completion_tokens` for reasoning models), `_run_with_retry()` (flat delay between retries, transient/content-filter retry), `_record_response_usage()`.
- `src/processors/` — converts source files to lists of text pages: `PdfProcessor` (CJK LAParams; `--scanned` routes through vision), `DocxProcessor`, `TxtProcessor` (splits by `default_page_size`), `MarkdownProcessor`, `JsonProcessor` (recursively flattened), `ExcelProcessor` (requires `openpyxl`), `ImageProcessor` (base64 + blank-page detection).
- `src/output/` — writes results based on the `-o` extension (`.txt/.md/.pdf/.docx/.xlsx/.json`); Markdown tables become real tables in PDF/DOCX/XLSX; unsupported extensions and rich-format failures silently fall back to `.txt`.
- `src/tracking/token_tracker.py` — per-professor, per-calendar-month token accounting, thread-safe (`threading.Lock` around `record_usage()`).

### Professor configuration
`settings.toml` pattern: `[professors.<netid>]` tables with `name` (display name), `key`, `backup_key` (optional). The table name is the person's university netID — letters and digits only, validated by `normalize_netid()` in `src/config.py`, and used verbatim as a filename. Missing/blank required fields → `ValueError` re-raised as `CLIError`, process exits 1. `settings.toml` also holds `[webui]` secrets, `[endpoints.<name>].key` credentials, `[shared_settings].path`, and `[usage_sources]` — see `src/settings_store.py` and `docs/configuration.md`.

### Token tracking
In the `data/` folder of the person's own files folder — active: `token_usage_{netid}.json` (current month only); archives: `archives/{netid}/{YYYY-MM}.json`, written automatically on month rollover.
- `usage report --all-time` aggregates the active file + all archives on demand (not loaded eagerly).
- Exceeding the monthly budget only logs warnings at thresholds (e.g. 80%, 100%) — it never halts processing; the only way to stop usage is revoking the professor's API key externally.

### Model catalog & alternate endpoints
- `model_catalog.json` (in the person's own files folder; created by setup from `templates/model_catalog.template.json`) holds pricing/`supports_vision` per model, keyed by `[config.provider_map]` for provider slug quirks (e.g. `google` → `vertex-ai` for PortKey).
- `openai/model-name` or `google/model-name` passed to `-m` auto-fetches and saves pricing from PortKey on first use. Other providers must be added to the JSON by hand — there is no CLI catalog management.
- Colon syntax in `-m` (e.g. `-m my_cluster:llama-3-70b`) looks up the matching `[endpoints.<name>]` table (merged from `settings.*.toml`) plus its credential (`endpoints.<name>.key` in `settings.toml`) and points the OpenAI-compatible client at that alternate `base_url`, bypassing the model catalog entirely.

### Where files live
The package (the code, replaced on upgrade) and the person's own files are kept apart. `src/paths.py` resolves the split; a `.installation` marker inside the package records the folder, and its absence is the signal "not set up yet".

| Location | Contents |
|---|---|
| The person's files folder (`~/PU_AISandbox_data` by default) | `settings.toml`, `model_catalog.json`, `preferences.toml`, `data/` |
| The package | `settings.default.toml`, `plugins/*/settings.toml`, `templates/`, `.installation` |

### Configuration layering (highest precedence last)
`settings.default.toml` (in the package, tracked) → an optional shared file (path set via `settings.toml`'s `shared_settings.path`) → `preferences.toml` in the person's files folder → `plugins/*/settings.toml` (each plugin's `src/settings.py` walks up to find its own) → CLI flags. `settings.toml` itself (keys, endpoint credentials, webui secrets, usage sources) is never layered — it's this installation's own private configuration, edited via the built-in `settings` command, the web interface's `/settings` page, or by hand.

## Documentation & docstring standard

**End users are Princeton faculty from non-CS disciplines** — they understand the tool's purpose but not programming terminology. This governs every docstring and doc change:

- Open docstrings with one plain-English sentence on *what* the function does, not its mechanism. Explain each parameter and return value in terms the caller cares about, not just type. Define technical terms inline (tokens, API key, JSON) with a plain-English analogy where useful (e.g. tokens ≈ words; a lock ≈ a turn-taking mechanism preventing simultaneous writes).
- Avoid unexplained jargon: "serialize," "instantiate," "iterate," "callback," "mutex," "idempotent," etc.
- In READMEs/`docs/`, lead each section with what the user *accomplishes*; use numbered steps; show realistic example commands, not abstract flag lists; explain likely causes and next steps for documented error messages.
- Describe the code as it is now. Don't explain what something used to be called or how it used to work — that belongs in the git history, not in docs, docstrings or comments.
- Documentation serves two audiences: end users (README, `docs/cli-reference.md`, `docs/token-usage-guide.md`, `docs/configuration.md`) and plugin developers (`docs/plugin-authoring-guide.md`, `docs/architecture.md`, `templates/plugin.py.template`). Keep a fact in one place and link to it; duplicated flag tables drift.
- See `.github/copilot-instructions.md` for a full worked docstring example — follow that style for new/meaningfully-modified functions.

## Git commit workflow

- Follow the `.gitmessage` template at repo root: subject `<type>(<scope>): <short summary>` (imperative, ≤72 chars; types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`), body sections `Why:` / `What changed:` / `Notes:`.
- After any code change, propose a commit message in this format.
- **Do not run `git commit` or `git add` — the user handles all commits themselves. However, if the user tells you otherwise, follow their instructions.**

## Further reading

- [`docs/architecture.md`](docs/architecture.md) — request lifecycle, the `sys.modules` injection pattern, data-flow example
- [`docs/cli-reference.md`](docs/cli-reference.md) — full flag reference for every command
- [`docs/configuration.md`](docs/configuration.md) — where files live, and the `settings.toml`/`model_catalog.json`/`settings.default.toml` schema
- [`docs/token-usage-guide.md`](docs/token-usage-guide.md) — token tracking and budget troubleshooting
- [`docs/plugin-authoring-guide.md`](docs/plugin-authoring-guide.md) — step-by-step new-plugin guide
- [`templates/plugin.py.template`](templates/plugin.py.template) — annotated skeleton to copy for a new plugin
- `plugins/*/README.md` — signposts only. Each names what is genuinely particular to that plugin and points at the docs above for everything else. Keep them that way: a fact repeated there is a fact that will drift.
