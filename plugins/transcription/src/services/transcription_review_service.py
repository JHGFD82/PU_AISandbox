"""Transcription review service — base plugin.

Reviews AI OCR output for errors and misreadings.  Returns a structured JSON report.
"""

import json
import logging
import re
from typing import Any, Optional

from ..models import (
    get_default_model,
    get_model_system_role,
    maybe_sync_model_pricing,
    resolve_model,
)
from ..tracking.token_tracker import TokenTracker
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

    This is the base version. Extension plugins may augment review guidance and
    parameters for additional scripts or workflows.
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

    def _get_model(self) -> str:
        """Get the model to use for transcription review, preferring the catalog's transcription_review default."""
        review_default = get_default_model("transcription_review")
        model = resolve_model(requested_model=self.custom_model, prefer_model=review_default)
        maybe_sync_model_pricing(model)
        return model

    def build_prompts(
        self,
        language: str,
        text: str = "[transcription text would appear here]",
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) without calling the API.

        Used by --dry-run and --notes preview modes.
        """
        spec = TranscriptionReviewPromptSpec(
            language=language,
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
        temperature, top_p, max_tokens = self._resolve_sampling_params(
            model, TRANSCRIPTION_REVIEW_TEMPERATURE, TRANSCRIPTION_REVIEW_TOP_P, TRANSCRIPTION_REVIEW_MAX_TOKENS
        )
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
            Full language name (e.g. ``"English"``), as returned by
            ``parse_single_language_code``.
        kanbun, kanbun_main:
            Accepted for signature compatibility with extension-plugin
            overrides (e.g. East Asian kanbun review guidance). Unused by
            this base (English-only) implementation.
        """
        spec = TranscriptionReviewPromptSpec(
            language=language,
            system_note=self.system_note,
            user_note=self.user_note,
        )
        system_prompt = spec.system_prompt()
        user_prompt = spec.user_prompt(text)

        model = self._get_model()
        system_role = get_model_system_role(model)

        def body(_attempt: int) -> Optional[str]:
            response = self._call_api(model, system_role, system_prompt, user_prompt)
            self._record_response_usage(response, model, critical=False)
            raw = ""
            if response.choices and response.choices[0].message:
                raw = response.choices[0].message.content or ""
            return self._inject_model_and_validate(raw, model, language)

        return self._run_with_retry(body, model, "transcription_review")
