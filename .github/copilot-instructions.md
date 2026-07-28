# PU AI Sandbox - AI Coding Assistant Instructions

## Project Overview
Modular AI sandbox platform for Princeton University faculty. Provides professor-specific API key management, per-professor token tracking, and a plugin-based command architecture for AI-powered workflows.

**End users are primarily digital humanists and other non-CS faculty.** They understand what the tool does for their research but are not expected to know programming terminology. All documentation and docstrings must be written with this audience in mind (see Documentation and Docstring Standards below).

If a required professor environment variable (`PROF_[ID]_NAME`, `PROF_[ID]_KEY`) is missing or invalid (empty string, missing entirely, or contains only whitespace), `get_api_key()` raises `ValueError`, which is caught and re-raised as `CLIError` with a descriptive message. The process terminates with exit code 1.

If a professor exceeds their monthly token budget, `TokenTracker` logs a warning at each threshold (e.g., 80%, 100%) but does **not** halt processing — all API calls continue. To stop usage, the professor's API key must be revoked externally.

## Architecture Pattern
**Multi-Professor Service Architecture**: Each professor has isolated API keys and token tracking through environment-based configuration.

- **Entry Point**: `main.py` → `src/cli.py` (controller/parser) → runtime handlers in `src/runtime/`
- **Core Services**: `BaseService` and shared API plumbing in `src/services/` — the AI services themselves (`TranslationService`, `ImageProcessorService`) live in the plugin that owns them. `TokenTracker` in `src/tracking/`, processors in `src/processors/`, `FileOutputHandler` in `src/output/`
- **Configuration**: `.settings` (TOML, git-ignored) for people and API keys, layered `settings.*.toml` for runtime defaults, `src/model_catalog.json` for model pricing. There is no `.env` — it was replaced; see `src/settings_store.py`
- **Plugin System**: All user-facing commands (translate, transcribe, transcription_review, prompt) are implemented as plugins in `plugins/`. `src/cli.py` discovers and loads them via `src/runtime/plugin_loader.py` at startup. Only `usage` is built-in.

### Plugin Architecture

**Step 1 — File location**: Place plugin code at `plugins/<name>/plugin.py`.

**Step 2 — Protocol**: Expose a module-level `plugin` object that implements the `ModePlugin` protocol:
- `commands: list[str]` — command names this plugin handles
- `register_subparsers(subparsers)` — adds argparse subcommands
- `run(args, professor, model, temperature, top_p, max_tokens)` — executes the command

**Step 3 — Token tracking**: Inside `run()`, pass `professor` to any service that writes to `TokenTracker`. Services obtain a tracker internally; do not construct `TokenTracker` directly in the plugin.

**Conventions**:
| Type | Location | Git status | Use as template? |
|------|----------|------------|------------------|
| Bundled | `plugins/prompt/`, `plugins/translation/`, `plugins/transcription/` | Tracked by main repo | `plugins/prompt/` is the canonical template |
| External (EA) | `plugins/translation-ea/`, `plugins/transcription-ea/` | Separate repos, git-ignored | Reference only — extend the matching bundled plugin |

**Adding a new plugin**: Copy `plugins/prompt/`, rename the class and `commands` list, implement `register_subparsers` and `run`. No changes to `src/` are required.

## Professor Configuration System
The system uses a specific environment variable pattern:
```bash
PROF_[ID]_NAME=professor_name     # Display name
PROF_[ID]_KEY=primary_api_key     # Primary Azure OpenAI key
PROF_[ID]_BACKUP_KEY=backup_key   # Fallback key
```

**Safe Name Conversion**: Professor names are converted to safe filenames using `make_safe_filename()` - spaces become underscores, special chars removed. This affects:
- Token usage files: `data/token_usage_{safe_name}.json`
- CLI argument parsing and validation
- Error message formatting

## Token Tracking Architecture
**Per-Professor, Per-Month Isolation**: Each professor gets a separate active file for the current month, with past months automatically archived.
- **Active file**: `data/token_usage_{safe_name}.json` — covers the current calendar month only
- **Archives**: `data/archives/{safe_name}/{YYYY-MM}.json` — one file per past month, written automatically on month rollover
- **File structure**: `{month, total_usage, model_usage, daily_usage, session_history}` — all totals are for that month only
- **Pricing**: Loaded from `src/model_catalog.json` with configurable units (default: per 1M tokens)
- **Budget tracking**: Monthly limits with percentage warnings; resets naturally each month
- **All-time totals**: Computed on demand by aggregating the active file + all archive files via `get_all_time_usage()`

## Key Development Workflows

### Adding People
1. Run `python main.py settings add-professor` (prompts for netID, display name and keys — keys are entered hidden, never as flags)
2. Or hand-edit `.settings`: a `[professors.<netid>]` table with `name`, `key`, and optionally `backup_key`
3. Run `python main.py --show-config` to verify configuration
4. Token tracking files auto-created on first use

People are identified by **netID** (e.g. `jh43`) — letters and digits only, validated by `normalize_netid()` in `src/config.py` and used verbatim as a filename. `name` is a display name only.

### Testing CLI Changes
```bash
# Global commands (no professor required)
python main.py --help
python main.py --list-models

# Usage / reporting
python main.py jh43 usage report              # Current month + budget status
python main.py jh43 usage report --all-time   # Above + all-time totals
python main.py jh43 usage report 2025-07      # Report for a specific archived month
python main.py jh43 usage months              # List all archived month files
python main.py jh43 usage daily               # Today's usage
python main.py jh43 usage daily 2026-03-01    # Specific date

# Custom prompts (fully interactive — text entered at runtime, end with ---)
python main.py jh43 prompt                    # User prompt only
python main.py jh43 prompt -s                 # System prompt first, then user prompt
python main.py jh43 prompt -o response.txt    # Save response to file
python main.py jh43 prompt -m gpt-4o-mini     # Use specific model
python main.py jh43 prompt -s --dry-run       # Preview prompts without API call
```

For translate, transcribe, and transcription_review command examples, see the README in each plugin repo.

## Critical Implementation Details

### Error Handling Pattern
- **API Failures**: Automatic retries with a flat delay in `TranslationService`
- **Graceful Degradation**: If an individual unit of work (e.g., a document page or a single file in a batch) fails during processing, an error message is logged for that unit and processing continues with the remaining units.

### Thread Safety
- `TokenTracker.record_usage()` is protected by an internal `threading.Lock`, so concurrent plugin workers cannot corrupt token counts.

### Model Selection and Configuration
- **Default Models**: `OCR_MODEL=gpt-4o-mini` for OCR, `DEFAULT_MODEL=gpt-4o` for translation
- **Custom Model**: Use `-m/--model MODEL_NAME` flag to override defaults for both translation and OCR
- **OpenAI/Google Auto-Registration**: Use `openai/model-name` or `google/model-name` with `-m` — if not already in the catalog, pricing is fetched from [PortKey](https://api.portkey.ai) and saved automatically on first use
- **Other Providers**: Add the model manually to `src/model_catalog.json`; edit the file directly following the template schema
- **Provider Slug Mapping**: PortKey uses different slugs for some providers (e.g. `google` → `vertex-ai`). These mappings live in `model_catalog.json` under `config.provider_map`, not in code.
- **List Models**: `python main.py --list-models` shows all catalog models with pricing and vision support
- **Configuration**: Models and pricing defined in `src/model_catalog.json` (git-ignored; copy from `src/model_catalog.template.json` to set up) with `supports_vision` boolean flag
- **No CLI catalog management**: There are no CLI commands to add/update/sync models. Use `provider/model` with `-m` for auto-registration, or edit the JSON directly.

## Custom Prompt Command
- **Command**: `python main.py <professor> prompt` — sends a freeform prompt without translation framing
- **Implemented as a plugin**: `plugins/prompt/plugin.py` — ships with the main repo and serves as the reference template for new plugins
- **Fully interactive**: no text arguments; user types input at runtime and ends with `---` on its own line
- **System prompt**: `-s/--system` is a boolean flag; when set, the system prompt is collected first, then the user prompt
- **Output**: response printed to console; optionally saved with `-o`
- **Dry run**: `--dry-run` shows prompt structure without making an API call
- **Token tracking**: usage tracked via `TokenTracker` created inside the plugin's `run()` method
- **Interactive helper**: `_collect_multiline(label)` is a module-level helper in `plugins/prompt/plugin.py`; translation's `-c` mode uses its own equivalent in the translation plugin

## External Dependencies
- **PortKey**: Uses `SANDBOX_ENDPOINT` and `SANDBOX_API_VERSION` from config
- **Font Management**: Custom fonts in `fonts/` directory for PDF and Word output
- **Princeton-Specific**: API keys from Princeton's AI Sandbox service

## Common Patterns to Follow
- **Safe Name Usage**: Always use `make_safe_filename()` for file operations
- **Professor Context**: Pass professor name to `TokenTracker` and any service calls
- **Configuration Loading**: Use `load_professor_config()` for env var parsing
- **Error Messages**: Include available professors and suggest both full/safe names
- **Logging**: Use structured logging with professor context where applicable

## Testing Without API Keys
Use `python main.py --show-config` to validate professor configuration without making API calls.

## Test Coverage Notes
- **Thread safety**: `tests/test_token_tracker.py::TestConcurrentRecordUsage` — 16 concurrent `record_usage()` calls, exact token count, call count, session history length
- **Plugin loading**: `tests/test_plugin_loader.py` — discovery, `ModePlugin` protocol validation, error handling for malformed plugins
- **Core services and processors**: tests in `tests/` cover `TranslationService`, `ImageProcessorService`, processors in `src/processors/`, and `FileOutputHandler` — these all live in the main repo even though they are invoked by external plugins

## Documentation and Docstring Standards

**Primary audience**: Princeton faculty from digital humanities and other non-CS disciplines. Assume readers understand what the tool accomplishes for their research, but not how programming constructs work internally. Avoid jargon like "serialize", "instantiate", "iterate", "callback", "mutex", or "idempotent" without plain-English definitions.

### Docstrings (write these for new functions and for functions being meaningfully modified)
- Open with one plain-English sentence describing *what the function does*, not its internal mechanism.
- Explain each parameter in human terms, not just its type. Always include a realistic example value.
- Describe return values in terms of what the caller will do with them, not just their data type.
- When a function performs multiple meaningful steps, name those steps in plain language in the docstring body.
- Define any technical term used within the docstring itself (e.g., tokens, API key, JSON).
- Analogies are encouraged where they clarify abstract concepts (e.g., tokens as a measure of text length roughly equivalent to words; a threading lock as a turn-taking mechanism that prevents two workers from writing at the same time).

**Example — preferred docstring style**:
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
        professor: The person's netID as configured in .settings (e.g., 'jh43'),
                   used to locate the correct usage file under data/.
    """
```

### User-Facing Documentation (READMEs, docs/)
- Lead every section with what the user *accomplishes*, not with how the system is structured internally.
- Use numbered steps for any multi-step process, even when programmers might understand prose.
- Define every technical term the first time it appears (e.g., "API key — a private password that grants access to the AI service").
- Show CLI flags with realistic example commands in context, not just abstract flag descriptions.
- Prefer active voice and direct address: "Run this command" rather than "The command can be run."
- When documenting error messages, explain what likely caused the error in plain language and what the user should do next.

## Git Commit Workflow
- A `.gitmessage` template exists at the repo root — always follow its format when writing commits:
  - **Subject**: `<type>(<scope>): <short summary>` (imperative mood, ≤ 72 chars)
  - **Types**: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`
  - **Body sections**: `Why:`, `What changed:`, `Notes:` (when relevant)
- After making **any** code changes, always propose a commit message in this format before ending the response.
- **Do not run `git commit` or `git add`. The user handles all git commits themselves. However, if the user tells you otherwise, follow their instructions.**
