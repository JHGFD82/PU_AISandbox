# PU AI Sandbox

[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FJHGFD82%2FPU_AISandbox%2Fmain%2Fpyproject.toml&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![codecov](https://codecov.io/gh/JHGFD82/PU_AISandbox/branch/main/graph/badge.svg)](https://codecov.io/gh/JHGFD82/PU_AISandbox)

Modular AI platform for Princeton University faculty. Commands are implemented as plugins, so capabilities can be added or updated independently without touching core code.

> **Access requirement:** Each user must have a valid Princeton University AI Sandbox API key (available through OIT). This tool is for Princeton faculty and authorized delegates only.

## Architecture

```text
main.py
  → src/cli.py            controller + argument parser; loads plugins, routes commands
    → src/config.py       language registry, netID lookup, optional-setting registry
    → src/settings.py     layered settings (defaults → shared → preferences → plugin → flags)
    → src/paths.py        where this person's own files live
    → src/first_run.py    what setup creates, and what it must never overwrite
  → src/runtime/
      plugin_loader.py    discovers plugins/*/plugin.py at startup
      info_commands.py    built-in: --list-models, usage subcommands
      sandbox_processor.py  shared service wiring used by plugins
  → src/services/         shared plumbing every AI service builds on — BaseService
                          (API client, retries, usage recording), error handling
  → src/models/           model catalog, pricing, model-name resolution
  → src/processors/       document ingestion (PDF, DOCX, TXT, MD, JSON, XLSX, image)
  → src/tracking/         per-professor token accounting and budget reporting
  → src/output/           text / Markdown / PDF / Word / Excel / JSON output
  → plugins/
      prompt/             bundled plugin (ships with this repo)
      translation/        bundled plugin — base English translation (ships with this repo)
      transcription/      bundled plugin — base English OCR (ships with this repo)
      webui/              bundled plugin — the browser interface (ships with this repo)
      translation-ea/     optional: separate git repo — EA language translation
      transcription-ea/   optional: separate git repo — EA language OCR
```

The AI services themselves — `TranslationService`, `ImageProcessorService` and the
rest — live inside the plugin that owns them, not in `src/`. `src/services/` keeps
only what all of them share. That is what lets a new capability be added without
editing core.

`src/cli.py` decides *what* to run. Plugins decide *how* to run it. Only the `usage` subcommand is built in.

For deeper documentation, see the `docs/` folder:

- [`docs/architecture.md`](docs/architecture.md) — request lifecycle, component descriptions, data-flow diagrams
- [`docs/cli-reference.md`](docs/cli-reference.md) — full flag reference for all commands
- [`docs/configuration.md`](docs/configuration.md) — where your files live, and the schema for `settings.toml`, `model_catalog.json` and `settings.default.toml`
- [`docs/token-usage-guide.md`](docs/token-usage-guide.md) — token tracking, usage commands, budget settings, and troubleshooting
- [`docs/plugin-authoring-guide.md`](docs/plugin-authoring-guide.md) — step-by-step guide to writing new plugins

---

## Getting Started

### The short version

```bash
git clone https://github.com/JHGFD82/PU_AISandbox.git
cd PU_AISandbox
python3 start.py
```

That's it. `start.py` does everything else: finds a Python new enough to run the sandbox, installs what it needs, asks where to keep your files, and opens the web interface in your browser.

The first time, it tells you what it is about to install and waits for you to press return, so nothing several minutes long starts by surprise. Then a page opens asking where to keep your files. Answer it and the same window moves straight on to the sandbox. (If you would rather answer at the command line, `python main.py settings setup` asks the same thing — but nothing makes you.)

The first run takes a few minutes — about 200 MB of software is downloaded. Every run after that reaches the web interface in about a second, so this is also the normal way to open the sandbox day to day.

Leave the terminal window open while you're using it. Closing it, or pressing Ctrl-C, stops the sandbox.

### If it says you need a newer Python

Macs come with Python 3.9, and the sandbox needs 3.11 or newer. `start.py` looks for a newer one already on your computer and uses it if there is one — so this only comes up if there genuinely isn't.

If it does, it will tell you exactly what to do: install Python from [python.org/downloads](https://www.python.org/downloads/) (or `brew install python@3.13` if you use Homebrew), then run `python3 start.py` again. Installing a newer Python alongside the old one is safe and won't disturb anything.

### Adding people

Once the sandbox is running, add whoever will be using it. When there is nobody configured yet, the web interface opens straight onto its Settings page for exactly this reason — fill in the **Professors** panel and press *Add professor*.

The same thing at the command line:

```bash
python main.py settings add-professor
```

Either way it asks for three things:

| | |
|---|---|
| **NetID** | The university username they sign in with — `jh43`. Letters and digits only, not their name and not an email address. This is how the sandbox tells one person from another: it picks their API key, names their usage file, and is what you type to run a command as them. It can't be changed later without removing them and adding them again. |
| **Display name** | `Jeff Heller`. Only ever shown to people — in reports and in the person picker — so write it however reads best. |
| **API key** | Princeton faculty obtain these through OIT; each person registers independently. A backup key is optional, and gets used automatically if the primary one ever stops working. |

Keys are never displayed once saved — the settings page shows only whether one is set. At the command line they're typed hidden, never as a flag.

### Adding a model

A fresh copy has no models in it. Which ones you can use depends on your
institution's AI sandbox rather than on this software, so the list is not
something this repository can ship — **check Princeton's own AI Sandbox
documentation for the models currently offered**, and add the ones you want.

On the Settings page, under **Models**, type a name in the form
`provider/model` — `openai/gpt-4o`, `anthropic/claude-opus-4-8`,
`google/gemini-3-pro-preview` — and press *Add and test*. The price is looked
up, and the model is then asked a few one-token questions to find out what it
can do: whether it can read a scanned page, and which settings it turns down.
That takes a few seconds and a fraction of a cent, once.

If you only ever use an alternate endpoint of your own — a departmental cluster,
say — you need no models here at all. See
[Alternate endpoints](docs/configuration.md#alternate-ai-endpoints).

### Your first five minutes

With a person and a model added, the smallest thing that works:

```bash
source .venv/bin/activate                  # Windows: .venv\Scripts\activate

python main.py jh43 prompt                 # ask a question; end with --- on its own line
python main.py jh43 prompt --dry-run       # see what would be sent, spending nothing
python main.py jh43 prompt -m gpt-4o-mini  # ask a particular model
python main.py jh43 usage report           # what it has cost so far
```

Replace `jh43` with the netID you added. `--dry-run` is worth trying first: it
shows the exact prompt the model would receive, without making a call.

That is the smallest part of what this does. Translating and transcribing
documents, per-person budgets, settings shared across a group, and models on
your own hardware are all covered in the documentation:

| | |
|---|---|
| [`docs/cli-reference.md`](docs/cli-reference.md) | every command and flag |
| [`docs/configuration.md`](docs/configuration.md) | where files live, and every setting |
| [`docs/token-usage-guide.md`](docs/token-usage-guide.md) | budgets and cost tracking |

### Using it from the command line instead

The web interface is one way in; everything is also available as commands. If you prefer that, activate the environment `start.py` created and use `main.py` directly:

```bash
source .venv/bin/activate      # Windows: .venv\Scripts\activate
python main.py --help
```

### Working on the code

If you plan to *change* the sandbox rather than use it, also install the development tools — a linter, a type checker and the test runner:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
```

---

## Upgrading

Your API keys, usage history and saved conversations live **outside** this folder — in the folder you chose during setup, `PU_AISandbox_data` in your home folder unless you picked somewhere else. That is deliberate: it means getting a newer version can never take them with it.

If you installed with `git clone`, the tidiest way is:

```bash
cd PU_AISandbox
git pull
python3 start.py
```

`start.py` notices if anything new is needed and installs it; if nothing changed, it goes straight to the web interface.

If you would rather start from a clean copy — or you downloaded a ZIP rather than cloning — that is equally safe:

1. Delete the whole `PU_AISandbox` folder.
2. Get a fresh one (clone or download again).
3. Run `python3 start.py`. It rebuilds the environment, then opens a page that has already found your existing files and asks you to confirm:

```
Your files are already here
    /Users/you/PU_AISandbox_data

    your settings and API keys (2 people configured)
    your model catalogue
    your usage history (11 months)

    [ Use these files ]

  › My files are somewhere else
```

One click and you're done — nothing to copy, nothing to re-enter.

If your files live somewhere other than the folder it found — on an external drive, or wherever you chose the first time — open **My files are somewhere else** and point it there, typing the folder or pressing *Browse…* to find it. Whatever is in that folder is used exactly as it is: nothing is overwritten, and nothing is lost.

---

## Usage

### Global commands (no professor required)

```bash
python main.py --help
python main.py --show-config
python main.py --list-models
```

### Usage reporting

```bash
python main.py jh43 usage report              # current month + budget status
python main.py jh43 usage report --all-time   # above + all-time totals
python main.py jh43 usage report 2025-07      # a specific archived month
python main.py jh43 usage months              # list all archived month files
python main.py jh43 usage daily               # today's usage
python main.py jh43 usage daily 2026-03-01    # specific date
```

### Prompt (built-in plugin)

Send a freeform prompt to the AI without any translation or OCR framing. Text is entered interactively and terminated with `---` on its own line.

```bash
python main.py jh43 prompt                    # user prompt only
python main.py jh43 prompt -s                 # system prompt first, then user prompt
python main.py jh43 prompt -o response.txt    # save response to file
python main.py jh43 prompt -m gpt-4o-mini     # specific model
python main.py jh43 prompt -s --dry-run       # preview without API call
```

### Translation and transcription

**Input formats:** `.pdf`, `.docx`, `.txt`, `.md`, `.json`, `.xlsx`/`.xls`, and image files (`.png`, `.jpg`, etc.)  
**Output formats:** `.txt`, `.md`, `.pdf`, `.docx`, `.xlsx` (Excel), `.json` — inferred from the `-o` extension.

```bash
python main.py jh43 translate jp-en -i paper.pdf -o output.pdf          # PDF to PDF
python main.py jh43 translate zh-en -i article.docx -o translated.docx  # DOCX to DOCX
python main.py jh43 translate jp-en -i data.json -o result.md           # JSON in, Markdown out
python main.py jh43 translate jp-en -i data.xlsx -o result.xlsx         # Excel in, Excel out
python main.py jh43 translate jp-en -i paper.pdf -p "1-10"              # page range
python main.py jh43 translate jp-en -i paper.pdf --scanned              # scanned PDF
python main.py jh43 translate jp-en -c                                  # paste text

python main.py jh43 transcribe en -i scan.png -o result.txt             # single image
python main.py jh43 transcribe en -i scans/                             # folder of images
```

To use an alternate AI endpoint (an HPC cluster, or a provider's own API), use colon syntax with the name of an `[endpoints.<name>]` table — defined in `settings.default.toml`, a shared file, or your `preferences.toml`, with its API key in `settings.toml`:

```bash
python main.py jh43 translate jp-en -i paper.pdf -m my_cluster:llama-3-70b
python main.py jh43 prompt -m cloud_provider:some-model
```

See [`docs/cli-reference.md`](docs/cli-reference.md) for the full flag reference and [`docs/configuration.md`](docs/configuration.md) for endpoint setup.

---

## The Web Interface

Everything above happens at the terminal. If you'd rather not work that way, the sandbox also has a browser interface: a chat window, a spending sidebar, and a form for running translation or transcription jobs on whole documents without typing a command.

Start it:

```bash
python main.py webui serve
```

Then open **http://127.0.0.1:8000** in your browser. Leave the terminal window open — closing it stops the server. Press `Ctrl+C` there when you're finished.

### What you can do in it

- **Chat** with any model in your catalog, switching model, temperature and response length per conversation. Conversations are saved and reappear in the sidebar next time.
- **Attach a document** to a question — a PDF, Word file, or spreadsheet — and ask about its contents.
- **Run a translation or transcription** on a whole document or a folder, from a form rather than a command line. It runs in the background: you'll see per-page progress, and a download link when it finishes. You can keep chatting in another conversation while it works.
- **Watch the spending** — this month's and all-time cost, broken down by model, updating as you go.
- **Change settings** — add professors, set API keys, configure shared usage folders — from a settings page, instead of hand-editing `settings.toml`.

### Who can reach it

By default the server listens on `127.0.0.1`, which means **only your own computer can reach it**. Nobody else on the network can, so there's no password to set up and nothing to configure.

If you want to reach it from another device — a tablet, or a second computer — you'll need to both open it up *and* set a passphrase, because opening it up without one would let anyone who can reach the port read every professor's conversations and spend their API budget. The sandbox will refuse to start in that combination and tell you so.

```bash
python main.py webui set-passphrase          # choose a passphrase (asked for at a hidden prompt)
python main.py webui serve --host 0.0.0.0    # now reachable from other devices
```

Even then, be deliberate: this is a research tool holding real spending credentials, not something to leave open on a shared network.

### If a job is interrupted

Jobs run in memory, so restarting the server ends any job that was mid-run. The conversation will say so rather than sitting there looking busy forever. Every job form has a page-range field, so you can start a new job from wherever the last one stopped and combine the output files yourself.

---

## Token Usage Tracking

Everyone is tracked separately, one calendar month at a time. The files live in the `data` folder of the folder you chose during setup:

- **Active file**: `token_usage_{netid}.json` — the current month only
- **Archives**: `archives/{netid}/{YYYY-MM}.json` — one file per past month, written automatically on the first use of a new month
- Every total in a file covers that month alone, so no file grows without end
- All-time totals are worked out on demand by adding up the active file and all the archives (`usage report --all-time`)
- The monthly limit lives in `model_catalog.json`; the warning threshold in `settings.default.toml`

**The monthly limit is advisory.** Passing it prints warnings; it never stops a command. See [`docs/token-usage-guide.md`](docs/token-usage-guide.md#what-the-budget-does-and-doesnt-do).

---

## Model Catalog

`model_catalog.json`, in your files folder, holds the price and capabilities of every model this installation knows about.

**Adding models:**
- **OpenAI or Google**: use `openai/model-name` or `google/model-name` with `-m` the first time — the price is fetched from [PortKey](https://api.portkey.ai) and saved automatically.
- **Any other provider**: edit `model_catalog.json` directly. The minimum an entry needs:

```json
{
  "models": {
    "mistral-small": {
      "input": 0.1,
      "output": 0.3,
      "supports_vision": false
    }
  }
}
```

Prices are per 1,000,000 tokens (default `pricing_unit`). Set `"supports_vision": true` for vision-capable models.

---

## Runtime Settings

`settings.default.toml` in the sandbox folder holds the defaults for everything below. To change any of them, put just the lines you want to change into `preferences.toml` in your own files folder — setup creates it for you, already commented with examples. Because it lives outside the sandbox folder, your adjustments survive upgrading.

A group can also share a settings file between installations, sitting between the two (pointed at by `shared_settings.path` in `settings.toml`). See [`docs/configuration.md`](docs/configuration.md#how-settings-layer) for how the layers merge.

| Section | Key | Default | Effect |
|---------|-----|---------|--------|
| `[prompt]` | `temperature` | `0.7` | Sampling temperature for prompt command |
| `[prompt]` | `top_p` | `1.0` | Nucleus sampling top-p for prompt command |
| `[prompt]` | `max_tokens` | `4000` | Max response tokens for prompt command |
| `[prompt]` | `default_system_prompt` | `"You are a helpful assistant."` | System prompt used when `-s` is not passed |
| `[retry]` | `page_delay_seconds` | `3.0` | Pause between pages in sequential mode |
| `[retry]` | `max_retries` | `10` | Max retry attempts on transient errors |
| `[retry]` | `retry_delay_seconds` | `5.0` | Seconds to wait between retries (the same every time) |
| `[processing]` | `default_parallel_workers` | `1` | Default `-w` value (1 = sequential) |
| `[processing]` | `default_ocr_passes` | `1` | Default `-P` value for EA OCR; > 1 enables multi-pass refinement |
| `[processing]` | `default_page_size` | `2000` | Target characters per page when splitting DOCX/TXT |
| `[processing]` | `max_parallel_workers` | `50` | Hard cap on concurrent workers |
| `[output]` | `default_font_size` | `9` | Body font size (pt) for PDF/Word output |
| `[budget]` | `warning_threshold_pct` | `80` | Warn when spend exceeds this % of monthly limit |

See [`docs/configuration.md`](docs/configuration.md) for plugin-level settings (`[translation]`, `[ocr]`, etc.).

---

## Writing a New Plugin

1. Copy the annotated template:
   ```bash
   mkdir -p plugins/myplugin
   cp templates/plugin.py.template plugins/myplugin/plugin.py
   python main.py --help          # your command should now appear
   ```
2. Edit `plugin.py`: rename `MyPlugin`, set `commands`, implement `register_subparsers` and `run`.
3. Build a `SandboxProcessor` inside `run()` — it resolves the API key, tracks tokens and costs, and creates your service on first use.
4. Nothing in `src/` needs to change. The plugin is discovered at startup on its own.

`plugins/prompt/plugin.py` is a complete working example written to be read as a reference, and [`docs/plugin-authoring-guide.md`](docs/plugin-authoring-guide.md) is the full walkthrough — including how to add languages to an existing command, and how to give your command a form in the web interface.
