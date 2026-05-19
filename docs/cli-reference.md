# CLI Reference

```
python main.py [--show-config | --list-models]
python main.py <professor> <command> [options]
```

`<professor>` is the safe-filename form of a professor's name as configured in `.env` (e.g. `heller`, `smith`). It is case-insensitive.

---

## Global Commands

These do not require a professor name.

### `--show-config`

Print all configured professors, their data-file paths, and whether those files exist.

```bash
python main.py --show-config
```

### `--list-models`

Print all models in `src/model_catalog.json` with their pricing and capability flags.

```bash
python main.py --list-models
```

---

## `usage` — Token Reporting

### `usage report [YYYY-MM] [--all-time]`

Print a usage report for the professor.

| Form | Output |
|------|--------|
| `usage report` | Current month + budget status |
| `usage report --all-time` | Above plus all-time totals from all archived months |
| `usage report 2025-07` | Report for the specified archived month |

```bash
python main.py heller usage report
python main.py heller usage report --all-time
python main.py heller usage report 2025-07
```

### `usage months`

List all archived month files for the professor.

```bash
python main.py heller usage months
```

### `usage daily [YYYY-MM-DD]`

Print usage for a single day (default: today).

```bash
python main.py heller usage daily
python main.py heller usage daily 2026-03-01
```

---

## `prompt` — Custom Prompt

Send a freeform prompt to the AI model. Text is entered interactively and terminated with `---` on its own line.

```bash
python main.py heller prompt [options]
```

### Options

| Flag | Description |
|------|-------------|
| `-s`, `--system` | Collect a system (developer) prompt before the user prompt |
| `-o <path>`, `--output <path>` | Save the response to a file |
| `-m <model>` | Model to use (see [Specifying Models](#specifying-models)) |
| `-t <float>` | Temperature override (0.0–2.0) |
| `-T <float>` | Top-p override (0.0–1.0) |
| `-M <int>` | Max response tokens |
| `--dry-run` | Show prompts without making an API call |

### Examples

```bash
python main.py heller prompt
python main.py heller prompt -s
python main.py heller prompt -o response.txt
python main.py heller prompt -m gpt-4o-mini
python main.py heller prompt -s --dry-run
```

---

## `translate` — Document Translation

Translate a document from one language to another. Installed by the `translation` plugin (bundled) and optionally extended by `translation-ea` for East Asian languages.

```bash
python main.py heller translate <language-pair> [options]
```

**Language pair format:** `source-target`, e.g. `jp-en`, `zh-en`, `en-jp`.  
Available codes depend on installed plugins. Run `python main.py --help` to see registered languages.

### Options

#### Input

| Flag | Description |
|------|-------------|
| `-i <path>`, `--input <path>` | Input file — mutually exclusive with `-c`; supported formats listed below |
| `-c`, `--custom` | Enter source text interactively (end with `---` on its own line) — mutually exclusive with `-i` |
| `-p <range>`, `--page_nums <range>` | Pages to process; see [Page Range Format](#page-range-format) below |

**Supported input formats:**

| Extension | Handling |
|-----------|----------|
| `.pdf` | Native text extraction (page by page); use `--scanned` for scanned PDFs |
| `.docx` | Body text extracted in document order; tables rendered as tab-separated rows |
| `.txt` | Split into logical pages by character count (`default_page_size`) |
| `.md` | Markdown formatting preserved as-is during processing |
| `.json` | Recursively flattened to human-readable key/value lines, then split into pages |
| `.xlsx` / `.xls` | Each sheet rendered as header + tab-separated rows, then split into pages (requires `openpyxl`) |
| Image files | `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp` — sent to vision model directly |

#### Output

| Flag | Description |
|------|-------------|
| `-o <path>`, `--output <path>` | Output file path; output format is inferred from the extension — see table below |
| `--auto-save` | Auto-save the result with a timestamp-based filename (produces `.txt`) |
| `--progressive-save` | Write each page to the output file as it completes (`.txt` and `.md` only; incompatible with `--preserve-media` and workers > 1) |

**Supported output formats:**

| Extension | Output |
|-----------|--------|
| `.txt` | Plain text; Markdown tables rendered as ASCII box tables |
| `.md` | Markdown; AI response written as-is (models already produce Markdown); supports progressive save |
| `.pdf` | PDF with CJK font support; Markdown tables rendered as proper tables |
| `.docx` | Word document; Markdown tables rendered as proper tables |
| `.xlsx` | Excel workbook — Markdown tables become separate sheets ("Table 1", "Table 2", …); prose goes to a "Text" sheet; requires `openpyxl` |
| `.json` | JSON file — if the AI response is valid JSON it is pretty-printed; otherwise wrapped as `{"content": "..."}` |

Unsupported extensions fall back to `.txt`.

#### Document options

| Flag | Description |
|------|-------------|
| `-a`, `--abstract` | Signal that the document begins with an abstract (provides context for the model) |
| `--toc` | Document contains a table of contents: normalize dot leaders (e.g. `............`) to five dots between titles and page numbers |
| `--preserve-tables` | Hint the model to return tabular data as Markdown tables; rendered as proper tables in PDF/DOCX or ASCII in TXT |
| `--scanned` | Treat the PDF as a scanned document: render each page as an image and process through the vision OCR pipeline (PDF only) |
| `--spread` | Input is a two-page spread (two facing pages scanned together); applies to image file inputs and `--scanned` PDFs |
| `--preserve-media` | Carry embedded images from a `.docx` or `.pdf` source into the translated `.docx` output (requires `-i *.docx` or `-i *.pdf` and `-o *.docx`) |

#### Formatting

| Flag | Description |
|------|-------------|
| `-f <name>`, `--font <name>` | Custom font name for PDF/Word output (font file must be in `fonts/`) |
| `--font-size <pt>` | Body font size in points for PDF/Word output (default from `settings.toml`) |

#### Model and performance

| Flag | Description |
|------|-------------|
| `-w <int>`, `--workers <int>` | Parallel page workers (default: 1 = sequential); workers > 1 disables progressive save and uses untranslated source text as context |
| `-m <model>` | Model to use (see [Specifying Models](#specifying-models)) |
| `-t <float>` | Temperature override (0.0–2.0) |
| `-T <float>` | Top-p override (0.0–1.0) |
| `-M <int>` | Max response tokens |

#### Notes and dry-run

| Flag | Description |
|------|-------------|
| `-n`, `--notes` | Interactively append notes to system or user prompt |
| `-ns <text>` | Inline note appended to the system prompt |
| `-nu <text>` | Inline note appended to the user prompt |
| `-nb <text>` | Inline note appended to both prompts |
| `--dry-run` | Show prompts without making an API call |

### Page Range Format

The `-p` / `--page_nums` flag accepts a comma-separated list of page numbers and ranges (1-based):

| Example | Pages processed |
|---------|----------------|
| `"5"` | Page 5 only |
| `"1-10"` | Pages 1 through 10 |
| `"4,15-17,20"` | Pages 4, 15, 16, 17, and 20 |
| `"4, 15-17, 20"` | Same as above (spaces allowed) |

When omitted, all pages are processed.

### Examples

```bash
python main.py heller translate jp-en -i paper.pdf -o output.pdf
python main.py heller translate zh-en -i article.docx -o translated.docx
python main.py heller translate en-jp -i notes.txt -o notes_jp.txt
python main.py heller translate jp-en -i paper.pdf -w 4           # parallel, 4 workers
python main.py heller translate jp-en -i paper.pdf -p "1-10"      # first 10 pages only
python main.py heller translate jp-en -i paper.pdf --scanned      # scanned PDF via vision
python main.py heller translate jp-en -i spread.jpg --spread      # two-page spread image
python main.py heller translate zh-en -i doc.docx -o out.docx --preserve-media
python main.py heller translate jp-en -i paper.pdf --preserve-tables
python main.py heller translate jp-en -i paper.pdf --dry-run
python main.py heller translate jp-en -c                          # paste text interactively
```

---

## `transcribe` — OCR Transcription

Transcribe the text content of an image file (or folder of images) using OCR. Installed by the `transcription` plugin (bundled). The `transcription-ea` extension plugin adds flags for East Asian languages (marked below).

```bash
python main.py heller transcribe <language> [options]
```

**Language:** A single language code, e.g. `en`. Available codes depend on installed plugins.

### Options

| Flag | Description |
|------|-------------|
| `-i <path>`, `--input <path>` | Input image file or folder of images |
| `-o <path>`, `--output <path>` | Output file path |
| `--spread` | Input is a two-page spread (two facing pages scanned together) *(requires `transcription-ea`)* |
| `-P <int>`, `--passes <int>` | Number of OCR passes; > 1 refines output through multiple rounds (default from `settings.toml`) *(requires `transcription-ea`)* |
| `--preserve-tables` | Hint the model to return tabular data as Markdown tables *(requires `transcription-ea`)* |
| `-m <model>` | Model to use (must support vision) |
| `-t <float>` | Temperature override (0.0–2.0) |
| `-T <float>` | Top-p override (0.0–1.0) |
| `-M <int>` | Max response tokens |
| `-n`, `--notes` | Interactively append notes to prompts |
| `-ns <text>` | Inline note for system prompt |
| `-nu <text>` | Inline note for user prompt |
| `-nb <text>` | Inline note for both prompts |
| `--dry-run` | Show prompts without API call |

### Examples

```bash
python main.py heller transcribe en -i scan.png -o transcription.txt
python main.py heller transcribe en -i scans/        # folder of images
python main.py heller transcribe en -i scan.png --dry-run
```

---

## `transcription_review` — OCR Error Review

Review the output of a prior transcription for OCR errors. Returns a JSON report. Installed by the `transcription` plugin.

```bash
python main.py heller transcription_review <language> [-i <file> | -c] [options]
```

Input is the **text output** of a prior transcription run — not the original image.

### Options

| Flag | Description |
|------|-------------|
| `-i <path>`, `--input <path>` | Path to a saved transcription text file |
| `-c`, `--custom` | Paste transcription text interactively (end with `---`) |
| `-o <path>`, `--output <path>` | Save the JSON review report to a file |
| `-m <model>` | Model to use |
| `-n`, `--notes` | Interactively append notes to prompts |
| `--dry-run` | Show prompts without API call |

### Examples

```bash
python main.py heller transcription_review en -i result.txt
python main.py heller transcription_review en -c
python main.py heller transcription_review en -i result.txt -o review.json
```

---

## Common Flags

These flags are available on all plugin commands (`prompt`, `translate`, `transcribe`, `transcription_review`):

| Flag | Description |
|------|-------------|
| `-o <path>`, `--output <path>` | Save output to file |
| `-m <model>`, `--model <model>` | Override the default model |
| `-t <float>`, `--temperature <float>` | Sampling temperature (0.0–2.0) |
| `-T <float>`, `--top-p <float>` | Nucleus sampling top-p (0.0–1.0) |
| `-M <int>`, `--max-tokens <int>` | Maximum response tokens |
| `--dry-run` | Show prompts that would be sent, without making an API call |

---

## Specifying Models

**Model already in catalog** — use the bare name:

```bash
python main.py heller prompt -m gpt-4o
python main.py heller prompt -m gpt-4o-mini
```

**Model not yet in catalog** — prefix with `provider/` to auto-register pricing from PortKey:

```bash
python main.py heller prompt -m openai/gpt-4o-new
python main.py heller prompt -m google/gemini-2.5-pro
```

Supported auto-registration providers: `openai`, `google`. For all other providers, add the model entry to `src/model_catalog.json` manually.

**Alternate endpoint with colon syntax** — prefix with `api_name:` to route to a
configured endpoint from `apis.json`. The model bypasses the catalog entirely and
is passed directly to the endpoint:

```bash
python main.py heller prompt -m my_cluster:llama-3-70b-instruct
python main.py heller translate jp-en -i paper.pdf -m my_cluster:llama-3-70b-instruct
python main.py heller prompt -m cloud_provider:model-name
```

The part before the colon is the endpoint key from `apis.json`; everything after
is the model name passed to that endpoint. The API key for the endpoint is read
from `API_<UPPERCASE_KEY>_KEY` in `.env`. Token usage is still recorded normally.

**Default endpoint** — if `apis.json` sets a `"default"` endpoint key, all bare
model strings (no colon, no `provider/` prefix) are routed there automatically.

See [Configuration → apis.json](configuration.md#apisjson----alternate-ai-endpoint-connections)
for how to define endpoints.

---

## Notes Flags

The `-n` / `--notes` flag launches an interactive prompt asking where to inject your note:

```
Add notes to (system / user / both / separate):
```

For non-interactive use, the inline flags `-ns`, `-nu`, and `-nb` set notes directly:

```bash
# Append a note to the system prompt
python main.py heller translate jp-en -i paper.pdf -ns "Preserve all footnote markers."

# Append the same note to both prompts
python main.py heller translate jp-en -i paper.pdf -nb "Academic journal style."

# Append different notes to each prompt
python main.py heller translate jp-en -i paper.pdf \
    -ns "Use formal register." \
    -nu "Preserve original paragraph breaks."
```

Notes are appended as additional instruction blocks to the relevant prompt and do not replace the default system or user prompt.
