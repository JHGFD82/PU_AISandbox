# Prompt Plugin

Adds the `prompt` command: send a question straight to an AI model, with no translation or transcription framing around it. Ships with the main repository.

> **This file is a signpost, not a reference.** Nothing important is documented only here. It exists so that someone who opens this folder looking for the real information finds the way to it.

| Looking for | It's in |
|---|---|
| How to run the command, and every flag it takes | [`docs/cli-reference.md`](../../docs/cli-reference.md#prompt--a-freeform-question) |
| The defaults it uses (temperature, response length, the system prompt) | [`docs/configuration.md`](../../docs/configuration.md#prompt) — the `[prompt]` section of `settings.default.toml` |
| What token usage costs, and how it's recorded | [`docs/token-usage-guide.md`](../../docs/token-usage-guide.md) |
| How to write a plugin of your own | [`docs/plugin-authoring-guide.md`](../../docs/plugin-authoring-guide.md) |
| How plugins fit into the whole | [`docs/architecture.md`](../../docs/architecture.md) |
| What this plugin's own code does | the module docstring at the top of [`plugin.py`](plugin.py) |

## What's particular to this one

This is the **reference plugin**. It's a real, working plugin, written to be read — the shortest complete example of the contract every other plugin follows. If you're starting a new plugin, copy `templates/plugin.py.template` for an annotated skeleton and read `plugin.py` here for a finished one.

Unlike `translation` and `transcription`, it owns a single command, registers a single service, has no settings file of its own, and takes part in no dispatch. That is what makes it worth reading first.
