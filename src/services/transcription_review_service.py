"""Transcription review service — reviews AI OCR output for errors and misreadings."""

import json
import logging
import re
from typing import Any, Optional

from ..models import (
    get_model_system_role,
    get_model_max_completion_tokens,
)
from ..tracking.token_tracker import TokenTracker
from .api_errors import handle_api_errors
from .base_service import BaseService
from .prompts import TranscriptionReviewPromptSpec
from ..settings import (
    TRANSCRIPTION_REVIEW_TEMPERATURE,
    TRANSCRIPTION_REVIEW_TOP_P,
    TRANSCRIPTION_REVIEW_MAX_TOKENS,
)


class TranscriptionReviewService(BaseService):
    """Reviews AI-transcribed text for OCR errors and returns a structured JSON report.

    The model assesses the overall quality of the transcription, identifies the
    probable source, and reports each suspected error with candidates in descending
    confidence order.  The actual model name used is injected into ``meta.model``
    by the service after parsing the response, rather than relying on the model to
    self-report its name.
    """

    def __init__(
        self,
        api_key: str,
        professor: Optional[str] = None,
        token_tracker: Optional[TokenTracker] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(api_key, professor, token_tracker, None, model, temperature, top_p, max_tokens)

    def build_prompts(
        self,
        language: str,
        kanbun: bool = False,
        kanbun_main: bool = False,
        text: str = "[transcription text would appear here]",
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) without calling the API.

        Used by --dry-run and --notes preview modes.
        """
        spec = TranscriptionReviewPromptSpec(
            language=language,
            kanbun=kanbun,
            kanbun_main=kanbun_main,
            system_note=self.system_note,
            user_note=self.user_note,
        )
        return spec.system_prompt(), spec.user_prompt(text)

    def _call_api(
        self,
        model: str,
        system_role: str,
        system_prompt: str,
        user_prompt: str,
    ) -> Any:
        temperature = self.custom_temperature if self.custom_temperature is not None else TRANSCRIPTION_REVIEW_TEMPERATURE
        top_p = self.custom_top_p if self.custom_top_p is not None else TRANSCRIPTION_REVIEW_TOP_P
        max_tokens = self.custom_max_tokens if self.custom_max_tokens is not None else get_model_max_completion_tokens(model, TRANSCRIPTION_REVIEW_MAX_TOKENS)
        messages = [
            {"role": system_role, "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self._create_completion(
            model, messages, max_tokens,
            temperature=temperature, top_p=top_p,
        )

    @staticmethod
    def _inject_model_and_validate(raw: str, model: str, language: str) -> str:
        """Strip markdown fences, inject the model name, and return pretty-printed JSON.

        If the response cannot be parsed as JSON, returns the raw string with a
        logged warning so the caller still has something to show the user.
        """
        # Some models wrap JSON in ```json ... ``` despite being told not to.
        clean = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        clean = re.sub(r"\n?```$", "", clean).strip()

        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            logging.warning(
                "TranscriptionReviewService: model returned non-JSON response; "
                "displaying raw output."
            )
            return raw

        # Inject the actual model name — more reliable than asking the model to self-report.
        if isinstance(data.get("meta"), dict):
            data["meta"]["model"] = model
            if not data["meta"].get("language"):
                data["meta"]["language"] = language

        return json.dumps(data, ensure_ascii=False, indent=2)

    def review_transcription(
        self,
        text: str,
        language: str,
        kanbun: bool = False,
        kanbun_main: bool = False,
    ) -> str:
        """Review a transcription and return a JSON report string.

        Parameters
        ----------
        text:
            The transcription text to review.
        language:
            Full language name (e.g. ``"Japanese"``), as returned by
            ``parse_single_language_code``.
        kanbun:
            Whether the text contains kanbun with kundoku annotations.
        kanbun_main:
            Whether the transcription was produced in main-character-only mode
            (okurigana, furigana, kaeriten omitted intentionally).
        """
        model = self._get_model()
        system_role = get_model_system_role(model)
        spec = TranscriptionReviewPromptSpec(
            language=language,
            kanbun=kanbun,
            kanbun_main=kanbun_main,
            system_note=self.system_note,
            user_note=self.user_note,
        )
        system_prompt = spec.system_prompt()
        user_prompt = spec.user_prompt(text)

        logging.info(f"Reviewing transcription ({language}, {len(text)} chars) with model: {model}")

        try:
            response = self._call_api(model, system_role, system_prompt, user_prompt)
        except Exception as e:
            handle_api_errors(e, model)
            raise

        self._record_response_usage(response, model)
        content = self._extract_response_content(response)
        if content is not None:
            return self._inject_model_and_validate(content.strip(), model, language)
        return ""
