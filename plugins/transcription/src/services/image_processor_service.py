"""Image processing service for OCR operations — base plugin (English only)."""

import logging
from typing import Optional, Any

from ..models import (
    model_supports_vision, cannot_read_images_message, get_model_system_role,
    get_model_max_completion_tokens,
)
from .base_service import BaseService
from ..console import print_pass_result
from ..processors.image_processor import ImageProcessor
from ..tracking.token_tracker import TokenTracker
from .prompts import OcrPromptSpec

from ..settings import (
    OCR_ROLE,
    OCR_TEMPERATURE,
    OCR_MAX_TOKENS,
    OCR_TOP_P,
    OCR_FREQUENCY_PENALTY,
    OCR_PRESENCE_PENALTY,
)


class ImageProcessorService(BaseService):
    """Handles OCR operations using PortKey API.

    Base version: English only. Extension plugins may add language- or
    workflow-specific OCR options.
    """

    # Which models this service's work should use — see
    # ``src/runtime/model_role.py``. Read by ``BaseService._get_model()``.
    model_role = OCR_ROLE

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
        super().__init__(api_key, professor, token_tracker, token_tracker_file,
                         model, temperature, top_p, max_tokens)
        self.image_processor = ImageProcessor()

    def _create_ocr_prompt(
        self,
        target_language: str,
        vertical: bool = False,
        spread: bool = False,
    ) -> tuple[str, str]:
        """Create system and user prompts for OCR."""
        # Compatibility note: extension plugins may pass additional OCR kwargs
        # through shared dispatch paths. Accept compatible kwargs here to avoid
        # unexpected-keyword crashes.
        _ = (vertical, spread)
        spec = OcrPromptSpec(
            target_language=target_language,
            system_note=self.system_note,
            user_note=self.user_note,
        )
        return spec.system_prompt(), spec.user_prompt()

    def build_prompts(
        self,
        target_language: str,
        vertical: bool = False,
        spread: bool = False,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) without calling the API.

        Used by --dry-run mode to preview what would be sent to the model.
        """
        return self._create_ocr_prompt(target_language, vertical=vertical, spread=spread)

    def _build_refinement_prompt(
        self,
        target_language: str,
        vertical: bool = False,
        spread: bool = False,
    ) -> str:
        """Build the user prompt for a refinement pass (pass 2+)."""
        _ = (vertical, spread)
        spec = OcrPromptSpec(target_language=target_language)
        return spec.refinement_prompt()

    def _call_ocr_api(self, model: str, system_role: str, system_prompt: str,
                      user_prompt: str, data_url: str, max_tokens: int) -> Any:
        """Call the OCR API with the correct token-limit parameter for the model."""
        messages = self._image_messages(model, system_role, system_prompt, user_prompt, data_url)
        return self._ocr_completion(model, messages, max_tokens)

    def _call_refinement_api(self, model: str, system_role: str, system_prompt: str,
                              first_user_prompt: str, data_url: str,
                              prior_transcription: str, refinement_prompt: str,
                              max_tokens: int) -> Any:
        """Call the OCR API for a refinement pass, providing prior transcription as context."""
        messages = self._image_messages(model, system_role, system_prompt, first_user_prompt, data_url)
        messages += [
            {"role": "assistant", "content": prior_transcription},
            {"role": "user", "content": [
                {"type": "text", "text": refinement_prompt},
                self._build_image_content_block(model, data_url)
            ]},
        ]
        return self._ocr_completion(model, messages, max_tokens)

    def _ocr_completion(self, model: str, messages: list[dict[str, Any]], max_tokens: int) -> Any:
        """Send one OCR request with this service's sampling settings, and return the raw reply.

        The settings are the transcription defaults unless ``-t``/``-T`` asked
        for others, plus the penalties that discourage a model from repeating
        itself — a common way for a transcription to go wrong.
        """
        temperature = self.custom_temperature if self.custom_temperature is not None else OCR_TEMPERATURE
        top_p = self.custom_top_p if self.custom_top_p is not None else OCR_TOP_P
        if self.custom_temperature is not None or self.custom_top_p is not None:
            logging.debug(f"OCR API params: temperature={temperature}, top_p={top_p}")
        return self._create_completion(
            model, messages, max_tokens,
            temperature=temperature, top_p=top_p,
            frequency_penalty=OCR_FREQUENCY_PENALTY,
            presence_penalty=OCR_PRESENCE_PENALTY,
        )

    def _run_single_refinement_pass(self, model: str, system_role: str, system_prompt: str,
                                     user_prompt: str, data_url: str, prior_transcription: str,
                                     refinement_prompt: str, max_tokens: int, pass_num: int) -> str:
        """Execute one refinement pass with retry logic and return the refined transcription."""
        def body(attempt: int) -> Any:
            logging.debug(f"Making OCR refinement API call (pass {pass_num})")
            response = self._call_refinement_api(
                model, system_role, system_prompt,
                user_prompt, data_url, prior_transcription,
                refinement_prompt, max_tokens,
            )
            self._record_response_usage(response, model, critical=True)
            return self._text_or_retry(response, f"Refinement pass {pass_num}", attempt)

        return self._run_with_retry(
            body, model, f"OCR refinement pass {pass_num}",
            timeout_msg=f"OCR refinement pass {pass_num} returned no content after maximum retries",
        )

    def process_image_ocr(
        self,
        file_path: str,
        target_language: str,
        output_format: str = "console",
        vertical: bool = False,
        spread: bool = False,
        passes: int = 1,
    ) -> str:
        """Perform OCR on an image file using the specified model with retry logic.

        If passes > 1, each additional pass sends the image and prior transcription back
        to the model for review and correction.
        """
        model = self._get_model()
        catalog_model = self._catalog_model_name(model)

        if not model_supports_vision(catalog_model):
            raise ValueError(cannot_read_images_message(catalog_model))

        system_prompt, user_prompt = self._create_ocr_prompt(
            target_language,
            vertical=vertical,
            spread=spread,
        )

        data_url = self._image_for_request(self.image_processor, file_path)
        if data_url is None:
            return ""

        system_role = get_model_system_role(catalog_model)
        # A length asked for with -M wins, as it does for every other service;
        # otherwise the longest reply the catalog says this model can give.
        max_tokens = (self.custom_max_tokens if self.custom_max_tokens is not None
                      else get_model_max_completion_tokens(catalog_model, OCR_MAX_TOKENS))

        def body(attempt: int) -> Any:
            logging.debug("Making OCR API call")
            response = self._call_ocr_api(
                model, system_role, system_prompt, user_prompt, data_url, max_tokens
            )
            self._record_response_usage(response, model, critical=True)
            return self._text_or_retry(response, "OCR", attempt)

        transcription = self._run_with_retry(
            body, model, "OCR transcription",
            timeout_msg="OCR returned no content after maximum retries",
        )

        if passes > 1 and not self._suppress_inline_print:
            print_pass_result(f"Pass 1/{passes} result", transcription)

        refinement_prompt = self._build_refinement_prompt(
            target_language,
            vertical=vertical,
            spread=spread,
        )
        for pass_num in range(2, passes + 1):
            if not self._suppress_inline_print:
                print(f"  Pass {pass_num}/{passes}: Refining...")
            logging.info(f"Starting OCR refinement pass {pass_num}/{passes}")
            transcription = self._run_single_refinement_pass(
                model, system_role, system_prompt,
                user_prompt, data_url, transcription,
                refinement_prompt, max_tokens, pass_num,
            )
            if pass_num < passes and not self._suppress_inline_print:
                print_pass_result(f"Pass {pass_num}/{passes} result", transcription)

        return transcription
