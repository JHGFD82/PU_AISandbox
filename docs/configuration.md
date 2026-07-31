# Configuration

The sandbox keeps two things apart:

- **The package** — the code, which is what you replace when you upgrade.
- **Your files** — settings, API keys, the model catalogue, usage history and conversations. These live in a folder of your own, outside the package, so replacing the package can never take them with it.

Setup asks where your folder should go, offering `PU_AISandbox_data` in your home folder. A small marker file (`.installation`) inside the package records the answer; its absence is what tells the sandbox this copy hasn't been set up yet.

| File | Where it lives | Purpose |
|------|----------------|---------|
| `settings.toml` | your files folder | API keys, endpoint credentials, web UI secrets, external usage-data sources — this installation's own private configuration |
| `model_catalog.json` | your files folder | Model pricing and capabilities |
| `preferences.toml` | your files folder | Your own adjustments to how the sandbox behaves |
| `data/` | your files folder | Usage history, archives, conversations |
| `settings.default.toml` | the package, tracked by git | The defaults everyone starts from, plus alternate-endpoint definitions |
| A shared file | anywhere (optional) | Defaults a group wants to share, e.g. via Dropbox |
| `plugins/*/settings.toml` | the package, tracked by git | Each plugin's own defaults |

For a one-screen summary of every one of these — where it can live, whether a personal override exists, and how it's edited — see [Settings at a glance](#settings-at-a-glance) near the end.

---

## First-run checklist

```bash
# 1. Choose where your files live, and create them. This makes
#    settings.toml, model_catalog.json and preferences.toml in the
#    folder you pick. Nothing to copy by hand.
python main.py settings setup

# 2. Add whoever will be using it (prompts for netID, name and keys)
python main.py settings add-professor

# 3. Check it
python main.py --show-config    # who is configured — makes no API calls
python main.py --list-models    # the model catalogue
```

Setup runs on its own the first time you use a copy that hasn't been set up, so you can also just run the command you actually wanted and answer its questions. `python3 start.py` covers the same two steps in a browser instead: a page asking where your files go, then the web interface's Settings page for adding people. Neither route is the lesser one — they write the same `settings.toml`.

If your files already exist — because you replaced the package with a newer copy — setup finds them, shows you what it found, and offers to carry them forward unchanged.

---

## `settings.toml` — this installation's private configuration

Everything specific to this one installation, and never to be synced or shared: API keys, the web UI's passphrase hash and session secret, alternate-endpoint credentials, and the list of external usage-data sources this installation reads.

Editing it programmatically (with the `settings` command below) is safe precisely because every edit happens locally, driven by a command typed at this machine's own keyboard — never over a network call, never as part of syncing files between machines. That reasoning does not extend to putting `settings.toml` in a synced folder (Dropbox, iCloud, OneDrive). Setup warns if the folder you choose looks synced, and re-checks at startup, because turning on Desktop & Documents syncing is one checkbox that retroactively uploads what is already there.

### Format

```toml
[professors.jh43]
name = "Jeff Heller"
key = "sk-...primary_key..."
backup_key = "sk-...backup_key..."   # optional

[professors.as12]
name = "Alice Smith"
key = "sk-...primary_key..."
```

- The table name (`jh43`, `as12`) is the person's **netID** — the university username they sign in with. It selects their API key, names their usage file, and is what you type on the command line.
- `name` — their display name. Shown in reports and in the web interface's person picker and nowhere else, so write it however reads best.
- `key` — primary PortKey API key, obtained through Princeton OIT.
- `backup_key` — used if the primary key fails. A warning is printed when it is.

### Why netIDs

A netID is letters and digits only, which means it can be used as a filename exactly as typed. That is the whole reason it is the identifier — there is nothing to make safe about `jh43`, so there is nothing for two parts of the code to disagree about.

Capitalisation doesn't matter when you type it. Anything that isn't a letter or a digit is rejected with an error, since the usual cause is a display name typed where a netID was wanted.

```bash
python main.py jh43 usage report
python main.py JH43 usage report            # same person
python main.py "Jeff Heller" usage report   # error: that's a display name
```

### Editing it from the command line

Rather than hand-editing the file, the built-in `settings` command adds and removes people and sets any dotted-path value. Unlike every other command, `settings` never needs a netID first — you need it precisely when nobody is configured yet:

```bash
python main.py settings setup                    # choose where your files live
python main.py settings add-professor            # prompts for netID, name and keys
python main.py settings remove-professor jh43    # asks to confirm first
python main.py settings list                     # the optional-settings list
python main.py settings set webui.session_secret            # prompts for a value
python main.py settings set webui.session_secret --generate  # or generate one
python main.py settings unset webui.session_secret
python main.py settings export-shared           # a draft for a group to follow
```

Secrets are always entered at a hidden prompt, never accepted as a command-line flag, so they can't end up in shell history or be seen by another process listing running commands.

### Optional values

A plugin can declare its own optional dotted-path setting with `register_setting()` (`src/config.py`, the same mechanism a plugin uses to add a language), so it appears automatically in `--show-config` and `settings list`. Currently registered:

| Dotted path | Set by | Purpose |
|-------------|--------|---------|
| `webui.passphrase_hash` | `webui set-passphrase` | Unlock-gate passphrase for the web interface |
| `webui.session_secret` | `settings set webui.session_secret` (or `--generate`) | Keeps browser sessions signed in across server restarts |
| `shared_settings.path` | `settings set shared_settings.path` | Path to a shared settings file — see [How settings layer](#how-settings-layer) |
| `endpoints.<name>.key` | `settings set endpoints.my_cluster.key` | API key for an alternate endpoint — see [Alternate AI endpoints](#alternate-ai-endpoints) |

All are optional. Leaving them unset means no unlock gate, a fresh session secret each restart, no shared settings, and no alternate endpoints.

### Checking the configuration

```bash
python main.py --show-config
```

Prints everyone configured, their data-file paths and whether those files exist, and every optional setting registered above — including whether each is set, never the value itself.

---

## `model_catalog.json` — model pricing and capabilities

This lives in your files folder rather than in `settings.toml` for a practical reason: the sandbox writes to it on its own, registering pricing the first time you use `-m provider/model-name`. That kind of frequent automatic write is exactly what causes conflicts in a shared or synced file. Runtime settings change rarely enough to be safe to share; model pricing can change every time someone tries a new model.

### Schema

```json
{
  "config": {
    "pricing_unit": 1000000,
    "monthly_limit": 250.0,
    "provider_map": {
      "google": "vertex-ai",
      "mistral": "mistral-ai"
    }
  },
  "models": {
    "gpt-4o": {
      "input": 2.5,
      "output": 10.0,
      "supports_vision": true,
      "portkey_id": "openai/gpt-4o",
      "last_sync": "2026-05-11T13:33:46"
    }
  }
}
```

#### `config`

| Key | Type | Description |
|-----|------|-------------|
| `pricing_unit` | int | What the prices are per — `1000000` means prices are per 1M tokens |
| `monthly_limit` | float | Monthly spending limit in dollars, shown in usage reports. Advisory: see [Token Usage Guide](token-usage-guide.md#what-the-budget-does-and-doesnt-do) |
| `provider_map` | object | Maps a provider shorthand to the name PortKey uses |

### Which model does which job

Not set here. Each plugin declares the models its own work should use, and ships them in its own `settings.toml` — so `translate` names its models in `plugins/translation/settings.toml`, and the sandbox itself keeps no list of what any plugin wants. That is what lets a plugin be added without editing anything in `src/`.

To change one, edit your own `preferences.toml` — every plugin's model list is already listed there for you, commented out, with the plugin author's note about it. Uncomment the line and edit it. You never need to open anything inside `plugins/`. See [Plugin settings](#plugin-settings), and [`plugin-authoring-guide.md`](plugin-authoring-guide.md#which-models-your-plugin-uses) for what a plugin declares.

Each list is tried in order, and if none of the models in it are left the sandbox falls back to the cheapest one in the catalogue that can do the job, saying so. That is a safety net, not a preference — it keeps things working but chooses on price alone.

### Adding models

**OpenAI or Google** — use `provider/model-name` with `-m` on any command. The price is fetched from PortKey and saved automatically:

```bash
python main.py jh43 prompt -m openai/gpt-4o-new
python main.py jh43 prompt -m google/gemini-2.5-pro
```

**Any other provider** — add the entry by hand. There is no CLI for managing the catalogue:

```json
"mistral-small": {
  "input": 0.1,
  "output": 0.3,
  "supports_vision": false
}
```

### Viewing it

```bash
python main.py --list-models
```

---

## `settings.default.toml` — runtime defaults

Tracked by git and shipped with the package. It holds the defaults for everyone; to change any of them for yourself, copy the lines you want into `preferences.toml` in your own files folder.

```toml
[prompt]
temperature = 0.7
top_p = 1.0
max_tokens = 4000
default_system_prompt = "You are a helpful assistant."

[retry]
page_delay_seconds = 3.0
max_retries = 10
retry_delay_seconds = 5.0

[processing]
default_parallel_workers = 1
default_ocr_passes = 1
default_page_size = 2000
max_parallel_workers = 50

[output]
default_font_size = 9

[budget]
warning_threshold_pct = 80
```

### `[prompt]`

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.7` | How varied the wording is, for the `prompt` command |
| `top_p` | `1.0` | Another way of controlling variety, for the `prompt` command |
| `max_tokens` | `4000` | Longest response the `prompt` command will accept |
| `default_system_prompt` | `"You are a helpful assistant."` | Used when `-s` isn't passed |

### `[retry]`

| Key | Default | Effect |
|-----|---------|--------|
| `page_delay_seconds` | `3.0` | Pause between pages when processing one at a time |
| `max_retries` | `10` | How many times to retry after a temporary failure or a content-filter refusal |
| `retry_delay_seconds` | `5.0` | How long to wait between those attempts — the same every time |

### `[processing]`

| Key | Default | Effect |
|-----|---------|--------|
| `default_parallel_workers` | `1` | Default `-w` value; `1` means one page at a time |
| `default_ocr_passes` | `1` | Default `-P` value; more than one enables multi-pass OCR refinement |
| `default_page_size` | `2000` | Characters per page when splitting a `.docx` or `.txt` into pages |
| `max_parallel_workers` | `50` | Hard cap on how many pages can be in flight at once |

### `[output]`

| Key | Default | Effect |
|-----|---------|--------|
| `default_font_size` | `9` | Body text size, in points, for PDF and Word output |

### `[budget]`

| Key | Default | Effect |
|-----|---------|--------|
| `warning_threshold_pct` | `80` | Print a warning once spending passes this share of `monthly_limit` |

Command-line flags (`-t`, `-T`, `-M`, `-w` and the rest) override all of these, for that one run.

---

## How settings layer

Settings merge from up to three layers. Each is optional, and each overrides only the keys it actually mentions — a layer that doesn't name a section, or a key inside one, leaves the layer below untouched.

1. **`settings.default.toml`** in the package — the defaults, the same for everyone.
2. **A shared file**, if `shared_settings.path` is set in `settings.toml`. Any path, in the same format as `settings.default.toml` — for example one synced across a research group with Dropbox. This lets a group share a cluster's worker count, a group-wide font size, even a shared endpoint definition, without anyone hand-editing their own copy. Nothing changes unless the pointer is set.
3. **`preferences.toml`** in your files folder — your own adjustments, and the last word. Even with a shared file in play, a setting placed here wins, so one person can override just their own quirk without touching the file everyone else reads. Setup creates it already commented with examples, and because it sits outside the package it survives upgrading.

```toml
# preferences.toml
[processing]
default_parallel_workers = 4
```

`settings.toml` itself is never layered — it is this installation's own private configuration, not a set of defaults.

### Setting up a shared file for a group

If several people should follow the same settings — a lab agreeing on which models to use, or one worker count for a shared cluster — one person looks after a file everyone points at. Nothing creates or edits that file automatically, including the sandbox: it lives somewhere that syncs, and several installations writing to it is how you get conflicted copies. So it is made deliberately.

Whoever looks after it does this:

**1. Make a draft.** Either way works — nobody has to use the terminal for this.

*In the web interface:* open **Settings → Shared settings**. Two ways from there:

- **Choose settings for the group…** opens a list of every setting with what each one does. Tick what the group should share, adjust the values, and download the result. Anything your group already decided is ticked for you, and anything that has appeared since is marked **NEW** — with a filter to show only those.
- **Download the whole file instead** hands you the same file with everything commented out, to edit in a text editor.

*At the command line:*

```bash
python main.py settings export-shared
```

This writes **`shared-settings.toml`** into their own files folder (`~/PU_AISandbox_data/shared-settings.toml` unless they chose somewhere else). The command prints the full path, and `--output` puts it somewhere else instead.

Either way it lists **every** setting the sandbox and the installed plugins have, with each author's explanation, all commented out — so a draft placed unedited changes nothing for anyone.

**2. Edit it, and rename it if you like.**

If you used **Choose settings for the group…**, this is already done — the downloaded file contains exactly what you ticked. Otherwise, uncomment the settings the group should share and adjust them; the file is plain text with an explanation above every setting.

Either way the name is yours to choose.

The name is yours to choose. `shared-settings.toml` is just what the draft comes out as; `nurikabe-lab.toml` or `chinese-history-group.toml` may mean more to the people using it. Each installation stores the full **path** you give it, not a name, so renaming costs nothing — do it now, before anyone points at the file, and you won't have to update them later.

**3. Put it where the group can reach it.**

A synced folder, a network share, anywhere every member can read.

**4. Tell each member to point at it, once.** Again, either way.

*In the web interface:* **Settings → Shared settings → Shared settings file path**, paste the path, Save.

*At the command line:*

```bash
python main.py settings set shared_settings.path /path/to/whatever-you-called-it.toml
```

That is the only step each member does, and they only do it once.

**Keeping it current.** Settings appear as plugins are updated. A member who needs one that the shared file doesn't mention will see the plugin's own value in their `preferences.toml` rather than a `# currently set by your group's shared settings` label — that's their cue to ask. Whoever looks after the file then makes a fresh draft — **Choose settings for the group…** again, where anything added since their file was written is marked **NEW** and can be filtered down to on its own, or:

```bash
python main.py settings export-shared --from /path/to/shared-settings.toml
```

The download button needs no argument: it carries across from whatever this installation's shared-settings path points at. Decisions already in the file are carried across exactly as written, trailing comments and all. Anything that has appeared since is marked `# NEW:` and left commented, so a second draft shows what is worth a look rather than needing to be read from scratch. They edit, and replace the file in the shared location.

A plugin's own settings layer the same way (see [Plugin settings](#plugin-settings)): the plugin's `settings.toml` first, then the shared file, then `preferences.toml`. So `[translation] temperature = 0.2` in your `preferences.toml` overrides what the translation plugin ships, without editing anything inside `plugins/`.

---

## Alternate AI endpoints

An `[endpoints.<name>]` table describes an AI endpoint other than the built-in service — a model running on an HPC cluster or other self-hosted inference server, or a provider's direct API (many expose an OpenAI-compatible interface reachable with just a URL and a key).

Definitions merge through the same three layers as every other setting. Only the credential is kept apart, in `settings.toml`, because credentials are never meant to be shared or layered.

An endpoint must speak the OpenAI API's language, which nearly every self-hosted server and provider does; that is assumed unless you set `openai_compatible = false`, which makes the sandbox refuse it plainly instead of failing in a way that looks like the endpoint's fault. Setting `verify_ssl = false` turns off the check on the endpoint's certificate — sometimes the only way to reach a cluster with an internal one — and the sandbox writes a warning to the log each time it connects that way.

Calls to one of these are counted but carry no cost, and are reported on their own — the sandbox's prices are Princeton's and do not describe anyone else's service. See [Alternate endpoints are counted, but not costed](token-usage-guide.md#alternate-endpoints-are-counted-but-not-costed).

### Defining one

```toml
# settings.default.toml, or a shared file, or preferences.toml
[endpoints.my_cluster]
name = "My HPC Cluster"
base_url = "http://my-cluster.internal:8000/v1"
openai_compatible = true
default_model = "llama-3-70b-instruct"
timeout = 30
verify_ssl = false
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | No | table name | Label shown in logs |
| `base_url` | **Yes** | — | Root URL of the API |
| `openai_compatible` | No | `false` | When `true`, uses the OpenAI SDK pointed at `base_url` |
| `default_model` | No | none | Model used when the colon syntax names none |
| `timeout` | No | `30` | Request timeout in seconds |
| `verify_ssl` | No | `true` | Set `false` for an internal cluster with a self-signed certificate |

`settings.default.toml` is tracked by git, so put definitions you're comfortable committing there or in a shared file, and anything specific to this installation in `preferences.toml`.

### Its API key

```toml
# settings.toml
[endpoints.my_cluster]
key = "sk-..."
```

```bash
python main.py settings set endpoints.my_cluster.key
```

### Using it

```bash
python main.py jh43 prompt -m my_cluster:llama-3-70b
```

The part before the colon names the `[endpoints.<name>]` table; everything after is passed to that endpoint as the model name. The model catalogue is bypassed entirely. Token usage is still recorded.

### Sending everything there

Set `default_endpoint` to route every bare model name to an endpoint instead of the built-in service:

```toml
[config]
default_endpoint = "my_cluster"
```

Unset — the default — bare model names go to the built-in service.

See [CLI Reference → Specifying models](cli-reference.md#specifying-models) for the full syntax.

---

## External usage-data sources

`[usage_sources]` in `settings.toml` lists other installations whose usage data this one should include when building reports. It's managed entirely through `usage sources` commands rather than hand-edited.

This lets one installation report on another's spending. For example: a professor runs their own copy pointed at a Dropbox-synced folder, and whoever manages several people's accounts registers that folder as a source on their own installation, so one report covers everyone without anyone copying files around.

Two modes:

- **`read-only`** (the default) — only the other installation writes there; this one just reads.
- **`shared-write`** — both installations record usage there, one file per call, so a file-sync service never sees two conflicting edits to the same file.

```bash
python main.py jh43 usage sources list
python main.py jh43 usage sources add --label "Prof. Smith" --path /path/to/shared/data --mode read-only
python main.py jh43 usage sources add --label "This installation" --path /path/to/shared/data --mode shared-write --for-professor jh43
python main.py jh43 usage sources remove "Prof. Smith"
```

`add` prompts for anything you don't pass as a flag. A netID is required on the command line for consistency with every other `usage` subcommand, even though the source list itself isn't scoped to one person — see `src/settings_store.py`.

---

## Plugin settings

Each bundled plugin ships its own `settings.toml` in its plugin directory, tracked alongside the plugin. The plugin's `src/settings.py` finds it by walking up from its own file to the nearest `settings.toml` containing one of its sections. Plugin settings never collide with the root `settings.default.toml` — they have no section names in common.

**You don't have to go looking in `plugins/` to change any of this.** On every run the sandbox reads each installed plugin's settings file and adds anything not already there to your own `preferences.toml`, commented out, carrying the plugin author's explanation with it. Uncomment a line to take that setting over.

They arrive commented rather than live on purpose. A live copy would pin the setting the moment it appeared, so a plugin shipping a corrected value later — a retired model swapped out — would be silently overruled by the frozen copy. Commented, the plugin's value keeps applying until you deliberately take it over.

Nothing you have already written is touched, so a value you have set, or a comment you have added, stays exactly as it is.

**A shared settings file is never written to.** It belongs to a group, is looked after by one person, and usually lives somewhere that syncs — several installations appending to it is how you end up with duplicated blocks or conflicted copies. Whoever looks after it produces it deliberately and tells the group to point at it.

Where a shared file already sets something, your `preferences.toml` shows *that* value rather than the plugin's, labelled `# currently set by your group's shared settings`. So uncommenting a line can never quietly undo a decision your group has made — and if a setting you need isn't in the shared file at all, you'll see the plugin's value instead, which is your cue to ask whoever looks after it to add it.

See [`plugin-authoring-guide.md`](plugin-authoring-guide.md#plugin-settings) for how to add settings to a new plugin.

### `translation` — `plugins/translation/settings.toml`

Also used by `translation-ea`, which ships an identical file.

**`[translation]`**

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.5` | How varied the wording is |
| `top_p` | `0.5` | Another way of controlling variety |
| `max_tokens` | `4000` | Longest response per page |
| `context_percentage` | `0.65` | How much of the previous page is carried forward as context (0.0–1.0) |

**`[image_translation]`** — used with `--scanned` or an image input.

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.3` | Slightly varied, to read ambiguous characters from context |
| `max_tokens` | `8000` | Higher, because the output holds both a transcript and a translation |

### `transcription` — `plugins/transcription/settings.toml`

Also used by `transcription-ea`.

**`[ocr]`**

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.0` | Completely predictable — the best defence against invented text |
| `top_p` | `0.1` | Very low, to stop the model inventing characters |
| `max_tokens` | `4000` | Longest response per image |
| `frequency_penalty` | `0.5` | Discourages repeating the same words |
| `presence_penalty` | `0.3` | Encourages variety |

**`[transcription_review]`**

| Key | Default | Effect |
|-----|---------|--------|
| `temperature` | `0.1` | Low, for precise error detection |
| `top_p` | `0.5` | Focused |
| `max_tokens` | `4000` | Longest report; raise it for very long transcriptions |

### `webui` — `plugins/webui/settings.toml`

| Key | Effect |
|-----|--------|
| `session_cookie_name` | Name of the browser cookie that keeps you signed in |
| `compaction_threshold` | How full a model's context window has to get before a conversation is summarised on the next turn |

---

## Optional dependencies

Some formats need a package that isn't in the core `requirements.txt`. Their absence degrades gracefully.

| Package | Needed for | Without it |
|---------|-----------|------------|
| `openpyxl` | `.xlsx` output, and `.xlsx`/`.xls` input | Output falls back to `.txt`; input raises an error |

```bash
pip install openpyxl
```

---

## Settings at a glance

| Setting | Where it's stored | Can it live somewhere shared? | Personal override? | Command line | Web interface |
|---|---|---|---|---|---|
| Names and API keys | `settings.toml`, your files folder | No — never sync `settings.toml` | N/A | ✅ `settings add-professor` / `remove-professor` | ✅ `/settings` — add, remove, replace primary or backup key |
| Web UI passphrase | `settings.toml` (`webui.passphrase_hash`) | No | N/A | ✅ `webui set-passphrase` | ✅ `/settings` — hashed server-side, never shown |
| Web UI session secret | `settings.toml` (`webui.session_secret`) | No | N/A | ✅ `settings set` / `--generate` | ✅ `/settings` |
| Shared-settings pointer | `settings.toml` (`shared_settings.path`) | No — it's just a path | N/A | ✅ `settings set` / `unset` | ✅ `/settings` |
| A shared-settings draft to edit and place | Nowhere — handed to you, never saved | ✅ that's the point; you place it | N/A | ✅ `settings export-shared` | ✅ `/settings` → Choose settings for the group, or download the whole file |
| Endpoint API keys | `settings.toml` (`endpoints.<name>.key`) | No | N/A | ✅ `settings set` / `unset` | ✅ `/settings` — one field per endpoint already defined |
| Endpoint definitions | `settings.default.toml`, a shared file, or `preferences.toml` | ✅ definitions may live in the tracked file or a shared one | ✅ `preferences.toml` wins | ❌ hand-edit TOML | ❌ read-only on `/settings`, with a copyable snippet |
| Model pricing | `model_catalog.json`, your files folder | Not designed for it — kept separate precisely to avoid concurrent-write conflicts | N/A | Indirectly: `-m provider/model` registers pricing | ❌ |
| Runtime defaults | `settings.default.toml`, in the package | N/A — this is the shared baseline | ✅ `preferences.toml` | ❌ hand-edit TOML | ❌ |
| Shared runtime defaults | Wherever `shared_settings.path` points | ✅ that's the point | ✅ `preferences.toml` still wins | Pointer only (`settings set shared_settings.path`) | Pointer only, via `/settings` |
| Plugin defaults | Each plugin's own directory, tracked by git | N/A | ❌ no per-plugin override file | ❌ hand-edit TOML | ❌ |
| External usage sources | `settings.toml` (`[usage_sources]`) | ✅ that's the point — it points at another installation's `data/` folder | N/A | ✅ `usage sources add/list/remove` | ✅ `/settings` |

Every "✅ `/settings`" row is served by the web interface's `/settings` route (`plugins/webui/src/templates/settings.html` and the `/api/settings/*` routes in `plugins/webui/src/app.py`), behind the same unlock passphrase as the rest of it. A first-time visitor with nobody configured is sent straight there rather than to an empty chat screen, and the section order flips depending on whether anyone is configured yet — people first on an empty installation, shared settings first once there's at least one person, since that's the more likely thing to come back and adjust.
