# PU AI Sandbox — AI Coding Assistant Instructions

## Project overview

Modular AI platform for Princeton University faculty. Provides per-person API key management, per-person token and cost tracking, and a plugin-based command architecture for AI-powered workflows.

**End users are primarily digital humanists and other non-CS faculty.** They understand what the tool does for their research but aren't expected to know programming terminology. All documentation and docstrings must be written with this audience in mind — see [Documentation and docstring standards](#documentation-and-docstring-standards) below.

Two behaviours worth knowing before changing anything near them:

- If a person's configuration is missing or invalid (a blank key, a missing table), `get_api_key()` raises `ValueError`, which is caught and re-raised as `CLIError` with a message the user can act on. The process exits 1.
- If someone exceeds their monthly budget, `TokenTracker` logs warnings at each threshold (80%, 100%) but does **not** halt processing. Every API call continues. The only way to stop spending is to have the API key revoked externally.

## Architecture

- **Entry point**: `main.py` → `src/cli.py` (controller and parser) → runtime handlers in `src/runtime/`
- **Core services**: `BaseService` and the shared API plumbing live in `src/services/`. The AI services themselves (`TranslationService`, `ImageProcessorService`) live in the plugin that owns them. `TokenTracker` is in `src/tracking/`, document processors in `src/processors/`, `FileOutputHandler` in `src/output/`.
- **Plugin system**: every user-facing command (`translate`, `transcribe`, `transcription_review`, `prompt`, `webui`) is a plugin under `plugins/`. `src/cli.py` discovers and loads them through `src/runtime/plugin_loader.py` at startup. Only `usage` and `settings` are built in.

### Where files live

The package (the code) and the settings location are kept apart, so replacing the package on upgrade can't take API keys and months of history with it. `src/paths.py` is the only module that answers "where":

| Location | Contents |
|----------|----------|
| The person's settings location (`~/PU_AISandbox_data` by default) | `settings.toml`, `model_catalog.json`, `preferences.toml`, `data/` |
| The package | `settings.default.toml`, `plugins/*/settings.toml`, `templates/`, `.installation` (the marker naming the settings location) |

The `.installation` marker has to live in the package because the sandbox needs to know where the settings file is before it can read it. Its absence is the signal "this copy hasn't been set up".

### Configuration

- `settings.toml` — this installation's private configuration: `[professors.<netid>]` tables with `name`/`key`/`backup_key`, web UI secrets, `[endpoints.<name>].key` credentials, `[usage_sources]`. Read and written through `src/settings_store.py`. Never layered, never synced.
- Runtime defaults layer: `settings.default.toml` → an optional shared file (`shared_settings.path`) → `preferences.toml` → `plugins/*/settings.toml` → CLI flags.
- `model_catalog.json` — pricing and per-model capabilities, keyed through `[config.provider_map]` for provider slug differences (e.g. `google` → `vertex-ai` for PortKey).

There is no `.env` and no `PROF_*` environment variables. Everything is in `settings.toml`.

## Plugin architecture

1. **File location**: `plugins/<name>/plugin.py`.
2. **Protocol**: expose a module-level `plugin` object satisfying `ModePlugin` (`src/runtime/plugin.py` — structural typing, no base class): `commands: list[str]`, `register_subparsers(subparsers)`, `run(args, professor, model, temperature, top_p, max_tokens)`.
3. **Token tracking**: build a `SandboxProcessor` inside `run()`. It resolves the API key, creates the `TokenTracker`, wires alternate endpoints, and instantiates services on first access. Never construct a `TokenTracker` or a service directly in a plugin.
4. **Import timing**: import `SandboxProcessor` *inside* `run()`, never at module scope — its class statement discovers plugin-registered mixins the moment it is first imported.

A plugin makes its own files findable by registering them in `sys.modules` at import time, under three names core looks for: `src.services.<name>` (services), `src.runtime.<name>` (a module exporting a class called `Mixin`, for orchestration methods), and `pu_plugin.<name>.settings` (constants reachable through `src.settings`).

| Type | Location | Git status | Use as template? |
|------|----------|------------|------------------|
| Bundled | `plugins/prompt/`, `translation/`, `transcription/`, `webui/` | Tracked by this repo | `templates/plugin.py.template` is the annotated skeleton; `plugins/prompt/` is the working reference |
| External (EA) | `plugins/translation-ea/`, `plugins/transcription-ea/` | Separate repos, git-ignored | Reference only — extend the matching bundled plugin rather than editing in place |

**Adding a plugin never requires changing anything in `src/`.** The full walkthrough — including extension plugins, `DispatchPlugin` routing, and giving a command a form in the web interface — is in [`docs/plugin-authoring-guide.md`](../docs/plugin-authoring-guide.md).

## People

People are identified by **netID** (e.g. `jh43`) — letters and digits only, validated by `normalize_netid()` in `src/config.py` and used verbatim as a filename. `name` is a display name, shown in reports and the web interface and nowhere else. There is no name-to-filename conversion anywhere; that's the whole reason netIDs are the identifier.

```bash
python main.py settings add-professor    # prompts for netID, display name and keys
python main.py --show-config             # verify, without making an API call
```

Usage files are created automatically on first use.

## Token tracking

- **Active file**: `data/token_usage_{netid}.json` — the current calendar month only
- **Archives**: `data/archives/{netid}/{YYYY-MM}.json` — one file per past month, written automatically on the first call of a new month
- **Shape**: `{month, total_usage, model_usage, daily_usage, session_history}` — every total covers that month alone
- **All-time totals**: computed on demand by `get_all_time_usage()`, summing the active file and all archives
- **Shared-write mode**: for a person configured to share a folder with another installation, each call writes its own small file instead of rewriting a shared one, so a file-sync service never has two conflicting edits. Every public `TokenTracker` method behaves identically in both modes.
- **Thread safety**: `record_usage()` is protected by an internal `threading.Lock`, so concurrent workers can't corrupt counts.

## Testing CLI changes

```bash
python main.py --help
python main.py --list-models
python main.py --show-config

python main.py jh43 usage report              # current month + budget status
python main.py jh43 usage report --all-time
python main.py jh43 usage report 2025-07
python main.py jh43 usage months
python main.py jh43 usage daily

python main.py jh43 prompt                    # user prompt only
python main.py jh43 prompt -s                 # system prompt first
python main.py jh43 prompt -s --dry-run       # preview, no API call
```

`docs/cli-reference.md` is the full flag reference for every command.

## Model selection

- **Defaults per mode**: `model_catalog.json` → `config.defaults` (`translation`, `ocr`, `image_translation`)
- **Override**: `-m` / `--model`
- **OpenAI and Google auto-registration**: `-m openai/model-name` or `-m google/model-name` fetches pricing from [PortKey](https://api.portkey.ai) and saves it on first use
- **Other providers**: add the entry to `model_catalog.json` by hand. There are no CLI commands for managing the catalog.
- **Alternate endpoints**: `-m my_cluster:model-name` routes to an `[endpoints.<name>]` table, bypassing the catalog entirely

## Error handling

- **API failures**: `BaseService._run_with_retry()` retries with a flat delay, classifying transient errors and content-filter refusals
- **Graceful degradation**: if one unit of work (a page, one file in a batch) fails, the error is logged for that unit and the rest continue
- **User-facing errors**: raise `CLIError` for anything the *user* has to fix. It's printed as plain text and the process exits, rather than a raw traceback reaching a non-programmer.

## Test coverage notes

- **Thread safety**: `tests/test_token_tracker.py::TestConcurrentRecordUsage` — 16 concurrent `record_usage()` calls, checking exact token count, call count and session-history length
- **Plugin loading**: `tests/test_plugin_loader.py` — discovery, `ModePlugin` validation, malformed plugins
- **Plugin suites**: each bundled plugin has its own `tests/` directory, listed explicitly in `pytest.ini`'s `testpaths` — a new plugin's tests must be added there

## Documentation and docstring standards

**Primary audience**: Princeton faculty from digital humanities and other non-CS disciplines. Assume readers understand what the tool accomplishes for their research, but not how programming constructs work internally. Avoid jargon like "serialize", "instantiate", "iterate", "callback", "mutex" or "idempotent" without a plain-English definition.

### Docstrings

Write these for new functions, and for functions being meaningfully modified.

- Open with one plain-English sentence describing *what the function does*, not its internal mechanism.
- Explain each parameter in human terms, not just its type. Include a realistic example value.
- Describe return values in terms of what the caller will do with them.
- When a function performs several meaningful steps, name those steps in plain language.
- Define any technical term used within the docstring itself (tokens, API key, JSON).
- Analogies are encouraged where they clarify something abstract — tokens as a measure of text length roughly equivalent to words; a lock as a turn-taking mechanism that stops two workers writing at once.

**Preferred style:**

```python
def record_usage(self, model: str, tokens: int, professor: str) -> None:
    """
    Record that a professor used a certain number of tokens with a specific AI model.

    Updates the professor's running usage total so that budget warnings can fire
    and monthly reports stay accurate. The usage file is written to disk after
    every call so that no data is lost if the program exits unexpectedly.

    Args:
        model: The AI model that processed the request, as named in the model
               catalog (e.g., 'gpt-4o'). Tokens are the unit AI providers use
               to measure text length — roughly one token per word.
        tokens: The number of tokens consumed by this request.
        professor: The person's netID as configured in settings.toml (e.g., 'jh43'),
                   used to locate the correct usage file under data/.
    """
```

### User-facing documentation (READMEs, `docs/`)

- Lead every section with what the user *accomplishes*, not with how the system is built.
- Use numbered steps for any multi-step process, even where prose would do for a programmer.
- Define every technical term the first time it appears ("API key — a private password that grants access to the AI service").
- Show flags in realistic example commands, not as abstract descriptions.
- Prefer active voice and direct address: "Run this command", not "The command can be run".
- When documenting an error message, explain what likely caused it and what to do next.
- Describe the code as it is now. Don't explain what something used to be called or how it used to work — that belongs in the git history, not the docs.

## Git commit workflow

- A `.gitmessage` template exists at the repo root — always follow its format:
  - **Subject**: `<type>(<scope>): <short summary>` (imperative, ≤72 chars)
  - **Types**: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`
  - **Body sections**: `Why:`, `What changed:`, `Notes:` (where relevant)
- After making **any** code change, propose a commit message in this format before ending the response.
- **Do not run `git commit` or `git add`. The user handles all commits themselves — unless they tell you otherwise, in which case follow their instructions.**
