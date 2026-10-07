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

Answers one question: which folder holds this person's settings, catalog and data. A marker file (`.installation`) inside the package records the answer, and has to live there because the sandbox needs to know where the settings file is before it can read it. Its absence is exactly the signal "this copy hasn't been set up" — no version numbers to compare.

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
- **Routes to alternate endpoints**: if `model` contains colon syntax (e.g. `"my_cluster:llama-3-70b"`), loads the matching `[endpoints.<name>]` definition from the merged settings layers plus its credential from `settings.toml`, points the OpenAI-compatible client at that `base_url`, and uses the model as given rather than choosing or pricing one from the catalog. The model is still recorded in the catalog as `name:model`, so it appears in the lists — see `src/models/endpoint_models.py`

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

`_register` is the plugin's own folder bound to `register_plugin_module()` in `src/runtime/plugin.py`, the one function every plugin — and every plugin's tests, through `tests/plugin_modules.py` — files its modules with. `SandboxProcessor.__getattr__` then finds the module in `sys.modules` and instantiates the service class on first access. Nothing in `src/` changes when a plugin is added.

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
| Your settings location | `settings.toml` (API keys, endpoint credentials, web UI secrets, external usage sources), `model_catalog.json`, `preferences.toml`, `data/` |
| The package | `settings.default.toml`, `plugins/*/settings.toml`, `templates/`, `.installation` (the marker naming your settings location) |

### Replacing the package

The package is a git clone, so an upgrade is `git fetch` and `git merge --ff-only`. `plugins/webui/src/upgrade.py` does that from the browser, and `plugins/webui/src/git_tool.py` is where both it and the plugin installer get a git that cannot stop and wait for a password.

Three things about it are worth knowing before changing it:

- **`git merge --ff-only`, never `git pull`.** A pull that cannot fast-forward leaves a half-merged working tree, which nobody is recovering from in a browser. It refuses instead, and a preflight rules out the cases worth explaining first — no `.git`, an *enclosing* repository rather than this one, a detached HEAD, a branch other than `main`, a `main` with no upstream or following some other branch, a dirty tree, or local commits. Only `main` is ever offered an update: another branch's distance from its own upstream is somebody's work in progress, not a new version. Each answer also records the branch and commit it was about (`Available.position`), and `GET /api/updates` looks again rather than repeat one the checked-out copy has since moved away from.
- **The restart skips `start.py`.** `_restart_into_the_new_code()` execs `main.py`, so nothing re-reads `requirements.txt`. The update installs changed dependencies itself, and writes `.venv/.requirements-stamp` only on success — a stale stamp is what makes the next `python3 start.py` notice and repair a half-installed environment.
- **`start.py` owns the fingerprint.** It cannot import from `src/` (it runs on whatever Python the computer has, before `.venv` exists), so `upgrade.py` loads it by path and asks it for `requirements_fingerprint()`, `STAMP` and `venv_python()` rather than keeping a second copy.

Because every installed copy fast-forwards, **`main` must never be rebased or force-pushed** once this has shipped: every copy would become non-fast-forwardable at once, and each would report it as "this copy has changes of its own".

### Starting and stopping

`start.py` is the way in for people. It does the part every copy of the sandbox needs — finding a Python new enough to run it, and installing its software into `.venv` — and then, if the web interface is there, hands over to `plugins/webui/launcher/launcher.py`, which does everything else. It is run three ways:

| | |
|---|---|
| `python3 start.py` | From a terminal. Installs what is missing (after asking), then the launcher's `open_from_a_terminal()`: first-time setup if needed, an icon on the Desktop the first time, then `webui serve` in that window. |
| `start.py --launch` | What the icon runs. The launcher's `launch()` stops a copy already running, starts `--run-hidden` on its own with no window and its output going to a log file, opens the browser once the loading page answers, and exits. |
| `start.py --run-hidden` | The long-running part of an icon start, `run_hidden()`: the same steps as a terminal run, with progress shown on the loading page rather than printed. |

Six things about it are worth knowing before changing it:

- **The launcher is the web interface's, and is loaded by path.** It lives in the plugin folder because a copy with the web interface removed has no use for it; `start.py` falls back to setting up in the terminal when `has_the_web_interface()` says no or the launcher is missing. `start.py` loads it with `importlib` from its path and hands itself over (`attach()`), so finding a Python and installing live in one place. Like `start.py`, the launcher has to parse on the Python a Mac ships and must not import `src/` or anything installed, because it runs before `.venv` exists. `plugins/webui/tests/launcher/test_launcher.py` checks both.
- **The port is the web interface's setting, asked of the sandbox.** `port` under `[webui]` can be changed in a shared file or `preferences.toml` like any other setting, so the launcher runs `.venv`'s Python to load the plugin's own `src/settings.py` and print `WEBUI_PORT`, then passes `--port` to `webui setup` and `webui serve`. Before `.venv` exists there is nothing to ask, and it reads `port =` from `plugins/webui/settings.toml` itself. If installing then turns up a different answer, the loading page starts a second copy of itself on the new port and tells the browser to go there (`LoadingPage.move_to()`).
- **The loading page holds the port until the sandbox takes it.** `LoadingPage` is a small server, built from what comes with Python, that answers with `loading.html`. The page asks `/__loading` every half second; when anything other than the loading page answers, it reloads onto the sandbox. `hand_over()` lets go of the port only after the browser has fetched the page once, so a browser slow to open never lands on nothing.
- **Every start is a fresh start, and only this copy is ever stopped.** Each start writes a random stop token to `.venv/.stop-token` (readable by its owner only) and passes it to the server in `PU_SANDBOX_STOP_TOKEN`, which `os.execv` in `_restart_into_the_new_code()` carries across a restart. The next start presents it to `POST /__stop`, added to both the setup app and the sandbox by `plugins/webui/src/stopping.py`. 200 means it is stopping; 409 means it is busy (a job or an update running), and the launcher opens it instead; anything else means the port belongs to something else, which is reported and never touched.
- **Quit replaced Lock.** `POST /quit` needs the passphrase, refuses with 409 under the same conditions as `/__stop`, and stops the server through `stopping.stop_soon()`. Both routes find the server at `app.state.server`, which `run_server()` and `webui setup` put there; `run_server()` gives open requests five seconds to finish (`timeout_graceful_shutdown`), because a browser tab can hold one open indefinitely and the launcher is waiting for the port.
- **The icon is made on the computer, not shipped, and survives Python going away.** `make_shortcut()` writes a small `.app` on a Mac, a `.lnk` on Windows through PowerShell, and a `.desktop` entry on Linux. None is subject to download checks, since nothing about it was downloaded, so none needs signing. On a Mac and Linux the icon runs `open-sandbox.sh` with the Python it was made with; the script falls back to the usual places for one, and with none at all opens `loading.html` from disk unfilled, which then explains that Python needs installing. The Mac `.app` is an AppleScript application made with `osacompile`, renamed from `applet` to the sandbox's name, given `LSUIElement`, `sandbox.icns` and the `NS…FolderUsageDescription` keys, and signed again ad hoc (`codesign -s -`): a Mac keeps applications out of Documents, Desktop and Downloads until the person allows them, and only a real program can ask — a plain script there is refused silently and the icon does nothing. If the person refuses, the applet says how to allow it. A plain-script `.app` is the fallback when `osacompile` fails, and works only outside those folders. On Windows the shortcut runs `pyw.exe`, which finds whichever Python 3 is installed, when there is one.

### Configuration layers

| Source | Controls |
|--------|----------|
| `settings.default.toml` (package, tracked) | Core defaults: temperature, retry, workers, font size, budget threshold, alternate-endpoint *definitions* |
| A shared file (optional, path set by `shared_settings.path` in `settings.toml`) | Same shape as `settings.default.toml`; overrides it, is overridden by `preferences.toml` |
| `preferences.toml` (your settings location) | This person's own adjustments; applied last |
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
