# Architecture

PU AI Sandbox is a modular CLI platform. The core is deliberately thin: it discovers plugins, builds an argument parser, and routes commands. All application logic lives in plugins.

---

## Request lifecycle

```
python main.py jh43 translate jp-en -i doc.pdf
         │
         ▼
   main.py  ──────────────────────────────────────────────► src/cli.py::main()
                                                                    │
                         ┌──────────────────────────────────────────┘
                         │
                         ▼
              load_plugins(plugins/)          ← discovers plugins/*/plugin.py
                         │                      builds dict: command → ModePlugin
                         ▼
              create_argument_parser(plugins)  ← calls each plugin's
                         │                       register_subparsers()
                         ▼
              parser.parse_args()              ← validates language codes, flags
                         │
                         ▼
              _plugins[args.command].run(...)  ← dispatches to the owning plugin
                         │
                         ▼
              plugin creates SandboxProcessor  ← resolves the API key, creates the
                         │                       TokenTracker, wires up any
                         │                       alternate endpoint
                         ▼
              SandboxProcessor lazily loads    ← reads sys.modules for the plugin
              plugin-owned services              services injected at import time
                         │
                         ▼
              Service calls the PortKey API    ← with retry and error classification
                         │
                         ▼
              TokenTracker.record_usage()      ← writes to the data folder
```

Before any of that, `main.py` checks the Python version and then whether this copy has been set up — see [Where files live](#where-files-live).

---

## Key components

### `src/cli.py` — controller

The single entry point:

- Calls `load_plugins()` before building the parser, so plugins can register their languages first
- Builds the argument parser, delegating subcommand registration to each plugin
- Routes the parsed command to `_plugins[cmd].run()`, or to the built-in `usage` and `settings` handlers
- Exports `add_common_flags()` and `add_notes_flags()` for plugins to call

### `src/paths.py` — where files live

Answers one question: which folder holds this person's settings, catalogue and data. A marker file (`.installation`) inside the package records the answer, and has to live there because the sandbox needs to know where the settings file is before it can read it. Its absence is exactly the signal "this copy hasn't been set up" — no version numbers to compare.

### `src/first_run.py` and `src/setup_prompts.py` — setup

`first_run.py` holds the decisions (what a folder already contains, which files to create, what must never be overwritten); `setup_prompts.py` asks them at the terminal, and `plugins/webui/src/setup_web.py` asks the same questions in a browser. Both routes go through `first_run.py`, so the two can't drift on what counts as an existing setup.

### `src/runtime/plugin_loader.py` — discovery

Scans `plugins/*/plugin.py` at startup, alphabetically. For each file:

1. Imports the module via `importlib.util`
2. Checks it has a module-level `plugin` attribute with `commands`, `register_subparsers` and `run`
3. Maps each command name to the plugin object

If two plugins claim the same command *and* both declare a `handles` list, the loader builds a `DispatchPlugin` instead of raising a conflict.

### `src/runtime/plugin.py` — the protocol

`ModePlugin` is a `typing.Protocol`. A plugin class doesn't inherit from anything — it just needs three members:

| Member | Type | Purpose |
|--------|------|---------|
| `commands` | `list[str]` | The command names this plugin owns |
| `register_subparsers(subparsers)` | method | Adds subcommands to the parser |
| `run(args, professor, model, ...)` | method | Executes the command |

Several optional members (`requires_professor`, `handles`, `ui_action`, `run_ui_action`, `preview_ui_action`, `get_peer_guidance`) are read with `getattr()` rather than declared on the protocol, so a plugin that declares none of them is still valid. See [`plugin-authoring-guide.md`](plugin-authoring-guide.md#the-contract).

### `src/runtime/sandbox_processor.py` — service wiring

`SandboxProcessor` is what a plugin constructs to call AI services. It:

- Resolves the professor's API key and display name
- Creates a `TokenTracker` for them
- Holds the document processors and the file output handler
- **Lazily** loads plugin-owned services through `__getattr__`
- **Composes plugin-owned command mixins** as base classes at class-definition time
- **Routes to alternate endpoints**: if `model` contains colon syntax (e.g. `"my_cluster:llama-3-70b"`), loads the matching `[endpoints.<name>]` definition from the merged settings layers plus its credential from `settings.toml`, points the OpenAI-compatible client at that `base_url`, and bypasses the model catalogue

The lazy loader follows a naming convention: attribute `translation_service` maps to `sys.modules["src.services.translation_service"].TranslationService`. Plugins inject their service files into `sys.modules` at import time; the processor instantiates them on first access.

Only `_FileTypeMixin` (file-type detection, needed by every mode) and `_CommandMixin` (interactive helpers) are statically listed in the class definition. Everything mode-specific comes from plugin-registered mixins discovered at import time.

### `src/runtime/dispatch_plugin.py` — multi-plugin routing

When two plugins share a command — `translate` is handled by both `translation` and `translation-ea` — a `DispatchPlugin` wraps them. It:

- Maintains a `source_registry` mapping language code → owning plugin
- Reads `args.language_code[0]` at runtime to find the owner and delegates `run()`
- Collects destination-side "peer guidance" from the other plugin via `get_peer_guidance(token)` and injects it into `args._peer_guidance`
- Forwards `ui_action` and `run_ui_action` straight through to the primary plugin, which is why extension plugins contribute composer fields through the standalone registry in `src/runtime/ui_action.py` rather than through `DispatchPlugin`

### `src/services/base_service.py` — service foundation

Every AI service extends `BaseService`, which provides:

- PortKey client initialisation
- `_create_completion()` — handles the `max_tokens` vs `max_completion_tokens` difference for reasoning models
- `_run_with_retry()` — flat delay between retries, transient-error detection, content-filter retry
- `_record_response_usage()` — extracts token counts and calls `TokenTracker.record_usage()`
- `_get_model()` — resolves the model name and syncs pricing if the model is new

### `src/tracking/token_tracker.py` — token accounting

Per-professor and scoped to one calendar month, in the data folder:

- **Active**: `token_usage_{netid}.json` — the current month only
- **Archives**: `archives/{netid}/{YYYY-MM}.json` — written automatically on the first use of a new month

All-time totals (`usage report --all-time`) are computed on demand by summing the active file with all archives, rather than loading everything eagerly. Writes are guarded by a lock so two threads can't record usage at the same time.

### `src/processors/` — document ingestion

Converts source files into lists of text pages for AI services to work through.

| Processor | Input | Notes |
|-----------|-------|-------|
| `PdfProcessor` | `.pdf` | CJK-optimised layout parameters; `--scanned` routes through vision instead |
| `DocxProcessor` | `.docx` | Body and tables in document order |
| `TxtProcessor` | `.txt` | Split by the `default_page_size` character target |
| `MarkdownProcessor` | `.md` | Markdown formatting preserved as-is |
| `JsonProcessor` | `.json` | Recursively flattened to key/value lines |
| `ExcelProcessor` | `.xlsx` / `.xls` | Each sheet as a header plus tab-separated rows; needs `openpyxl` |
| `ImageProcessor` | `.png` `.jpg` `.jpeg` `.gif` `.bmp` `.tiff` `.webp` | Base64-encodes for a vision model; blank-image detection skips empty pages |

### `src/output/` — writing results

Writes AI output in the format implied by the output file's extension.

| Extension | Handler | Behaviour |
|-----------|---------|-----------|
| `.txt` | `save_to_text_file` | Markdown tables drawn as ASCII box tables |
| `.md` | `save_to_markdown` | Written as-is; supports progressive (append-as-you-go) save |
| `.pdf` | `pdf_builder` | CJK fonts; Markdown tables become real tables |
| `.docx` | `docx_builder` | 1" margins, 1.5 line spacing; Markdown tables become real tables; optional image reinsertion |
| `.xlsx` | `excel_builder` | Markdown tables become separate sheets, prose goes to a "Text" sheet; needs `openpyxl`, falls back to `.txt` without it |
| `.json` | `json_builder` | Valid JSON is pretty-printed; plain text is wrapped as `{"content": "..."}` |

Unsupported extensions and rich-format failures fall back to `.txt`.

---

## Plugin isolation and `sys.modules` injection

Plugins own their service files *and* their command-orchestration logic. Because `src/` ships neither translation nor transcription business logic, a plugin has to make those modules findable under the names core looks for:

```python
# In plugin.py, at module level, before any import that needs the module
_register("src.services.translation_service", "src/services/translation_service.py")
```

`SandboxProcessor.__getattr__` then finds the module in `sys.modules` and instantiates the service class on first access. Nothing in `src/` changes when a plugin is added.

### The same convention for orchestration methods

Multi-step methods on the sandbox itself — `translate_document`, `translate_custom_text`, `process_image_translation`, `process_image` — belong to the plugin that implements them, registered under a `"src.runtime.<name>"` key instead of `"src.services.<name>"`:

```python
_register("src.runtime.document_handler", "src/runtime/document_handler.py")
```

The registered module must export a class named `Mixin`. `src/runtime/sandbox_processor.py` scans `sys.modules` for every key starting with `"src.runtime."` and includes each one's `Mixin` as a base class, at the moment `SandboxProcessor`'s class statement first executes:

```python
class SandboxProcessor(*_discover_plugin_mixins(), _FileTypeMixin, _CommandMixin):
    ...
```

This works because `SandboxProcessor` is only ever imported lazily inside a plugin's `run()`, never at module scope anywhere in `src/`, and `load_plugins()` runs every plugin's registrations before any `run()` is dispatched — so every plugin mixin is already registered by the time `_discover_plugin_mixins()` runs.

`plugins/translation/` and `plugins/transcription/` each register their own `src.runtime.*` module name; they can't share one, since a plugin's document- and image-handling methods are its own file. Each plugin's `conftest.py` mirrors the same registrations for its own test suite.

A third name uses the same mechanism: a module registered as `pu_plugin.<name>.settings` has its constants exposed through `src.settings`'s `__getattr__`, so `from src.settings import SOME_CONSTANT` reaches a plugin's own settings without `src/settings.py` naming any plugin.

---

## Where files live

The package holds the code and is what gets replaced on upgrade. Everything belonging to the person using it lives in a separate folder, chosen at setup — `~/PU_AISandbox_data` by default.

| Location | Contents |
|----------|----------|
| Your files folder | `settings.toml` (API keys, endpoint credentials, web UI secrets, external usage sources), `model_catalog.json`, `preferences.toml`, `data/` |
| The package | `settings.default.toml`, `plugins/*/settings.toml`, `templates/`, `.installation` (the marker naming your files folder) |

### Configuration layers

| Source | Controls |
|--------|----------|
| `settings.default.toml` (package, tracked) | Core defaults: temperature, retry, workers, font size, budget threshold, alternate-endpoint *definitions* |
| A shared file (optional, path set by `shared_settings.path` in `settings.toml`) | Same shape as `settings.default.toml`; overrides it, is overridden by `preferences.toml` |
| `preferences.toml` (your files folder) | This person's own adjustments; applied last |
| `plugins/*/settings.toml` | Plugin-specific defaults; each plugin's `src/settings.py` walks up to find its own |
| CLI flags | Runtime overrides; always win |

`settings.toml` is not part of this stack. It's this installation's own private configuration, edited through the `settings` command or the web interface rather than layered.

---

## Data flow: a translation

```
TranslationPlugin.run(args, professor, ...)
  │
  ├─ SandboxProcessor(professor, model, ...)   ← creates the TokenTracker
  │     └─ __getattr__("translation_service")
  │           └─ TranslationService(api_key, professor, token_tracker=..., model=...)
  │
  └─ _execute_translate(sandbox, args, source_language, target_language)
        │
        ├─ detect the file type (PDF / DOCX / text / image)
        ├─ extract pages or text blocks
        ├─ for each page: TranslationService.translate_page(text, prev_context)
        │     └─ BaseService._run_with_retry(body_fn, model, "translation")
        │           └─ BaseService._create_completion(model, messages, max_tokens)
        │                 └─ PortKey API call
        │           └─ BaseService._record_response_usage(response, model)
        │                 └─ TokenTracker.record_usage(...)
        └─ FileOutputHandler.save(output, format)
```
