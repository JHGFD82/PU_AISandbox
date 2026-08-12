# CLAUDE.md

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

Core is deliberately thin — it discovers plugins, builds an argument parser, and routes commands. All translation/transcription/prompt logic lives in `plugins/`. `docs/architecture.md` has the request lifecycle and a description of every core module.

Only `usage` and `settings` are built in; everything else (`translate`, `transcribe`, `transcription_review`, `prompt`, `webui`) is a plugin.

### Plugin system

How to write one, the `sys.modules` registration names, and how `DispatchPlugin` routes shared commands are in [`plugins/CLAUDE.md`](plugins/CLAUDE.md), which loads whenever you work under `plugins/`, and in `docs/plugin-authoring-guide.md`. Two rules live here instead, because they also bite when editing `src/runtime/`:

- `SandboxProcessor` must be imported *inside* a plugin's `run()`, never at module scope: its class statement runs mixin discovery the moment it is first imported, so an import at module scope silently costs later-loading plugins their orchestration methods.
- **Adding a plugin never requires changing `src/`.** If core has to change to make a plugin work, that's a gap in the plugin contract — fix the contract rather than working around it.

### Professor configuration
`settings.toml` pattern: `[professors.<netid>]` tables with `name` (display name), `key`, `backup_key` (optional). The table name is the person's university netID — letters and digits only, validated by `normalize_netid()` in `src/config.py`, and used verbatim as a filename. Missing/blank required fields → `ValueError` re-raised as `CLIError`, process exits 1. `settings.toml` also holds `[webui]` secrets, `[endpoints.<name>].key` credentials, `[shared_settings].path`, and `[usage_sources]` — see `src/settings_store.py` and `docs/configuration.md`.

### Token tracking
In the `data/` folder of the settings location — active: `token_usage_{netid}.json` (current month only); archives: `archives/{netid}/{YYYY-MM}.json`, written automatically on month rollover.
- `usage report --all-time` aggregates the active file + all archives on demand (not loaded eagerly).
- Exceeding the monthly budget only logs warnings at thresholds (e.g. 80%, 100%) — it never halts processing; the only way to stop usage is revoking the professor's API key externally.

### Model catalog & alternate endpoints
- `model_catalog.json` (in the settings location; created by setup from `templates/model_catalog.template.json`) holds pricing/`supports_vision` per model, keyed by `[config.provider_map]` for provider slug quirks (e.g. `google` → `vertex-ai` for PortKey).
- `openai/model-name` or `google/model-name` passed to `-m` auto-fetches and saves pricing from PortKey on first use, then tests the model to fill in `supports_vision` and any `rejects`/`prefers` — see `src/models/capabilities.py`. Pricing for other providers must still be added to the JSON by hand. `settings test-model [model]` re-runs that testing on demand.
- Colon syntax in `-m` (e.g. `-m my_cluster:llama-3-70b`) looks up the matching `[endpoints.<name>]` table — defined in `preferences.toml` or a shared file, never in the package — plus its credential, which may sit in that same table or in `settings.toml` (which wins) and points the OpenAI-compatible client at that alternate `base_url`, bypassing the model catalog entirely.

### Where files live
The package (the code, replaced on upgrade) and the settings location are kept apart. `src/paths.py` resolves the split; a `.installation` marker inside the package records the folder, and its absence is the signal "not set up yet".

| Location | Contents |
|---|---|
| The person's settings location (`~/PU_AISandbox_data` by default) | `settings.toml`, `model_catalog.json`, `preferences.toml`, `data/` |
| The package | `settings.default.toml`, `plugins/*/settings.toml`, `templates/`, `.installation` |

### Configuration layering (highest precedence last)
`settings.default.toml` (in the package, tracked) → an optional shared file (path set via `settings.toml`'s `shared_settings.path`) → `preferences.toml` in the person's settings location → `plugins/*/settings.toml` (each plugin's `src/settings.py` walks up to find its own) → CLI flags. `settings.toml` itself (keys, endpoint credentials, webui secrets, usage sources) is never layered — it's this installation's own private configuration, edited via the built-in `settings` command, the web interface's `/settings` page, or by hand.

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
