# Plugins

Loaded when working with anything under `plugins/`. The two rules that also
bite from core code — the `SandboxProcessor` import-timing rule, and "adding a
plugin never requires changing `src/`" — stay in the root `CLAUDE.md`, because
they matter when editing `src/runtime/` too, where this file isn't loaded.

`templates/plugin.py.template` is the annotated skeleton to copy; `plugins/prompt/` is the complete working reference. [`docs/plugin-authoring-guide.md`](../docs/plugin-authoring-guide.md) is the full walkthrough.

## The contract

- A plugin is a directory `plugins/<name>/plugin.py` exposing a module-level `plugin` object satisfying the `ModePlugin` protocol (`src/runtime/plugin.py`, structural typing — no base class needed): `commands: list[str]`, `register_subparsers(subparsers)`, `run(args, professor, model, temperature, top_p, max_tokens)`.
- Optional members, all read with `getattr()` rather than declared on the protocol: `requires_professor`, `handles`, `register_command_flags`, `get_peer_guidance`, and the web-interface trio `ui_action` / `run_ui_action` / `preview_ui_action` (`src/runtime/ui_action.py`).
- Plugins call `register_language(code, name)` (`src/config.py`) at import time to populate `LANGUAGE_MAP`, which argparse type-hooks validate against — this is why plugins must be loaded before the parser is built (the loader guarantees ordering).

## Which plugins are which

`plugins/translation/`, `plugins/transcription/` and `plugins/webui/` are bundled (tracked in this repo; translation and transcription cover base English only). `plugins/translation-ea/` and `plugins/transcription-ea/` are optional East Asian-language extensions living in **separate git repos** (git-ignored here) — treat them as reference only, extending the matching bundled plugin's patterns rather than editing in place.

If two plugins claim the same command and both declare a `handles` list, `src/runtime/dispatch_plugin.py` builds a `DispatchPlugin` that routes by `args.language_code[0]` to the owning plugin (e.g. `translate` shared between `translation` and `translation-ea`), instead of raising a conflict. `handles` holds whatever that argument parses to, which differs by command: short codes for `translate`, full language names for `transcribe`.

## The three registration names

- **Plugins own their service modules.** Because `src/services/` no longer ships translation/transcription logic, a plugin must inject its service file into `sys.modules` at import time (see the `_register()` pattern in `docs/architecture.md`) so `SandboxProcessor.__getattr__` can find and lazily instantiate it — e.g. attribute `translation_service` maps to `sys.modules["src.services.translation_service"].TranslationService`.
- **Plugins own their command-orchestration methods too** (e.g. `translate_document`, `process_image`) via the same convention under a `"src.runtime.<name>"` key instead of `"src.services.<name>"`. The registered module exports a class named `Mixin`; `SandboxProcessor`'s class statement discovers every such `Mixin` in `sys.modules` and includes it as a base class at import time (see `_discover_plugin_mixins()` in `src/runtime/sandbox_processor.py`, and "The same convention for orchestration methods" in `docs/architecture.md`). Only `_FileTypeMixin` (file-type detection, needed by every mode) and `_CommandMixin` (interactive helpers) are core and always present.
- A module registered as `pu_plugin.<name>.settings` has its constants exposed through `src/settings.py`'s `__getattr__`, so `from src.settings import SOME_CONSTANT` reaches a plugin's own settings without `src/` naming any plugin.
