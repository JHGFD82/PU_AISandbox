"""Translation service for the PU AI Sandbox."""

import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Iterable
from itertools import islice
from tqdm import tqdm

from pdfminer.pdfpage import PDFPage

from ..models import (
    resolve_model, get_model_system_role,
    maybe_sync_model_pricing, get_model_max_completion_tokens,
    OutputOptions,
)
from .api_errors import APISignal
from .base_service import BaseService
from .parallel_utils import tqdm_logging, update_pbar_postfix
from .prompts import TranslationPromptSpec
from ..output.file_output import FileOutputHandler
from ..processors.pdf_processor import PDFProcessor, generate_process_text, detect_numbered_content
from ..tracking.token_tracker import TokenTracker
from .constants import PAGE_DELAY_SECONDS, MAX_PARALLEL_WORKERS
from ..settings import (
    TRANSLATION_TEMPERATURE,
    TRANSLATION_MAX_TOKENS,
    TRANSLATION_TOP_P,
    CONTEXT_PERCENTAGE,
)

# Regex pattern for matching citation/reference numbers in CJK and ASCII brackets
_CITATION_NUM_RE: str = r'[（\(](\d+)[）\)]'


class TranslationService(BaseService):
    """Handles translation operations using PortKey API."""

    def __init__(self, api_key: str, professor: Optional[str] = None, token_tracker: Optional[TokenTracker] = None, token_tracker_file: Optional[str] = None, model: Optional[str] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, max_tokens: Optional[int] = None):
        super().__init__(api_key, professor, token_tracker, token_tracker_file, model, temperature, top_p, max_tokens)
        self.pdf_processor = PDFProcessor()
        # Set to True in parallel mode to suppress per-page console output
        self._suppress_inline_print: bool = False
        self.kanbun: bool = False
        self.tables: bool = False
        # Tracks image-only (blank) pages skipped during a translation run
        self._blank_page_count: int = 0
        self._blank_page_lock = threading.Lock()
        # Tracks API/connection errors batched for summary in parallel mode
        self._api_error_count: int = 0
        self._api_error_lock = threading.Lock()
    
    def _get_model(self) -> str:
        """Get the model to use, preferring custom model if specified."""
        model = resolve_model(requested_model=self.custom_model)
        maybe_sync_model_pricing(model)
        return model

    def _call_translation_api(self, model: str, system_role: str,
                               system_prompt: str, user_prompt: str) -> Any:
        """Call the translation API with the correct token-limit parameter for the model."""
        temperature = self.custom_temperature if self.custom_temperature is not None else TRANSLATION_TEMPERATURE
        top_p = self.custom_top_p if self.custom_top_p is not None else TRANSLATION_TOP_P
        if self.custom_temperature is not None or self.custom_top_p is not None:
            logging.debug(f"Translation API params: temperature={temperature}, top_p={top_p}")
        messages = [
            {"role": system_role, "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        max_tokens = self.custom_max_tokens if self.custom_max_tokens is not None else get_model_max_completion_tokens(model, TRANSLATION_MAX_TOKENS)
        return self._create_completion(
            model, messages, max_tokens,
            temperature=temperature, top_p=top_p,
        )
    
    def _create_translation_prompt(self, source_language: str, target_language: str, output_format: str = "console", text: str = "", context_type: str = "none") -> tuple[str, str]:
        """Create system and user prompt templates for translation."""
        has_numbered = detect_numbered_content(text) if text else False
        has_table_markers = '[TABLE_' in text if text else False
        logging.debug(f"Numbered content detected: {has_numbered}")
        spec = TranslationPromptSpec(
            source_language=source_language,
            target_language=target_language,
            output_format=output_format,
            has_numbered=has_numbered,
            context_type=context_type,
            kanbun=self.kanbun,
            system_note=self.system_note,
            user_note=self.user_note,
            has_table_markers=has_table_markers,
            tables=self.tables,
        )
        return spec.system_prompt(), spec.user_prompt()
    
    def build_prompts(self, text: str, source_language: str, target_language: str, output_format: str = "console", context_type: str = "none") -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for the given text without calling the API.

        Used by --dry-run mode to preview what would be sent to the model.
        """
        system_prompt, user_prompt_template = self._create_translation_prompt(source_language, target_language, output_format, text, context_type=context_type)
        return system_prompt, user_prompt_template + text

    def translate_text(self, text: str, source_language: str, target_language: str, output_format: str = "console", context_type: str = "none") -> "str | APISignal":
        """Translate text using the specified model with retry logic for content filters."""
        model = self._get_model()
        system_prompt, user_prompt_template = self._create_translation_prompt(source_language, target_language, output_format, text, context_type=context_type)
        user_prompt = user_prompt_template + text

        def body(attempt: int) -> Any:
            logging.debug(f'Making API call to model: {model}')
            system_role = get_model_system_role(model)
            response = self._call_translation_api(model, system_role, system_prompt, user_prompt)
            self._record_response_usage(response, model)
            if response.choices and len(response.choices) > 0 and response.choices[0].message:
                content = response.choices[0].message.content
                if content is not None and isinstance(content, str):
                    if not self._suppress_inline_print:
                        print("\n" + content)
                    return content
                return None  # content was None or wrong type — retry
            if not self._suppress_inline_print:
                print("\n[No content returned by the model]")
            logging.warning('No content returned by the model.')
            return ""  # terminal empty result

        return self._run_with_retry(
            body, model, "translation",
            timeout_msg="Translation returned no content after maximum retries.",
            return_signal_on_error=True,
        )

    def translate_page_text(self, abstract_text: str, page_text: str, previous_page: str, 
                          source_language: str, target_language: str, output_format: str = "console",
                          previous_translated: str = "") -> str:
        """Translate page text with context."""
        context_type = "abstract" if abstract_text else ("previous_page" if previous_page else "none")
        process_text = generate_process_text(abstract_text, page_text, previous_page, CONTEXT_PERCENTAGE, previous_translated)
        return self.translate_text(process_text, source_language, target_language, output_format, context_type=context_type)

    @staticmethod
    def _find_split_point(text: str, middle_index: int) -> int:
        """Find a natural split point near the middle of text.

        Prefers a double-newline paragraph boundary, then any sentence-ending
        punctuation. Falls back to the raw middle index if neither is found.
        """
        # Prefer a paragraph break within ±100 chars of the middle
        for offset in range(100):
            for candidate in (middle_index + offset, middle_index - offset):
                if 0 < candidate < len(text) and text[candidate:candidate + 2] == '\n\n':
                    return candidate + 2

        # Fall back to a sentence boundary within ±50 chars
        for offset in range(50):
            for candidate in (middle_index + offset, middle_index - offset):
                if 0 < candidate < len(text) and text[candidate] in '.!?。':
                    return candidate + 1

        return middle_index

    def generate_text(self, abstract_text: str, page_text: str, previous_page: str,
                     page_num: int, source_language: str, target_language: str, output_format: str = "console",
                     previous_translated: str = "") -> str:
        """Generate translated text for a page, handling context length limits."""
        # Short-circuit for image-only (blank) pages — no text to translate,
        # no API call, no error message in the output.
        if not page_text.strip():
            with self._blank_page_lock:
                self._blank_page_count += 1
            logging.debug(
                f"Page {page_num + 1} has no extractable text (likely image-only); skipping."
            )
            return f"\n\n-- Page {page_num + 1} -- \n"

        result: list[str] = []
        parts_to_translate: deque[str] = deque([page_text])
        
        # Debug logging
        logging.debug(f"Starting translation of page {page_num + 1}, original text length: {len(page_text)} chars")
        
        # Check for numbered citations in the original text
        citation_numbers = re.findall(_CITATION_NUM_RE, page_text)
        if citation_numbers:
            logging.debug(f"Page {page_num + 1} contains citation numbers: {citation_numbers}")

        while parts_to_translate:
            # Use popleft() to ensure FIFO processing - translate parts in the correct order
            current_part = parts_to_translate.popleft()
            logging.debug(f"Translating part {len(result) + 1} of page {page_num + 1}, length: {len(current_part)} chars")
            
            translated_text = self.translate_page_text(
                abstract_text, current_part, previous_page, source_language, target_language, output_format, previous_translated
            )

            if translated_text == APISignal.CONTEXT_LENGTH_EXCEEDED:
                # Split the text in half and add to FRONT of queue to maintain order
                middle_index = len(current_part) // 2
                split_point = self._find_split_point(current_part, middle_index)

                first_half = current_part[:split_point].strip()
                second_half = current_part[split_point:].strip()
                
                # Prepend in reverse order so first_half ends up at the front
                if second_half:
                    parts_to_translate.appendleft(second_half)
                if first_half:
                    parts_to_translate.appendleft(first_half)
                    
                logging.warning(f"Context length exceeded on page {page_num + 1}, split into {len([p for p in [first_half, second_half] if p])} parts")
                
            elif translated_text == APISignal.CONTENT_FILTER:
                result.append(f"\n***Content filter triggered on page {page_num + 1} - text skipped***\n")
                logging.error(f"Content filter triggered on page {page_num + 1}")
            elif translated_text == '':
                result.append(f"\n***Translation error on page {page_num + 1}.***\n")
                logging.error(f"Translation returned empty result on page {page_num + 1}")
            else:
                result.append(translated_text)
                logging.debug(f"Successfully translated part {len(result)} of page {page_num + 1}, output length: {len(translated_text)} chars")
                
                # Check if numbered citations were preserved in translation
                translated_numbers = re.findall(_CITATION_NUM_RE, translated_text)
                if translated_numbers:
                    logging.debug(f"Part {len(result)} of page {page_num + 1} contains translated numbers: {translated_numbers}")

        final_result = f"\n\n-- Page {page_num + 1} -- \n\n" + "\n".join(result)
        logging.debug(f"Completed translation of page {page_num + 1}, final length: {len(final_result)} chars")
        return final_result

    def _make_pdf_triples(
        self,
        pages: Iterable[PDFPage],
        start_page: int,
    ) -> Iterable[tuple[int, str, str]]:
        """Yield (index, page_text, previous_page_text) for each PDF page."""
        page_text = ""
        for i, page in enumerate(pages, start=start_page):
            previous_page = page_text
            page_text = self.pdf_processor.process_page(page)
            yield i, page_text, previous_page

    @staticmethod
    def _make_text_triples(
        text_pages: List[str],
    ) -> Iterable[tuple[int, str, str]]:
        """Yield (index, page_text, previous_page_text) for each pre-extracted text page."""
        previous_page = ""
        for i, page_text in enumerate(text_pages):
            yield i, page_text, previous_page
            previous_page = page_text

    def _translate_pages_parallel(
        self,
        all_triples: List[tuple[int, str, str]],
        abstract_text: str,
        source_language: str,
        target_language: str,
        output_format: str,
        unit_label: str,
        workers: int,
        opts: OutputOptions,
    ) -> List[str]:
        """Translate pages in parallel using a ThreadPoolExecutor.

        Each page is dispatched as an independent API call.  Previous-page
        context is the untranslated source text of the prior page (not the
        prior translation), because translation order is non-deterministic
        in parallel mode.

        Results are written to numbered temp files so that memory stays
        bounded even for very large documents.  Temp files are deleted in a
        ``finally`` block so they are cleaned up even if a worker raises.
        """
        n_pages = len(all_triples)
        actual_workers = min(workers, n_pages, MAX_PARALLEL_WORKERS)
        self._suppress_inline_print = True
        self._api_error_count = 0

        if opts.progressive_save:
            print(
                "Warning: --progressive-save is not compatible with parallel workers "
                "and has been disabled for this run."
            )
            logging.warning("progressive_save disabled: incompatible with workers > 1")

        if actual_workers < workers:
            cap_reason = (
                f"document has {n_pages} {unit_label}(s)"
                if actual_workers == n_pages
                else f"max_parallel_workers={MAX_PARALLEL_WORKERS} in settings.toml"
            )
            logging.info(f"workers capped at {actual_workers} ({cap_reason})")

        tmpdir = tempfile.mkdtemp(prefix="pu_sandbox_translate_")
        tmp_paths: Dict[int, str] = {}

        # Warm the pricing cache on the main thread before workers are dispatched.
        # Without this, all workers start simultaneously with an empty cache and
        # each independently fetches + logs the pricing sync.
        self._get_model()

        def _translate_one(index: int, page_text: str, previous_page: str) -> tuple[int, str]:
            translated = self.generate_text(
                abstract_text, page_text, previous_page, index,
                source_language, target_language, output_format,
                previous_translated="",  # no prior translation in parallel mode
            )
            tmp_path = os.path.join(tmpdir, f"page_{index:06d}.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(translated)
            return index, tmp_path

        try:
            futures: Dict = {}
            with ThreadPoolExecutor(max_workers=actual_workers) as executor:
                for idx, page_text, previous_page in all_triples:
                    future = executor.submit(_translate_one, idx, page_text, previous_page)
                    futures[future] = idx

                desc = f"Translating ({actual_workers} workers)... "
                baseline_tokens = self.token_tracker.usage_data["total_usage"].get("total_tokens", 0)
                baseline_cost = self.token_tracker.usage_data["total_usage"].get("total_cost", 0.0)

                with tqdm_logging():
                    with tqdm(total=n_pages, desc=desc, ascii=True) as pbar:
                        for future in as_completed(futures):
                            idx = futures[future]
                            try:
                                _, tmp_path = future.result()
                                tmp_paths[idx] = tmp_path
                            except Exception as e:
                                error_msg = f"\n***Translation error on {unit_label} {idx + 1}: {e}***\n"
                                logging.debug(f"Parallel worker error on {unit_label} {idx + 1}: {e}")
                                with self._api_error_lock:
                                    self._api_error_count += 1
                                try:
                                    tmp_path = os.path.join(tmpdir, f"page_{idx:06d}.tmp")
                                    with open(tmp_path, "w", encoding="utf-8") as f:
                                        f.write(error_msg)
                                    tmp_paths[idx] = tmp_path
                                except OSError:
                                    logging.debug(f"Could not write error temp file for {unit_label} {idx + 1}")
                            update_pbar_postfix(pbar, self.token_tracker.usage_data, baseline_tokens, baseline_cost)
                            pbar.update(1)

            # Assemble results in original page order
            document_text: list[str] = []
            for idx, _, _ in all_triples:
                tmp_path = tmp_paths.get(idx)
                if tmp_path and os.path.exists(tmp_path):
                    with open(tmp_path, "r", encoding="utf-8") as f:
                        document_text.append(f.read())
                else:
                    document_text.append(f"\n***Missing result for {unit_label} {idx + 1}***\n")

            return document_text

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _translate_page_sequence(
        self,
        page_triples: Iterable[tuple[int, str, str]],
        abstract_text: str,
        source_language: str,
        target_language: str,
        output_format: str,
        first_index: int,
        unit_label: str,
        opts: OutputOptions,
        input_file_path: Optional[str],
        workers: int = 1,
    ) -> List[str]:
        """Translate a sequence of (index, page_text, previous_page) triples.

        Shared by translate_document and translate_text_pages.  When
        ``workers > 1`` the generator is materialised up-front and pages are
        dispatched in parallel; otherwise the existing sequential path with
        per-page delays and optional progressive save is used.
        """
        if workers > 1:
            all_triples = list(page_triples)
            document_text = self._translate_pages_parallel(
                all_triples,
                abstract_text=abstract_text,
                source_language=source_language,
                target_language=target_language,
                output_format=output_format,
                unit_label=unit_label,
                workers=workers,
                opts=opts,
            )
        else:
            # --- sequential path ---
            document_text = []
            progressive_output_path: Optional[str] = None
            previous_translated = ""

            for i, page_text, previous_page in tqdm(page_triples, desc="Translating... ", ascii=True):
                try:
                    translated_text = self.generate_text(
                        abstract_text, page_text, previous_page, i,
                        source_language, target_language, output_format, previous_translated
                    )
                    document_text.append(translated_text)
                    previous_translated = translated_text

                    if i > first_index:
                        time.sleep(PAGE_DELAY_SECONDS)

                    if opts.progressive_save and (opts.output_file or opts.auto_save):
                        progressive_output_path = FileOutputHandler.save_page_progressively(
                            translated_text,
                            input_file_path,
                            opts.output_file,
                            opts.auto_save,
                            source_language,
                            target_language,
                            is_first_page=(i == first_index),
                        )

                except Exception as e:
                    error_message = f"\n***Translation error on {unit_label} {i + 1}: {e}***\n"
                    document_text.append(error_message)
                    print(f"Error on {unit_label} {i + 1}: {e}")

                    if opts.progressive_save and (opts.output_file or opts.auto_save):
                        FileOutputHandler.save_page_progressively(
                            error_message,
                            input_file_path,
                            opts.output_file,
                            opts.auto_save,
                            source_language,
                            target_language,
                            is_first_page=(i == first_index),
                        )
                    continue

            if opts.progressive_save and progressive_output_path:
                print(f"\nProgressive translation saved to: {progressive_output_path}")

        # --- post-run summary (both paths) ---
        with self._blank_page_lock:
            skipped = self._blank_page_count
            self._blank_page_count = 0
        if skipped:
            msg = (
                f"  {skipped} image-only {unit_label}(s) had no extractable text and were skipped"
                " (run with --verbose for details)."
            )
            print(msg)
            logging.info(msg.strip())

        with self._api_error_lock:
            failed = self._api_error_count
            self._api_error_count = 0
        if failed:
            msg = (
                f"  {failed} {unit_label}(s) failed due to API/connection errors and were skipped"
                " (run with --verbose for details)."
            )
            print(msg)
            logging.info(msg.strip())

        return document_text

    # ------------------------------------------------------------------
    # Table translation helpers (Markdown round-trip)
    # ------------------------------------------------------------------

    @staticmethod
    def _rows_to_markdown(rows: List[List[str]]) -> str:
        """Convert a cell grid to a Markdown table string.

        Rows with unequal column counts are padded to the maximum width.
        A separator row is inserted after the first (header) row.
        """
        if not rows:
            return ""
        ncols = max(len(row) for row in rows)
        lines: List[str] = []
        for i, row in enumerate(rows):
            padded = list(row) + [''] * (ncols - len(row))
            lines.append('| ' + ' | '.join(padded) + ' |')
            if i == 0:
                lines.append('|' + '|'.join(' --- ' for _ in range(ncols)) + '|')
        return '\n'.join(lines)

    @staticmethod
    def _parse_markdown_table(md: str) -> Optional[List[List[str]]]:
        """Parse a Markdown table string back to a cell grid.

        Separator rows (containing only ``-``, ``:``, and ``|``) are skipped.
        Returns ``None`` if no valid table rows are found.
        """
        rows: List[List[str]] = []
        for line in md.strip().splitlines():
            line = line.strip()
            if not line.startswith('|'):
                continue
            # Skip separator rows like |---|---|
            if not line.replace('|', '').replace('-', '').replace(' ', '').replace(':', ''):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            rows.append(cells)
        return rows if rows else None

    def translate_table_grid(
        self,
        rows: List[List[str]],
        source_language: str,
        target_language: str,
    ) -> List[List[str]]:
        """Translate a table's cell grid via a dedicated Markdown round-trip API call.

        Converts *rows* to a Markdown table, sends it to the model with a
        table-specific system prompt, then parses the response back to a grid.

        Falls back to the original *rows* if:
        * the API returns no content or an error signal,
        * the Markdown cannot be parsed, or
        * the translated row count does not match the original.
        """
        if not rows:
            return rows

        from .prompts import fragments as F

        md = TranslationService._rows_to_markdown(rows)
        system_prompt = F.TRANSLATION_TABLE_SYSTEM.format(
            source=source_language, target=target_language
        )

        model = self._get_model()
        system_role = get_model_system_role(model)

        def body(attempt: int) -> Any:
            response = self._call_translation_api(model, system_role, system_prompt, md)
            self._record_response_usage(response, model)
            if response.choices and response.choices[0].message:
                return response.choices[0].message.content
            return None

        result_md = self._run_with_retry(
            body, model, "table_translation",
            timeout_msg="Table translation returned no content after maximum retries.",
            return_signal_on_error=True,
        )

        if not result_md or isinstance(result_md, APISignal):
            logging.warning("Table translation failed — using original table content")
            return rows

        parsed = TranslationService._parse_markdown_table(str(result_md))
        if not parsed:
            logging.warning("Could not parse table translation response — using original")
            return rows

        if len(parsed) != len(rows):
            logging.warning(
                f"Table translation returned {len(parsed)} row(s), expected {len(rows)} — "
                "using original table content"
            )
            return rows

        return parsed

    @staticmethod
    def _resolve_output_format(opts: OutputOptions) -> str:
        if opts.output_file:
            ext = opts.output_file.lower().rsplit('.', 1)[-1] if '.' in opts.output_file else ''
            format_map = {'pdf': 'pdf', 'docx': 'docx', 'txt': 'txt'}
            return format_map.get(ext, 'file')
        if opts.auto_save:
            return 'txt'
        return 'console'

    def translate_document(self, pages: Iterable[PDFPage], abstract_text: Optional[str],
                           start_page: int, end_page: Optional[int],
                           source_language: str, target_language: str,
                           opts: OutputOptions = OutputOptions(),
                           input_file_path: Optional[str] = None,
                           workers: int = 1) -> List[str]:
        """Translate all pages in a document."""
        output_format = self._resolve_output_format(opts)
        pages = islice(pages, start_page, None if end_page is None else end_page + 1)
        return self._translate_page_sequence(
            self._make_pdf_triples(pages, start_page),
            abstract_text=abstract_text or '',
            source_language=source_language,
            target_language=target_language,
            output_format=output_format,
            first_index=start_page,
            unit_label='page',
            opts=opts,
            input_file_path=input_file_path,
            workers=workers,
        )
    
    def translate_text_pages(self, text_pages: List[str], abstract_text: Optional[str],
                            source_language: str, target_language: str,
                            opts: OutputOptions = OutputOptions(),
                            input_file_path: Optional[str] = None,
                            workers: int = 1) -> List[str]:
        """Translate pre-extracted text pages (e.g., from Word documents)."""
        output_format = self._resolve_output_format(opts)
        return self._translate_page_sequence(
            self._make_text_triples(text_pages),
            abstract_text=abstract_text or '',
            source_language=source_language,
            target_language=target_language,
            output_format=output_format,
            first_index=0,
            unit_label='section',
            opts=opts,
            input_file_path=input_file_path,
            workers=workers,
        )
