# PU_AISandbox
Tools using the Princeton University AI Sandbox for East Asian language processing.

## Important Note
This application is designed exclusively for Princeton University faculty members (or delegates) with access to the AI Sandbox. You must have a valid Princeton University AI Sandbox API key to use this tool.

## Features
- Translate between Chinese, Japanese, Korean, and English in any direction
- Extract text from PDF files, Word documents (.docx), and plain text files (.txt) and translate
- Optical Character Recognition (OCR) from image files (.jpg, .png, .gif, .bmp, .webp)
- Support for custom text input
- Model selection for cost optimization and quality control
- List available models with pricing and capabilities
- Save translations to text files, PDF files, or Word documents (.docx)
- Auto-save functionality with timestamped filenames
- Page range selection for PDF processing
- Abstract context support for better translations
- CJK font support for PDF and Word document generation
- Per-professor token usage tracking and budget monitoring

## Architecture

Execution flow with package roles inline:

```text
main.py
  -> src/cli.py
     (controller + argument parser; validates command shape and routes execution)
     ↳ uses src/config.py
       (shared config, model catalog loading, language/page/professor parsing helpers)
  -> src/runtime/
     (runtime orchestration)
     - info_commands.py: list-models, usage-report
     - sandbox_processor.py: translation/OCR command orchestration
  -> src/services/
     (API-facing operations)
     - translation_service.py
     - image_processor_service.py
  -> src/processors/
     (PDF/DOCX/TXT/image preprocessing)
  -> src/tracking/token_tracker.py
     (token usage accounting + pricing lookup from src/model_catalog.json)
  -> src/output/file_output.py
     (text/pdf/docx output handling)
```

In short: `src/cli.py` decides *what* to run; `src/runtime/` and below decide *how* to run it.

## Requirements
- Python 3.7+
- Princeton University AI Sandbox access
- Valid AI Sandbox API key (Princeton University faculty only)

## Usage

### Basic Translation
```bash
# Translate PDF from Chinese to English
python main.py heller translate CE -i document.pdf

# Translate Word document from Japanese to Korean
python main.py smith translate JK -i document.docx

# Translate text file from Korean to English
python main.py heller translate KE -i document.txt

# Translate custom text from Japanese to Korean
python main.py smith translate JK -c
```

### Save Output Options
```bash
# Save translation to a specific text file
python main.py heller translate CE -i document.pdf -o translation.txt

# Save Word document translation to PDF
python main.py heller translate CE -i document.docx -o translation.pdf

# Save text file translation to Word document
python main.py heller translate CE -i document.txt -o translation.docx

# Auto-save with timestamp in the same directory as input
python main.py heller translate CE -i document.txt --auto-save
```

### Advanced Options
```bash
# Translate specific pages of PDF with abstract context
python main.py smith translate CE -i document.pdf -p 5-10 -a

# Translate text file and save to Word with custom font
python main.py heller translate JE -i document.txt -o translation.docx -f AppleGothic

# Use custom font for PDF/Word output
python main.py smith translate CE -i document.pdf -o translation.pdf -f AppleGothic

# Carry embedded images from a Word document into the translated Word document
python main.py heller translate CE -i document.docx -o translation.docx --preserve-media
```

**`--preserve-media` requirements and restrictions:**
- Requires a Word document (`.docx`) as input (`-i`)
- Requires an explicit `.docx` output file (`-o translation.docx`)
- Incompatible with `--progressive-save`, `--auto-save`, `-c` (custom text), and non-`.docx` output formats
- Images are reinserted at proportionally equivalent positions in the translated document (approximate due to paragraph count differences between source and translation)
- PDF media preservation is not yet supported

### Model Selection
```bash
# List all available models with pricing and vision support
python main.py --list-models

# Use a model already in model_catalog.json
python main.py heller translate CE -i document.pdf -m gpt-4o-mini

# First-time use of an OpenAI or Google model not yet in the catalog
# (auto-fetches pricing from PortKey and adds it to model_catalog.json)
python main.py heller translate CE -i document.pdf -m openai/gpt-4o-mini
python main.py heller transcribe E -i image.jpg -m google/gemini-2.5-flash

# After first use the short name works directly
python main.py heller translate CE -i document.pdf -m gpt-4o-mini

# Use different models for cost optimization
python main.py heller translate JE -i document.txt -m gpt-4o-mini  # Lower cost
python main.py heller translate CE -i complex.pdf -m gpt-4o        # Higher quality
```

**Adding models to the catalog:**
- **OpenAI or Google models**: Use `openai/model-name` or `google/model-name` with `-m` on the first invocation. Pricing is fetched automatically from [PortKey](https://api.portkey.ai) and saved to `src/model_catalog.json`.
- **All other models** (Mistral, Llama, etc.): Edit `src/model_catalog.json` directly. Copy `src/model_catalog.template.json` as a starting point if you don't have a catalog yet.

**Available models (examples — run `--list-models` for current list):**
- `gpt-4o-mini` - Most cost-effective, supports vision (recommended for OCR)
- `gpt-4o` - Balanced performance and cost, supports vision (default for translation)
- `gpt-5` - Latest model, supports vision

**Note:** OCR requires vision-capable models. If you specify a model that doesn't support vision for OCR operations, you'll receive an error.

### Image OCR Processing
```bash
# Extract text from image to console
python main.py heller transcribe E -i document_scan.jpg

# Extract text and save to file
python main.py smith transcribe J -i photo.png -o output.txt

# Use specific model for better accuracy
python main.py heller transcribe C -i scan.jpg -o chinese_text.txt -m gpt-4o

# Extract English text using cost-effective model
python main.py smith transcribe E -i receipt.jpg -o receipt.txt -m gpt-4o-mini
```

### Custom Prompts
Send a freeform prompt to the AI without any translation or OCR framing:

```bash
# Send a prompt (text entered interactively, end with ---)
python main.py heller prompt

# Include a system prompt first, then enter the user prompt
python main.py heller prompt -s

# Save the response to a file
python main.py heller prompt -o response.txt

# Use a specific model
python main.py heller prompt -m gpt-4o-mini

# Dry run — preview the prompts without making an API call
python main.py heller prompt -s --dry-run
```

When you run `prompt`, you are asked to type your input and finish with `---` on its own line. If `-s` is given, the system prompt is collected first, then the user prompt. Token usage is tracked the same way as translation.

### Token Usage Tracking
```bash
# Current month report + budget status
python main.py heller usage report

# Report for a specific archived month
python main.py heller usage report 2025-07

# Current month + all-time totals across all archived months
python main.py heller usage report --all-time

# List all archived month files
python main.py heller usage months

# Today's usage
python main.py heller usage daily

# Usage for a specific date
python main.py heller usage daily 2026-02-14
```

### Progressive Saving
For large documents or unreliable connections, use progressive saving to save each page immediately after translation:

```bash
# Save each page as it's translated (useful for error recovery)
python main.py heller translate CE -i large_document.txt --progressive-save -o output.txt

# Combine with auto-save for timestamped progressive saving
python main.py smith translate JK -i document.txt --progressive-save --auto-save
```

**Note:** When using `--progressive-save`, the translation is saved incrementally as each page is processed, which helps prevent data loss if the process is interrupted. Progressive saving only supports text file output (.txt) - PDF and Word document output are not compatible with progressive saving.

**Incompatible with parallel mode:** `--progressive-save` is automatically disabled (with a printed warning) when `-w/--workers` is greater than 1, because pages complete in non-deterministic order.

### Parallel Processing
Speed up translation of long documents and OCR of image folders by dispatching multiple pages or images simultaneously:

```bash
# Translate a PDF with 4 parallel workers
python main.py heller translate CE -i document.pdf -w 4

# Transcribe a folder of images with 4 parallel workers
python main.py heller transcribe E -i ./scans/ -w 4

# Parallel workers + output file
python main.py heller translate CE -i large.docx -w 8 -o output.txt
```

**How parallel translation context works:** In sequential mode the previous page's *translation* is used as context for the next page. In parallel mode (workers > 1), each page instead receives the *untranslated source text* of the prior page, because translations are not available in order. For most documents this produces equivalent quality; for highly sequential content (e.g. numbered lists spanning many pages) sequential mode may be preferable.

**Key behaviour notes:**
- `words=1` (the default) is identical to the previous sequential behaviour
- Result order is preserved: pages/images are always assembled in their original order regardless of which worker finishes first
- Results are written to temp files during processing (RAM stays bounded for large documents)
- The per-page delay (`PAGE_DELAY_SECONDS`) is removed in parallel mode; the API handles rate limiting
- `context_length_exceeded` recursive splitting still works inside each parallel worker
- For transcription, `-w` only applies when processing a *folder* of images; it is ignored for single-image input
- The `prompt` command has no `-w` flag (it is always a single API call)
- The easy-to-adjust default worker count lives in `src/services/constants.py` as `DEFAULT_PARALLEL_WORKERS`

### Error Handling and Retries
The system includes robust error handling for API failures:

- **Automatic Retries**: If the API returns an error, the system will automatically retry the translation
- **Error Recovery**: If retries fail, the system will insert a translation error message and continue to the next page
- **Graceful Degradation**: Processing continues even when individual pages fail, ensuring you get translations for successful pages

This ensures that temporary API issues or problematic pages don't stop the entire translation process.

## Input File Support

### PDF Files (.pdf)
- Full text extraction with CJK optimization
- Page range selection support (`-p` option)
- Maintains document structure and context

### Word Documents (.docx)
- Text extraction from paragraphs and sections
- Images and embedded objects are ignored (text-only processing)
- Document is automatically split into logical sections for optimal translation
- Page number selection not supported (entire document is processed)

### Text Files (.txt)
- Direct UTF-8 text processing
- Automatic paragraph detection and logical section splitting
- Preserves original text structure and formatting
- Page number selection not supported (entire file is processed)

### Image Files (.jpg, .jpeg, .png, .gif, .bmp, .webp)
- Optical Character Recognition (OCR) using vision-capable models
- Extracts text from images containing CJK or English text
- Requires vision-capable models (gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-5)
- Use single-character language code for target language (E, C, S, T, J, K)
- Example: `python main.py heller transcribe E -i photo.jpg -o extracted.txt`

**Note:** For Word document input, only .docx format is supported. Legacy .doc files are not supported.

## Language Codes

### For Translation (two-character codes)
- `C` = Chinese (generic)
- `S` = Simplified Chinese
- `T` = Traditional Chinese
- `J` = Japanese
- `K` = Korean
- `E` = English

Examples:
- `CE` = Chinese to English
- `SE` = Simplified Chinese to English
- `TE` = Traditional Chinese to English
- `JK` = Japanese to Korean
- `EJ` = English to Japanese
- `ET` = English to Traditional Chinese
- `KC` = Korean to Chinese

### For OCR (single-character codes)
- `E` = Extract as English
- `C` = Extract as Chinese (generic)
- `S` = Extract as Simplified Chinese
- `T` = Extract as Traditional Chinese
- `J` = Extract as Japanese
- `K` = Extract as Korean

**Note:** OCR uses single-character codes to specify the target language, while translation uses two-character codes to specify source and target. S and T are valid only for OCR (transcription); for translation, use them as part of a two-character code such as `SE`, `TE`, `ES`, or `ET`.

## Installation

1. Install required packages:
```bash
pip install -r requirements.txt
```

   **Note:** For Word document (.docx) export functionality, ensure `python-docx` is installed. If you encounter issues with Word document export, you can install it separately:
   ```bash
   pip install python-docx
   ```

2. Set up professor configuration in `.env` file:

   Copy the example file and configure professors:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` and add professor configurations:
   ```env
   # Professor Configuration
   PROF_1_NAME=heller
   PROF_1_KEY=your_api_key_here
   PROF_1_BACKUP_KEY=your_backup_key_here
   
   PROF_2_NAME=smith
   PROF_2_KEY=another_api_key_here
   PROF_2_BACKUP_KEY=another_backup_key_here
   ```
   
   **Getting your AI Sandbox API key:**
   This package allows for management of multiple faculty, however each faculty member must register your keys individually. Princeton University faculty can obtain your AI Sandbox API keys through OIT.
   
   **Professor Selection:**
   Use professor names as configured in the `.env` file. The system supports:
   - Simple names (just last names work fine: `heller`, `smith`)
   - Full names (if they contain spaces, use quotes: `"john smith"`)
   - Safe names (spaces converted to underscores: `john_smith`)
   
   Run `python main.py --show-config` to see available professors and their safe names.

## Token Usage Tracking

Each professor has separate token usage tracking and monthly budgets:
- **Active file**: `data/token_usage_{name}.json` — current calendar month only
- **Archives**: `data/archives/{name}/{YYYY-MM}.json` — one file per past month, written automatically on the first use of a new month
- All totals in each file cover that month only; no single file grows indefinitely
- Monthly limits are configurable in `src/model_catalog.json`
- All-time totals are computed on demand by aggregating the active file with all archive files (`usage report --all-time`)

## Model Catalog

Model pricing and capabilities are stored locally in `src/model_catalog.json` (not tracked by git — each installation has its own copy).

**Setting up the catalog:**
1. Copy the template: `cp src/model_catalog.template.json src/model_catalog.json`
2. Add models in one of two ways:
   - **OpenAI or Google models**: Just use `openai/model-name` or `google/model-name` with `-m` on first run — pricing is fetched automatically from [PortKey](https://api.portkey.ai) and saved
   - **All other models**: Edit `src/model_catalog.json` directly, following the schema in the template

**Manual catalog entry format:**
```json
{
  "config": { "pricing_unit": 1000000, "monthly_limit": 250.0 },
  "models": {
    "mistral-small": {
      "input": 0.1,
      "output": 0.3,
      "supports_vision": false
    }
  }
}
```
Prices are per `pricing_unit` tokens (default 1,000,000).  Set `supports_vision: true` for models that accept image input.

## Output File Formats

### Text Files (.txt)
- UTF-8 encoded
- Preserves original formatting
- Ideal for further processing

### PDF Files (.pdf)
- Professional formatting
- Supports CJK characters
- Suitable for sharing and archiving

### Word Documents (.docx)
- Microsoft Word compatible
- Professional formatting with CJK font support
- Editable output for further customization
- 1.5 line spacing and proper margins

### Auto-Save Naming
When using `--auto-save`, files are saved with the format:
```
[input_filename]_[source_lang]to[target_lang]_[timestamp].[extension]
```

Example: `document_ChinesetoEnglish_20250710_152416.txt`
