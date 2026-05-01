"""Combined OCR + translation service — single vision prompt resolves ambiguous characters using translation context."""

import logging
import os
import re
from typing import Any, Optional

from ..models import (
    get_model_system_role, model_supports_vision, get_vision_capable_models,
    get_model_max_completion_tokens, resolve_model, get_default_model,
    maybe_sync_model_pricing,
)
from .base_service import BaseService
from ..processors.image_processor import ImageProcessor
from ..tracking.token_tracker import TokenTracker
from .constants import MAX_RETRIES
from .prompts import ImageTranslationPromptSpec

from ..settings import IMAGE_TRANSLATION_MAX_TOKENS, IMAGE_TRANSLATION_TEMPERATURE


class ImageTranslationService(BaseService):
    """Handles combined OCR + translation from images in a single API call.

    Designed for reasoning-capable vision models (e.g. gpt-5) that can hold
    both transcription and translation in context simultaneously, resolving
    ambiguous characters using translation context rather than guessing during
    a separate OCR pass.

    Returns both a transcript and a translation so the caller can present or
    save either or both.
    """

    def __init__(
        self,
        api_key: str,
        professor: Optional[str] = None,
        token_tracker: Optional[TokenTracker] = None,
        token_tracker_file: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(api_key, professor, token_tracker, token_tracker_file, model, temperature, top_p, max_tokens)
        self.image_processor = ImageProcessor()
        self.tables: bool = False
        # Set to True in parallel mode to suppress per-image console output
        self._suppress_inline_print: bool = False

    def _get_model(self) -> str:
        """Get model to use, preferring the catalog image_translation default."""
        img_trans_default = get_default_model("image_translation")
        model = resolve_model(
            requested_model=self.custom_model,
            prefer_model=img_trans_default,
            require_vision=True,
        )
        maybe_sync_model_pricing(model)
        if not self.custom_model and model != img_trans_default:
            logging.warning(
                f"Preferred image translation model '{img_trans_default}' not available. "
                f"Using '{model}' instead."
            )
        return model

    def _get_max_tokens(self, model: str) -> int:
        """Get token budget for this model, using per-model catalog override if set."""
        if self.custom_max_tokens is not None:
            return self.custom_max_tokens
        return get_model_max_completion_tokens(model, IMAGE_TRANSLATION_MAX_TOKENS)

    def _build_system_prompt(self, source_language: str, target_language: str, vertical: bool = False, spread: bool = False) -> str:
        spec = ImageTranslationPromptSpec(
            source_language=source_language,
            target_language=target_language,
            vertical=vertical,
            spread=spread,
            tables=self.tables,
        )
        return spec.system_prompt()

    def _build_user_prompt(self, source_language: str, target_language: str, vertical: bool = False, spread: bool = False) -> str:
        spec = ImageTranslationPromptSpec(
            source_language=source_language,
            target_language=target_language,
            vertical=vertical,
            spread=spread,
            tables=self.tables,
        )
        return spec.user_prompt()

    def build_prompts(self, source_language: str, target_language: str, vertical: bool = False, spread: bool = False) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) without calling the API.

        Used by --dry-run mode to preview what would be sent to the model.
        """
        spec = ImageTranslationPromptSpec(
            source_language=source_language,
            target_language=target_language,
            vertical=vertical,
            spread=spread,
            tables=self.tables,
            system_note=self.system_note,
            user_note=self.user_note,
        )
        return spec.system_prompt(), spec.user_prompt()

    def _call_api(
        self,
        model: str,
        system_role: str,
        system_prompt: str,
        user_prompt: str,
        data_url: str,
        max_tokens: int,
    ) -> Any:
        """Call the API with parameters appropriate for the given model."""
        messages: list[dict[str, Any]] = [
            {"role": system_role, "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        temperature = self.custom_temperature if self.custom_temperature is not None else IMAGE_TRANSLATION_TEMPERATURE
        if self.custom_temperature is not None:
            logging.debug(f"Image translation API params: temperature={temperature}")
        return self._create_completion(model, messages, max_tokens, temperature=temperature)

    def _parse_response(self, content: str) -> tuple[str, str]:
        """Extract [TRANSCRIPT] and [TRANSLATION] sections from the model response.

        Falls back to treating the full response as the translation if the
        expected section headers are absent.
        """
        transcript_match = re.search(
            r"\[TRANSCRIPT\](.*?)(?=\[TRANSLATION\]|\Z)", content, re.DOTALL
        )
        translation_match = re.search(r"\[TRANSLATION\](.*)", content, re.DOTALL)

        transcript = transcript_match.group(1).strip() if transcript_match else ""
        translation = translation_match.group(1).strip() if translation_match else ""

        if not transcript and not translation:
            logging.warning(
                "Could not parse [TRANSCRIPT]/[TRANSLATION] sections from response; "
                "treating full response as translation."
            )
            translation = content.strip()

        return transcript, translation

    def process_image_translation(
        self,
        file_path: str,
        source_language: str,
        target_language: str,
        vertical: bool = False,
        spread: bool = False,
    ) -> tuple[str, str]:
        """Transcribe and translate an image in a single API call.

        Args:
            file_path: Absolute path to the image file.
            source_language: Language of text in the image (e.g. 'Chinese').
            target_language: Language to translate into (e.g. 'English').
            vertical: Whether the text is predominantly vertical (top-to-bottom, right-to-left).
            spread: Whether the image is a two-page spread (two facing pages).

        Returns:
            (transcript, translation) — either may be empty if parsing fails.

        Raises:
            ValueError: If the selected model does not support vision.
            RuntimeError: If no valid response is received after all retries.
        """
        model = self._get_model()

        if not model_supports_vision(model):
            vision_models = get_vision_capable_models()
            raise ValueError(
                f"Model '{model}' does not support image processing. "
                f"Vision-capable models: {vision_models}"
            )

        system_role = get_model_system_role(model)
        system_prompt, user_prompt = self.build_prompts(source_language, target_language, vertical=vertical, spread=spread)
        max_tokens = self._get_max_tokens(model)

        try:
            data_url = self.image_processor.local_image_to_data_url(file_path)
        except Exception as e:
            logging.error(f"Failed to read image {os.path.basename(file_path)}: {e}")
            raise

        def body(attempt: int) -> Any:
            logging.debug(
                f"Making image translation API call to model: {model} "
                f"(system role: {system_role}, max_tokens: {max_tokens})"
            )
            response = self._call_api(
                model, system_role, system_prompt, user_prompt, data_url, max_tokens
            )
            self._record_response_usage(response, model, critical=True)
            if (
                response.choices
                and len(response.choices) > 0
                and response.choices[0].message
            ):
                content = response.choices[0].message.content
                if content is None:
                    logging.warning(
                        f"Response content is None. "
                        f"Raw message: {response.choices[0].message}"
                    )
                    return None
                if not isinstance(content, str):
                    logging.warning(
                        f"Unexpected content type {type(content)}: {content!r}. Retrying..."
                    )
                    return None
                if not content.strip():
                    logging.warning(
                        f"Empty response (attempt {attempt + 1}/{MAX_RETRIES}). Retrying..."
                    )
                    return None
                return self._parse_response(content)
            logging.warning("No choices in API response. Retrying...")
            return None

        return self._run_with_retry(
            body, model, "image translation",
            timeout_msg=(
                "Image translation returned no content after maximum retries — "
                "check model response format in debug logs."
            ),
        )
