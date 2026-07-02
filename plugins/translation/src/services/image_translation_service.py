"""Reads and translates text found in images (e.g. scanned book pages), in one AI call.

Instead of first extracting text from an image (optical character
recognition, or OCR) and then translating it as a separate step, this
service sends the image straight to a vision-capable AI model along with
instructions to transcribe and translate at the same time. This works
better for handwriting or unusual fonts, because the model can use its
understanding of the target language to resolve an ambiguous character
based on what makes sense in context, rather than guessing during a
transcription-only pass with no translation context available yet.
"""

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
    """Transcribes and translates the text in an image using one vision-capable AI model call.

    Built and used internally by the translation plugin's ``run()`` method
    for image-based inputs (e.g. photos or scans) — a plugin author does not
    need to construct this directly, but ``process_image_translation`` below
    is a useful reference for handling images and vision models in a plugin
    of your own.

    Returns both the transcribed original-language text and its translation,
    so the caller can present or save either one, or both.
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
        """Set up an image translation service for one professor's request.

        These parameters are supplied automatically by ``SandboxProcessor``
        when a plugin accesses ``self.image_translation_service`` — see
        ``BaseService.__init__`` for the full explanation of each one.
        """
        super().__init__(api_key, professor, token_tracker, token_tracker_file, model, temperature, top_p, max_tokens)
        self.image_processor = ImageProcessor()
        self.tables: bool = False

    def _get_model(self) -> str:
        """Decide which vision-capable AI model to use, syncing its price if needed.

        Uses the model the user explicitly requested if one was given (as
        long as it supports reading images), otherwise falls back to the
        catalog's configured image-translation default. Also makes sure the
        model's price is up to date before returning it.
        """
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
        """Decide the maximum response length to request from the model, in tokens.

        Tokens are the small chunks of text (roughly a word or word-piece)
        that AI models process and bill by. Uses the user's explicit
        override if one was given, otherwise the model catalog's configured
        limit for this specific model.

        Args:
            model: The model whose token limit is being looked up, e.g.
                   ``'gpt-4o'``.

        Returns:
            The maximum number of tokens the model may generate in its
            response.
        """
        if self.custom_max_tokens is not None:
            return self.custom_max_tokens
        return get_model_max_completion_tokens(model, IMAGE_TRANSLATION_MAX_TOKENS)

    def _build_system_prompt(self, source_language: str, target_language: str, vertical: bool = False, spread: bool = False) -> str:
        """Build the system prompt (the model's standing instructions) for one image translation request.

        Args:
            source_language: The language the image's text is written in.
            target_language: The language to translate into.
            vertical: Whether the text runs top-to-bottom, right-to-left,
                      as in traditional vertical Japanese or Chinese layout.
            spread: Whether the image shows two facing pages side by side
                    (a two-page spread) rather than a single page.

        Returns:
            The system prompt text.
        """
        spec = ImageTranslationPromptSpec(
            source_language=source_language,
            target_language=target_language,
            vertical=vertical,
            spread=spread,
            tables=self.tables,
        )
        return spec.system_prompt()

    def _build_user_prompt(self, source_language: str, target_language: str, vertical: bool = False, spread: bool = False) -> str:
        """Build the user-facing prompt text that accompanies the image in the API request.

        Args:
            source_language: The language the image's text is written in.
            target_language: The language to translate into.
            vertical: Whether the text runs top-to-bottom, right-to-left.
            spread: Whether the image shows two facing pages side by side.

        Returns:
            The user prompt text.
        """
        spec = ImageTranslationPromptSpec(
            source_language=source_language,
            target_language=target_language,
            vertical=vertical,
            spread=spread,
            tables=self.tables,
        )
        return spec.user_prompt()

    def build_prompts(self, source_language: str, target_language: str, vertical: bool = False, spread: bool = False) -> tuple[str, str]:
        """Build the prompts that would be sent to the model, without actually calling it.

        Used by ``--dry-run`` mode so a user can preview exactly what would
        be sent to the AI before committing to an API call (and its cost).

        Args:
            source_language: The language the image's text is written in.
            target_language: The language to translate into.
            vertical: Whether the text runs top-to-bottom, right-to-left.
            spread: Whether the image shows two facing pages side by side.

        Returns:
            A two-item tuple of ``(system_prompt, user_prompt)`` exactly as
            they would be sent to the model.
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
        """Send one image-translation request to the AI model and return its raw response.

        Args:
            model: The model to call, e.g. ``'gpt-4o'``.
            system_role: The role name to use for the system-prompt message
                         (e.g. ``'system'`` or ``'developer'``, depending on
                         what the model expects).
            system_prompt: The standing instructions telling the model how
                           to transcribe and translate.
            user_prompt: The user-facing instructions accompanying the
                         image.
            data_url: The image, encoded as a data URL (the image's raw
                      bytes converted to text so it can be embedded directly
                      in the API request instead of hosted separately).
            max_tokens: The maximum length, in tokens, the model may
                        generate in its response.

        Returns:
            The raw API response object, passed on to
            ``_record_response_usage()`` and then parsed by the caller.
        """
        messages: list[dict[str, Any]] = [
            {"role": system_role, "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    self._build_image_content_block(model, data_url),
                ],
            },
        ]
        temperature = self.custom_temperature if self.custom_temperature is not None else IMAGE_TRANSLATION_TEMPERATURE
        if self.custom_temperature is not None:
            logging.debug(f"Image translation API params: temperature={temperature}")
        return self._create_completion(model, messages, max_tokens, temperature=temperature)

    def _parse_response(self, content: str) -> tuple[str, str]:
        """Split the model's raw response into its transcript and translation sections.

        The model is instructed to mark its response with ``[TRANSCRIPT]``
        and ``[TRANSLATION]`` section headers; this method extracts the text
        under each one.

        Args:
            content: The model's raw response text.

        Returns:
            A ``(transcript, translation)`` tuple. Both are empty strings if
            the model signaled that the image had no readable text (an
            illustration-only page). If the expected section headers are
            missing from an otherwise non-empty response, the whole response
            is treated as the translation and the transcript is left empty.
        """
        # Model signals no readable text on this page (illustration-only)
        if content.strip() == "[NO_TEXT]":
            logging.debug("Image page contains no readable text (illustration-only); skipping.")
            return "", ""

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
        """Transcribe and translate the text found in one image, in a single AI model call.

        Args:
            file_path: The absolute path to the image file on disk (e.g.
                       ``/Users/heller/scans/page_012.png``).
            source_language: The language of the text in the image (e.g.
                              ``'Chinese'``).
            target_language: The language to translate into (e.g.
                              ``'English'``).
            vertical: Whether the text runs top-to-bottom, right-to-left, as
                      in traditional vertical Japanese or Chinese layout.
            spread: Whether the image shows two facing pages side by side (a
                    two-page spread) rather than a single page.

        Returns:
            A ``(transcript, translation)`` tuple. If the image is blank or
            contains no readable text, both are empty strings and no API
            call is made.

        Raises:
            ValueError: If the selected model does not support reading
                images (vision).
            RuntimeError: If no valid response is received after all retry
                attempts.
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

        if self.image_processor.is_blank_image(file_path):
            logging.info(
                f"Skipping blank image {os.path.basename(file_path)} — no API call made."
            )
            return "", ""

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
