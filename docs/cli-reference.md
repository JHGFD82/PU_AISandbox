# CLI Reference

```
python main.py [--show-config | --list-models]
python main.py <netid> <command> [options]
python main.py settings <subcommand>
python main.py webui <subcommand>
```

`<netid>` is the university username of whoever's API key and budget the command should use — `jh43`, not `Jeff Heller`. It's letters and digits only, and case doesn't matter. See [Configuration → Why netIDs](configuration.md#why-netids).

`settings` and `webui` don't take a netID, since you need them precisely when nobody is configured yet.

If you'd rather not use the command line at all, `python3 start.py` sets everything up and opens the web interface instead. See the [README](../README.md).

---

## Global flags

These work on any command.

| Flag | Description |
|------|-------------|
| `--verbose` | Print detailed logging about what the sandbox is doing |
| `--debug-api` | Log the raw request and response payloads — useful when a provider returns an error that doesn't explain itself |

Both are accepted before or after the command (`main.py --verbose jh43 translate …` and `main.py jh43 translate … --verbose` are the same).

---

## Global commands

These don't need a netID.

### `--show-config`

Print everyone configured, their data-file paths, whether those files exist, and every optional setting (and whether it's set — never its value). Makes no API calls.

```bash
python main.py --show-config
```

### `--list-models`

Print every model in the catalogue with its pricing and capability flags.

```bash
python main.py --list-models
```

---

## `settings` — people, keys and optional values

```bash
python main.py settings setup                     # choose where your files live
python main.py settings add-professor             # prompts for netID, name and keys
python main.py settings add-professor --netid jh43 --name "Jeff Heller"
python main.py settings remove-professor jh43     # asks to confirm first
python main.py settings list                      # optional values and whether each is set
python main.py settings set webui.session_secret            # prompts for a value
python main.py settings set webui.session_secret --generate  # or generate one
python main.py settings unset webui.session_secret
python main.py settings export-shared            # draft a settings file for a group
python main.py settings export-shared --from /path/to/current-shared.toml
```

| Subcommand | What it does |
|------------|--------------|
| `setup` | Chooses where the sandbox keeps your files and creates the starting files there. Runs on its own the first time you use a copy that hasn't been set up. |
| `add-professor` | Adds someone. Prompts for anything not passed as a flag. |
| `remove-professor <netid>` | Removes someone, after confirming. |
| `list` | Lists every optional setting and whether it's currently set. |
| `set <dotted.path>` | Sets one value, prompting for it. Add `--generate` for a random secret. |
| `unset <dotted.path>` | Removes one value. |
| `export-shared` | Writes a draft settings file for a group to follow — every setting the sandbox and its plugins have, commented out, with each author's explanation. `--output` chooses where; `--from` carries decisions across from a file already in use and marks anything new since. You place the result yourself; see [Configuration](configuration.md#setting-up-a-shared-file-for-a-group). |

API keys and other secrets are always typed at a hidden prompt, never accepted as a flag, so they can't end up in shell history or be read by another process listing running commands.

See [Configuration](configuration.md#settingstoml--this-installations-private-configuration) for the file this writes and every value it accepts.

---

## `webui` — the browser interface

```bash
python main.py webui serve                    # start it, then open http://127.0.0.1:8000
python main.py webui serve --host 0.0.0.0     # reachable from other devices
python main.py webui serve --port 8080
python main.py webui setup                    # do first-time setup in a browser
python main.py webui set-passphrase           # set the unlock passphrase
```

| Subcommand | What it does |
|------------|--------------|
| `serve` | Starts the server. `--host` defaults to `127.0.0.1` (this computer only), `--port` to `8000`. Leave the terminal open; Ctrl-C stops it. |
| `setup` | Serves a single setup page on this computer only, and stops as soon as you've answered it. `start.py` offers this as an alternative to answering at the terminal. |
| `set-passphrase` | Sets the passphrase that unlocks the web interface, at a hidden prompt. Only the hash is stored. |

Serving on anything other than `127.0.0.1` **requires** a passphrase — without one, anyone who can reach the port could read every conversation and spend every configured budget. The server refuses to start in that combination and says so.

---

## `usage` — token reporting

### `usage report [YYYY-MM] [--all-time]`

| Form | Output |
|------|--------|
| `usage report` | This month, plus budget status |
| `usage report --all-time` | The above, plus totals across every archived month |
| `usage report 2025-07` | One archived month |

```bash
python main.py jh43 usage report
python main.py jh43 usage report --all-time
python main.py jh43 usage report 2025-07
```

### `usage months`

List every archived month for that person.

```bash
python main.py jh43 usage months
```

### `usage daily [YYYY-MM-DD]`

Usage for a single day; today if no date is given.

```bash
python main.py jh43 usage daily
python main.py jh43 usage daily 2026-03-01
```

### `usage sources` — other installations' data

Registers another installation's `data/` folder so its usage appears in this one's reports. See [Configuration → External usage-data sources](configuration.md#external-usage-data-sources).

```bash
python main.py jh43 usage sources list
python main.py jh43 usage sources add --label "Prof. Smith" --path /path/to/data --mode read-only
python main.py jh43 usage sources remove "Prof. Smith"
```

| Flag | Description |
|------|-------------|
| `--label <text>` | A short name for this source, e.g. `"Prof. Smith"` |
| `--path <path>` | The other installation's `data/` folder |
| `--mode read-only\|shared-write` | `read-only` (default) if only the other side writes there; `shared-write` if this installation records usage there too |
| `--for-professor <netid>` | Which person this source is for. Required with `--mode shared-write`. |

`add` prompts for anything you don't pass as a flag.

---

## `prompt` — a freeform question

Send a prompt to the AI model with no translation or OCR framing around it. Text is typed interactively and finished with `---` on a line of its own.

```bash
python main.py jh43 prompt [options]
```

| Flag | Description |
|------|-------------|
| `-s`, `--system` | Ask for a system prompt first, then the user prompt |
| `-o <path>`, `--output <path>` | Save the response to a file |
| `-m <model>` | Model to use — see [Specifying models](#specifying-models) |
| `-t <float>` | How varied the wording is, 0.0–2.0 |
| `-T <float>` | Another way of controlling variety, 0.0–1.0 |
| `-M <int>` | Longest response to accept |
| `--dry-run` | Show the prompts without calling the API |

```bash
python main.py jh43 prompt
python main.py jh43 prompt -s
python main.py jh43 prompt -o response.txt
python main.py jh43 prompt -m gpt-4o-mini
python main.py jh43 prompt -s --dry-run
```

---

## `translate` — document translation

Translate a document from one language into another. Provided by the `translation` plugin, and extended by `translation-ea` for East Asian languages if that's installed.

```bash
python main.py jh43 translate <language-pair> [options]
```

**Language pair:** `source-target`, e.g. `jp-en`, `zh-en`, `en-jp`. Which codes exist depends on which plugins are installed — `python main.py --help` lists the registered ones.

### Input

| Flag | Description |
|------|-------------|
| `-i <path>`, `--input <path>` | Input file. Can't be combined with `-c`. |
| `-c`, `--custom` | Type or paste the source text instead, finishing with `---` on its own line. Can't be combined with `-i`. |
| `-p <range>`, `--page_nums <range>` | Which pages to process — see [Page ranges](#page-ranges) |

**Input formats**

| Extension | Handling |
|-----------|----------|
| `.pdf` | Text extracted page by page; use `--scanned` for a scan with no text layer |
| `.docx` | Body text in document order; tables as tab-separated rows |
| `.txt` | Split into pages by character count (`default_page_size`) |
| `.md` | Markdown formatting preserved as-is |
| `.json` | Flattened to readable key/value lines, then split into pages |
| `.xlsx` / `.xls` | Each sheet as a header plus tab-separated rows, then split into pages (needs `openpyxl`) |
| Images | `.png` `.jpg` `.jpeg` `.gif` `.bmp` `.tiff` `.webp` — sent straight to a vision model |

### Output

| Flag | Description |
|------|-------------|
| `-o <path>`, `--output <path>` | Where to write the result. The extension decides the format. |
| `--auto-save` | Save automatically under a timestamped filename (produces `.txt`) |
| `--progressive-save` | Write each page as it finishes (`.txt` and `.md` only; can't be combined with `--preserve-media` or more than one worker) |

**Output formats**

| Extension | Output |
|-----------|--------|
| `.txt` | Plain text; Markdown tables drawn as ASCII box tables |
| `.md` | Markdown, written as-is; supports progressive save |
| `.pdf` | PDF with CJK font support; Markdown tables become real tables |
| `.docx` | Word document; Markdown tables become real tables |
| `.xlsx` | Excel workbook — Markdown tables become separate sheets ("Table 1", "Table 2", …), prose goes to a "Text" sheet; needs `openpyxl` |
| `.json` | If the response is valid JSON it's pretty-printed; otherwise wrapped as `{"content": "..."}` |

Anything else falls back to `.txt`.

### Document options

| Flag | Description |
|------|-------------|
| `-a`, `--abstract` | The document starts with an abstract; gives the model that context |
| `--toc` | The document has a table of contents: tidy the dot leaders (`............`) to five dots between title and page number |
| `--preserve-tables` | Ask the model to return tabular data as Markdown tables, which become real tables in PDF/Word or ASCII in plain text |
| `--scanned` | Treat a PDF as a scan: render each page as an image and read it with a vision model (PDF only) |
| `--spread` | The input is a two-page spread — two facing pages scanned together. Applies to images and `--scanned` PDFs. |
| `--preserve-media` | Carry embedded images from a `.docx` or `.pdf` source into the translated `.docx` (needs `-i` on a `.docx`/`.pdf` and `-o` on a `.docx`) |

### Formatting

| Flag | Description |
|------|-------------|
| `-f <name>`, `--font <name>` | Font name for PDF/Word output. The font file must be in `fonts/`. |
| `--font-size <pt>` | Body text size in points for PDF/Word output |

### Model and speed

| Flag | Description |
|------|-------------|
| `-w <int>`, `--workers <int>` | How many pages to work on at once (default 1). More than one turns off progressive save and uses untranslated source text as context. |
| `-m <model>` | Model to use — see [Specifying models](#specifying-models) |
| `-t <float>` | How varied the wording is, 0.0–2.0 |
| `-T <float>` | Another way of controlling variety, 0.0–1.0 |
| `-M <int>` | Longest response to accept per page |

### Notes and dry run

| Flag | Description |
|------|-------------|
| `-n`, `--notes` | Ask interactively where to add a note |
| `-ns <text>` | Add a note to the system prompt |
| `-nu <text>` | Add a note to the user prompt |
| `-nb <text>` | Add the same note to both |
| `--dry-run` | Show the prompts without calling the API |

### Examples

```bash
python main.py jh43 translate jp-en -i paper.pdf -o output.pdf
python main.py jh43 translate zh-en -i article.docx -o translated.docx
python main.py jh43 translate en-jp -i notes.txt -o notes_jp.txt
python main.py jh43 translate jp-en -i paper.pdf -w 4           # four pages at once
python main.py jh43 translate jp-en -i paper.pdf -p "1-10"      # first ten pages
python main.py jh43 translate jp-en -i paper.pdf --scanned      # a scan, read by vision
python main.py jh43 translate jp-en -i spread.jpg --spread      # two facing pages
python main.py jh43 translate zh-en -i doc.docx -o out.docx --preserve-media
python main.py jh43 translate jp-en -i paper.pdf --preserve-tables
python main.py jh43 translate jp-en -i paper.pdf --dry-run
python main.py jh43 translate jp-en -c                          # paste the text
```

---

## `transcribe` — reading text off images

Read the text in an image, or in a folder of images. Provided by the `transcription` plugin; `transcription-ea` adds the flags marked below for East Asian languages.

```bash
python main.py jh43 transcribe <language> [options]
```

**Language:** a single code, e.g. `en`. Which codes exist depends on which plugins are installed.

| Flag | Description |
|------|-------------|
| `-i <path>`, `--input <path>` | An image file, or a folder of images processed in order |
| `-o <path>`, `--output <path>` | Where to write the result |
| `--spread` | The input is a two-page spread *(needs `transcription-ea`)* |
| `-P <int>`, `--passes <int>` | How many reading passes; more than one refines the result over several rounds *(needs `transcription-ea`)* |
| `--preserve-tables` | Ask the model to return tabular data as Markdown tables *(needs `transcription-ea`)* |
| `-m <model>` | Model to use — must be able to read images |
| `-t <float>` | How varied the wording is, 0.0–2.0 |
| `-T <float>` | Another way of controlling variety, 0.0–1.0 |
| `-M <int>` | Longest response to accept |
| `-n`, `--notes` | Ask interactively where to add a note |
| `-ns` / `-nu` / `-nb <text>` | Add a note to the system prompt, the user prompt, or both |
| `--dry-run` | Show the prompts without calling the API |

```bash
python main.py jh43 transcribe en -i scan.png -o transcription.txt
python main.py jh43 transcribe en -i scans/        # a whole folder
python main.py jh43 transcribe en -i scan.png --dry-run
```

---

## `transcription_review` — checking a transcription

Look over the output of an earlier transcription for mistakes, and return a report as JSON. Provided by the `transcription` plugin.

The input is the **text** a transcription produced — not the original image.

```bash
python main.py jh43 transcription_review <language> [-i <file> | -c] [options]
```

| Flag | Description |
|------|-------------|
| `-i <path>`, `--input <path>` | A saved transcription text file |
| `-c`, `--custom` | Paste the transcription instead, finishing with `---` |
| `-o <path>`, `--output <path>` | Where to save the report |
| `-m <model>` | Model to use |
| `-n`, `--notes` | Ask interactively where to add a note |
| `--dry-run` | Show the prompts without calling the API |

```bash
python main.py jh43 transcription_review en -i result.txt
python main.py jh43 transcription_review en -c
python main.py jh43 transcription_review en -i result.txt -o review.json
```

---

## Flags every command shares

Available on `prompt`, `translate`, `transcribe` and `transcription_review`:

| Flag | Description |
|------|-------------|
| `-o <path>`, `--output <path>` | Save the result to a file |
| `-m <model>`, `--model <model>` | Use a particular model |
| `-t <float>`, `--temperature <float>` | How varied the wording is, 0.0–2.0 |
| `-T <float>`, `--top-p <float>` | Another way of controlling variety, 0.0–1.0 |
| `-M <int>`, `--max-tokens <int>` | Longest response to accept |
| `--dry-run` | Show the prompts that would be sent, without calling the API |

---

## Page ranges

`-p` / `--page_nums` takes a comma-separated list of page numbers and ranges, counting from 1:

| Example | Pages processed |
|---------|-----------------|
| `"5"` | Page 5 |
| `"1-10"` | Pages 1 to 10 |
| `"4,15-17,20"` | Pages 4, 15, 16, 17 and 20 |
| `"4, 15-17, 20"` | The same — spaces are fine |

Leave it off to process everything.

---

## Specifying models

**Already in the catalogue** — use the bare name:

```bash
python main.py jh43 prompt -m gpt-4o
python main.py jh43 prompt -m gpt-4o-mini
```

**Not in the catalogue yet** — put `provider/` in front and the price is fetched from PortKey and saved:

```bash
python main.py jh43 prompt -m openai/gpt-4o-new
python main.py jh43 prompt -m google/gemini-2.5-pro
```

Only `openai` and `google` can be looked up this way. For any other provider, add the model to `model_catalog.json` in your files folder by hand — see [Configuration → Adding models](configuration.md#adding-models).

**An alternate endpoint** — put `name:` in front to route to a configured `[endpoints.<name>]`:

```bash
python main.py jh43 prompt -m my_cluster:llama-3-70b-instruct
python main.py jh43 translate jp-en -i paper.pdf -m my_cluster:llama-3-70b-instruct
```

The part before the colon names the endpoint; everything after is passed to it as the model name. The catalogue is bypassed entirely, and the endpoint's API key is read from `endpoints.<name>.key` in `settings.toml`. Token usage is still recorded.

**Everything at once** — if `[config] default_endpoint` is set in any settings layer, every bare model name goes there rather than to the built-in service.

See [Configuration → Alternate AI endpoints](configuration.md#alternate-ai-endpoints) for how to define one.

---

## Notes

`-n` / `--notes` asks where to put your note:

```
Add notes to (system / user / both / separate):
```

The inline flags set them directly, which is what you want in a script:

```bash
# A note on the system prompt
python main.py jh43 translate jp-en -i paper.pdf -ns "Preserve all footnote markers."

# The same note on both prompts
python main.py jh43 translate jp-en -i paper.pdf -nb "Academic journal style."

# Different notes on each
python main.py jh43 translate jp-en -i paper.pdf \
    -ns "Use formal register." \
    -nu "Preserve original paragraph breaks."
```

Notes are added as extra instructions alongside the default prompts. They never replace them.
