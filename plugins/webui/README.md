# Web Interface Plugin

Adds the `webui` command: a browser interface for people who would rather not work at a terminal. A chat window, a spending sidebar, a settings page, and a form for running translation or transcription jobs on whole documents. Ships with the main repository.

> **This file is a signpost, not a reference.** Nothing important is documented only here. It exists so that someone who opens this folder looking for the real information finds the way to it.

| Looking for | It's in |
|---|---|
| How to start it, and who can reach it | [`README.md`](../../README.md#the-web-interface) — start there if you just want to use it |
| Its subcommands and flags | [`docs/cli-reference.md`](../../docs/cli-reference.md#webui--the-browser-interface) |
| Its own settings (cookie name, conversation compaction) | [`docs/configuration.md`](../../docs/configuration.md#webui--pluginswebuisettingstoml) |
| What the `/settings` page can and can't edit | [`docs/configuration.md`](../../docs/configuration.md#settings-at-a-glance) |
| How to give your own plugin a form in the composer | [`docs/plugin-authoring-guide.md`](../../docs/plugin-authoring-guide.md#a-button-in-the-web-interface) |
| How plugins fit into the whole | [`docs/architecture.md`](../../docs/architecture.md) |
| What this plugin's own code does | the module docstrings at the top of [`plugin.py`](plugin.py), [`src/app.py`](src/app.py), [`src/jobs.py`](src/jobs.py) and [`src/auth.py`](src/auth.py) |

## What's particular to this one

**It's the only plugin that isn't scoped to one person.** It sets `requires_professor = False`, so `python main.py webui serve` takes no netID — which professor is active is chosen later, in the browser. Every other command spends one person's budget and says so on the command line.

**It renders other plugins' forms without knowing anything about them.** A plugin that declares a `ui_action` gets a button in the chat composer; the front end builds the form purely from the declared field kinds. Nothing in this folder needs changing to add one.

**Job state is deliberately in memory only.** Restarting the server ends any job that was mid-run, and the conversation says so rather than sitting there looking busy. There's no resume — the escape valve is the page-range field on every job form.

**One gate, not per-person login.** A single passphrase unlocks the whole local server. Serving on anything other than `127.0.0.1` requires one to be set; the server refuses to start otherwise, because opening the port without it would let anyone who can reach it read every conversation and spend every configured budget.
