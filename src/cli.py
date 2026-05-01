"""CLI controller: argument parsing, validation, and top-level dispatch."""

import argparse
import logging
import sys

from dotenv import load_dotenv

from .config import parse_language_code, parse_single_language_code, validate_page_nums
from .errors import CLIError
from .runtime import SandboxProcessor, handle_info_commands
from .services.constants import DEFAULT_PARALLEL_WORKERS
from .settings import DEFAULT_OCR_PASSES

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Set up logging configuration."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by translate, transcribe, and prompt subparsers."""
    parser.add_argument('-o', '--output', dest='output_file', type=str, help='Output file path')
    parser.add_argument('-m', '--model', dest='model', type=str, help='Model to use (e.g., gpt-4o, gpt-4o-mini)')
    parser.add_argument('-t', '--temperature', dest='temperature', type=float, default=None, help='Sampling temperature override (0.0–2.0)')
    parser.add_argument('-T', '--top-p', dest='top_p', type=float, default=None, help='Nucleus sampling top-p override (0.0–1.0)')
    parser.add_argument('-M', '--max-tokens', dest='max_tokens', type=int, default=None, help='Maximum response tokens (overrides model default)')
    parser.add_argument('--dry-run', dest='dry_run', action='store_true', help='Print the prompt(s) that would be sent without making any API calls')


def _add_notes_flags(parser: argparse.ArgumentParser) -> None:
    """Add the interactive and inline notes flags to a subparser."""
    parser.add_argument(
        '-n', '--notes',
        dest='notes',
        action='store_true',
        help='Interactively append ad-hoc notes to the system prompt, user prompt, or both before sending',
    )
    parser.add_argument('-ns', '--note-system', dest='note_system', type=str, default=None, metavar='TEXT',
                        help='Inline note appended to the system prompt')
    parser.add_argument('-nu', '--note-user', dest='note_user', type=str, default=None, metavar='TEXT',
                        help='Inline note appended to the user prompt')
    parser.add_argument('-nb', '--note-both', dest='note_both', type=str, default=None, metavar='TEXT',
                        help='Inline note appended to both the system and user prompts')


def create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description='Translate documents between Chinese, Japanese, Korean, and English using Princeton AI Sandbox',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\nLanguage codes:
  Two characters for translation (source→target): CE, EC, JE, EJ, KE, EK, JK, KJ, SE, TE, ...
  One character  for transcription (OCR):          E (English), C (Chinese), S (Simplified Chinese),
                                                    T (Traditional Chinese), J (Japanese), K (Korean)

Usage / reporting:
  python main.py heller usage report              Current month report + budget status
  python main.py heller usage report --all-time   Above + all-time totals across all archived months
  python main.py heller usage report 2025-07      Report for a specific archived month
  python main.py heller usage months              List all archived month files
  python main.py heller usage daily               Today's usage
  python main.py heller usage daily 2026-03-01    Usage for a specific date

Global commands (no professor required):
  python main.py --show-config
  python main.py --list-models

Specifying a model (-m / --model):
  Already in catalog — use the bare model name:
    python main.py heller translate CE -m gpt-4o
    python main.py heller translate CE -m gpt-4o-mini
  Not yet in catalog — use 'provider/model' to auto-register from PortKey:
    python main.py heller translate CE -m openai/gpt-4o-new
    python main.py heller translate CE -m google/gemini-2.5-pro
  Supported auto-register providers: openai, google.
  For all other providers, add the model directly to src/model_catalog.json.

Translation:
  python main.py heller translate CE -i document.pdf
  python main.py heller translate JE -i document.docx -p 1-5   Specific page range
  python main.py heller translate KE -c                         Custom text input
  python main.py heller translate CE -i doc.pdf -a              Supply an abstract for context
  python main.py heller translate CE -i doc.pdf -o out.docx     Save as Word document
  python main.py heller translate CE -i doc.pdf --auto-save     Auto-save with timestamp
  python main.py heller translate CE -i doc.pdf --progressive-save  Save each page immediately
  python main.py heller translate CE -i doc.pdf -f MyFont       Custom font (must be in fonts/)
  python main.py heller translate CE -i doc.pdf -m gpt-4o       Use a specific model
  python main.py heller translate CE -i doc.pdf --dry-run       Preview prompt without API call
  python main.py heller translate CE -i doc.pdf -n              Append ad-hoc notes to prompt
  python main.py heller translate CE -i doc.pdf -w 4            Translate 4 pages in parallel
  python main.py heller translate JE -i spread.jpg --spread     Image is a two-page spread (images only)
  python main.py heller translate CE -i doc.docx -o out.docx --preserve-media  Carry images from source Word doc into output
  python main.py heller translate JE -i scanned_book.pdf --scanned            Treat PDF pages as scanned images (OCR+translate via vision model)
  python main.py heller translate JE -i scanned_book.pdf --scanned -w 4       Scanned PDF with 4 parallel workers
    Note: -w > 1 passes untranslated source text as context (not prior translation) and
    disables --progressive-save. Each page's context_length_exceeded splitting still works.
    --preserve-media requires a .docx input and a .docx output file (-o out.docx).
    --scanned is only valid for PDF inputs; incompatible with --preserve-media.

Transcription (OCR):
  python main.py heller transcribe E -i image.jpg
  python main.py heller transcribe E -i image.jpg -o output.txt
  python main.py heller transcribe E -i ./scans/                Folder of images (sorted by name)
  python main.py heller transcribe E -i ./scans/ -o combined.txt  Combine all results into one file
  python main.py heller transcribe E -i ./scans/ -w 4           Process 4 images in parallel
    Note: -w only applies to folder mode; ignored for single-image input.
  python main.py heller transcribe S -i scan.png                Simplified Chinese
  python main.py heller transcribe T -i scan.png                Traditional Chinese
  python main.py heller transcribe J -i scan.png                Japanese (kanji + kana)
  python main.py heller transcribe C -i scan.png -m gpt-4o-mini Use a specific model
  python main.py heller transcribe J -i scan.png -v             Vertical text layout
  python main.py heller transcribe J -i scan.png --spread        Two-page spread (facing pages)
  python main.py heller transcribe J -i scan.png --kanbun        Kanbun (漢文) with 返り点/送り仮名 annotations
  python main.py heller transcribe E -i image.jpg --dry-run     Preview prompt without API call
  python main.py heller transcribe E -i image.jpg -n            Append ad-hoc notes to prompt

Custom prompt:
  python main.py heller prompt                   Interactive user prompt
  python main.py heller prompt -s                System prompt first, then user prompt
  python main.py heller prompt -o response.txt   Save response to file
  python main.py heller prompt -m gpt-4o-mini    Use a specific model
  python main.py heller prompt --dry-run         Preview prompt without API call

Transcription review (OCR error detection):
  python main.py heller transcription_review J -i transcription.txt   Review a saved Japanese transcription
  python main.py heller transcription_review J -c                      Paste transcription text interactively
  python main.py heller transcription_review J -i trans.txt -o report.json  Save JSON report to file
  python main.py heller transcription_review J --kanbun -i kanbun.txt  Text contains kanbun annotations
  python main.py heller transcription_review J -i trans.txt --dry-run  Preview prompt without API call
        """,
    )

    # Global commands (no professor required)
    parser.add_argument(
        '--show-config',
        dest='show_config',
        action='store_true',
        help='Show professor configuration and data-file status',
    )
    parser.add_argument(
        '--list-models',
        dest='list_models',
        action='store_true',
        help='List all available models and their capabilities',
    )

    # Professor-based commands use subparsers
    parser.add_argument(
        'professor',
        type=str,
        nargs='?',
        help='Professor name for API key lookup',
    )

    # Add subparsers for commands (usage, translate, transcribe)
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # ===== USAGE COMMAND =====
    usage_parser = subparsers.add_parser('usage', help='View token usage and costs')
    usage_subparsers = usage_parser.add_subparsers(dest='usage_subcommand', help='Usage subcommand')

    # usage report [YYYY-MM]
    report_parser = usage_subparsers.add_parser(
        'report',
        help='Display usage report (current month by default)',
    )
    report_parser.add_argument(
        'month',
        type=str,
        nargs='?',
        default=None,
        metavar='YYYY-MM',
        help='Archived month to report on (e.g. 2025-07). Omit for current month.',
    )
    report_parser.add_argument(
        '--all-time',
        action='store_true',
        default=False,
        help='Include all-time totals aggregated from all archived months (current month only)',
    )

    # usage months
    usage_subparsers.add_parser('months', help='List all archived months for this professor')

    # usage daily [date]
    daily_parser = usage_subparsers.add_parser('daily', help='Display daily usage')
    daily_parser.add_argument(
        'date',
        type=str,
        nargs='?',
        default='today',
        help='Date in YYYY-MM-DD format (defaults to today)',
    )

    # ===== TRANSLATE COMMAND =====
    translate_parser = subparsers.add_parser('translate', help='Translate documents or text')
    translate_parser.add_argument(
        'language_code',
        type=parse_language_code,
        help='Translation direction (CE, JE, KE, etc.)',
    )

    # Input options
    input_group = translate_parser.add_mutually_exclusive_group(required=False)
    input_group.add_argument('-i', '--input', dest='input_file', type=str, help='Input file path (PDF, DOCX, TXT)')
    input_group.add_argument('-c', '--custom', dest='custom_text', action='store_true', help='Input custom text')

    # Translation options
    translate_parser.add_argument(
        '-p', '--page_nums',
        dest='page_nums',
        type=validate_page_nums,
        help='Page numbers to process (e.g., "1" or "1-5")',
    )
    translate_parser.add_argument('-a', '--abstract', dest='abstract', action='store_true', help='Text has an abstract')
    translate_parser.add_argument('--auto-save', dest='auto_save', action='store_true', help='Auto-save with timestamp')
    translate_parser.add_argument(
        '--progressive-save',
        dest='progressive_save',
        action='store_true',
        help='Save each page immediately (text output only)',
    )
    translate_parser.add_argument('-f', '--font', dest='custom_font', type=str, help='Custom font name (must be in fonts/)')
    translate_parser.add_argument(
        '--kanbun',
        dest='kanbun',
        action='store_true',
        help='Source text is kanbun (漢文): apply kundoku word-order reconstruction and Classical Chinese reading conventions',
    )
    translate_parser.add_argument(
        '-w', '--workers',
        dest='workers',
        type=int,
        default=DEFAULT_PARALLEL_WORKERS,
        metavar='N',
        help=(
            'Number of parallel translation workers (default: %(default)s). '
            'Each page is sent as an independent API call. '
            'Workers > 1 uses untranslated source text as context and disables progressive save.'
        ),
    )
    translate_parser.add_argument('--spread', dest='spread', action='store_true', help='Image is a two-page spread (two facing pages scanned together); applies to image file inputs and --scanned PDFs')
    translate_parser.add_argument(
        '--scanned',
        dest='scanned',
        action='store_true',
        help='Treat the PDF as a scanned image document: each page is rendered as an image and processed via the OCR+translation pipeline (vision model). PDF only.',
    )
    translate_parser.add_argument(
        '--preserve-media',
        dest='preserve_media',
        action='store_true',
        help='Carry embedded images from a .docx source into the translated .docx output (requires -i *.docx and -o *.docx)',
    )
    _add_common_flags(translate_parser)
    _add_notes_flags(translate_parser)

    # ===== TRANSCRIBE COMMAND =====
    transcribe_parser = subparsers.add_parser('transcribe', help='Transcribe images using OCR')
    transcribe_parser.add_argument(
        'language_code',
        type=parse_single_language_code,
        help='Target language: E (English), C (Chinese), S (Simplified Chinese), T (Traditional Chinese), J (Japanese), K (Korean)',
    )
    transcribe_parser.add_argument('-i', '--input', dest='input_file', type=str, required=False, help='Input image file path, or a folder of images to process in order')
    transcribe_parser.add_argument('-v', '--vertical', dest='vertical', action='store_true', help='Text is predominantly vertical (top-to-bottom, right-to-left columns)')
    transcribe_parser.add_argument('--spread', dest='spread', action='store_true', help='Image is a two-page spread (two facing pages scanned together)')
    kanbun_group = transcribe_parser.add_mutually_exclusive_group()
    kanbun_group.add_argument(
        '--kanbun',
        dest='kanbun',
        action='store_true',
        help='Image contains kanbun (漢文): preserve 返り点, 送り仮名, and other kundoku annotations exactly as written',
    )
    kanbun_group.add_argument(
        '--kanbun-main',
        dest='kanbun_main',
        action='store_true',
        help='Image contains kanbun (漢文): transcribe ONLY the large main-line kanji; omit okurigana, furigana, kaeriten, and other small annotations',
    )
    transcribe_parser.add_argument('-P', '--passes', dest='passes', type=int, default=DEFAULT_OCR_PASSES, metavar='N', help='Number of OCR passes (default: 1). Passes > 1 send the image and prior transcription back to the model for review and correction.')
    transcribe_parser.add_argument(
        '-w', '--workers',
        dest='workers',
        type=int,
        default=DEFAULT_PARALLEL_WORKERS,
        metavar='N',
        help=(
            'Number of parallel OCR workers when processing a folder of images (default: %(default)s). '
            'Ignored for single-image input. Multi-pass OCR within each image always runs sequentially.'
        ),
    )
    _add_common_flags(transcribe_parser)
    _add_notes_flags(transcribe_parser)

    # ===== PROMPT COMMAND =====
    prompt_parser = subparsers.add_parser('prompt', help='Send a custom prompt to the AI model')
    prompt_parser.add_argument(
        '-s', '--system',
        dest='include_system_prompt',
        action='store_true',
        help='Prompt for a system (developer) prompt before the user prompt',
    )
    _add_common_flags(prompt_parser)

    # ===== TRANSCRIPTION REVIEW COMMAND =====
    review_parser = subparsers.add_parser(
        'transcription_review',
        help='Review AI transcription output for OCR errors (returns JSON report)',
    )
    review_parser.add_argument(
        'language_code',
        type=parse_single_language_code,
        help='Language of the transcription: E (English), C (Chinese), S (Simplified Chinese), T (Traditional Chinese), J (Japanese), K (Korean)',
    )
    review_input_group = review_parser.add_mutually_exclusive_group(required=False)
    review_input_group.add_argument(
        '-i', '--input',
        dest='input_file',
        type=str,
        help='Path to a text file containing the transcription result to review',
    )
    review_input_group.add_argument(
        '-c', '--custom',
        dest='custom_text',
        action='store_true',
        help='Paste the transcription text interactively (end with --- on its own line)',
    )
    review_kanbun_group = review_parser.add_mutually_exclusive_group()
    review_kanbun_group.add_argument(
        '--kanbun',
        dest='kanbun',
        action='store_true',
        help='Text contains kanbun (漢文) with kundoku annotations (返り点, 送り仮名)',
    )
    review_kanbun_group.add_argument(
        '--kanbun-main',
        dest='kanbun_main',
        action='store_true',
        help='Transcription was produced in main-character-only mode (okurigana, furigana, kaeriten were omitted intentionally — do not flag their absence as errors)',
    )
    _add_common_flags(review_parser)
    _add_notes_flags(review_parser)

    return parser


def main() -> None:
    """Main entry point for the CLI application."""
    setup_logging()

    try:
        parser = create_argument_parser()
        args = parser.parse_args()

        # Handle global commands (no professor required)
        if args.show_config or args.list_models:
            if handle_info_commands(args):
                return

        # All other commands require professor name
        if not args.professor:
            raise CLIError(
                "Professor name is required.\n"
                "Usage: python main.py <professor_name> <command> [options]\n"
                "\nAvailable commands:\n"
                "  usage report [YYYY-MM] [--all-time]  Token usage report\n"
                "  usage months                         List archived month files\n"
                "  usage daily [YYYY-MM-DD]             Daily usage\n"
                "  translate <code> -i <file>           Translate a document\n"
                "  transcribe <lang> -i <image>         OCR an image\n"
                "  transcription_review <lang> -i <file>  Review a transcription for OCR errors\n"
                "\nOr for global commands: python main.py --show-config | --list-models"
            )

        # Handle professor-specific commands
        if not args.command:
            raise CLIError(
                f"No command specified for professor '{args.professor}'.\n"
                "\nAvailable commands:\n"
                "  usage report [YYYY-MM] [--all-time]  Token usage report\n"
                "  usage months                         List archived month files\n"
                "  usage daily [YYYY-MM-DD]             Daily usage\n"
                "  translate <code> -i <file>           Translate a document\n"
                "  transcribe <lang> -i <image>         OCR an image\n"
                "  transcription_review <lang> -i <file>  Review a transcription for OCR errors\n"
                "\nRun 'python main.py --help' for full usage information."
            )

        # Route to appropriate handler
        if args.command == 'usage':
            if handle_info_commands(args):
                return
        elif args.command in ('translate', 'transcribe', 'prompt', 'transcription_review'):
            model = getattr(args, 'model', None)
            temperature = getattr(args, 'temperature', None)
            top_p = getattr(args, 'top_p', None)
            max_tokens = getattr(args, 'max_tokens', None)
            sandbox = SandboxProcessor(args.professor, model=model, temperature=temperature, top_p=top_p, max_tokens=max_tokens)
            sandbox.run(args)
        else:
            raise CLIError(f"Unknown command: {args.command}")

    except CLIError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
