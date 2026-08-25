# Princeton University AI Sandbox

[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FJHGFD82%2FPU_AISandbox%2Fmain%2Fpyproject.toml&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![codecov](https://codecov.io/gh/JHGFD82/PU_AISandbox/branch/main/graph/badge.svg)](https://codecov.io/gh/JHGFD82/PU_AISandbox)

A toolkit for accessing the Princeton University AI Sandbox service from OIT. Accessible through both web-based or command-line interfaces, with a modular plug-in architecture.

> **Access requirement:** Each person must have a valid Princeton University AI Sandbox API key (available through OIT). This tool is for Princeton faculty and authorized delegates only.

## Where everything lives

An installation is three separate locations, referred to throughout this document and the rest of the documentation by the names below:

| | |
|---|---|
| **`package`** | The code itself, which is the `PU_AISandbox` folder you downloaded. This is replaced whenever you upgrade, and nothing of yours is stored inside it. |
| **`settings`** | Your API keys, your preferences and the model catalog. Setup asks where this should go, suggesting `PU_AISandbox_data` in your home folder. |
| **`data`** | Everything recorded as you work: the cost of each call, the months already closed, and your saved conversations. This lives inside `settings` unless a separate location is specified for a professor, described under [Optional information](#optional-information). |

Keeping `settings` and `data` apart from `package` is what makes upgrading (or deleting the package outright) painless, as covered under [Upgrading](#upgrading).

## Architecture

```text
main.py
  → src/cli.py            controller + argument parser; loads plugins, routes commands
    → src/config.py       language registry, netID lookup, optional-setting registry
    → src/settings.py     layered settings (defaults → shared → preferences → plugin → flags)
    → src/paths.py        resolves the `package`, `settings` and `data` locations
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
  → plugins/              added capabilities that can come bundled or downloaded into this folder
      prompt/             bundled plugin (ships with this repo)
      translation/        bundled plugin — base English translation (ships with this repo)
      transcription/      bundled plugin — base English OCR (ships with this repo)
      webui/              bundled plugin — the browser interface (ships with this repo)
```

Currently, there are no additional plugins available, however East Asia-specific modules for the translation and transcription plugins are available via [Jeff Heller's GitHub profile](https://www.github.com/JHGFD82).

Services can be built within a plugin to allow for additional processing capabilities (translation has `TranslationService` and `ImageProcessorService` built into it) and should not live within the code of this package.

`src/cli.py` decides *what* to run. Plugins decide *how* to run it. Only the `usage` subcommand is built in.

For deeper documentation, see the `docs/` folder:

- [`docs/architecture.md`](docs/architecture.md) — request lifecycle, component descriptions, data-flow diagrams
- [`docs/cli-reference.md`](docs/cli-reference.md) — full flag reference for all commands
- [`docs/configuration.md`](docs/configuration.md) — where `settings` and `data` live, and the schema for `settings.toml`, `model_catalog.json` and `settings.default.toml`
- [`docs/token-usage-guide.md`](docs/token-usage-guide.md) — token tracking, usage commands, budget settings, and troubleshooting
- [`docs/plugin-authoring-guide.md`](docs/plugin-authoring-guide.md) — step-by-step guide to writing new plugins

---

## Getting Started

### Installation

This package may be installed either by downloading this package and unzipping it in a folder of your choice, or through the command line:

```bash
git clone https://github.com/JHGFD82/PU_AISandbox.git
cd PU_AISandbox
python3 start.py
```

`start.py` does everything: detects whether to provide instructions on installing required software in an existing virtual environment or create a new one, which then includes finding an installed Python version new enough to run the sandbox (version 3.11 or higher), installing the additional software (approximately 200 MB), and finally opening the web interface in your browser for first-time setup.

**NOTE:** It is typical for an error message to appear when the web UI is launched. Reload the page if your browser doesn't do it automatically.

### If you already have a virtual environment of your own

If you would rather skip `start.py` and install this package within your own pre-configured virtual environment, activate it first and run:

```bash
pip install -r requirements.txt
python main.py webui serve
```

### If you want to set up the sandbox without the web interface

Plugins are the core of the feature set within this package, but may be deleted if not needed. This is especially true of the web UI plugin, located at `plugins/webui`. Every other command will continue to function properly, including `translate`, `transcribe`, `prompt`, `usage`, and `settings`. `start.py` will notice the missing plugin and sets up the sandbox in the terminal instead of opening a browser.

### If it says you need a newer Python

Macs come with Python 3.9, and the sandbox needs 3.11 or newer. `start.py` looks for it automatically on launch and will prompt you to do either of the following:

- download it directly from [python.org/downloads](https://www.python.org/downloads/) or
- on Macs with Homebrew installed, run `brew install python@3.13` in the command line.

After installation, run `python3 start.py` again. Installing a newer Python alongside the old one is safe and won't disturb anything.

### Adding people

#### Via web UI

Once the sandbox is running, the package will detect that no professors have been added yet and will display the appropriate Settings page.

#### Via command line

Adding a professor can also be done at the command line without the web UI:

```bash
python main.py settings add-professor
```

#### Required information

You will be prompted for three required fields:

| | |
|---|---|
| **NetID** | Their University username (i.e. `jh43`). Letters and digits only, without "@princeton.edu". This is how the sandbox tells one person from another: it picks their API key, names their usage file, and is what you type to run a command as them. This value is unchanged once confirmed. |
| **Display name** | `Jeff Heller`. Only ever shown publicly, in both the web interface and in reports. |
| **API key** | Princeton faculty obtain these through OIT; each person registers independently. A backup key is optional. |

Keys are never displayed once saved — the settings page shows only whether one is set. At the command line, characters are typed in silently (not displayed as typed).

### Optional information

A separate `data` location can be designated for a professor, holding their conversations and usage records rather than keeping them alongside `settings`. Remote storage options like Dropbox or OneDrive are welcome to be used for this purpose, in the case of sharing access to the sandbox with multiple users through a single API key, or for one person working across several computers.

### Adding models

A fresh copy of this package comes with no models installed. **Check the [official AI Sandbox documentation](https://princeton.service-now.com/service?id=kb_article_view&sysparm_article=KB0014337) for the models currently offered**, and add them with either method below. The sandbox looks up pricing information for the model and then sends a series of one-token prompts to query which features are supported. This process takes a few seconds, and the designated person will be charged a tiny fraction of a penny (the total cost of the model capability test).

#### Via web UI

On the Settings page, under **Models**, type a name in the form `provider/model`. Examples:
- openai/gpt-5.2
- anthropic/claude-opus-4-8
- google/gemini-3-pro-preview
- mistral/mistral-7b-instruct-v0.1
- meta/llama-3-70b-instruct

After specifying who covers the initial token cost, press the *Add and test* button. The model will be added after a few seconds. Repeat for each model.

#### Via command line

Adding a model can be done on the command line with the following command:

```bash
python main.py settings test-model [provider/model] [--professor [netID]]
```

If more than one person is being managed by this installation (with their own API keys), you must specify the `--professor [netID]` flag so an account can be charged for the testing of available features.

#### The clusters

This package also supports sending prompts to models located on Princeton University clusters. See [Alternate endpoints](docs/configuration.md#alternate-ai-endpoints).

### Your first five minutes with the command line

With a person and a model added, you can test the basics of this package's capabilities with these basic commands:

```bash
python main.py jh43 prompt                 # ask a question; end with --- on its own line
python main.py jh43 prompt --dry-run       # see what would be sent, spending nothing
python main.py jh43 prompt -m gpt-4o-mini  # ask a particular model
python main.py jh43 usage report           # what it has cost so far
```

Replace `jh43` with the netID you added. `--dry-run` is worth trying first: it shows the exact prompt the model would receive, without making a call. Regardless of what command you run, this will always provide a preview of both system and user prompts.

### Other documentation

That is the smallest part of what this does. Translating and transcribing documents, per-person budgets, settings shared across a group, and models on your own hardware are all covered in the documentation:

| | |
|---|---|
| [`docs/cli-reference.md`](docs/cli-reference.md) | every command and flag |
| [`docs/configuration.md`](docs/configuration.md) | where `settings` and `data` live, and every setting |
| [`docs/token-usage-guide.md`](docs/token-usage-guide.md) | budgets and cost tracking |

### Working on the code

If you plan to *change* the sandbox rather than use it, also install the development tools. These include a code linter, type checker and the test runner:

```bash
source .venv/bin/activate      # for pip and pytest, which are not main.py
pip install -r requirements-dev.txt
```

---

## Upgrading

Your `settings` and `data` live **outside** the `package` location, in the folder you chose during setup, which by default is `PU_AISandbox_data` in your home folder. This ensures that upgrading this package (or even deleting it outright) is painless as your files are unaffected by this process.

To upgrade this package, the tidiest way is the following commands if you installed with `git clone`:

```bash
cd PU_AISandbox
git pull
python3 start.py
```

`start.py` notices if anything new is needed and installs it. If nothing has changed, it goes straight to the web interface.

If you downloaded a ZIP rather than cloning, or if you want to start fresh, that is a safe way to do that:

1. Delete the whole `PU_AISandbox` folder.
2. Get a fresh one (clone or download again).
3. Run `python3 start.py`. The setup proceeds in the same method as described in the "Getting Started" section of this document. Upon detection of an existing installation, however, you will be shown what was found and offered the choice of using it or specifying another location. Both `settings` and every `data` location it points to are named separately, since the two are frequently in different places:

```
Found an existing installation:

  Settings location
    /Users/you/PU_AISandbox_data
      your settings and API keys   (2 people configured)
      your model catalog

  Data locations
      Heller   (11 months of spending and 28 conversations)
        /Users/you/Dropbox/PU_AISandbox_spend
      Conlan   (4 months of spending)
        /Users/you/PU_AISandbox_data/data

  Some of this is kept outside the settings location, in
  folders named in those settings. All of it is part of
  this installation, and using it means using all of it.

Use this installation? [Y/n]
```

**Nothing is overwritten or deleted regardless of the option chosen.**

---

## Usage from the Command Line

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

Send a freeform prompt to the AI without any template text given to either the system or user prompt.

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

To use an alternate AI endpoint (an HPC cluster, or a provider's own API), use colon syntax with the name of an `[endpoints.<name>]` table, defined in `settings.default.toml`, a shared file, or your `preferences.toml`, with its API key in `settings.toml`:

```bash
python main.py jh43 translate jp-en -i paper.pdf -m my_cluster:llama-3-70b
python main.py jh43 prompt -m cloud_provider:some-model
```

See [`docs/cli-reference.md`](docs/cli-reference.md) for the full flag reference and [`docs/configuration.md`](docs/configuration.md) for endpoint setup.

---

## Usage from the Web UI

Everything above happens at the terminal. If you'd rather not work that way, the sandbox also has a browser interface: a chat window, a spending sidebar, and a form for running translation or transcription jobs on whole documents without typing a command.

Start it:

```bash
python main.py webui serve
```

Your web browser should automatically open and navigate to **http://127.0.0.1:8000**. If it opens to an error message, wait a few seconds and reload the page if it does not happen automatically. Leave the terminal window open as closing it stops the server that runs the interface.

### Features

- **Chat** with any model in your catalog, switch models, adjust compatible settings, and adjust the system prompt per conversation. Conversations are saved in the left sidebar.
- **Run capabilities from plugins** on a whole document or a folder. Depending on the options you specify, you may see per-page progress, but in all cases a download link will appear when the process completes. You can keep chatting in another conversation while it works.
- **Watch the spending** — this month's and all-time cost, broken down by model, updating as you go.
- **Change settings** — add professors, set API keys, configure shared usage folders — from a settings page.

### Local or remote hosting

By default the server listens on `127.0.0.1`, which means **only your own computer can reach it**. A passphrase to open the web UI is not required in this instance.

If you want to reach it from another device — a tablet, or a second computer — you'll need to both install it on that device *and* set a passphrase. Leaving the passphrase empty would allow anyone who can reach the server to read every professor's conversations and spend their API budget. The sandbox will refuse to start in that combination and tell you so.

Enabling this option is done in the command line:

```bash
python main.py webui set-passphrase          # choose a passphrase (asked for at a hidden prompt)
python main.py webui serve --host 0.0.0.0    # now reachable from other devices
```

If you exercise this option, be deliberate: this is a research tool holding real spending credentials, not something to leave open on a shared network.

### If a job is interrupted

Jobs run in memory, so restarting the server ends any job that was mid-run, and that will be reported in the conversation. Resuming interrupted jobs is not possible, therefore it is suggested to use the page range options to specify the pages that were skipped and combine the resulting files yourself.

---

## Token Usage Tracking

This toolkit tracks usage one calendar month at a time for each professor that is added. The current month's usage file and the archive live in that professor's `data` location:

- **Active file**: `token_usage_{netid}.json` — the current month only
- **Archives**: `archives/{netid}/{YYYY-MM}.json` — one file per past month, written automatically on the first use of a new month
- The monthly cost mentioned in a JSON file covers that month alone
- All-time totals (from the command line: `usage report --all-time`) are worked out on demand by adding up the active file and all archived months
- The monthly limit lives in `model_catalog.json`; the warning threshold in `settings.default.toml`

**The monthly limit is advisory.** Warnings are presented when going over the monthly budget but never stop a command from executing. See [`docs/token-usage-guide.md`](docs/token-usage-guide.md#what-the-budget-does-and-doesnt-do).

---

## Model Catalog

`model_catalog.json`, in your `settings` location, holds the price and capabilities of every model this installation knows about.

Instructions on how to add models can be found under the **Getting Started** section of this document.

---

## Runtime Settings

Both the web UI and command line share settings across the installation. Settings are loaded in a layered method, using this order:

1. Default settings from within the `settings.default.toml` file located inside the `package` location; this file may be edited but may not survive updates to the sandbox in future versions.
2. (Optional) Shared settings between multiple installations, if specified by the `shared_settings.path` section of the `settings.toml` file located in the `settings` location
3. `preferences.toml`, located in the `settings` location

See [`docs/configuration.md`](docs/configuration.md#how-settings-layer) for more information on the layered setting process.

### Editing runtime settings

For any setting you wish to change:
1. remove the "#" from both:
   - the header of the setting you wish to change (surrounded by `[]`, e.g. `[translation]`) and
   - the setting itself
2. Adjust the setting

### Built-in settings

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
