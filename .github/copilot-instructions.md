# PU AI Sandbox - AI Coding Assistant Instructions

## Project Overview
Academic translation tool for Princeton University faculty to translate between Chinese, Japanese, Korean, and English using Azure OpenAI API. Features professor-specific API key management, per-professor token tracking, and privacy-friendly configuration.

## Architecture Pattern
**Multi-Professor Service Architecture**: Each professor has isolated API keys and token tracking through environment-based configuration.

- **Entry Point**: `main.py` → `src/cli.py` (controller/parser) → runtime handlers in `src/runtime/`
- **Core Services**: `TranslationService` and `ImageProcessorService` in `src/services/`, `TokenTracker` in `src/tracking/`, processors in `src/processors/`, `FileOutputHandler` in `src/output/`
- **Configuration**: Environment variables (`.env`) for professor configs, `model_catalog.json` for model pricing

## Professor Configuration System
The system uses a specific environment variable pattern:
```bash
PROF_[ID]_NAME=professor_name     # Display name
PROF_[ID]_KEY=primary_api_key     # Primary Azure OpenAI key
PROF_[ID]_BACKUP_KEY=backup_key   # Fallback key
```

**Safe Name Conversion**: Professor names are converted to safe filenames using `make_safe_filename()` - spaces become underscores, special chars removed. This affects:
- Token usage files: `data/token_usage_{safe_name}.json`
- CLI argument parsing and validation
- Error message formatting

## Token Tracking Architecture
**Per-Professor, Per-Month Isolation**: Each professor gets a separate active file for the current month, with past months automatically archived.
- **Active file**: `data/token_usage_{safe_name}.json` — covers the current calendar month only
- **Archives**: `data/archives/{safe_name}/{YYYY-MM}.json` — one file per past month, written automatically on month rollover
- **File structure**: `{month, total_usage, model_usage, daily_usage, session_history}` — all totals are for that month only
- **Pricing**: Loaded from `src/model_catalog.json` with configurable units (default: per 1M tokens)
- **Budget tracking**: Monthly limits with percentage warnings; resets naturally each month
- **All-time totals**: Computed on demand by aggregating the active file + all archive files via `get_all_time_usage()`

## Key Development Workflows

### Adding New Professors
1. Add to `.env`: `PROF_N_NAME=name`, `PROF_N_KEY=key`, `PROF_N_BACKUP_KEY=backup`
2. Run `python main.py --show-config` to verify configuration
3. Token tracking files auto-created on first use

### Testing CLI Changes
```bash
# Global commands (no professor required)
python main.py --help
python main.py --list-models

# Usage / reporting
python main.py heller usage report              # Current month + budget status
python main.py heller usage report --all-time   # Above + all-time totals
python main.py heller usage report 2025-07      # Report for a specific archived month
python main.py heller usage months              # List all archived month files
python main.py heller usage daily               # Today's usage
python main.py heller usage daily 2026-03-01    # Specific date

# Translation
python main.py heller translate CE -c                        # Custom text input
python main.py heller translate CE -i test.pdf               # PDF translation
python main.py heller translate CE -i test.docx              # Word document
python main.py heller translate CE -i test.txt               # Plain text file
python main.py heller translate CE -i test.pdf -p 1-5        # Page range
python main.py heller translate CE -i test.pdf -p "4,15-17,20,30-55"  # Multi-range
python main.py heller translate CE -i test.pdf -o out.docx   # Output as Word
python main.py heller translate CE -i test.pdf -w 4          # 4 parallel workers
python main.py heller translate CE -i test.docx -o out.docx --preserve-media  # Carry DOCX images into output Word doc
python main.py heller translate CE -i test.pdf -o out.docx --preserve-media   # Carry PDF images into output Word doc

# Transcription (OCR) — use single language char, not a pair
python main.py heller transcribe E -i test.jpg               # OCR to console
python main.py heller transcribe E -i test.jpg -o output.txt # OCR to file
python main.py heller transcribe E -i test.jpg -m gpt-4o-mini # Specific model
python main.py heller transcribe E -i ./scans/ -w 4          # Folder OCR, 4 parallel workers

# Transcription review (OCR error detection) — use single language char
python main.py heller transcription_review J -i transcription.txt   # Review a saved Japanese transcription
python main.py heller transcription_review J -c                      # Paste transcription text interactively
python main.py heller transcription_review J -i trans.txt -o report.json  # Save JSON report to file
python main.py heller transcription_review J --kanbun -i kanbun.txt  # Text contains kanbun annotations
python main.py heller transcription_review J -i trans.txt --dry-run  # Preview prompt without API call

# Custom prompts (fully interactive — text entered at runtime, end with ---)
python main.py heller prompt                                  # User prompt only
python main.py heller prompt -s                               # System prompt first, then user prompt
python main.py heller prompt -o response.txt                  # Save response to file
python main.py heller prompt -m gpt-4o-mini                   # Use specific model
python main.py heller prompt -s --dry-run                     # Preview prompts without API call
```

### Language Code Pattern
Two-character codes: `CE` (Chinese→English), `JK` (Japanese→Korean), etc.
- Parsed in `parse_language_code()` using `LANGUAGE_MAP` from `src/config.py`
- Validation ensures source ≠ target, valid language codes only

## Critical Implementation Details

### Progressive Saving
- **Text format only**: `--progressive-save` saves each page immediately as it finishes
- **Incompatible with parallel mode**: `--progressive-save` is silently disabled (with a printed warning) when `workers > 1`
- **Incompatible with --preserve-media**: combining these two raises a `CLIError` immediately.

### Media Preservation (`--preserve-media`)
- **Flag**: `--preserve-media` (boolean, translate command only)
- **Scope**: `.docx` or `.pdf` input with a `.docx` output (`-o`). Images extracted from the source are reinserted into the output Word document at proportional positions.
- **Incompatible combinations** (all raise `CLIError` before any API call):
  - `--progressive-save` — cannot combine
  - `-c` / `--custom` — pasted text has no embedded media
  - Non-`.docx`, non-`.pdf` input (TXT, image) — only DOCX and PDF extraction are implemented
  - No output path or `--auto-save` without `-o` — auto-save defaults to `.txt`
  - `.txt` output extension — cannot embed images
  - `.pdf` output extension — PDF output not yet implemented (use `.docx`)
- **DOCX extraction**: `DocxProcessor.extract_media(file_obj)` — iterates paragraph runs, finds `w:drawing` → `a:blip` elements, reads `ImagePart.blob`, records `position_fraction` (para_idx / total_paras), and EMU dimensions from `wp:extent`.
- **PDF extraction**: `PdfMediaExtractor.extract_media(file_obj)` (`src/processors/pdf_media_extractor.py`) — uses PyMuPDF (`fitz`); iterates pages, deduplicates by xref, computes `position_fraction = (page_index + y_frac) / total_pages`, converts pixel dims to EMU (12700 EMU/px at 72 DPI), skips images < 512 bytes.
- **Reinsertion**: `FileOutputHandler.save_to_docx(..., media=...)` — images are inserted using proportional positioning: each image is appended immediately after the translated paragraph whose cumulative fraction (`i / total_paras`) first meets or exceeds the image's `position_fraction`.
- **Data model**: `src/models/embedded_media.py` — `EmbeddedMedia` dataclass (`data`, `content_type`, `position_fraction`, `width_emu`, `height_emu`)
- **Pipeline flow**: `_run_translate` validates → `translate_document` extracts media via `DocxProcessor` or `PdfMediaExtractor` (before translation) → `save_translation_output` forwards media → `save_to_docx` reinserts
- **Dependencies**: `python-docx` (DOCX extraction/reinsertion), `pymupdf` (PDF extraction)

### Parallel Processing
- **Flag**: `-w N` / `--workers N` (default: `1` = sequential). The default is defined as `DEFAULT_PARALLEL_WORKERS` in `src/services/constants.py`.
- **Translation (`-w N`)**: Each page is sent as an independent `ThreadPoolExecutor` worker. Previous-page context is the **untranslated source text** of the prior page (not the prior translation). `context_length_exceeded` recursive splitting still works within each worker.
- **Transcription folder mode (`-w N`)**: Each image in a folder is dispatched to a separate worker. Multi-pass OCR (`-P N`) still runs sequentially within each worker. Workers have no effect for single-image input.
- **Prompt command**: No `-w` flag — always a single call.
- **Custom text (`-c`)**: `-w` is accepted but ignored (not paginated).
- **Temp files**: Results are written to numbered temp files so RAM stays bounded for large documents; temp directory is deleted in a `finally` block.
- **Rate limiting**: `PAGE_DELAY_SECONDS` between pages is removed in parallel mode; let the API handle rate limiting.
- **Thread safety**: `TokenTracker.record_usage()` is protected by an internal `threading.Lock`, so concurrent workers cannot corrupt token counts.
- **Worker capping**: If `workers > page_count`, the executor is silently capped to the number of pages (logged at INFO level).

### Error Handling Pattern
- **API Failures**: Automatic retries with exponential backoff in `TranslationService`
- **Progressive Saving**: Text-only (no PDF/Word support), saves each page immediately
- **Graceful Degradation**: Failed pages get error messages, processing continues

### File Output Strategy
- **Text Files**: Direct UTF-8 output with proper paragraph breaks
- **PDF Files**: Uses `reportlab` with CJK font support from `fonts/` directory
- **Word Documents**: Uses `python-docx` with CJK font support, professional formatting (1.5 line spacing, proper margins)
- **Auto-save**: Timestamped filenames with language codes, placed in source file directory
- **Absolute Path Resolution**: Input files converted to absolute paths to ensure output placement regardless of execution directory

### PDF Processing Specifics
- **CJK Optimization**: Custom `LAParams` in `PDFProcessor` for better CJK text extraction
- **Context Preservation**: Previous page context (65% of content) passed to translation
- **Page Range Support**: 1-based user input converted to 0-based internally; supports multi-range syntax `"4,15-17,20,30-55"` via `_parse_page_ranges()` (replaces `_parse_page_nums`)

### Output Format Support
- **Text Files (.txt)**: Direct UTF-8 output, supports progressive saving
- **PDF Files (.pdf)**: Uses `reportlab` with CJK font validation via `_get_cjk_font()`
- **Word Documents (.docx)**: Uses `python-docx` with CJK font selection via `_get_docx_font()`
- **Path Resolution**: Input files converted to absolute paths in CLI for consistent output placement
- **Progressive Save Limitation**: Only text format supports progressive saving; PDF/Word fall back to text

### Input File Support
- **PDF Files (.pdf)**: Full text extraction with page range support via `PDFProcessor`
- **Word Documents (.docx)**: Text-only extraction via `DocxProcessor`, split into logical sections
- **Text Files (.txt)**: Direct UTF-8 processing via `TxtProcessor`, automatic paragraph detection
- **Image Files (.jpg, .jpeg, .png, .gif, .bmp, .webp)**: OCR via `ImageProcessorService` with vision-capable models
- **Legacy PDF Argument**: `--input_PDF` maintained for backward compatibility, use `-i/--input` for new code
- **File Type Detection**: Automatic detection based on file extension; images automatically trigger OCR instead of translation

### Image OCR Processing
- **Automatic Detection**: Image files are detected by extension and routed to OCR automatically
- **Language Code**: Use single character (E, C, S, T, J, K) for target language, not translation pairs (CE, JE)
- **Vision Model Validation**: Automatically selects and validates vision-capable models from `model_catalog.json`
- **Token Tracking**: OCR usage tracked in same file as translation via shared `TokenTracker`
- **Output**: Extracted text printed to console and optionally saved to file with `-o` flag
- **Model Selection**: Use `-m/--model` to specify which model to use (e.g., `gpt-4o`, `gpt-4o-mini`, `gpt-5`)
- **Example**: `python main.py professor transcribe E -i image.jpg -o extracted.txt -m gpt-4o-mini`

### Model Selection and Configuration
- **Default Models**: `OCR_MODEL=gpt-4o-mini` for OCR, `DEFAULT_MODEL=gpt-4o` for translation
- **Custom Model**: Use `-m/--model MODEL_NAME` flag to override defaults for both translation and OCR
- **OpenAI/Google Auto-Registration**: Use `openai/model-name` or `google/model-name` with `-m` — if not already in the catalog, pricing is fetched from [PortKey](https://api.portkey.ai) and saved automatically on first use
- **Other Providers**: Add the model manually to `src/model_catalog.json`; edit the file directly following the template schema
- **Provider Slug Mapping**: PortKey uses different slugs for some providers (e.g. `google` → `vertex-ai`). These mappings live in `model_catalog.json` under `config.provider_map`, not in code.
- **List Models**: `python main.py --list-models` shows all catalog models with pricing and vision support
- **Vision Validation**: ImageProcessorService automatically validates model supports vision, falls back to defaults if not
- **Model Priority**: Custom model → OCR_MODEL/DEFAULT_MODEL → first available vision-capable model
- **Configuration**: Models and pricing defined in `src/model_catalog.json` (git-ignored; copy from `src/model_catalog.template.json` to set up) with `supports_vision` boolean flag
- **No CLI catalog management**: There are no CLI commands to add/update/sync models. Use `provider/model` with `-m` for auto-registration, or edit the JSON directly.

### File Path Handling
- **Input Path Resolution**: `os.path.abspath()` applied to input files in runtime processing methods
- **Output Path Resolution**: User-specified output paths converted to absolute paths in CLI `run()` method
- **Directory Placement**: Output files placed in same directory as source file via `generate_output_filename()`

## Custom Prompt Command
- **Command**: `python main.py <professor> prompt` — sends a freeform prompt without translation framing
- **Fully interactive**: no text arguments; user types input at runtime and ends with `---` on its own line
- **System prompt**: `-s/--system` is a boolean flag; when set, the system prompt is collected first, then the user prompt
- **Output**: response printed to console; optionally saved with `-o`
- **Dry run**: `--dry-run` shows prompt structure without making an API call
- **Token tracking**: usage tracked via the same `TokenTracker` as translation
- **Interactive helper**: `SandboxProcessor._collect_multiline(label)` is the shared static method used by all interactive `---`-terminated input (translate `-c`, prompt, and abstract input)

## External Dependencies
- **PortKey**: Uses `SANDBOX_ENDPOINT` and `SANDBOX_API_VERSION` from config
- **Font Management**: Custom fonts in `fonts/` directory for PDF and Word output
- **Document Generation**: `reportlab` for PDF, `python-docx` for Word documents
- **Image Processing**: `ImageProcessor` for image-to-data-url conversion, `ImageProcessorService` for OCR
- **Princeton-Specific**: API keys from Princeton's AI Sandbox service

## Common Patterns to Follow
- **Safe Name Usage**: Always use `make_safe_filename()` for file operations
- **Professor Context**: Pass professor name to `TokenTracker` and `TranslationService`
- **Configuration Loading**: Use `load_professor_config()` for env var parsing
- **Error Messages**: Include available professors and suggest both full/safe names
- **Logging**: Use structured logging with professor context where applicable

## Testing Without API Keys
Use `python main.py --show-config` to validate professor configuration without making API calls.

## Test Coverage Notes
- **Parallel translation**: `tests/test_parallel_translation.py` — order correctness, context passing, temp file cleanup, worker capping, progressive-save warning, context-length splitting inside workers
- **Parallel OCR**: `tests/test_parallel_ocr.py` — folder mode order, worker exceptions, multi-pass forwarding, worker capping, empty folder error
- **Thread safety**: `tests/test_token_tracker.py::TestConcurrentRecordUsage` — 16 concurrent `record_usage()` calls, exact token count, call count, session history length
- **Media preservation**: `tests/test_preserve_media.py` — `EmbeddedMedia` dataclass, `DocxProcessor.extract_media()`, CLI flag parsing, all incompatible-flag `CLIError` cases, `save_to_docx()` with positional reinsertion, `save_translation_output()` media forwarding

## Git Commit Workflow
- A `.gitmessage` template exists at the repo root — always follow its format when writing commits:
  - **Subject**: `<type>(<scope>): <short summary>` (imperative mood, ≤ 72 chars)
  - **Types**: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`
  - **Body sections**: `Why:`, `What changed:`, `Notes:` (when relevant)
- After making code changes, **propose a commit message** in this format for the user to review.
- **The user handles all git commits themselves. Never run `git commit` or `git add`.**
