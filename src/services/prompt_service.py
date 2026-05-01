"""Custom prompt service for direct AI interaction."""

import logging
from typing import Any, Optional

from ..models import (
    get_model_system_role,
    get_model_max_completion_tokens,
)
from ..tracking.token_tracker import TokenTracker
from .api_errors import handle_api_errors
from .base_service import BaseService


from ..settings import (
    DEFAULT_SYSTEM_PROMPT,
    PROMPT_MAX_TOKENS,
    PROMPT_TEMPERATURE,
    PROMPT_TOP_P,
)


class PromptService(BaseService):
    """Sends custom prompts to the AI model and returns the response."""

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
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) without calling the API.

        Used by --dry-run mode to preview what would be sent to the model.
        """
        return system_prompt or DEFAULT_SYSTEM_PROMPT, user_prompt

    def _call_api(self, model: str, system_role: str, system_prompt: str, user_prompt: str) -> Any:
        """Call the API with parameters appropriate for the given model."""
        temperature = self.custom_temperature if self.custom_temperature is not None else PROMPT_TEMPERATURE
        top_p = self.custom_top_p if self.custom_top_p is not None else PROMPT_TOP_P
        if self.custom_temperature is not None or self.custom_top_p is not None:
            logging.info(f"Prompt API params: temperature={temperature}, top_p={top_p}")
        messages = [
            {"role": system_role, "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        max_tokens = self.custom_max_tokens if self.custom_max_tokens is not None else get_model_max_completion_tokens(model, PROMPT_MAX_TOKENS)
        return self._create_completion(
            model, messages, max_tokens,
            temperature=temperature, top_p=top_p,
        )

    def send_prompt(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send a custom prompt and return the response text."""
        model = self._get_model()
        system_role = get_model_system_role(model)
        effective_system = system_prompt if system_prompt else DEFAULT_SYSTEM_PROMPT

        logging.info(f"Sending custom prompt to model: {model}")
        try:
            response = self._call_api(model, system_role, effective_system, user_prompt)
        except Exception as e:
            handle_api_errors(e, model)
            raise

        self._record_response_usage(response, model)
        return self._extract_response_content(response) or ""
