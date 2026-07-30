"""Translates document text using the Princeton AI Sandbox's AI models, page by page.

This is the core AI-calling class behind the ``translate`` command. A plugin
author building a similar service can use this as a reference: it extends
``BaseService`` (which handles the actual network call and retry behavior)
and adds translation-specific logic on top — building translation prompts,
splitting oversized pages when a model's context window is exceeded,
translating in parallel across multiple pages, and handling tables
separately from regular prose so that rows and columns survive translation.
"""

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
    get_model_system_role,
    OutputOptions,
)
from .api_errors import APISignal
from .base_service import BaseService
from .parallel_utils import tqdm_logging, update_pbar_postfix, cap_worker_count
from .prompts import TranslationPromptSpec
from ..output.file_output import FileOutputHandler
from ..processors.pdf_processor import PDFProcessor, generate_process_text, detect_numbered_content
from ..runtime.ui_action import PageTextCallback, ProgressCallback
from ..tracking.token_tracker import TokenTracker
from .constants import PAGE_DELAY_SECONDS, MAX_PARALLEL_WORKERS
from ..settings import (
    TRANSLATION_ROLE,
    TRANSLATION_TEMPERATURE,
    TRANSLATION_MAX_TOKENS,
    TRANSLATION_TOP_P,
    CONTEXT_PERCENTAGE,
)

# Matches a citation/reference number in parentheses, in either CJK-style
# full-width brackets (（1）) or standard ASCII brackets ((1)) — used to check
# whether numbered references survived translation intact.
_CITATION_NUM_RE: str = r'[（\(](\d+)[）\)]'


class TranslationService(BaseService):
    """Translates document text into a target language using an AI model.

    Built and used internally by the translation plugin's ``run()`` method —
    a plugin author does not need to construct this directly, but studying
    its methods (especially ``translate_document`` and
    ``translate_text_pages``) is a useful reference for building a similar
    AI-calling service.
    """

    # Which models this service's work should use — see
    # ``src/runtime/model_role.py``. Read by ``BaseService._get_model()``.
    model_role = TRANSLATION_ROLE

    def __init__(self, api_key: str, professor: Optional[str] = None, token_tracker: Optional[TokenTracker] = None, token_tracker_file: Optional[str] = None, model: Optional[str] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, max_tokens: Optional[int] = None):
        """Set up a translation service for one professor's request.

        These parameters are supplied automatically by ``SandboxProcessor``
        when a plugin accesses ``self.translation_service`` — see
        ``BaseService.__init__`` for the full explanation of each one.
        """
        super().__init__(api_key, professor, token_tracker, token_tracker_file, model, temperature, top_p, max_tokens)
        self.pdf_processor = PDFProcessor()
        self.variant_notes: list[str] = []  # appended to system prompt; set by dispatching plugin
        self.tables: bool = False
        self.toc: bool = False
        # Tracks image-only (blank) pages skipped during a translation run
        self._blank_page_count: int = 0
        self._blank_page_lock = threading.Lock()
        # Tracks API/connection errors batched for summary in parallel mode
        self._api_error_count: int = 0
        self._api_error_lock = threading.Lock()

    def _call_translation_api(self, model: str, system_role: str,
                               system_prompt: str, user_prompt: str) -> Any:
        """Send one translation request to the AI model and return its raw response.

        Args:
            model: The model to call, e.g. ``'gpt-4o'``.
            system_role: The role name to use for the system-prompt message
                         (e.g. ``'system'`` or ``'developer'``, depending on
                         what the model expects).
            system_prompt: The instructions telling the model how to
                           translate (source/target language, formatting
                           rules, etc.).
            user_prompt: The actual text to be translated, plus any
                         surrounding context.

        Returns:
            The raw API response object, passed on to
            ``_record_response_usage()`` and ``_extract_response_content()``.
        """
        temperature, top_p, max_tokens = self._resolve_sampling_params(
            model, TRANSLATION_TEMPERATURE, TRANSLATION_TOP_P, TRANSLATION_MAX_TOKENS
        )
        messages = [
            {"role": system_role, "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self._create_completion(
            model, messages, max_tokens,
            temperature=temperature, top_p=top_p,
        )
    
    def _create_translation_prompt(self, source_language: str, target_language: str, output_format: str = "console", text: str = "", context_type: str = "none") -> tuple[str, str]:
        """Build the system and user prompt text that will be sent to the AI model.

        Args:
            source_language: The language the text is currently written in
                              (e.g. ``'Japanese'``).
            target_language: The language to translate into (e.g.
                              ``'English'``).
            output_format: The output destination, used to adjust formatting
                            instructions (e.g. ``'console'``, ``'pdf'``,
                            ``'docx'``, ``'txt'``).
            text: The text to be translated, used only to detect whether it
                  contains numbered references or table placeholder markers
                  so the prompt can include the right instructions.
            context_type: What kind of surrounding context is being supplied
                          alongside this page — ``'none'``, ``'abstract'``,
                          or ``'previous_page'``.

        Returns:
            A two-item tuple of ``(system_prompt, user_prompt_template)``.
        """
        has_numbered = detect_numbered_content(text) if text else False
        has_table_markers = '[TABLE_' in text if text else False
        logging.debug(f"Numbered content detected: {has_numbered}")
        spec = TranslationPromptSpec(
            source_language=source_language,
            target_language=target_language,
            output_format=output_format,
            has_numbered=has_numbered,
            context_type=context_type,
            variant_notes=self.variant_notes,
            system_note=self.system_note,
            user_note=self.user_note,
            has_table_markers=has_table_markers,
            tables=self.tables,
            toc=self.toc,
        )
        return spec.system_prompt(), spec.user_prompt()
    
    def build_prompts(self, text: str, source_language: str, target_language: str, output_format: str = "console", context_type: str = "none") -> tuple[str, str]:
        """Build the prompts that would be sent to the model, without actually calling it.

        Used by ``--dry-run`` mode so a user can preview exactly what would
        be sent to the AI before committing to an API call (and its cost).

        Args:
            text: The text that would be translated.
            source_language: The language the text is currently written in.
            target_language: The language it would be translated into.
            output_format: The output destination, used to adjust formatting
                            instructions.
            context_type: What kind of surrounding context would be
                          supplied — ``'none'``, ``'abstract'``, or
                          ``'previous_page'``.

        Returns:
            A two-item tuple of ``(system_prompt, user_prompt)`` exactly as
            they would be sent to the model.
        """
        system_prompt, user_prompt_template = self._create_translation_prompt(source_language, target_language, output_format, text, context_type=context_type)
        return system_prompt, user_prompt_template + text

    def translate_text(self, text: str, source_language: str, target_language: str, output_format: str = "console", context_type: str = "none") -> "str | APISignal":
        """Translate a single chunk of text into the target language.

        Retries automatically if the model's safety filter blocks the
        content or if the response comes back empty, up to the retry limit
        built into ``BaseService``.

        Args:
            text: The text to translate.
            source_language: The language the text is currently written in
                              (e.g. ``'Japanese'``).
            target_language: The language to translate into (e.g.
                              ``'English'``).
            output_format: The output destination, used to adjust formatting
                            instructions (e.g. ``'console'``, ``'pdf'``,
                            ``'docx'``, ``'txt'``).
            context_type: What kind of surrounding context is being supplied
                          — ``'none'``, ``'abstract'``, or ``'previous_page'``.

        Returns:
            The translated text, or a special marker value (an ``APISignal``)
            if the request hit the model's safety filter or its maximum
            context length instead of completing normally.
        """
        model = self._get_model()
        system_prompt, user_prompt_template = self._create_translation_prompt(source_language, target_language, output_format, text, context_type=context_type)
        user_prompt = user_prompt_template + text

        def body(attempt: int) -> Any:
            logging.debug(f'Making API call to model: {model}')
            system_role = get_model_system_role(model)
            response = self._call_translation_api(model, system_role, system_prompt, user_prompt)
            self._record_response_usage(response, model)
            content = self._extract_response_content(response)
            if content is not None:
                if not self._suppress_inline_print:
                    print("\n" + content)
                return content
            if response.choices and response.choices[0].message:
                return None  # choices present but content was None or wrong type — retry
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
        """Translate one page of a document, giving the model relevant surrounding context.

        Args:
            abstract_text: An abstract or summary of the whole document, if
                           one is available, used to help the model translate
                           terminology consistently. Empty string if none.
            page_text: The text of the page to translate.
            previous_page: The untranslated text of the page immediately
                           before this one, used as context. Empty string if
                           this is the first page.
            source_language: The language the document is currently written
                              in.
            target_language: The language to translate into.
            output_format: The output destination, used to adjust formatting
                            instructions.
            previous_translated: The already-translated text of the previous
                                 page, used as additional context in
                                 sequential (non-parallel) translation runs.
                                 Empty string if unavailable.

        Returns:
            The translated page text.
        """
        context_type = "abstract" if abstract_text else ("previous_page" if previous_page else "none")
        process_text = generate_process_text(abstract_text, page_text, previous_page, CONTEXT_PERCENTAGE, previous_translated)
        return self.translate_text(process_text, source_language, target_language, output_format, context_type=context_type)

    @staticmethod
    def _find_split_point(text: str, middle_index: int) -> int:
        """Find a natural place to break a page of text in half.

        Used when a page is too long for the model's context window and
        needs to be split into two smaller pieces for separate translation.
        Prefers to split at a paragraph break near the middle, then at a
        sentence-ending punctuation mark, so translated halves don't cut off
        mid-sentence when possible.

        Args:
            text: The text to find a split point within.
            middle_index: The rough midpoint character position to search
                          around.

        Returns:
            The character index to split at. Falls back to ``middle_index``
            exactly if no paragraph break or sentence ending is found nearby.
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
        """Translate one page of a document, automatically splitting it if it's too long.

        If the model reports that the page exceeds its maximum context
        length, the page is split in half (see ``_find_split_point``) and
        each half is translated separately, recursively splitting further if
        needed. Image-only pages (no extractable text) are skipped entirely
        with no API call made, since there's nothing to translate.

        Args:
            abstract_text: An abstract or summary of the whole document, if
                           available. Empty string if none.
            page_text: The text of the page to translate. An empty or
                       whitespace-only string is treated as an image-only
                       page and skipped.
            previous_page: The untranslated text of the previous page, used
                           as context.
            page_num: The zero-based page number (page 1 is ``0``), used for
                      the page marker written into the output and for log
                      messages.
            source_language: The language the document is currently written
                              in.
            target_language: The language to translate into.
            output_format: The output destination, used to adjust formatting
                            instructions.
            previous_translated: The already-translated text of the previous
                                 page, used as additional context.

        Returns:
            The translated page text, with a page marker (e.g.
            ``'-- Page 3 --'``) at the top so the output layer can locate
            page boundaries later (for example, to reinsert images at the
            right page in a PDF-to-Word translation).
        """
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
        """Turn a sequence of raw PDF pages into (page number, page text, previous page text) triples.

        Args:
            pages: The PDF pages to process, from the pdfminer library.
            start_page: The zero-based page number to start counting from.

        Yields:
            A ``(index, page_text, previous_page_text)`` tuple for each page,
            where ``previous_page_text`` is the untranslated text of the
            immediately preceding page (empty string for the first page).
        """
        page_text = ""
        for i, page in enumerate(pages, start=start_page):
            previous_page = page_text
            page_text = self.pdf_processor.process_page(page)
            yield i, page_text, previous_page

    @staticmethod
    def _make_text_triples(
        text_pages: List[str],
    ) -> Iterable[tuple[int, str, str]]:
        """Turn a list of already-extracted text pages into (page number, page text, previous page text) triples.

        Used for formats like Word documents where pages are extracted as
        plain text ahead of time, rather than read directly from a PDF.

        Args:
            text_pages: The document's text, already split into pages.

        Yields:
            A ``(index, page_text, previous_page_text)`` tuple for each page,
            where ``previous_page_text`` is the untranslated text of the
            immediately preceding page (empty string for the first page).
        """
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
        on_progress: Optional[ProgressCallback] = None,
    ) -> List[str]:
        """Translate every page of a document at the same time using multiple worker threads.

        Each page is sent to the AI model as an independent request, so
        pages can complete in any order. Because of this, each page uses the
        untranslated text of the previous page as context (rather than its
        translation, which might not exist yet). Results are written to
        temporary files on disk as they complete, rather than held in
        memory, so very large documents don't consume excessive memory; the
        temporary files are always cleaned up afterward, even if a worker
        thread fails partway through.

        Args:
            all_triples: The full list of ``(page_number, page_text,
                         previous_page_text)`` triples to translate, from
                         ``_make_pdf_triples`` or ``_make_text_triples``.
            abstract_text: An abstract or summary of the whole document, if
                           available.
            source_language: The language the document is currently written
                              in.
            target_language: The language to translate into.
            output_format: The output destination, used to adjust formatting
                            instructions.
            unit_label: What to call each unit of work in progress messages
                        (e.g. ``'page'`` or ``'section'``).
            workers: How many pages to translate at the same time.
            opts: The output settings for this run — used here only to check
                  whether progressive saving was requested, which isn't
                  supported in parallel mode and is disabled with a warning
                  if so.
            on_progress: Called with ``(completed_count, total_count)`` after
                         each page finishes, in *completion* order (which may
                         differ from page order — a plain count is safe to
                         report regardless of which page just finished,
                         unlike streaming a specific page's text). ``None``
                         (the default, and what every CLI call passes) means
                         no progress reporting — only the webui's background
                         job runner passes one. Previously silently
                         unsupported on this parallel path (only the local
                         console ``tqdm`` bar reported anything, invisible to
                         a caller running this in a background thread).

        Returns:
            The translated text for every page, in original page order
            (not completion order).
        """
        n_pages = len(all_triples)
        actual_workers = cap_worker_count(workers, n_pages, MAX_PARALLEL_WORKERS, unit_label, "document")
        self._suppress_inline_print = True
        self._api_error_count = 0

        if opts.progressive_save:
            print(
                "Warning: --progressive-save is not compatible with parallel workers "
                "and has been disabled for this run."
            )
            logging.warning("progressive_save disabled: incompatible with workers > 1")

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
            completed = 0
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
                            completed += 1
                            if on_progress is not None:
                                on_progress(completed, n_pages)

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
        on_progress: Optional[ProgressCallback] = None,
        total_units: Optional[int] = None,
        on_page_text: Optional[PageTextCallback] = None,
    ) -> List[str]:
        """Translate a whole document's pages, either one at a time or in parallel.

        Shared by ``translate_document`` and ``translate_text_pages``. When
        ``workers`` is greater than 1, every page is dispatched to run at
        the same time (see ``_translate_pages_parallel``). Otherwise, pages
        are translated one after another, with a short pause between API
        calls and optional progressive saving (writing each translated page
        to disk immediately, so progress isn't lost if the run is
        interrupted).

        Args:
            page_triples: The document's pages as ``(page_number, page_text,
                          previous_page_text)`` triples.
            abstract_text: An abstract or summary of the whole document, if
                           available.
            source_language: The language the document is currently written
                              in.
            target_language: The language to translate into.
            output_format: The output destination, used to adjust formatting
                            instructions.
            first_index: The zero-based page number of the very first page
                         being translated, used to calculate delays between
                         requests.
            unit_label: What to call each unit of work in progress and error
                        messages (e.g. ``'page'`` or ``'section'``).
            opts: The output settings for this run (whether to save
                  progressively, the output file path, etc.).
            input_file_path: The original source file's path, used to name
                             the progressively-saved output file. ``None``
                             if progressive saving wasn't requested.
            workers: How many pages to translate at the same time. ``1``
                     translates pages one after another instead.
            on_progress: Called with ``(completed_count, total_count)`` after
                         each page finishes. Honored on *both* paths below —
                         sequential (in page order) and parallel (in
                         completion order, forwarded to
                         ``_translate_pages_parallel``'s own ``on_progress``).
                         A plain count is a meaningful signal regardless of
                         which page just finished, unlike streaming a
                         specific page's text (see ``on_page_text`` below),
                         so there's no reason to withhold it just because
                         ``workers > 1``.
            total_units: How many pages ``page_triples`` will yield, if
                         known up front — filled in as the "total" half of
                         ``on_progress``'s callback. Meaningless (and
                         ignored) if ``on_progress`` is ``None``.
            on_page_text: Called with ``(page_number, translated_text)``
                          right after each page's translation succeeds — a
                          sibling to ``on_progress`` carrying the actual
                          text instead of just a count (see
                          ``PageTextCallback``'s docstring for why these
                          are separate callbacks). Same sequential-only
                          restriction as ``on_progress``, and not called at
                          all for a page that errors (the error message is
                          still printed and appended to the returned text,
                          same as before this existed — a webui job just
                          won't get a live ping for that one page, only the
                          final result once everything finishes).

        Returns:
            The translated text for every page, in original page order.
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
                on_progress=on_progress,
            )
        else:
            # --- sequential path ---
            document_text = []
            progressive_output_path: Optional[str] = None
            previous_translated = ""
            completed_units = 0

            for i, page_text, previous_page in tqdm(page_triples, desc="Translating... ", ascii=True):
                try:
                    translated_text = self.generate_text(
                        abstract_text, page_text, previous_page, i,
                        source_language, target_language, output_format, previous_translated
                    )
                    document_text.append(translated_text)
                    previous_translated = translated_text

                    if on_page_text is not None:
                        on_page_text(i + 1, translated_text)

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
                            "Translation",
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
                            "Translation",
                            is_first_page=(i == first_index),
                        )
                    continue

                finally:
                    if on_progress is not None:
                        completed_units += 1
                        on_progress(completed_units, total_units if total_units is not None else completed_units)

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
        """Convert a table's cell grid into a Markdown-formatted table string.

        Markdown is a simple text formatting style where a table is written
        using vertical bars (``|``) to separate columns and a row of dashes
        under the header. This is the format sent to the AI model for table
        translation, since models handle plain text more reliably than raw
        table objects.

        Args:
            rows: The table's cell text, as a list of rows (each row a list
                  of cell strings). Rows with unequal column counts are
                  padded to match the widest row.

        Returns:
            The table formatted as Markdown text, with a separator row
            inserted after the header row.
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
        """Convert a Markdown-formatted table string back into a cell grid.

        The reverse of ``_rows_to_markdown``, used to read the AI model's
        translated table response back into rows and columns.

        Args:
            md: The Markdown table text returned by the model.

        Returns:
            The table's cell text as a list of rows, or ``None`` if no valid
            table rows could be found in ``md``.
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
        """Translate a table's cell text, keeping its row-and-column structure intact.

        Sends the table to the AI model as Markdown (a simple text
        formatting style using pipe characters to represent rows and
        columns) with instructions specific to table translation, then
        converts the model's response back into a grid of cells.

        Args:
            rows: The table's original cell text, as a list of rows (each
                  row a list of cell strings).
            source_language: The language the table is currently written in.
            target_language: The language to translate into.

        Returns:
            The translated cell grid, in the same row/column shape as
            ``rows``. Falls back to returning ``rows`` unchanged if the
            model's response is empty, can't be parsed back into a table, or
            doesn't have the same number of rows as the original — this
            keeps the original content visible rather than losing it if
            translation fails.
        """
        if not rows:
            return rows

        from .prompts import translation_fragments as F

        md = TranslationService._rows_to_markdown(rows)
        system_prompt = F.TRANSLATION_TABLE_SYSTEM.format(
            source=source_language, target=target_language
        )

        model = self._get_model()
        system_role = get_model_system_role(model)

        def body(attempt: int) -> Any:
            response = self._call_translation_api(model, system_role, system_prompt, md)
            self._record_response_usage(response, model)
            return self._extract_response_content(response)

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
        """Decide which formatting instructions to use, based on the requested output file type.

        Args:
            opts: The output settings for this run.

        Returns:
            ``'pdf'``, ``'docx'``, or ``'txt'`` if the output file has a
            matching extension; ``'txt'`` if auto-save was requested with no
            explicit extension; or ``'console'`` if the result is only being
            printed to the screen.
        """
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
                           workers: int = 1,
                           on_progress: Optional[ProgressCallback] = None,
                           total_units: Optional[int] = None,
                           on_page_text: Optional[PageTextCallback] = None) -> List[str]:
        """Translate every page of a PDF (or other page-based document) into the target language.

        Args:
            pages: The document's pages, from the pdfminer library.
            abstract_text: An abstract or summary of the whole document, if
                           one is available, used to help the model
                           translate terminology consistently. ``None`` if
                           there is no abstract.
            start_page: The zero-based page number to start translating from
                        (page 1 is ``0``).
            end_page: The zero-based page number to stop translating at,
                      inclusive. ``None`` translates through to the end of
                      the document.
            source_language: The language the document is currently written
                              in (e.g. ``'Japanese'``).
            target_language: The language to translate into (e.g.
                              ``'English'``).
            opts: The output-related settings for this run (output file
                  path, whether to save progressively, etc.).
            input_file_path: The original source file's path, used to name
                             progressively-saved output files. ``None`` if
                             progressive saving wasn't requested.
            workers: How many pages to translate at the same time. ``1``
                     (the default) translates pages one after another.
            on_progress: Called with ``(completed_count, total_count)`` after
                         each page finishes (success or error), in page
                         order. ``None`` (the default — what every CLI call
                         passes) means no progress reporting; only the
                         webui's background job runner passes one. Only
                         honored when ``workers`` is ``1`` — see
                         ``_translate_page_sequence``'s docstring for why
                         the parallel path doesn't support this yet.
            total_units: How many pages this call will translate, if known
                         up front (the caller already computed this to build
                         the page range) — used only to fill in the
                         "total" half of ``on_progress``'s callback.
                         ``None`` if unknown or if ``on_progress`` isn't set.
            on_page_text: Called with ``(page_number, translated_text)``
                          right after each page finishes — see
                          ``_translate_page_sequence``'s docstring.

        Returns:
            The translated text for every requested page, in page order.
        """
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
            on_progress=on_progress,
            total_units=total_units,
            on_page_text=on_page_text,
        )

    def translate_text_pages(self, text_pages: List[str], abstract_text: Optional[str],
                            source_language: str, target_language: str,
                            opts: OutputOptions = OutputOptions(),
                            input_file_path: Optional[str] = None,
                            workers: int = 1,
                            on_progress: Optional[ProgressCallback] = None,
                            on_page_text: Optional[PageTextCallback] = None) -> List[str]:
        """Translate a list of already-extracted text pages (e.g. from a Word document).

        Used instead of ``translate_document`` for source formats that don't
        come as PDF pages — the text has already been split into logical
        pages ahead of time.

        Args:
            text_pages: The document's text, already split into pages.
            abstract_text: An abstract or summary of the whole document, if
                           available. ``None`` if there is no abstract.
            source_language: The language the document is currently written
                              in.
            target_language: The language to translate into.
            opts: The output-related settings for this run.
            input_file_path: The original source file's path, used to name
                             progressively-saved output files. ``None`` if
                             progressive saving wasn't requested.
            workers: How many pages to translate at the same time. ``1``
                     (the default) translates pages one after another.
            on_progress: Called with ``(completed_count, total_count)`` after
                         each page finishes (success or error), in page
                         order. ``None`` (the default) means no progress
                         reporting. Only honored when ``workers`` is ``1``.
            on_page_text: Called with ``(page_number, translated_text)``
                          right after each page finishes — see
                          ``_translate_page_sequence``'s docstring.

        Returns:
            The translated text for every page, in page order.
        """
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
            on_progress=on_progress,
            total_units=len(text_pages),
            on_page_text=on_page_text,
        )
