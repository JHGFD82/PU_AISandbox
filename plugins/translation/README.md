# Translation Plugin (base)

Adds the `translate` command, which translates a document from one language into another. Ships with the main repository; covers English.

> **This file is a signpost, not a reference.** Nothing important is documented only here. It exists so that someone who opens this folder looking for the real information finds the way to it.

| Looking for | It's in |
|---|---|
| How to run the command, and every flag it takes | [`docs/cli-reference.md`](../../docs/cli-reference.md#translate--document-translation) |
| Which file formats go in and come out | [`docs/cli-reference.md`](../../docs/cli-reference.md#input) |
| The defaults it uses (temperature, response length, rolling context) | [`docs/configuration.md`](../../docs/configuration.md#translation--pluginstranslationsettingstoml) — this plugin's own `settings.toml` |
| How to add another language to `translate` | [`docs/plugin-authoring-guide.md`](../../docs/plugin-authoring-guide.md#writing-an-extension-plugin) |
| What token usage costs, and how it's recorded | [`docs/token-usage-guide.md`](../../docs/token-usage-guide.md) |
| How plugins fit into the whole | [`docs/architecture.md`](../../docs/architecture.md) |
| What this plugin's own code does | the module docstring at the top of [`plugin.py`](plugin.py), which doubles as the extension-author walkthrough |

## What's particular to this one

**It's the service owner.** It registers `TranslationService`, `ImageTranslationService`, the prompt-building classes and the translation prompt text under the shared `src.*` names, so a language extension such as `translation-ea` uses this implementation rather than bundling a copy of its own. It also holds `_execute_translate()`, the shared execution logic every translation plugin delegates to once it has done its own language-specific setup.

**It's also the English handler**, so `translate en-jp` routes here, and it supplies English destination-side guidance when English is the target.

**Dispatch.** With an extension such as `plugins/translation-ea/` installed, the loader merges both into a `DispatchPlugin` and routes each request by its source language. An extension must **not** call `register_subparsers()` — `translate` already exists here, and registering it twice is a conflict that makes the loader silently skip the extension. Extensions hook in with `handles` and `register_command_flags()` instead. `handles` here holds short codes (`["en"]`), because `translate` takes a language *pair*; `transcribe` differs.
