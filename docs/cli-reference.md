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

| Flag | Description |
|------|-------------|
| `-i <path>`, `--input <path>` | Input file (PDF, DOCX, or plain text) |
| `-o <path>`, `--output <path>` | Output file path |
| `-f <format>` | Output format: `txt`, `pdf`, `docx` |
| `-w <int>` | Parallel workers (default: 1 = sequential) |
| `-m <model>` | Model to use |
| `-t <float>` | Temperature override |
| `-T <float>` | Top-p override |
| `-M <int>` | Max response tokens |
| `-n`, `--notes` | Interactively append notes to system or user prompt |
| `-ns <text>` | Inline note appended to the system prompt |
| `-nu <text>` | Inline note appended to the user prompt |
| `-nb <text>` | Inline note appended to both prompts |
| `--dry-run` | Show prompts without API call |

### Examples

```bash
python main.py heller translate jp-en -i paper.pdf -o output.pdf -f pdf
python main.py heller translate zh-en -i article.docx -o translated.docx -f docx
python main.py heller translate en-jp -i notes.txt -o notes_jp.txt
python main.py heller translate jp-en -i paper.pdf -w 4   # parallel, 4 workers
python main.py heller translate jp-en -i paper.pdf --dry-run
```

---

## `transcribe` — OCR Transcription

Transcribe the text content of an image file (or folder of images) using OCR. Installed by the `transcription` plugin (bundled).

```bash
python main.py heller transcribe <language> [options]
```

**Language:** A single language code, e.g. `en`. Available codes depend on installed plugins.

### Options

| Flag | Description |
|------|-------------|
| `-i <path>`, `--input <path>` | Input image file or folder of images |
| `-o <path>`, `--output <path>` | Output file path |
| `-m <model>` | Model to use (must support vision) |
| `-t <float>` | Temperature override |
| `-T <float>` | Top-p override |
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
