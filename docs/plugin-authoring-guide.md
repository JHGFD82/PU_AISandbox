# Plugin Authoring Guide

Plugins are self-contained directories under `plugins/`. The core never needs to change when you add one.

---

## Plugin Types

There are two kinds of plugins:

**Standalone plugins** introduce a new, independent CLI command. They implement `register_subparsers()` to create the command parser, and their `run()` is called directly by the CLI router. The built-in `prompt`, `translation`, and `transcription` plugins are all standalone plugins.

**Extension plugins** extend an existing command with additional language or domain support. Instead of introducing a new command, they declare `handles` (the source-language shortcodes they own) and implement `register_command_flags()` to append their flags to the base plugin's shared parser. The plugin loader detects the `handles` overlap and automatically merges them into a `DispatchPlugin` at startup. `translation-ea` and `transcription-ea` are examples of extension plugins.

> **Extension plugins must not call `register_subparsers()`.** They hook into the base standalone plugin's parser — they must never register a parallel command. Doing so would cause the plugin to conflict with the base plugin and be silently skipped.

---

## Quick Start

```bash
cp -r plugins/prompt plugins/myplugin
# edit plugins/myplugin/plugin.py
```

The plugin loader discovers `plugins/*/plugin.py` at startup. There is no registration step.

---

## Minimal Plugin Structure

```
plugins/myplugin/
├── plugin.py          # required — loader entry point
└── src/
    └── services/
        └── my_service.py   # optional — inject via sys.modules
```

---

## The Plugin Contract

Your `plugin.py` must expose a module-level attribute named `plugin` that is an instance of a class with three members:

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

The loader checks for `commands`, `register_subparsers`, and `run`. Anything else is optional.

---

## Token Tracking (Mandatory)

Every plugin that makes API calls **must** create a `TokenTracker` and pass it to every service. This is enforced by convention, not code — omitting it causes silent billing gaps.

```python
from src.tracking.token_tracker import TokenTracker

def run(self, args, professor, model, temperature, top_p, max_tokens):
    api_key, _ = get_api_key(professor)
    token_tracker = TokenTracker(professor=professor)   # MANDATORY

    svc = MyService(
        api_key, professor,
        token_tracker=token_tracker,   # pass it here
        model=model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )
    ...
```

---

## Adding a Service

If your plugin has its own service class, inject it into `sys.modules` at the top of `plugin.py`, before any imports that need it:

```python
import importlib.util, sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent

def _register(module_name: str, rel_path: str) -> None:
    if module_name in sys.modules:
        return
    path = _PLUGIN_DIR / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)

_register("src.services.my_service", "src/services/my_service.py")
from src.services.my_service import MyService   # now safe to import
```

`SandboxProcessor.__getattr__` will also auto-instantiate the service on first attribute access, following the naming convention `my_service` → `MyService`. So you can also do:

```python
sandbox = SandboxProcessor(professor, model=model, ...)
sandbox.my_service.do_something(...)   # lazily instantiated
```

---

## Writing an Extension Plugin (DispatchPlugin)

To add language support to an existing command like `translate`, write an **extension plugin** — one that hooks into the existing `translate` command rather than registering a new one:

1. **Declare `handles`** — the shortcodes your plugin owns as source languages:

   ```python
   class MyTranslationPlugin:
       commands: list[str] = ["translate"]
       handles: list[str] = ["jp", "zh"]   # shortcodes you own
   ```

2. **Do not implement `register_subparsers()`.** Extension plugins must implement `register_command_flags(parser)` instead. The loader calls this to append your flags to the shared parser. Implementing `register_subparsers()` would create a command conflict and your plugin would be silently dropped.

   ```python
   def register_command_flags(self, parser: argparse.ArgumentParser) -> None:
       parser.add_argument("--my-option", ...)
   ```

3. **Skip the sys.modules injection block.** The base translation plugin registers shared service modules. Load order is alphabetical, so `translation` always loads before `translation-ea`.

4. **Delegate to the shared executor:**

   ```python
   def run(self, args, professor, model, temperature, top_p, max_tokens):
       import sys
       _base = sys.modules.get("pu_plugin.translation.plugin")
       source_language = src.config.LANGUAGE_MAP[args.language_code[0]]
       target_language = src.config.LANGUAGE_MAP[args.language_code[1]]
       _base._execute_translate(sandbox, args, source_language, target_language)
   ```

5. **Optionally implement `get_peer_guidance(token)`** to contribute destination-side conventions when your language appears as a translation target. Return a string or `None`:

   ```python
   def get_peer_guidance(self, token: str) -> Optional[str]:
       if token == "jp":
           return "Use kanji level appropriate for academic audiences."
       return None
   ```

---

## Registering Languages

Call `register_language()` at module import time (not inside a class or function):

```python
from src.config import register_language

register_language("jp", "Japanese")
register_language("zh", "Chinese")
```

This populates `LANGUAGE_MAP` before argparse validates language codes. Because plugins load before the argument parser is built, all registered codes are available to the parser.

---

## Plugin Settings

Add a `settings.toml` to your plugin directory. Your plugin's `src/settings.py` can load it by walking up from `__file__`:

```toml
# plugins/myplugin/settings.toml
[myplugin]
default_passes = 2
```

```python
# plugins/myplugin/src/settings.py
from pathlib import Path
import tomllib

_settings_file = Path(__file__).parent.parent / "settings.toml"

def _load():
    if _settings_file.exists():
        with open(_settings_file, "rb") as f:
            return tomllib.load(f)
    return {}

_data = _load()

def get(section: str, key: str, default=None):
    return _data.get(section, {}).get(key, default)
```

---

## Testing

Place tests in `plugins/myplugin/tests/`. The root `pytest.ini` auto-discovers them. Use the `_use_template_catalog` fixture from the core `conftest.py` to avoid touching the real `model_catalog.json`:

```python
# plugins/myplugin/tests/conftest.py
import pytest
from tests.conftest import _use_template_catalog  # re-export from core

@pytest.fixture(autouse=True)
def use_template_catalog(_use_template_catalog):
    pass
```

---

## Checklist

**All plugins:**
- [ ] `plugin.py` at `plugins/myplugin/plugin.py`
- [ ] Module-level `plugin = MyPlugin()` at the bottom of `plugin.py`
- [ ] `commands` list declared on the class
- [ ] `TokenTracker(professor=professor)` created in `run()` and passed to every service
- [ ] Services injected into `sys.modules` before any import that needs them
- [ ] Languages registered via `register_language()` at module level
- [ ] Tests in `plugins/myplugin/tests/`

**Standalone plugins** (new independent command):
- [ ] `register_subparsers()` implemented
- [ ] Do *not* declare `handles` unless this plugin is also intended as a dispatch primary

**Extension plugins** (extending an existing command):
- [ ] `handles` declared with the source-language shortcodes this plugin owns
- [ ] `register_command_flags()` implemented — **not** `register_subparsers()`
- [ ] No `sys.modules` injection — the base standalone plugin handles that
- [ ] `run()` delegates to the base plugin's shared executor (e.g. `_execute_translate`)
