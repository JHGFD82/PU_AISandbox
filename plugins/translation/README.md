# Translation Plugin (Base)

Built-in translation plugin that ships with [PU AI Sandbox](https://github.com/princeton-oit/PU_AISandbox). It is a **standalone plugin** — it registers the `translate` command and serves two roles:

1. **Service owner** — injects the shared service layer (`TranslationService`, `ImageTranslationService`, prompt specs, and `translation_fragments`) into `sys.modules` so that any other translation plugin can use them without bundling copies.

2. **English handler** — owns English as a source language and houses `_execute_translate()`, the shared execution function that all translation plugins delegate to.

This plugin does not need to be cloned or installed separately — it is part of the main repo.

---

## Dispatch Model

When an **extension plugin** (e.g. `plugins/translation-ea/`) is also installed, the plugin loader merges both plugins into a `DispatchPlugin`. The base plugin always drives translations whose **source** language is English. Extension plugins contribute routing for their declared languages.

Extension plugins must **not** call `register_subparsers()` — the `translate` command is already registered by this base plugin. They hook in by declaring `handles` (the source-language shortcodes they own) and implementing `register_command_flags()`. Calling `register_subparsers()` would cause a command conflict and the extension plugin would be silently skipped by the loader.

See [dispatch_plugin.py](../../src/runtime/dispatch_plugin.py) for the framework implementation and [docs/plugin-authoring-guide.md](../../docs/plugin-authoring-guide.md) for the full authoring guide.

---

## Adding a New Language Extension Plugin

This plugin's `plugin.py` is also the reference template for extension plugin authors. Key steps:

1. Clone `plugins/translation/plugin.py` into your new plugin directory.
2. Remove the `sys.modules` injection block — the base plugin already handles that.
3. Set `handles` to the source-language shortcodes your plugin owns.
4. Implement `register_command_flags()` to add your language-specific CLI flags. **Do not implement `register_subparsers()`.**
5. In `run()`, append your variant notes (if any) before calling `_base_module._execute_translate()`.
6. Optionally implement `get_peer_guidance(token)` to contribute destination-side conventions.

See the module docstring in `plugin.py` for a complete walkthrough.

---

## Configuration

### `settings.toml`

Controls default model parameters:

```toml
[translation]
temperature = 0.5          # 0.0 = deterministic, 2.0 = maximum creativity
top_p = 0.5                # Nucleus sampling threshold
max_tokens = 4000          # Maximum response tokens per page
context_percentage = 0.65  # Fraction of the previous page passed as context

[image_translation]
temperature = 0.3
max_tokens = 8000
```

The plugin searches upward from its own directory for the nearest `settings.toml` containing `[translation]` or `[image_translation]`, so edits to the root-level `settings.toml` in the main repo are also picked up.

---

## Usage

```bash
python main.py <professor> translate <language-code> [options]
```

### Examples

```bash
# Translate a PDF from English to Japanese:
python main.py heller translate E-J -i article.pdf -o article_ja.pdf

# Translate a Word document, preserving embedded images:
python main.py heller translate E-J -i paper.docx -o paper_ja.docx --preserve-media

# Translate specific pages only:
python main.py heller translate E-J -i book.pdf -p 5-10 -o ch5-10_ja.txt

# Enter custom text interactively:
python main.py heller translate E-J -c

# Translate in parallel (4 workers):
python main.py heller translate E-J -i long.pdf -o long_ja.pdf -w 4

# Dry run — print prompts without calling the API:
python main.py heller translate E-J -i article.pdf --dry-run
```

---

## Flag Reference

### Input / Output

| Flag | Description |
|------|-------------|
| `-i FILE`, `--input FILE` | Input file (PDF, DOCX, TXT, or image). Mutually exclusive with `-c`. |
| `-c`, `--custom` | Enter custom text interactively. Mutually exclusive with `-i`. |
| `-o FILE`, `--output FILE` | Output file path. Extension determines format: `.txt`, `.pdf`, `.docx`. |
| `-p RANGE`, `--page_nums RANGE` | Pages to process, e.g. `1` or `3-7` (PDF/DOCX only). |

### Output Format

| Flag | Description |
|------|-------------|
| `--auto-save` | Auto-save output with a timestamp suffix. |
| `--progressive-save` | Write each translated page immediately (text output only; disabled with `--workers > 1`). |
| `-f FONT`, `--font FONT` | Custom font name for PDF/DOCX output (must exist in `fonts/`). |
| `--font-size PT` | Body font size in points for PDF/DOCX output (default: 9). |

### Translation Behavior

| Flag | Description |
|------|-------------|
| `-a`, `--abstract` | Document has an abstract; used as context for the model. |
| `--toc` | Document has a table of contents: normalize dot leaders. |
| `--preserve-tables` | Return tabular data as Markdown tables. |

### Media and Document Structure

| Flag | Description |
|------|-------------|
| `--preserve-media` | Copy embedded images from `.docx`/`.pdf` source into the translated `.docx` output. |
| `--scanned` | Treat PDF as a scanned image: render each page and OCR+translate. Cannot combine with `--preserve-media`. |
| `--spread` | Input image is a two-page spread. Applies to image inputs and `--scanned` PDFs. |

### Parallelism

| Flag | Default | Description |
|------|---------|-------------|
| `-w N`, `--workers N` | 1 | Parallel translation workers. Workers > 1 uses untranslated source text as context and disables `--progressive-save`. |

### Model Overrides

| Flag | Description |
|------|-------------|
| `-m MODEL`, `--model MODEL` | Model to use (e.g. `gpt-4o`). |
| `-t FLOAT`, `--temperature FLOAT` | Sampling temperature (0.0–2.0). |
| `-T FLOAT`, `--top-p FLOAT` | Nucleus sampling top-p (0.0–1.0). |
| `-M INT`, `--max-tokens INT` | Maximum response tokens (overrides `settings.toml`). |

### Prompt Notes

| Flag | Description |
|------|-------------|
| `-n`, `--notes` | Interactively append ad-hoc notes to the system prompt, user prompt, or both. |
| `-ns TEXT`, `--note-system TEXT` | Inline note appended to the system prompt. |
| `-nu TEXT`, `--note-user TEXT` | Inline note appended to the user prompt. |
| `-nb TEXT`, `--note-both TEXT` | Inline note appended to both the system and user prompts. |

### Diagnostics

| Flag | Description |
|------|-------------|
| `--dry-run` | Print the prompt(s) without making any API calls. |
