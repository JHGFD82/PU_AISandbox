# Plugin Authoring Guide

A plugin is a self-contained directory under `plugins/` that adds a command to the sandbox. `translate`, `transcribe`, `prompt` and the web interface are all plugins; the core knows nothing about any of them. At startup it looks in `plugins/`, loads every `plugin.py` it finds, and asks each one what commands it provides.

**Adding a plugin never requires editing anything in `src/`.** If you find yourself needing to change core code to make a plugin work, that is a gap in the plugin contract — please open an issue rather than working around it, because other people will hit it too.

---

## Two kinds of plugin

**Standalone plugins** own a command outright. They implement `register_subparsers()` to create the command's parser, and the CLI router calls their `run()` directly. `prompt`, `translation`, `transcription` and `webui` are all standalone.

**Extension plugins** add languages or options to a command another plugin already owns. Instead of registering a command, they declare `handles` (the languages they own) and implement `register_command_flags()` to append their flags to the base plugin's parser. When two plugins claim the same command and both declare `handles`, `src/runtime/dispatch_plugin.py` merges them into a `DispatchPlugin` that routes each request to whichever plugin owns that language. `translation-ea` and `transcription-ea` are extension plugins.

> **An extension plugin must not implement `register_subparsers()`.** The command already exists, so registering it a second time is a conflict and the plugin loader will silently skip your plugin.

---

## Quick start

```bash
mkdir -p plugins/myplugin
cp templates/plugin.py.template plugins/myplugin/plugin.py
python main.py --help          # your command should now appear
```

`templates/plugin.py.template` is an annotated skeleton — it explains every decision inline, and you delete whatever you don't need. For a complete *working* plugin written to be read as a reference, see `plugins/prompt/plugin.py`.

There is no registration step anywhere. The loader (`src/runtime/plugin_loader.py`) discovers `plugins/*/plugin.py` on its own, in alphabetical order.

---

## What a plugin directory looks like

```
plugins/myplugin/
├── plugin.py                      # required — the loader's entry point
├── settings.toml                  # optional — your own tuneable defaults
├── conftest.py                    # optional — registers your modules for pytest
├── src/
│   ├── settings.py                # optional — reads settings.toml
│   ├── runtime/
│   │   └── my_handler.py          # optional — multi-step methods on the sandbox
│   └── services/
│       └── my_service.py          # optional — the class that calls the AI model
└── tests/
```

Only `plugin.py` is required; a working plugin can be about thirty lines.

---

## The contract

`plugin.py` must define a module-level object named `plugin`. There is no base class to inherit from — the loader just checks that your object has the right attributes. `src/runtime/plugin.py` holds the formal description.

```python
class MyPlugin:
    commands: list[str] = ["mycommand"]

    def register_subparsers(self, subparsers) -> None:
        p = subparsers.add_parser("mycommand", help="What it does")
        p.add_argument("-i", "--input", dest="input_file", required=True)
        add_common_flags(p)   # adds -o, -m, -t, -T, -M, --dry-run

    def run(self, args, professor, model, temperature, top_p, max_tokens) -> None:
        ...

plugin = MyPlugin()
```

| Member | Type | Purpose |
|--------|------|---------|
| `commands` | `list[str]` | The command names this plugin owns |
| `register_subparsers(subparsers)` | method | Adds your command and its flags to the shared parser |
| `run(args, professor, model, temperature, top_p, max_tokens)` | method | Does the work |

### Optional members

| Member | Default | Effect |
|--------|---------|--------|
| `requires_professor: bool` | `True` | Set `False` if your command doesn't spend one person's API budget, so it can be run without a netID first. `webui` sets this, because which professor is active is chosen later, in the browser. |
| `model_roles: dict[str, ModelRole]` | **required** | Which models this plugin's work should use — see [Which models your plugin uses](#which-models-your-plugin-uses). A plugin without it is refused at load. |
| `handles: list[str]` | absent | Extension plugins only — the languages you own. Match the base command's own form: short codes for `translate`, full names for `transcribe` (see [Writing an extension plugin](#writing-an-extension-plugin)). |
| `register_command_flags(parser)` | absent | Extension plugins only — appends your flags to the base plugin's parser. |
| `get_peer_guidance(token)` | absent | Extension plugins only — contributes destination-side prompt guidance when your language is the *target*. |
| `ui_action` / `run_ui_action` / `preview_ui_action` | absent | Gives your command a form in the web interface — see [A button in the web interface](#a-button-in-the-web-interface). |

None of the optional members are part of the `ModePlugin` protocol. Each is read with `getattr(plugin, "...", None)`, so declaring none of them leaves your plugin command-line only.

---

## Which models your plugin uses

**This is required.** A plugin that doesn't declare it is refused at load, with a message saying what to add.

The reason is not bureaucracy. Without a declaration the sandbox has nothing to go on and falls through to the cheapest model in the catalogue — which keeps working, so nobody notices, and the answers quietly come from whichever model happens to be least expensive. That is how the `translate` command came to default to a four-billion-parameter model with nothing but a line in the terminal to say so.

Declare one **role** per distinct job your plugin does. Translating a document and translating a scan are two jobs: one needs to read text, the other needs to read a picture.

```python
# plugins/myplugin/src/settings.py
from src.runtime.model_role import ModelRole
from src.settings import plugin_settings

_s = plugin_settings(__file__, "myplugin")["myplugin"]

MYPLUGIN_ROLE = ModelRole(
    models=_s.get("models", ["gpt-4o", "gemini-2.5-pro", "gpt-4o-mini"]),
)
MYPLUGIN_SCAN_ROLE = ModelRole(
    models=_s.get("scan_models", ["gpt-5", "gpt-4o"]),
    requires_vision=True,      # it is reading a picture
)
```

```python
# plugins/myplugin/plugin.py
from src.settings import MYPLUGIN_ROLE, MYPLUGIN_SCAN_ROLE

class MyPlugin:
    model_roles = {"myplugin": MYPLUGIN_ROLE, "myplugin_scan": MYPLUGIN_SCAN_ROLE}
```

```python
# plugins/myplugin/src/services/my_service.py
class MyService(BaseService):
    model_role = MYPLUGIN_ROLE      # BaseService._get_model() reads this
```

That is the whole wiring. `BaseService._get_model()` honours a model named on the command line first, then works down your list, and only then falls back to the cheapest model that fits — logging which one it used and why.

**Why a list rather than one name.** Providers retire models. Naming a second and third choice means your command carries on working instead of stopping until someone reconfigures it. Every name should be one you'd be content to see used.

**Why read it from settings** rather than writing it into the code: that's what lets someone change it without editing your repository. See [Plugin settings](#plugin-settings).

**`requires_vision`** is enforced before price, so a job that reads pictures can never be handed a text-only model however cheap it is.

**A plugin that calls no AI model** says so explicitly with `model_roles = {}`. That's accepted — the point is that the decision was made rather than forgotten.

---

## Making your own files findable: `_register()`

Your plugin lives in `plugins/myplugin/`, but core looks for the pieces it wires up under fixed import paths in `src.*`. `_register()` loads one of your files from disk and files it under the name core expects — which is what lets `SandboxProcessor` find your code without core containing a single line about your plugin.

Copy this verbatim from `templates/plugin.py.template`:

```python
import importlib.util, sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent


def _register(module_name: str, rel_path: str, override: bool = False) -> None:
    """Make one of this plugin's files importable under the name core expects.

    Args:
        module_name: The name to register under (e.g.
                     ``'src.services.myplugin_service'``).
        rel_path: Where the file really is, relative to this plugin's own
                  directory.
        override: Normally ``False``: if another plugin already registered
                  this name, leave theirs alone. Pass ``True`` only if your
                  plugin is deliberately replacing another's module.
    """
    if module_name in sys.modules and not override:
        return
    path = _PLUGIN_DIR / rel_path
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    # Hang the module off its parent package too, so attribute-style access
    # (and pytest's monkeypatch) resolves it correctly.
    parts = module_name.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is not None:
            setattr(parent, parts[1], sys.modules[module_name])
```

**Call `_register()` at import time**, at the top of `plugin.py` before any import that needs the module — never inside `run()`. Core inspects what has been registered while it is being imported, and anything registered later is missed.

Register in dependency order (prompt fragments before the specs that use them, specs before services). `plugins/translation/plugin.py` is the worked example.

### The three names core looks for

| Register under | Core uses it for | Naming rule |
|----------------|------------------|-------------|
| `src.services.<name>` | Services `SandboxProcessor` instantiates on demand | `src.services.my_service` → attribute `sandbox.my_service` → class `MyService` inside it. Underscores become capitals: `foo_bar_service` → `FooBarService`. |
| `src.runtime.<name>` | Multi-step orchestration methods on the sandbox itself | The module must export a class named exactly `Mixin`. Every registered `Mixin` becomes a `SandboxProcessor` base class. |
| `pu_plugin.<name>.settings` | Your plugin's own settings constants | Any constant in it becomes importable as `from src.settings import YOUR_CONSTANT`. |

```python
_register("pu_plugin.myplugin.settings",   "src/settings.py")
_register("src.services.my_service",       "src/services/my_service.py")
_register("src.runtime.my_handler",        "src/runtime/my_handler.py")

from src.services.my_service import MyService   # now safe to import
```

Prefer a module name that includes your plugin's own name. Two plugins that register the same name without meaning to will silently get whichever loaded first.

---

## Using `SandboxProcessor`

Every plugin that makes API calls **must** go through `SandboxProcessor`. It resolves the professor's API key, creates the `TokenTracker`, wires up any alternate endpoint, and instantiates your service on first use — all in one place, so token tracking is structural rather than a convention someone can forget.

```python
def run(self, args, professor, model, temperature, top_p, max_tokens):
    # Imported here, not at the top of the file — see the note below.
    from src.runtime.sandbox_processor import SandboxProcessor

    sandbox = SandboxProcessor(
        professor,
        model=model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )
    result = sandbox.my_service.do_the_thing(...)
```

> **Import `SandboxProcessor` inside `run()`, never at module scope.** `SandboxProcessor`'s class statement discovers plugin-registered `Mixin` classes at the moment it is first imported. Importing it at the top of a plugin file would run that discovery while plugins are still loading, and any plugin that hadn't loaded yet would lose its orchestration methods. Nothing in `src/` imports it at module scope either, for the same reason.

If `model` contains a colon (e.g. `"my_cluster:llama-3-70b"`), `SandboxProcessor` loads the matching `[endpoints.<name>]` definition plus its credential from `settings.toml`, points the client at that `base_url`, and bypasses the model catalog. You get alternate-endpoint routing without writing anything for it.

---

## Plugin settings

Put your tuneable defaults in `plugins/myplugin/settings.toml` under sections named after your plugin:

```toml
# plugins/myplugin/settings.toml
[myplugin]
temperature = 0.5
max_tokens = 4000
```

Read them in `src/settings.py` by walking up from `__file__` to the nearest `settings.toml` that contains one of your sections, and expose each value as a module-level constant:

```python
# plugins/myplugin/src/settings.py
"""My plugin's settings — loaded from the nearest settings.toml with our sections."""

import tomllib
from pathlib import Path

_PLUGIN_SECTIONS = ("myplugin",)


def _load_settings() -> dict:
    p = Path(__file__).resolve().parent
    while p != p.parent:
        candidate = p / "settings.toml"
        if candidate.exists():
            with candidate.open("rb") as f:
                data = tomllib.load(f)
            if any(k in data for k in _PLUGIN_SECTIONS):
                return data
        p = p.parent
    return {}


_s = _load_settings().get("myplugin", {})

MYPLUGIN_TEMPERATURE: float = _s.get("temperature", 0.5)
MYPLUGIN_MAX_TOKENS: int = _s.get("max_tokens", 4000)
```

Register it as `pu_plugin.myplugin.settings` and your constants become importable from `src.settings` — `src/settings.py`'s `__getattr__` searches every registered `pu_plugin.*.settings` module:

```python
_register("pu_plugin.myplugin.settings", "src/settings.py")

from src.settings import MYPLUGIN_TEMPERATURE   # works anywhere in the project
```

Give every constant a name that includes your plugin's. If two plugins define the same constant name, the first one found wins and a warning names both — which one that is depends on load order rather than anything meaningful.

Your defaults are the starting point, not the last word. They layer the same way everything else does — your `settings.toml`, then a shared settings file if one is configured, then the person's `preferences.toml`, which wins. So someone can raise your worker count on one machine without editing a file that belongs to your repository.

That layering is why the loader takes `__file__` and your section names rather than you reading the file yourself: your plugin knows which sections it owns, and the package knows where the other layers live.

**Write your comments for the person using the sandbox, not for you.** On every run the package copies each setting in your `settings.toml` into their `preferences.toml`, commented out, *and brings your comments with it*. Whatever you write above or beside a setting is what a Princeton faculty member reads when deciding whether to change it — so `# Fraction of the previous page passed as context (0.0–1.0)` earns its keep, and `# see spec` does not. They never open your file; they read your words in theirs.

---

## Registering languages

Call `register_language()` at module import time — at the top level of `plugin.py`, not inside a class or function:

```python
from src.config import register_language

register_language("jp", "Japanese")
register_language("zh", "Chinese")
```

This fills in `LANGUAGE_MAP`, which the argparse type-hooks (`parse_language_code`, `parse_single_language_code`) validate against. Plugins are guaranteed to load before the parser is built, so every registered code is available by the time a command line is parsed.

---

## Writing an extension plugin

To add languages to an existing command like `translate`:

1. **Declare `handles`** — the languages you own:

   ```python
   class MyTranslationPlugin:
       commands: list[str] = ["translate"]      # same as the base plugin's
       handles: list[str] = ["jp", "zh"]        # what you own
   ```

   `DispatchPlugin` routes on `args.language_code[0]`, so `handles` has to
   hold whatever that argument parses to — and that differs by command.
   `translate` takes a pair and parses to **short codes** (`"jp"`), while
   `transcribe` takes one language and parses to a **full name**
   (`"Japanese"`). Compare `plugins/translation/plugin.py`'s
   `handles = ["en"]` with `plugins/transcription/plugin.py`'s
   `handles = ["English"]`. Get this wrong and nothing errors — your plugin
   simply never gets asked to do anything.

2. **Implement `register_command_flags(parser)`, not `register_subparsers()`.** The loader calls it to append your flags to the shared parser. Don't re-add `language_code` or anything the base plugin already defines.

   ```python
   def register_command_flags(self, parser: argparse.ArgumentParser) -> None:
       parser.add_argument("--my-option", action="store_true", help="...")
   ```

3. **Skip the `_register()` block.** The base plugin registers the shared service modules, and plugins load alphabetically, so `translation` is always loaded before `translation-ea`. You can write a plain `import src.services.translation_service`.

4. **Delegate to the base plugin's shared executor:**

   ```python
   def run(self, args, professor, model, temperature, top_p, max_tokens):
       import sys
       from src.runtime.sandbox_processor import SandboxProcessor
       from src.config import LANGUAGE_MAP

       sandbox = SandboxProcessor(professor, model=model, temperature=temperature,
                                  top_p=top_p, max_tokens=max_tokens)
       if getattr(args, "my_option", False):
           sandbox.translation_service.variant_notes.append(MY_NOTE)

       _base = sys.modules["pu_plugin.translation.plugin"]
       _base._execute_translate(
           sandbox, args,
           LANGUAGE_MAP[args.language_code[0]],
           LANGUAGE_MAP[args.language_code[1]],
       )
   ```

   Each entry in `variant_notes` is a plain string appended to the model's system prompt as its own additional-instructions block. Order is preserved.

5. **Optionally implement `get_peer_guidance(token)`** to contribute destination-side conventions when your language is the *target* rather than the source. Return a string or `None`:

   ```python
   def get_peer_guidance(self, token: str) -> Optional[str]:
       if token == "jp":
           return "Use kanji level appropriate for academic audiences."
       return None
   ```

---

## A button in the web interface

The web interface's chat composer can offer your command as a one-click background job: a button next to the message box that opens a small form, runs the job in the background, shows per-page progress, and posts a download link into the conversation when it finishes.

This is entirely optional and changes nothing about your command's behaviour at the command line. Declare it only if your command is the kind of page-by-page job that suits it and produces **one finished file** — `translate` and `transcribe` are the two worked examples.

### 1. Declare the action

```python
from src.runtime.ui_action import UiAction, UiField, UiJobResult

ui_action = UiAction(
    id="mycommand",              # unique; routes a job-start request back to you
    label="Do the thing",        # shown in the composer's action picker
    command="mycommand",         # informational only — the webui calls
                                 # run_ui_action() directly, never argparse
    progress_verb="Processing",  # "Processing... 3 of 12 done."
    fields=[
        UiField(name="target_language", label="Target language", kind="language"),
        UiField(name="file", label="Document", kind="file"),
        UiField(name="notes", label="Notes", kind="text", required=False,
                group="Options"),
    ],
)
plugin.ui_action = ui_action
```

`progress_verb` is a plain string rather than something derived from `label`, because English gerunds aren't a mechanical transformation (`"translate" + "ing"` gives "Translateing"). It defaults to `"Processing"`.

**`UiField` kinds:** `language` (a select populated from the language registry), `file`, `checkbox`, `text` (single- or multi-line), `select` (populated from `choices`, each `{"value": ..., "label": ...}`). The web interface renders purely off `kind` — it never needs plugin-specific knowledge to build a form.

**Other `UiField` options:** `required` (default `True`), `group` (a section heading printed above this field when it differs from the previous field's — purely cosmetic, but it turns a long list into readable sections), and `allow_folder` (for `kind="file"` only: adds a folder option beside the single-file one, the same way pointing the CLI's `-i` at a folder of images processes every image in it — the browser asks which they want, since one file input cannot do both). Files chosen that way arrive together in one scratch folder whose path reaches `fields["file_path"]`; read from it, don't expect it to outlive the job.

### 2. Implement `run_ui_action`

```python
def run_ui_action(self, fields, professor, model, on_progress, output_dir,
                  on_page_text=None) -> UiJobResult:
    """Run this action as a background job instead of a CLI invocation.

    Args:
        fields: The submitted form values, keyed by each UiField's ``name``.
        professor: Whose API key and budget this job spends.
        model: The model chosen in the composer, or ``None``.
        on_progress: ``Callable[[completed, total], None]`` or ``None`` — call
                     it as each page or image finishes so the composer can
                     show live progress.
        output_dir: A directory the webui has created and guarantees is
                    writable. Your one output file must be written under it;
                    the webui owns naming and cleanup for that directory.
        on_page_text: ``Callable[[page_number, text], None]`` or ``None`` —
                      call it with each page's finished text (1-indexed) so
                      the conversation can show output as it arrives. This is
                      what the CLI prints straight to the terminal.
    """
    ...
    return UiJobResult(
        output_path=...,           # absolute path to the one finished file
        output_filename=...,       # the name to present on download
        summary="Translated 12 pages, Japanese -> English",
        prompt_tokens=..., completion_tokens=..., cost=...,   # all optional
    )
```

The token and cost fields are optional but worth filling in: they let someone see that a job used its full response budget on some page, which the summary text alone can't show. `SandboxProcessor`'s tracker exposes `get_session_usage()` for exactly this — a running total scoped to one instance's lifetime.

There is no resume. If a job is interrupted, the escape valve is a page-range field, which is why `translate` includes one.

### 3. Optionally implement `preview_ui_action`

```python
def preview_ui_action(self, fields, professor, model) -> UiPromptPreview:
    ...
    return UiPromptPreview(system_prompt=..., user_prompt=..., model=...,
                           note="Image content would be attached to the user message")
```

This is `--dry-run` made interactive: the composer calls it after **every** change to the form and shows the result in a live prompt preview pane. So it must be cheap, must never make an API call, and must tolerate blank or half-filled forms — fall back to placeholder text rather than raising. It's fully independent of the pair above; a plugin can declare `ui_action` and skip the preview.

### 4. Extension plugins: contributing fields to someone else's action

An extension plugin never gets its own composer entry — only the base plugin's `ui_action` is exposed. It can still add fields to the base plugin's form, shown as a subsection that appears once the professor picks a language the extension owns:

```python
from src.runtime.ui_action import UiField, register_extension_ui_hooks


def _apply_kanbun(sandbox, fields):
    if str(fields.get("kanbun", "")).strip().lower() in ("true", "1", "on", "yes"):
        sandbox.translation_service.variant_notes.append(KANBUN_NOTE)


register_extension_ui_hooks(
    action_id="translate",     # must match the owning UiAction's id
    token="jp",                # a code from your own `handles`
    fields=[UiField(name="kanbun", label="Use Kanbun reading conventions",
                    kind="checkbox", required=False, group="Japanese (kanbun)")],
    apply=_apply_kanbun,
)
```

Call it once at import time, the same way you call `register_language()`. `apply(sandbox, fields)` runs just before the job starts, with the fully-constructed `SandboxProcessor` — so it can do anything `_execute_translate` can. It must not raise on a blank or default value: it is called on every job for that action and language, not only when something was changed.

`action_id` is required alongside `token` because two different actions can legitimately register the same language code for unrelated fields (`translate`'s Kanbun checkbox and `transcribe`'s vertical-text options both apply to `jp`). Keying by token alone would let whichever plugin imported last silently overwrite the other.

Which side of the job `token` refers to depends on the action: the *destination* language for `translate`, the OCR language for `transcribe`.

---

## Testing

Put tests in `plugins/myplugin/tests/` and **add that path to `testpaths` in the root `pytest.ini`** — discovery is by explicit list, not automatic:

```ini
[pytest]
testpaths = tests plugins/translation/tests plugins/prompt/tests plugins/transcription/tests plugins/webui/tests plugins/myplugin/tests
```

`plugin.py` itself is not pre-registered for tests, so a test that needs it loads it the same way `_register()` does. Add a `plugins/myplugin/conftest.py` mirroring your plugin's own registrations if your tests import your service or handler modules directly — `plugins/translation/conftest.py` is the pattern.

Use the core `conftest.py`'s `_use_template_catalog` fixture so tests never touch the real model catalog:

```python
# plugins/myplugin/tests/conftest.py
import pytest
from tests.conftest import _use_template_catalog   # re-export from core


@pytest.fixture(autouse=True)
def use_template_catalog(_use_template_catalog):
    pass
```

---

## Documentation

The people using this sandbox are Princeton faculty from non-CS disciplines. Write your docstrings for a colleague in your own department, not for a programmer: open with one plain-English sentence on *what* the function does rather than how, explain each parameter in terms the caller cares about, and define technical terms inline. The "Documentation & docstring standard" section of `CLAUDE.md` has the full standard and a worked example.

---

## Checklist

**Every plugin**

- [ ] `plugins/myplugin/plugin.py` exists, with `plugin = MyPlugin()` at module level
- [ ] `commands` declared on the class
- [ ] Every owned module registered with `_register()` at import time, in dependency order
- [ ] `SandboxProcessor` used in `run()` — never create a `TokenTracker` or a service by hand
- [ ] `SandboxProcessor` imported *inside* `run()`, not at module scope
- [ ] Languages registered with `register_language()` at module level
- [ ] Nothing in `src/` had to change
- [ ] Tests in `plugins/myplugin/tests/`, and that path added to `pytest.ini`
- [ ] `python main.py --help` lists your command, and `python main.py <netid> mycommand --dry-run` runs without spending anything

**Standalone plugins**

- [ ] `register_subparsers()` implemented
- [ ] `handles` *not* declared, unless this plugin is also the dispatch primary
- [ ] (Optional) `requires_professor = False` if the command doesn't spend one person's budget

**Extension plugins**

- [ ] `handles` declared in the form the base command parses to — short codes for `translate`, full names for `transcribe`
- [ ] `register_command_flags()` implemented — **not** `register_subparsers()`
- [ ] No `_register()` calls — the base plugin handles those
- [ ] `run()` delegates to the base plugin's shared executor (e.g. `_execute_translate`)
- [ ] (Optional) `get_peer_guidance()` and `register_extension_ui_hooks()` for destination-side behaviour

**Web interface entry (optional)**

- [ ] `ui_action` declared and assigned to the plugin object
- [ ] `run_ui_action()` returns a `UiJobResult` pointing at one file under `output_dir`
- [ ] `on_progress` and `on_page_text` called if your execution path already supports them
- [ ] (Optional) `preview_ui_action()` — cheap, no API calls, tolerant of blank fields
