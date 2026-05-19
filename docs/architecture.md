# Architecture

PU_AISandbox is a modular CLI platform. The core is deliberately thin: it discovers plugins, builds an argument parser, and routes commands. All application logic lives in plugins.

---

## Request Lifecycle

```
python main.py heller translate jp-en -i doc.pdf
         │
         ▼
   main.py  ──────────────────────────────────────────────► src/cli.py::main()
                                                                    │
                         ┌──────────────────────────────────────────┘
                         │
                         ▼
              load_plugins(plugins/)          ← discovers plugins/*/plugin.py
                         │                     builds dict: command → ModePlugin
                         ▼
              create_argument_parser(plugins)  ← calls plugin.register_subparsers()
                         │                       for each plugin
                         ▼
              parser.parse_args()              ← validates args (language codes, flags)
                         │
                         ▼
              _plugins[args.command].run(...)  ← dispatches to owning plugin
                         │
                         ▼
              plugin creates SandboxProcessor  ← owns API key resolution,
                                                 TokenTracker creation, and
                                                 alternate-endpoint wiring
                         │
                         ▼
              SandboxProcessor lazily loads    ← reads sys.modules for plugin
              plugin-owned services              services injected at import time
                         │
                         ▼
              Service calls PortKey API        ← with retry + error classification
                         │
                         ▼
              TokenTracker.record_usage()      ← persists to data/token_usage_*.json
```

---

## Key Components

### `src/cli.py` — Controller

The single entry point. Responsibilities:
- Calls `load_plugins()` before parser creation so plugins can register languages
- Builds the argument parser, delegating subcommand registration to each plugin
- Routes the parsed command to `_plugins[cmd].run()` or the built-in `usage` handler
- Exports `add_common_flags()` and `add_notes_flags()` for plugins to call

### `src/runtime/plugin_loader.py` — Discovery

Scans `plugins/*/plugin.py` at startup. For each file found:
1. Imports the module via `importlib.util`
2. Validates it has a module-level `plugin` attribute with `commands`, `register_subparsers`, and `run`
3. Adds each command name → plugin object to the result dict

If two plugins claim the same command *and* both declare a `handles` list, the loader creates a `DispatchPlugin` instead of raising a conflict.

### `src/runtime/plugin.py` — Protocol

`ModePlugin` is a `typing.Protocol` (structural typing). A plugin class doesn't need to inherit from anything — it just needs the three required members:

| Member | Type | Purpose |
|--------|------|---------|
| `commands` | `list[str]` | CLI subcommand names this plugin owns |
| `register_subparsers(subparsers)` | method | Adds subcommands to the parser |
| `run(args, professor, model, ...)` | method | Executes the command |

### `src/runtime/sandbox_processor.py` — Service Wiring

`SandboxProcessor` is the runtime object plugins construct to call AI services. It:
- Resolves the professor's API key and display name
- Creates a `TokenTracker` for the professor
- Holds processors (`PDFProcessor`, `ImageProcessor`) and the file output handler
- **Lazily** loads plugin-owned services via `__getattr__`

The lazy loader follows a naming convention: attribute `translation_service` maps to `sys.modules["src.services.translation_service"].TranslationService`. Plugins inject their service files into `sys.modules` at import time; the processor instantiates them on first access.

### `src/runtime/dispatch_plugin.py` — Multi-Plugin Routing

When two plugins share a command (e.g., `translate` is handled by both `translation` and `translation-ea`), a `DispatchPlugin` is created. It:
- Maintains a `source_registry` mapping language code → owning plugin
- At runtime, reads `args.language_code[0]` to find the owning plugin and delegates `run()`
- Optionally collects "peer guidance" from the destination-language plugin via `get_peer_guidance(token)` and injects it into `args._peer_guidance`

### `src/services/base_service.py` — Service Foundation

All AI services extend `BaseService`, which provides:
- PortKey client initialization
- `_create_completion()` — handles the `max_tokens` vs `max_completion_tokens` API difference for reasoning models
- `_run_with_retry()` — exponential backoff, transient error detection, content-filter retry
- `_record_response_usage()` — extracts token counts and calls `TokenTracker.record_usage()`
- `_get_model()` — resolves the model name and syncs pricing if the model is new

### `src/tracking/token_tracker.py` — Token Accounting

Per-professor, month-scoped. Files:
- **Active**: `data/token_usage_{name}.json` — current calendar month only
- **Archives**: `data/archives/{name}/{YYYY-MM}.json` — auto-written on first use of a new month

On-demand aggregation (`usage report --all-time`) sums the active file with all archives without loading everything into memory upfront.

### `src/config.py` — Language Registry and Professor Config

- `LANGUAGE_MAP` starts empty. Plugins call `register_language(code, name)` at import time to populate it. Argparse type-hooks (`parse_language_code`, `parse_single_language_code`) validate against this map at parse time — so plugins must load *before* the parser is built (the loader guarantees this).
- `load_professor_config()` scans environment variables for `PROF_*_NAME`, `PROF_*_KEY` blocks.
- `get_api_key(professor)` resolves primary key, falls back to backup key with a warning.

---

## Plugin Isolation and sys.modules Injection

Plugins own their service files. Because `src/services/` no longer ships translation or transcription logic, plugins must make those modules findable. The pattern:

```python
# In plugin.py, at module level (before any imports that need the module)
def _register(module_name: str, rel_path: str) -> None:
    if module_name in sys.modules:
        return
    path = _PLUGIN_DIR / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)

_register("src.services.translation_service", "src/services/translation_service.py")
```

`SandboxProcessor.__getattr__` then finds the module in `sys.modules` and instantiates the service class automatically. No changes to `src/` are ever needed when adding a plugin.

---

## Configuration Layers

| Source | Controls |
|--------|----------|
| `.env` | Professor API keys (`PROF_*_NAME/KEY/BACKUP_KEY`) |
| `settings.toml` (repo root) | Core defaults: temperature, retry, workers, font size, budget threshold |
| `plugins/*/settings.toml` | Plugin-specific defaults (each plugin's `src/settings.py` walks up to find it) |
| `src/model_catalog.json` | Model registry: pricing, vision support, token limits (git-ignored; per-installation) |
| CLI flags | Runtime overrides; always take precedence over all defaults |

---

## Data Flow: Translation Example

```
TranslationPlugin.run(args, professor, ...)
  │
  ├─ SandboxProcessor(professor, model, ...)   ← creates TokenTracker internally
  │     └─ __getattr__("translation_service")
  │           └─ TranslationService(api_key, professor, token_tracker=..., model=...)
  │
  └─ _execute_translate(sandbox, args, source_language, target_language)
        │
        ├─ detect file type (PDF / DOCX / text)
        ├─ extract pages/text blocks
        ├─ for each page: TranslationService.translate_page(text, prev_context)
        │     └─ BaseService._run_with_retry(body_fn, model, "translation")
        │           └─ BaseService._create_completion(model, messages, max_tokens)
        │                 └─ PortKey API call
        │           └─ BaseService._record_response_usage(response, model)
        │                 └─ TokenTracker.record_usage(...)
        └─ FileOutputHandler.save(output, format)
```
