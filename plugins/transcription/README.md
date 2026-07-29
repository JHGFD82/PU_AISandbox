# Transcription Plugin (base)

Adds the `transcribe` command, which reads the text off an image or a folder of images, and `transcription_review`, which checks an earlier transcription for mistakes. Ships with the main repository; covers English.

> **This file is a signpost, not a reference.** Nothing important is documented only here. It exists so that someone who opens this folder looking for the real information finds the way to it.

| Looking for | It's in |
|---|---|
| How to run the commands, and every flag they take | [`docs/cli-reference.md`](../../docs/cli-reference.md#transcribe--reading-text-off-images) and [`transcription_review`](../../docs/cli-reference.md#transcription_review--checking-a-transcription) |
| The defaults it uses (temperature, response length, penalties) | [`docs/configuration.md`](../../docs/configuration.md#transcription--pluginstranscriptionsettingstoml) — this plugin's own `settings.toml` |
| How to add another language to `transcribe` | [`docs/plugin-authoring-guide.md`](../../docs/plugin-authoring-guide.md#writing-an-extension-plugin) |
| What token usage costs, and how it's recorded | [`docs/token-usage-guide.md`](../../docs/token-usage-guide.md) |
| How plugins fit into the whole | [`docs/architecture.md`](../../docs/architecture.md) |
| What this plugin's own code does | the module docstring at the top of [`plugin.py`](plugin.py) |

## What's particular to this one

**It owns two commands.** `transcribe` and `transcription_review` are both registered here, and share the OCR service layer. That's why the plugin can appear in the loader's map twice, wrapped in two different `DispatchPlugin` objects when a language extension is installed — see `jobs.list_ui_actions()`'s note on deduplicating by action rather than by wrapper.

**It's the service owner.** It registers `ImageProcessorService`, `TranscriptionReviewService` and the OCR prompt classes under the shared `src.*` names, so a language extension such as `transcription-ea` uses this implementation rather than bundling a copy of its own.

**`handles` here holds full language names, not short codes.** `transcribe` takes a single language, which the argument parser resolves to a name before `DispatchPlugin` routes on it — so this plugin declares `handles = ["English"]`, where `translation` declares `handles = ["en"]`. Worth knowing before writing an extension: getting it wrong raises nothing, it just means your plugin never gets asked to do anything.
